# tests/unit/test_db_v3.py
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch


def _make_db():
    """Create a Database instance pointing at a temp file."""
    tmp = tempfile.mktemp(suffix=".db")
    with patch("shell.telemetry.db.DB_PATH", Path(tmp)):
        from shell.telemetry.db import Database
        return Database(), tmp


def test_tasks_table_exists():
    db, path = _make_db()
    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "tasks" in tables
    assert "task_events" in tables
    assert "task_memory" in tables
    assert "skill_patterns" in tables
    conn.close()
    db.close()


def test_tasks_columns():
    db, path = _make_db()
    conn = sqlite3.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    for expected in ("id", "name", "goal", "status", "tmux_window_id", "pid",
                     "step_count", "last_output", "created_at", "ended_at"):
        assert expected in cols, f"Missing column: {expected}"
    conn.close()
    db.close()


def test_skill_patterns_columns():
    db, path = _make_db()
    conn = sqlite3.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(skill_patterns)").fetchall()}
    for expected in ("id", "pattern_hash", "repo_path", "command_sequence",
                     "intent_keywords", "occurrence_count", "crystallised", "last_seen"):
        assert expected in cols, f"Missing column: {expected}"
    conn.close()
    db.close()
