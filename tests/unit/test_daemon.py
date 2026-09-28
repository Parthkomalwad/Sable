"""Phase 5 Task 0 (E1): the sabled foundation.

A scheduled plan runs `allow` steps, queues a `confirm` step to the inbox and
waits, and fails on `never`. The loop survives a failing handler, a second
daemon refuses to start, and runs left `running` by a dead daemon are `lost`.
"""
from __future__ import annotations

import sqlite3
import sys

import pytest

from sable.daemon import jobs, service
from sable.policy import queue


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_audit", lambda cwd, cmd: None)
    c = sqlite3.connect(tmp_path / "s.db")
    yield c
    c.close()


def _runner(log, output="(no output; exit 0)"):
    def run(cmd, cwd, **kw):
        log.append(cmd)
        return output
    return run


def _status(conn, rid):
    return conn.execute("SELECT status, step FROM job_runs WHERE id = ?", (rid,)).fetchone()


class TestRunPlan:
    def test_allow_steps_run_in_order(self, conn, tmp_path):
        ran = []
        rid = jobs.run_plan(conn, "hb", ["date", "echo hi"], cwd=str(tmp_path), run=_runner(ran))
        assert ran == ["date", "echo hi"]
        assert _status(conn, rid) == ("ok", 2)

    def test_a_failed_step_stops_the_run(self, conn, tmp_path):
        ran = []
        rid = jobs.run_plan(conn, "hb", ["false", "date"], cwd=str(tmp_path),
                            run=_runner(ran, "boom\n[exit 1]"))
        assert ran == ["false"] and _status(conn, rid) == ("failed", 0)

    def test_confirm_step_is_queued_and_the_run_waits(self, conn, tmp_path):
        ran = []
        rid = jobs.run_plan(conn, "prune", ["date", "rm -rf /tmp/old-builds"],
                            cwd=str(tmp_path), run=_runner(ran))
        assert ran == ["date"]
        assert _status(conn, rid) == ("waiting", 1)
        assert [p["command"] for p in queue.pending(conn)] == ["rm -rf /tmp/old-builds"]

    def test_an_approved_step_runs_once_on_resume(self, conn, tmp_path):
        ran = []
        rid = jobs.run_plan(conn, "prune", ["rm -rf /tmp/old-builds"], cwd=str(tmp_path), run=_runner(ran))
        queue.decide_request(conn, queue.pending(conn)[0]["id"], approve=True)
        jobs.resume_waiting(conn, cwd=str(tmp_path), run=_runner(ran))
        assert ran == ["rm -rf /tmp/old-builds"] and _status(conn, rid) == ("ok", 1)

    def test_resume_keeps_the_output_before_the_wait(self, conn, tmp_path):
        rid = jobs.run_plan(conn, "p", ["date", "rm -rf /tmp/old-builds"], cwd=str(tmp_path), run=_runner([]))
        queue.decide_request(conn, queue.pending(conn)[0]["id"], approve=True)
        jobs.resume_waiting(conn, run=_runner([]))
        out = conn.execute("SELECT output FROM job_runs WHERE id = ?", (rid,)).fetchone()[0]
        assert "$ date" in out and "$ rm -rf" in out

    def test_a_rejected_step_fails_the_run(self, conn, tmp_path):
        ran = []
        rid = jobs.run_plan(conn, "p", ["rm -rf /tmp/old-builds"], cwd=str(tmp_path), run=_runner(ran))
        queue.decide_request(conn, queue.pending(conn)[0]["id"], approve=False)
        jobs.resume_waiting(conn, run=_runner(ran))
        assert ran == [] and _status(conn, rid)[0] == "failed"

    def test_deny_step_fails_without_running(self, conn, tmp_path, monkeypatch):
        from sable.policy.tiers import Decision, Tier
        monkeypatch.setattr(jobs, "decide", lambda c: Decision(tier=Tier.DENY, rule=None, why="no", source="t"))
        ran = []
        rid = jobs.run_plan(conn, "bad", ["rm -rf /"], cwd=str(tmp_path), run=_runner(ran))
        assert ran == [] and _status(conn, rid)[0] == "failed"

    def test_running_rows_from_a_dead_daemon_are_lost(self, conn, tmp_path):
        jobs.ensure_table(conn)
        conn.execute("INSERT INTO job_runs (job, started_at, status, step, steps_json, cwd) "
                     "VALUES ('x', 0, 'running', 0, '[]', '/')")
        assert jobs.mark_lost(conn) == 1
        assert conn.execute("SELECT status FROM job_runs").fetchone()[0] == "lost"


class TestLoop:
    def test_a_failing_handler_does_not_stop_the_others(self, conn):
        seen = []

        def bad(c):
            raise OSError("disk gone")

        service.tick(conn, [bad, lambda c: seen.append(1)])
        assert seen == [1]

    def test_unit_file_runs_the_daemon(self, tmp_path):
        text = service.unit_text()
        assert "daemon run" in text and "Restart=on-failure" in text

    @pytest.mark.skipif(sys.platform == "win32", reason="flock is POSIX")
    def test_a_second_daemon_refuses_to_start(self, tmp_path):
        first = service.acquire_lock(tmp_path / "l")
        assert first is not None
        assert service.acquire_lock(tmp_path / "l") is None
