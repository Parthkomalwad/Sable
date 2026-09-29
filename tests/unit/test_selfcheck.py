"""Phase 9 Task 7 (K10): the weekly self-check, from existing data only."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from sable.core.db import _CREATE_AGENT_EVENTS
from sable.daemon import selfcheck
from sable.evals.runner import _CREATE_EVAL_RUNS
from sable.llm import registry
from sable.ui import state

SENT: list[str] = []
NOW = datetime(2026, 9, 27, 2, 30, tzinfo=timezone.utc)   # a Sunday


@pytest.fixture
def conn(tmp_path, monkeypatch):
    def no_backend(*a, **k):
        raise AssertionError("the self-check must never build a backend")
    monkeypatch.setattr(registry, "build_backend", no_backend)
    SENT.clear()
    monkeypatch.setattr(selfcheck.notify, "send", lambda t, b: SENT.append(b) or False)
    c = sqlite3.connect(str(tmp_path / "s.db"))
    c.execute(_CREATE_AGENT_EVENTS)
    yield c
    c.close()


def _goal(c, steps, tokens, usd, skills, age_days=1):
    ts = (NOW - timedelta(days=age_days)).isoformat()
    c.execute("INSERT INTO agent_events (ts, agent, kind, payload_json) VALUES (?, 'orchestrator', 'goal_done', ?)",
              (ts, json.dumps({"steps": steps, "tokens": tokens, "usd": usd, "skills": skills})))


def test_numbers_from_seeded_db(conn):
    _goal(conn, 3, 1000, 0.004, ["nginx"])
    _goal(conn, 5, 2000, 0.008, [])
    _goal(conn, 7, 4000, 0.010, [])
    _goal(conn, 99, 9, 9.0, [], age_days=10)     # outside the week
    conn.execute(_CREATE_EVAL_RUNS)
    for passed in (1, 1, 0):
        conn.execute("INSERT INTO eval_runs (run_id, created_at, backend, task, passed, steps, tokens, "
                     "cost_usd, seconds) VALUES ('r1', '2026-09-26T10:00:00', 'mock', 't', ?, 1, 0, 0, 1)",
                     (passed,))
    r = selfcheck.summarize(conn, NOW, failing=["old"])
    assert r.goals == 3
    assert r.with_skill == (1, 3.0, 1000.0, 0.004)
    assert r.without_skill[:3] == (2, 6.0, 3000.0)
    assert r.eval == ("mock", 2, 3, "2026-09-26T10:00:00")
    text = str(r)
    assert "skills matched 1 of 3 goals" in text
    assert "without: 6.0 steps" in text and "2/3 passed (mock, 2026-09-26)" in text
    assert len(text.splitlines()) <= 12


def test_no_skills_no_eval(conn):
    r = selfcheck.summarize(conn, NOW, failing=[])
    assert r.goals == 0 and r.eval is None
    assert "latest eval: none run" in str(r) and "failing" not in str(r)


def test_newly_failing_since_last_report(conn):
    selfcheck.run(conn, NOW, failing=["a"])
    r = selfcheck.run(conn, NOW, failing=["a", "b"])
    assert r.newly_failing == ["b"]
    assert "newly failing: b (see /skill doctor)" in str(r)
    db = conn.execute("PRAGMA database_list").fetchone()[2]
    assert "newly failing: b" in state.latest_selfcheck(db)
    assert len(SENT) == 2


def test_weekly_guard(conn, monkeypatch):
    runs = []
    monkeypatch.setattr(selfcheck, "run", lambda c, now: runs.append(now))
    local = NOW.replace(tzinfo=None)
    assert not selfcheck.due(conn, local - timedelta(days=1), "02:30")   # Saturday
    assert not selfcheck.due(conn, local.replace(minute=31), "02:30")
    assert selfcheck.due(conn, local, "02:30")
    assert not selfcheck.due(conn, local.replace(second=40), "02:30")   # restart, same minute
    assert selfcheck.due(conn, local + timedelta(days=7), "02:30")
    assert len(runs) == 2


def test_push_only_when_configured(conn, monkeypatch):
    posts = []
    monkeypatch.undo()
    monkeypatch.setattr(registry, "build_backend", lambda *a, **k: pytest.fail("backend built"))
    monkeypatch.setattr(selfcheck.notify.httpx, "post", lambda *a, **k: posts.append(a))
    monkeypatch.setattr(selfcheck.notify, "settings", lambda: {"server": "https://n"})
    selfcheck.run(conn, NOW, failing=[])
    assert posts == []
