"""Unit tests for reconcile (shell/tasks/reconcile.py).

libtmux and the tmux CLI are mocked: these tests never touch a real server.
"""
from __future__ import annotations

import sqlite3
import sys
from unittest.mock import MagicMock, patch

import pytest

_CREATE_TASKS = """
CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    goal         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'starting',
    tmux_window_id TEXT,
    pid          INTEGER,
    step_count   INTEGER NOT NULL DEFAULT 0,
    last_output  TEXT,
    created_at   TEXT NOT NULL,
    ended_at     TEXT
)
"""


@pytest.fixture(autouse=True)
def mock_libtmux():
    with patch.dict(sys.modules, {"libtmux": MagicMock()}):
        yield


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "sessions.db")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_TASKS)
    conn.commit()
    conn.close()
    return path


def _add_task(db_path: str, name: str, status: str, window_id: str | None) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO tasks (name, goal, status, tmux_window_id, created_at)"
        " VALUES (?, 'goal', ?, ?, '2026-01-01T00:00:00Z')",
        (name, status, window_id),
    )
    conn.commit()
    conn.close()


def _status(db_path: str, name: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT status FROM tasks WHERE name=?", (name,)).fetchone()[0]
    finally:
        conn.close()


def _run_reconcile(db_path: str, live_window_ids: list[str], session_found: bool = True):
    """Call reconcile() with a fake tmux session exposing live_window_ids."""
    from shell.tasks import reconcile as reconcile_module

    session = MagicMock()
    session.session_name = "sable-test"
    session.windows = [MagicMock(window_id=wid) for wid in live_window_ids]

    server = MagicMock()
    server.sessions = [session] if session_found else []

    completed = MagicMock()
    completed.stdout = "sable-test\n"

    with patch.object(reconcile_module.libtmux, "Server", return_value=server), \
         patch("subprocess.run", return_value=completed):
        return reconcile_module.reconcile(db_path)


def test_no_tasks_returns_empty(db_path):
    assert _run_reconcile(db_path, live_window_ids=[]) == []


def test_missing_tasks_table_returns_empty(tmp_path):
    empty = str(tmp_path / "empty.db")
    sqlite3.connect(empty).close()
    assert _run_reconcile(empty, live_window_ids=[]) == []


def test_task_with_live_window_is_left_running(db_path):
    _add_task(db_path, "alive", "running", "@1")

    lost = _run_reconcile(db_path, live_window_ids=["@1"])

    assert lost == []
    assert _status(db_path, "alive") == "running"


def test_task_with_dead_window_is_marked_lost(db_path):
    _add_task(db_path, "gone", "running", "@2")

    lost = _run_reconcile(db_path, live_window_ids=["@1"])

    assert lost == ["gone"]
    assert _status(db_path, "gone") == "lost"


def test_task_without_window_id_is_reported_lost(db_path):
    _add_task(db_path, "never-started", "starting", None)

    lost = _run_reconcile(db_path, live_window_ids=["@1"])

    assert lost == ["never-started"]


@pytest.mark.parametrize("status", ["running", "starting", "paused"])
def test_all_live_statuses_are_reconciled(db_path, status):
    _add_task(db_path, f"task-{status}", status, "@9")

    assert _run_reconcile(db_path, live_window_ids=[]) == [f"task-{status}"]


@pytest.mark.parametrize("status", ["completed", "lost"])
def test_finished_statuses_are_left_alone(db_path, status):
    _add_task(db_path, f"task-{status}", status, "@9")

    assert _run_reconcile(db_path, live_window_ids=[]) == []
    assert _status(db_path, f"task-{status}") == status


def test_all_tasks_lost_when_no_session_is_found(db_path):
    _add_task(db_path, "orphan", "running", "@1")

    lost = _run_reconcile(db_path, live_window_ids=["@1"], session_found=False)

    assert lost == ["orphan"]
    assert _status(db_path, "orphan") == "lost"


def test_mixed_tasks_report_only_the_dead_ones(db_path):
    _add_task(db_path, "alive", "running", "@1")
    _add_task(db_path, "dead", "running", "@2")

    lost = _run_reconcile(db_path, live_window_ids=["@1"])

    assert lost == ["dead"]
    assert _status(db_path, "alive") == "running"
