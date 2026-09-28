"""Phase 4 Task 3: the /dash command center."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from unittest import mock

import pytest

pytest.importorskip("textual")

from sable.ui import dash  # noqa: E402


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "sessions.db"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE tasks (id INTEGER PRIMARY KEY, name TEXT, goal TEXT, status TEXT,
                            step_count INTEGER, last_output TEXT);
        CREATE TABLE task_events (task_name TEXT, prompt_tokens INT, completion_tokens INT, cost_usd REAL);
        CREATE TABLE agent_events (id INTEGER PRIMARY KEY, agent TEXT, kind TEXT, payload_json TEXT);
    """)
    c.executemany("INSERT INTO tasks (name, goal, status, step_count, last_output) VALUES (?,?,?,?,?)", [
        ("build", "build it", "running", 3, "ok"),
        ("deploy", "deploy it", "running", 1, ""),
    ])
    c.execute("INSERT INTO task_events VALUES ('build', 100, 50, 0.01)")
    c.execute("INSERT INTO agent_events (agent, kind, payload_json) VALUES ('build', 'status', ?)",
              (json.dumps({"command": "make test"}),))
    c.commit()
    from sable.policy import breaker, queue
    queue.ensure_table(c)
    breaker.ensure_table(c)
    c.execute("INSERT INTO policy_queue (created_at, agent, command, rule, why) "
              "VALUES (0, 'deploy', 'rm -rf build', 'recursive-delete', 'no undo')")
    c.execute("INSERT INTO policy_queue (created_at, agent, command, rule, why) "
              "VALUES (0, 'build', 'git push -f', 'force-push', 'rewrites history')")
    c.execute("INSERT INTO breaker_trips (created_at, job, reason) VALUES (0, 'crawler', 'turns limit')")
    c.commit()
    c.close()
    return path


def _status(db, qid):
    c = sqlite3.connect(db)
    try:
        return c.execute("SELECT status FROM policy_queue WHERE id = ?", (qid,)).fetchone()[0]
    finally:
        c.close()


def _run(coro):
    return asyncio.run(coro)


def test_lanes_tree_and_queue_render(db):
    async def go():
        app = dash.DashApp(db)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            lanes = str(app.query_one("#lanes").render())
            assert "build" in lanes and "make test" in lanes and "$0.0100" in lanes
            tree = app.query_one("#tree")
            labels = [str(n.label) for n in tree.root.children]
            assert labels == ["awaiting approval (2)"]  # both have a pending request
            assert len(app.query_one("#queue").children) == 3
    _run(go())


def test_a_approves_the_focused_item(db):
    async def go():
        app = dash.DashApp(db)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            with mock.patch("sable.policy.queue.decide_request",
                            wraps=__import__("sable.policy.queue", fromlist=["x"]).decide_request) as spy:
                await pilot.press("a")
                await pilot.pause()
            assert spy.call_args.kwargs == {"approve": True} and spy.call_args.args[1] == 1
            assert len(app.query_one("#queue").children) == 2
    _run(go())
    assert _status(db, 1) == "approved" and _status(db, 2) == "pending"


def test_r_rejects(db):
    async def go():
        app = dash.DashApp(db)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("down", "r")
            await pilot.pause()
    _run(go())
    assert _status(db, 1) == "pending" and _status(db, 2) == "rejected"


def test_reset_clears_a_trip(db):
    async def go():
        app = dash.DashApp(db)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("down", "down", "x")
            await pilot.pause()
            assert len(app.query_one("#queue").children) == 2
    _run(go())
    c = sqlite3.connect(db)
    assert c.execute("SELECT status FROM breaker_trips").fetchone()[0] == "reset"
    c.close()


def test_q_exits(db):
    async def go():
        app = dash.DashApp(db)
        async with app.run_test() as pilot:
            await pilot.press("q")
            await pilot.pause()
            assert not app.is_running
    _run(go())


def test_dash_builtin_spawns_a_child_process():
    from sable.app.builtins import dispatch
    with mock.patch("subprocess.run") as run:
        assert dispatch._handle_dash_builtin() is True
    assert run.call_args.args[0] == [sys.executable, "-m", "sable.ui.dash"]


def test_dash_builtin_without_textual_says_so():
    from sable.app.builtins import dispatch
    with mock.patch("importlib.util.find_spec", return_value=None), \
         mock.patch("subprocess.run") as run, mock.patch.object(dispatch, "_out") as out:
        dispatch._handle_dash_builtin()
    run.assert_not_called()
    assert "textual" in out.call_args.args[0]
