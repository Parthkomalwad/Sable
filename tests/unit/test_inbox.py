"""Phase 5 Task 4 (E6): `/inbox`, one list of everything waiting on you."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from sable.app.builtins.dispatch import handle_builtin
from sable.core.config.schema import ShellConfig
from sable.policy import breaker, queue
from sable.ui import state


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr("sable.core.db.DB_PATH", path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    conn = sqlite3.connect(path)
    queue.ensure_table(conn)
    breaker.ensure_table(conn)
    now = time.time()
    conn.execute("INSERT INTO policy_queue (created_at, agent, command, rule, why) VALUES (?,?,?,?,?)",
                 (now - 7200, "deploy", "rm -rf build", "recursive-delete", "no undo"))
    conn.execute("INSERT INTO breaker_trips (created_at, job, reason) VALUES (?,?,?)",
                 (now - 60, "crawler", "turns limit reached"))
    conn.execute("INSERT INTO policy_queue (created_at, agent, command, rule, why) VALUES (?,?,?,?,?)",
                 (now - 30, "daemon:nightly", "git push", "push", "leaves the machine"))
    conn.commit()
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "skills_index.json").write_text(json.dumps([
        {"name": "tidy-logs", "file": "", "status": "pending", "created_at": "2020-01-01T00:00:00+00:00"},
        {"name": "old-one", "file": "", "created_at": "2020-01-01T00:00:00+00:00"},   # no status: enabled
    ]))
    yield SimpleNamespace(conn=conn, db=SimpleNamespace(_conn=conn), home=tmp_path)
    conn.close()


def _run(line, env, capsys):
    assert handle_builtin(line, env.db, "s1", ShellConfig.defaults())
    return capsys.readouterr().out


def test_inbox_all_orders_oldest_first_with_sources(env):
    items = state.inbox_all()
    assert [i.kind for i in items] == ["skill", "approval", "breaker", "approval"]
    assert items[0].text == "tidy-logs"
    assert items[3].agent == "scheduled job nightly"
    assert items[1].created_at < items[2].created_at


def test_inbox_lists_everything(env, capsys):
    text = _run("/inbox", env, capsys)
    for want in ("rm -rf build", "turns limit reached", "scheduled job nightly", "tidy-logs", "2h"):
        assert want in text
    assert "old-one" not in text


def test_show_and_unknown(env, capsys):
    assert "no undo" in _run("/inbox show 2", env, capsys)
    assert "no item 9" in _run("/inbox approve 9", env, capsys)
    assert "usage" in _run("/inbox approve x", env, capsys)


def test_approve_reject_go_through_queue(env, capsys):
    _run("/inbox approve 4", env, capsys)      # the daemon's step: its next tick resumes the run
    _run("/inbox reject 2", env, capsys)
    rows = dict(env.conn.execute("SELECT agent, status FROM policy_queue").fetchall())
    assert rows == {"daemon:nightly": "approved", "deploy": "rejected"}


def test_approve_breaker_resets(env, capsys):
    _run("/inbox approve 3", env, capsys)
    assert breaker.tripped(env.conn) == []


def test_skill_approve(env, capsys):
    _run("/inbox approve 1", env, capsys)
    data = json.loads((env.home / "skills" / "skills_index.json").read_text())
    assert data[0]["status"] == "enabled"
