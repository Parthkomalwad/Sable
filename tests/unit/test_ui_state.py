"""Phase 4 Task 0: the read-only state layer both UIs render."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys

import pytest

from sable.ui import state


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "sessions.db"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE tasks (id INTEGER PRIMARY KEY, name TEXT, goal TEXT, status TEXT,
                            step_count INTEGER, last_output TEXT);
        CREATE TABLE task_events (task_name TEXT, prompt_tokens INT, completion_tokens INT, cost_usd REAL);
        CREATE TABLE policy_queue (id INTEGER PRIMARY KEY, agent TEXT, command TEXT, rule TEXT,
                                   why TEXT, status TEXT);
        CREATE TABLE breaker_trips (id INTEGER PRIMARY KEY, job TEXT, reason TEXT, status TEXT);
        CREATE TABLE agent_events (id INTEGER PRIMARY KEY, agent TEXT, kind TEXT, payload_json TEXT);
    """)
    c.executemany("INSERT INTO tasks (name, goal, status, step_count, last_output) VALUES (?,?,?,?,?)", [
        ("build", "build it", "running", 3, "ok"),
        ("deploy", "deploy it", "running", 1, ""),
        ("old", "old work", "completed", 7, "done"),
    ])
    c.executemany("INSERT INTO task_events VALUES (?,?,?,?)", [("build", 100, 50, 0.01), ("build", 10, 5, 0.002)])
    c.execute("INSERT INTO policy_queue (agent, command, rule, why, status) VALUES "
              "('deploy', 'rm -rf build', 'recursive-delete', 'no undo', 'pending')")
    c.execute("INSERT INTO breaker_trips (job, reason, status) VALUES ('crawler', 'turns limit reached', 'tripped')")
    c.executemany("INSERT INTO agent_events (agent, kind, payload_json) VALUES (?,?,?)", [
        ("build", "status", json.dumps({"command": "make"})),
        ("build", "status", json.dumps({"command": "make test"})),
    ])
    c.commit()
    c.close()
    return path


def test_agents_have_badges_and_cost(db):
    rows = {a.name: a for a in state.agents(db)}
    assert rows["build"].badge == "running" and rows["build"].cost_usd == pytest.approx(0.012)
    assert rows["build"].tokens == 165
    assert rows["deploy"].badge == state.AWAITING       # a pending approval overrides "running"
    assert rows["old"].badge == "done"
    assert [a.name for a in state.agents(db)] == ["old", "deploy", "build"]   # newest first


def test_inbox_lists_approvals_then_trips(db):
    items = state.inbox(db)
    assert [(i.kind, i.agent) for i in items] == [("approval", "deploy"), ("breaker", "crawler")]
    assert items[0].why == "recursive-delete: no undo"


def test_tail_is_oldest_first(db):
    assert state.tail("build", 5, db) == ["status: make", "status: make test"]


def test_missing_db_or_tables_are_empty_not_errors(tmp_path):
    assert state.agents(tmp_path / "nope.db") == []
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert state.agents(empty) == [] and state.inbox(empty) == [] and state.tail("x", 5, empty) == []


def test_reads_never_write(db):
    before = db.stat().st_mtime_ns
    state.agents(db), state.inbox(db), state.tail("build", 5, db)
    assert db.stat().st_mtime_ns == before


def test_the_shell_never_imports_textual():
    """The case for textual rests on the shell not paying for it at start."""
    code = ("import sys, sable.app.main, sable.app.repl, sable.ui.state; "
            "print('textual' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.stdout.strip() == "False", out.stderr[-500:]
