"""Unit tests for PatternWatcher (shell/skills/pattern_watcher.py).

Uses a temp audit.log and a temp SQLite db. No LLM calls, no network.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from sable.skills.watcher import (
    THRESHOLD,
    PatternWatcher,
    compute_pattern_hash,
)

_CREATE_SKILL_PATTERNS = """
CREATE TABLE IF NOT EXISTS skill_patterns (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_hash     TEXT NOT NULL UNIQUE,
    repo_path        TEXT NOT NULL,
    command_sequence TEXT NOT NULL,
    intent_keywords  TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    crystallised     INTEGER NOT NULL DEFAULT 0,
    last_seen        TEXT NOT NULL
)
"""


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "sessions.db"
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_SKILL_PATTERNS)
    conn.commit()
    conn.close()
    return str(path)


def _write_log(tmp_path, rows: list[tuple[str, str, str]]) -> str:
    """rows are (session_id, cwd, command). Returns the log path."""
    log = tmp_path / "audit.log"
    ts = datetime.now(timezone.utc).isoformat()
    log.write_text(
        "".join(f"{ts}\t{sid}\t{cwd}\t{cmd}\n" for sid, cwd, cmd in rows),
        encoding="utf-8",
    )
    return str(log)


def _rows(db_path: str) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT pattern_hash, repo_path, occurrence_count, crystallised"
            " FROM skill_patterns"
        ).fetchall()
    finally:
        conn.close()


class TestComputePatternHash:
    def test_is_deterministic(self):
        assert compute_pattern_hash("/repo", ["b", "a"]) == compute_pattern_hash(
            "/repo", ["b", "a"]
        )

    def test_ignores_keyword_order(self):
        assert compute_pattern_hash("/repo", ["a", "b"]) == compute_pattern_hash(
            "/repo", ["b", "a"]
        )

    def test_differs_by_repo(self):
        assert compute_pattern_hash("/one", ["a"]) != compute_pattern_hash("/two", ["a"])

    def test_differs_by_keywords(self):
        assert compute_pattern_hash("/repo", ["a"]) != compute_pattern_hash("/repo", ["z"])


class TestObserve:
    def test_missing_log_returns_no_patterns(self, tmp_path, db_path):
        watcher = PatternWatcher(
            audit_log_path=str(tmp_path / "nope.log"), db_path=db_path
        )
        assert watcher.observe() == []

    def test_single_session_is_below_threshold(self, tmp_path, db_path):
        log = _write_log(tmp_path, [("s1", str(tmp_path), "docker compose build")])
        watcher = PatternWatcher(audit_log_path=log, db_path=db_path)

        assert watcher.observe() == []
        assert _rows(db_path)[0][2] == 1

    def test_threshold_sessions_cross_and_are_returned(self, tmp_path, db_path):
        repo = str(tmp_path)
        rows = [
            (f"s{i}", repo, cmd)
            for i in range(1, THRESHOLD + 1)
            for cmd in ("docker compose build", "docker compose up -d")
        ]
        watcher = PatternWatcher(audit_log_path=_write_log(tmp_path, rows), db_path=db_path)

        crossed = watcher.observe()

        assert len(crossed) == 1
        assert crossed[0]["occurrence_count"] >= THRESHOLD
        assert crossed[0]["repo_path"] == repo
        assert "docker" in crossed[0]["intent_keywords"]

    def test_command_sequence_records_names_without_arguments(self, tmp_path, db_path):
        rows = [
            (f"s{i}", str(tmp_path), cmd)
            for i in range(1, THRESHOLD + 1)
            for cmd in ("git status", "git push origin main")
        ]
        watcher = PatternWatcher(audit_log_path=_write_log(tmp_path, rows), db_path=db_path)

        crossed = watcher.observe()

        assert set(crossed[0]["command_sequence"].split("|")) == {"git"}

    def test_counts_accumulate_across_runs(self, tmp_path, db_path):
        repo = str(tmp_path)
        log = _write_log(tmp_path, [("s1", repo, "make test")])
        watcher = PatternWatcher(audit_log_path=log, db_path=db_path)

        watcher.observe()
        watcher.observe()

        assert _rows(db_path)[0][2] == 2

    def test_already_crystallised_pattern_is_not_returned_again(self, tmp_path, db_path):
        repo = str(tmp_path)
        rows = [(f"s{i}", repo, "make deploy") for i in range(1, THRESHOLD + 1)]
        watcher = PatternWatcher(audit_log_path=_write_log(tmp_path, rows), db_path=db_path)
        assert len(watcher.observe()) == 1

        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE skill_patterns SET crystallised=1")
        conn.commit()
        conn.close()

        assert watcher.observe() == []

    def test_separate_repos_are_separate_patterns(self, tmp_path, db_path):
        one = tmp_path / "one"
        two = tmp_path / "two"
        one.mkdir()
        two.mkdir()
        rows = [("s1", str(one), "make build"), ("s2", str(two), "make build")]
        watcher = PatternWatcher(audit_log_path=_write_log(tmp_path, rows), db_path=db_path)

        watcher.observe()

        assert len({r[0] for r in _rows(db_path)}) == 2

    def test_malformed_lines_are_skipped(self, tmp_path, db_path):
        log = tmp_path / "audit.log"
        ts = datetime.now(timezone.utc).isoformat()
        log.write_text(
            "not-a-row\n"
            "still\tnot\tenough\n"
            f"{ts}\ts1\t{tmp_path}\tmake test\n",
            encoding="utf-8",
        )
        watcher = PatternWatcher(audit_log_path=str(log), db_path=db_path)

        watcher.observe()

        assert len(_rows(db_path)) == 1

    def test_stopwords_and_short_tokens_are_not_keywords(self, tmp_path, db_path):
        rows = [("s1", str(tmp_path), "the a in at to deploy")]
        watcher = PatternWatcher(audit_log_path=_write_log(tmp_path, rows), db_path=db_path)

        watcher.observe()

        keywords = json.loads(
            sqlite3.connect(db_path)
            .execute("SELECT intent_keywords FROM skill_patterns")
            .fetchone()[0]
        )
        assert "deploy" in keywords
        for stopword in ("the", "a", "in", "at", "to"):
            assert stopword not in keywords

    def test_commands_with_no_usable_keywords_are_ignored(self, tmp_path, db_path):
        rows = [("s1", str(tmp_path), "a to in")]
        watcher = PatternWatcher(audit_log_path=_write_log(tmp_path, rows), db_path=db_path)

        assert watcher.observe() == []
        assert _rows(db_path) == []
