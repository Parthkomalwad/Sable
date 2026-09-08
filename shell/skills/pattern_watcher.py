"""PatternWatcher session-end observer for command pattern detection.

Called at /exit. Reads audit.log, groups commands by repo path and intent
keywords, upserts skill_patterns table, returns patterns that crossed
threshold (occurrence_count >= 3, crystallised = 0).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

STOPWORDS = frozenset({
    "the", "a", "an", "in", "at", "to", "for", "of", "and", "or", "is", "it"
})
THRESHOLD = 3


def compute_pattern_hash(repo_path: str, keywords: list[str]) -> str:
    """SHA256(sorted([repo_path] + sorted(keywords)) joined by '|')."""
    parts = sorted([repo_path] + sorted(keywords))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


class PatternWatcher:
    def __init__(self, audit_log_path: str | None = None, db_path: str | None = None) -> None:
        from shell.telemetry.db import AUDIT_LOG_PATH, DB_PATH
        self._log_path = Path(audit_log_path or str(AUDIT_LOG_PATH))
        self._db_path = db_path or str(DB_PATH)

    def _read_log(self) -> list[dict]:
        if not self._log_path.exists():
            return []
        entries = []
        with open(self._log_path, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 4:
                    continue
                ts, session_id, cwd, command = parts[0], parts[1], parts[2], parts[3]
                entries.append({
                    "ts": ts,
                    "session_id": session_id,
                    "cwd": os.path.realpath(cwd),
                    "command": command,
                })
        return entries

    def _extract_keywords(self, commands: list[str]) -> list[str]:
        tokens = set()
        for cmd in commands:
            for word in cmd.split():
                w = word.lower().strip(".-_")
                if w and w not in STOPWORDS and len(w) > 2:
                    tokens.add(w)
        return sorted(tokens)

    def _command_sequence(self, commands: list[str]) -> str:
        """Command names only, no arguments."""
        names = []
        for cmd in commands:
            name = cmd.strip().split()[0] if cmd.strip() else ""
            if name:
                names.append(name)
        return "|".join(names)

    def observe(self) -> list[dict]:
        """Read audit.log, group patterns, upsert DB, return threshold crossers."""
        entries = self._read_log()

        groups: dict[tuple, list[str]] = defaultdict(list)
        for e in entries:
            groups[(e["cwd"], e["session_id"])].append(e["command"])

        repo_sequences: dict[str, list[list[str]]] = defaultdict(list)
        for (repo_path, _), commands in groups.items():
            repo_sequences[repo_path].append(commands)

        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        now = datetime.now(timezone.utc).isoformat()

        crossed: list[dict] = []

        for repo_path, sessions in repo_sequences.items():
            all_commands = [cmd for session_cmds in sessions for cmd in session_cmds]
            keywords = self._extract_keywords(all_commands)
            if not keywords:
                continue
            seq = self._command_sequence(all_commands)
            ph = compute_pattern_hash(repo_path, keywords)

            existing = conn.execute(
                "SELECT id, occurrence_count, crystallised FROM skill_patterns WHERE pattern_hash=?",
                (ph,),
            ).fetchone()

            if existing:
                new_count = existing[1] + len(sessions)
                conn.execute(
                    "UPDATE skill_patterns SET occurrence_count=?, last_seen=? WHERE pattern_hash=?",
                    (new_count, now, ph),
                )
                if new_count >= THRESHOLD and existing[2] == 0:
                    crossed.append({
                        "pattern_hash": ph,
                        "repo_path": repo_path,
                        "occurrence_count": new_count,
                        "intent_keywords": keywords,
                        "command_sequence": seq,
                    })
            else:
                count = len(sessions)
                conn.execute(
                    """INSERT INTO skill_patterns
                       (pattern_hash, repo_path, command_sequence, intent_keywords,
                        occurrence_count, crystallised, last_seen)
                       VALUES (?, ?, ?, ?, ?, 0, ?)""",
                    (ph, repo_path, seq, json.dumps(keywords), count, now),
                )
                if count >= THRESHOLD:
                    crossed.append({
                        "pattern_hash": ph,
                        "repo_path": repo_path,
                        "occurrence_count": count,
                        "intent_keywords": keywords,
                        "command_sequence": seq,
                    })

        conn.commit()
        conn.close()
        return crossed
