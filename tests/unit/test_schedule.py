"""Phase 5 Task 1 (E2): /schedule, the cron matcher and the daemon handler."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest

from sable.daemon import cron, jobs, schedule
from sable.llm.base import LLMBackend, LLMResponse


@pytest.mark.parametrize("expr, when, expected", [
    ("* * * * *", "2026-09-28 13:07", True),
    ("*/2 * * * *", "2026-09-28 13:08", True),
    ("*/2 * * * *", "2026-09-28 13:07", False),
    ("0 3 * * *", "2026-09-28 03:00", True),
    ("0 3 * * *", "2026-09-28 03:01", False),
    ("0,30 9-17 * * *", "2026-09-28 12:30", True),
    ("0,30 9-17 * * *", "2026-09-28 18:30", False),
    ("10-20/5 * * * *", "2026-09-28 00:15", True),
    ("10-20/5 * * * *", "2026-09-28 00:25", False),
    ("0 0 * jan,sep *", "2026-09-01 00:00", True),
    ("0 0 * * mon-fri", "2026-09-28 00:00", True),    # a Monday
    ("0 0 * * sat,sun", "2026-09-28 00:00", False),
    ("0 0 * * 0", "2026-09-27 00:00", True),          # Sunday as 0
    ("0 0 * * 7", "2026-09-27 00:00", True),          # and as 7
    ("0 0 1 * mon", "2026-09-01 00:00", True),        # dom OR dow: the 1st
    ("0 0 1 * mon", "2026-09-28 00:00", True),        # dom OR dow: a Monday
    ("0 0 1 * mon", "2026-09-29 00:00", False),
    ("0 0 15 * *", "2026-09-28 00:00", False),
])
def test_matches(expr, when, expected):
    assert cron.matches(expr, datetime.strptime(when, "%Y-%m-%d %H:%M")) is expected


@pytest.mark.parametrize("expr, field", [
    ("* * * *", "5 fields"), ("60 * * * *", "minute"), ("* 24 * * *", "hour"),
    ("* * 0 * *", "day of month"), ("* * * foo *", "month"), ("* * * * 8", "day of week"),
    ("*/0 * * * *", "minute"), ("5-1 * * * *", "minute"),
])
def test_bad_expression_names_field(expr, field):
    with pytest.raises(ValueError, match=field):
        cron.matches(expr, datetime(2026, 1, 1))


def test_describe():
    assert cron.describe("* * * * *") == "every minute"
    assert "2 minutes" in cron.describe("*/2 * * * *")
    assert "03:00" in cron.describe("0 3 * * *")


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_audit", lambda cwd, cmd: None)
    monkeypatch.setattr(jobs, "_publish", lambda kind, payload: None)
    c = sqlite3.connect(tmp_path / "s.db")
    yield c
    c.close()


def _fake_run_plan(calls, status="ok"):
    def run_plan(conn, job, steps, *, cwd):
        calls.append((job, steps))
        jobs.ensure_table(conn)
        cur = conn.execute(
            "INSERT INTO job_runs (job, started_at, status, steps_json, cwd) VALUES (?, 0, ?, ?, ?)",
            (job, status, json.dumps(steps), cwd))
        conn.commit()
        return cur.lastrowid
    return run_plan


class TestHandler:
    def test_due_row_runs_once_per_minute(self, conn, monkeypatch):
        calls = []
        monkeypatch.setattr(jobs, "run_plan", _fake_run_plan(calls))
        sid = schedule.add(conn, "*/2 * * * *", ["date"], "hb", "/tmp")
        t = datetime(2026, 9, 28, 13, 8, 1)
        schedule.due(conn, t)
        schedule.due(conn, t.replace(second=40))   # same minute, next tick
        assert calls == [(f"schedule-{sid}", ["date"])]

    def test_not_due_does_not_run(self, conn, monkeypatch):
        calls = []
        monkeypatch.setattr(jobs, "run_plan", _fake_run_plan(calls))
        schedule.add(conn, "*/2 * * * *", ["date"], "hb", "/tmp")
        schedule.due(conn, datetime(2026, 9, 28, 13, 7))
        assert calls == []

    def test_paused_does_not_run(self, conn, monkeypatch):
        calls = []
        monkeypatch.setattr(jobs, "run_plan", _fake_run_plan(calls))
        sid = schedule.add(conn, "* * * * *", ["date"], "hb", "/tmp")
        schedule.set_paused(conn, sid, True)
        schedule.due(conn, datetime(2026, 9, 28, 13, 7))
        assert calls == []

    def test_waiting_run_is_not_started_twice(self, conn, monkeypatch):
        calls = []
        monkeypatch.setattr(jobs, "run_plan", _fake_run_plan(calls, status="waiting"))
        schedule.add(conn, "* * * * *", ["rm -rf /tmp/x"], "clean", "/tmp")
        schedule.due(conn, datetime(2026, 9, 28, 13, 7))
        schedule.due(conn, datetime(2026, 9, 28, 13, 8))
        assert len(calls) == 1

    def test_list_rm(self, conn):
        sid = schedule.add(conn, "* * * * *", ["date"], "hb", "/tmp")
        assert [r["id"] for r in schedule.list_all(conn)] == [sid]
        assert schedule.remove(conn, sid) and not schedule.list_all(conn)
        assert not schedule.remove(conn, sid)


class _Backend(LLMBackend):
    def __init__(self, raws):
        self.raws = list(raws)
        self.calls = 0

    async def complete(self, messages, system, on_text=None):
        self.calls += 1
        return LLMResponse(command="", explanation="", safe=True, plan=None, prompt_tokens=0,
                           completion_tokens=0, cost_usd=0.0, raw=self.raws.pop(0))


GOOD = '```json\n{"cron": "*/2 * * * *", "plan": ["date >> ~/hb.log"], "summary": "heartbeat"}\n```'


class TestDraft:
    def test_one_call_parses_fenced_json(self):
        from sable.app.builtins.schedule import draft
        b = _Backend([GOOD])
        assert draft(b, "every 2 minutes write the date") == {
            "cron": "*/2 * * * *", "plan": ["date >> ~/hb.log"], "summary": "heartbeat"}
        assert b.calls == 1

    def test_reasks_once_then_gives_up_with_raw(self):
        from sable.app.builtins.schedule import draft
        b = _Backend(["sure, here you go", GOOD])
        assert draft(b, "x")["cron"] == "*/2 * * * *" and b.calls == 2
        b = _Backend(["nope", "still nope"])
        with pytest.raises(ValueError, match="still nope"):
            draft(b, "x")

    def test_bad_cron_is_refused(self):
        from sable.app.builtins.schedule import draft
        with pytest.raises(ValueError, match="minute"):
            draft(_Backend(['{"cron": "99 * * * *", "plan": ["date"], "summary": "s"}']), "x")

    def test_approve_stores_and_cancel_does_not(self, conn, monkeypatch):
        from sable.app.builtins import schedule as b
        monkeypatch.setattr(b, "_backend", lambda config: _Backend([GOOD]))
        assert b.handle_schedule('"every 2 minutes"', conn, None, ask=lambda p: "q")
        assert schedule.list_all(conn) == []
        monkeypatch.setattr(b, "_backend", lambda config: _Backend([GOOD]))
        b.handle_schedule('"every 2 minutes"', conn, None, ask=lambda p: "")
        assert [r["cron"] for r in schedule.list_all(conn)] == ["*/2 * * * *"]
