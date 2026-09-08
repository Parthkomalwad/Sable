"""TaskMemory versioned context snapshots for task agents.

- Goal is always pinned at position 0 in context (never compressed).
- After each completed step: replace exchange with 1-2 sentence summary.
- Only current step's raw turns kept verbatim.
- Snapshots saved to ~/tasks/<name>/.agentic/memory/vN.json.
- Skills are content-hashed; if same hash seen in prior snapshot, skip re-embedding.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


class TaskMemory:
    def __init__(self, task_name: str, tasks_base_dir: str, db) -> None:
        self._name = task_name
        self._base = Path(tasks_base_dir).expanduser() / task_name / ".agentic" / "memory"
        self._base.mkdir(parents=True, exist_ok=True)
        self._db = db
        self._goal: str = ""
        self._turns: list[dict] = []
        self._version: int = 0
        self._seen_skill_hashes: set[str] = set()

    def set_goal(self, goal: str) -> None:
        self._goal = goal

    def add_turns(self, turns: list[dict]) -> None:
        self._turns.extend(turns)

    def summarise_last_step(self, summary: str) -> None:
        """Replace last step's raw turns with a 1-2 sentence summary."""
        self._turns = [{"role": "summary", "content": summary}]

    def build_context(self) -> list[dict]:
        """Return messages list with pinned goal at position 0."""
        pinned = {"role": "system", "content": f"[GOAL] {self._goal}"}
        return [pinned] + self._turns

    def save_snapshot(self) -> int:
        """Write current state to vN.json and return the version number."""
        self._version += 1
        snap = {
            "version": self._version,
            "goal": self._goal,
            "turns": self._turns,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        path = self._base / f"v{self._version}.json"
        path.write_text(json.dumps(snap, indent=2))
        try:
            self._db._conn.execute(
                """INSERT INTO task_memory (task_name, version, path, token_count, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (self._name, self._version, str(path), 0, snap["created_at"]),
            )
            self._db._conn.commit()
        except Exception:
            pass  # DB write is best-effort; file is the source of truth
        return self._version

    def load_snapshot(self, version: int) -> dict:
        """Load a specific snapshot by version number."""
        path = self._base / f"v{version}.json"
        return json.loads(path.read_text())

    def register_skill_hash(self, skill_name: str, content: str) -> str:
        """Hash skill content; mark as seen. Returns hash."""
        h = hashlib.sha256(content.encode()).hexdigest()
        self._seen_skill_hashes.add(h)
        return h

    def is_skill_seen(self, content_hash: str) -> bool:
        return content_hash in self._seen_skill_hashes
