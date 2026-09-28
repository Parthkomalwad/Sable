"""Phase 4 Task 2: the Textual sidebar, driven headless against a temp DB."""
from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from datetime import datetime, timezone

import pytest

pytest.importorskip("textual")

from sable.ui.sidebar import app as sidebar


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE tasks (id INTEGER PRIMARY KEY, name TEXT, goal TEXT, status TEXT,
                            step_count INTEGER, last_output TEXT);
        CREATE TABLE task_events (task_name TEXT, prompt_tokens INT, completion_tokens INT, cost_usd REAL);
        CREATE TABLE policy_queue (id INTEGER PRIMARY KEY, agent TEXT, command TEXT, rule TEXT,
                                   why TEXT, status TEXT);
        CREATE TABLE breaker_trips (id INTEGER PRIMARY KEY, job TEXT, reason TEXT, status TEXT);
        CREATE TABLE token_events (id INTEGER PRIMARY KEY, timestamp TEXT, total_tokens INT, cost_usd REAL);
        CREATE TABLE snippets (id INTEGER PRIMARY KEY, command TEXT, note TEXT, tags TEXT,
                               use_count INT, created_at TEXT);
    """)
    c.executemany("INSERT INTO tasks (name, goal, status, step_count, last_output) VALUES (?,?,?,?,?)", [
        ("build", "build it", "running", 3, ""),
        ("deploy", "deploy it", "running", 1, ""),
        ("old", "old work", "failed", 7, ""),
    ])
    c.execute("INSERT INTO policy_queue (agent, command, rule, why, status) VALUES "
              "('deploy', 'rm -rf build', 'recursive-delete', 'no undo', 'pending')")
    c.execute("INSERT INTO token_events (timestamp, total_tokens, cost_usd) VALUES (?, 100, 0.25)",
              (datetime.now(timezone.utc).isoformat(),))
    c.execute("INSERT INTO snippets (command, note, tags, use_count) VALUES ('make test', 'tests', '', 3)")
    c.commit()
    c.close()
    # Keep the test off the host: no git or tmux subprocesses.
    monkeypatch.setattr(sidebar, "_git", lambda: "not a git repo")
    monkeypatch.setattr(sidebar, "_window_width", lambda: None)
    return path


def test_snapshot_reads_badges_inbox_cost_snippets(db):
    snap = sidebar.snapshot(db)
    assert [(a.name, a.badge) for a in snap.agents] == [
        ("old", "failed"), ("deploy", "awaiting approval"), ("build", "running")]
    assert len(snap.inbox) == 1
    assert snap.today == pytest.approx(0.25)
    assert snap.days[-1] == pytest.approx(0.25) and len(snap.days) == 7
    assert snap.snippets == ["tests: make test"]


def _run(db, size, check, resize_to=None):
    async def go():
        app = sidebar.SidebarApp(db_path=db)
        async with app.run_test(size=size) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            check(app, size[0])
            if resize_to:
                await pilot.resize_terminal(*resize_to)
                await pilot.pause()
                check(app, resize_to[0])
    asyncio.run(go())


def _text(app, wid):
    return str(app.query_one(f"#{wid}").render())


def test_badges_and_inbox_render(db):
    def check(app, _w):
        agents = _text(app, "agents")
        assert "awaiting approval" in agents and "running" in agents and "failed" in agents
        assert "INBOX (1)" in _text(app, "inbox")
        assert "$0.2500" in _text(app, "cost")
    _run(db, (100, 50), check)


def test_hides_below_90_columns_and_comes_back(db):
    def check(app, width):
        narrow = app.query_one("#narrow")
        body = app.query_one("#body")
        assert narrow.display == (width < 90)
        assert body.display == (width >= 90)
    _run(db, (100, 50), check, resize_to=(80, 50))
    _run(db, (80, 50), check, resize_to=(120, 50))


def test_never_writes_the_db(db):
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    _run(db, (100, 50), lambda app, w: None)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert not db.with_name(db.name + "-wal").exists()
