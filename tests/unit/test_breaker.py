"""Phase 3 (I2): the per-job circuit breaker.

A job over any one budget stops before its next turn, never mid-command. A
trip is recorded in a table (Phase 5's `/inbox` reads it), announced on the
bus, and holds every job until `/breaker reset`.
"""
from __future__ import annotations

import sqlite3

import pytest

from sable.core.config.schema import ShellConfig
from sable.policy import breaker


def _b(tmp_path, **limits):
    return breaker.Breaker("job", breaker.Limits(**limits), str(tmp_path / "s.db"))


def _conn(tmp_path):
    return sqlite3.connect(str(tmp_path / "s.db"))


class TestLimits:
    def test_no_limits_never_trips(self, tmp_path):
        b = _b(tmp_path)
        for _ in range(100):
            b.record(tokens=10_000, usd=5.0, failed=True)
        assert b.check() is None

    def test_turns_stop_before_the_next_turn(self, tmp_path):
        b = _b(tmp_path, turns=3)
        ran = 0
        for _ in range(5):
            if b.check():
                break
            ran += 1
            b.record(tokens=1, usd=0.0)
        assert ran == 3

    @pytest.mark.parametrize("limit,spend", [
        ({"tokens": 100}, {"tokens": 100}),
        ({"usd": 0.5}, {"usd": 0.5}),
    ])
    def test_tokens_and_usd_trip(self, tmp_path, limit, spend):
        b = _b(tmp_path, **limit)
        assert b.check() is None
        b.record(**spend)
        reason = b.check()
        assert reason and next(iter(limit)) in reason

    def test_wall_clock_trips(self, tmp_path, monkeypatch):
        b = _b(tmp_path, wall_s=60)
        monkeypatch.setattr(breaker.time, "monotonic", lambda: b._start + 61)
        assert "wall_s" in b.check()

    def test_consecutive_failures_trip_and_a_success_clears_the_run(self, tmp_path):
        b = _b(tmp_path, consecutive_failures=2)
        b.record(failed=True)
        b.record(failed=False)
        b.record(failed=True)
        assert b.check() is None
        b.record(failed=True)
        assert "consecutive_failures" in b.check()


class TestTripState:
    def test_trip_is_recorded_and_holds_other_jobs(self, tmp_path):
        b = _b(tmp_path, turns=1)
        b.record()
        assert b.check()
        with _conn(tmp_path) as c:
            rows = breaker.tripped(c)
        assert len(rows) == 1 and rows[0]["job"] == "job"
        # A fresh job with no limits is still held: a trip pauses autonomous work.
        other = breaker.Breaker("other", breaker.Limits(), str(tmp_path / "s.db"))
        assert "/breaker reset" in other.check()

    def test_trip_publishes_a_bus_event(self, tmp_path):
        from sable.core.events.bus import EventBus

        b = _b(tmp_path, turns=1)
        b.record()
        b.check()
        kinds = [e.kind for e in EventBus(db_path=str(tmp_path / "s.db")).since(0)]
        assert "breaker" in kinds

    def test_reset_clears(self, tmp_path):
        b = _b(tmp_path, turns=1)
        b.record()
        b.check()
        with _conn(tmp_path) as c:
            assert breaker.reset(c) == 1
            assert breaker.tripped(c) == []
        assert breaker.Breaker("job", breaker.Limits(), str(tmp_path / "s.db")).check() is None


class TestPause:
    def _trip_other(self, tmp_path):
        other = _b(tmp_path, turns=1)
        other.record()
        assert other.check()

    def test_a_held_worker_waits_and_resumes_after_reset(self, tmp_path, monkeypatch):
        self._trip_other(tmp_path)
        clock = [0.0]
        monkeypatch.setattr(breaker.time, "monotonic", lambda: clock[0])
        worker = breaker.Breaker("w", breaker.Limits(wall_s=60), str(tmp_path / "s.db"),
                                 held_by_trips=False)
        paused, sleeps = [], []

        def fake_sleep(s):
            sleeps.append(s)
            clock[0] += 1000  # far past wall_s: paused time must not count
            if len(sleeps) == 3:
                with _conn(tmp_path) as c:
                    breaker.reset(c)

        assert worker.wait_while_held(on_pause=paused.append, sleep=fake_sleep) is True
        assert len(paused) == 1 and "job" in paused[0]
        assert len(sleeps) == 3 and worker.turns == 0 and worker.tokens == 0
        assert worker.check() is None

    def test_no_trip_means_no_pause(self, tmp_path):
        w = _b(tmp_path)
        assert w.wait_while_held(sleep=lambda s: pytest.fail("slept")) is False

    def test_a_worker_over_its_own_limit_still_stops(self, tmp_path):
        self._trip_other(tmp_path)
        w = breaker.Breaker("w", breaker.Limits(turns=1), str(tmp_path / "s.db"),
                            held_by_trips=False)
        w.record()
        assert w.check().startswith("turns")


class TestConfig:
    def test_defaults_are_unlimited(self):
        lim = breaker.Limits.from_config(ShellConfig.defaults())
        assert lim == breaker.Limits()

    def test_round_trip_and_old_configs_load(self):
        old = ShellConfig.from_dict({"model": "m"})
        assert old.per_job_budget == {} and old.breaker_consecutive_failures is None
        cfg = ShellConfig.from_dict({"model": "m", "per_job_budget": {"turns": 3, "usd": 0.5},
                                     "breaker_consecutive_failures": 4})
        again = ShellConfig.from_dict(cfg.to_dict())
        lim = breaker.Limits.from_config(again)
        assert (lim.turns, lim.usd, lim.consecutive_failures) == (3, 0.5, 4)

    @pytest.mark.parametrize("bad", [
        {"per_job_budget": {"bogus": 1}},
        {"per_job_budget": {"turns": -1}},
        {"breaker_consecutive_failures": 0},
    ])
    def test_bad_values_rejected(self, bad):
        with pytest.raises(ValueError):
            ShellConfig.from_dict({"model": "m", **bad})


class TestBuiltin:
    def test_status_and_reset(self, tmp_path, capsys):
        from sable.app.builtins.dispatch import _handle_breaker_builtin

        class _Db:
            _conn = _conn(tmp_path)

        _handle_breaker_builtin("", _Db)
        assert "not tripped" in capsys.readouterr().out
        b = _b(tmp_path, turns=1)
        b.record()
        b.check()
        _handle_breaker_builtin("", _Db)
        assert "job" in capsys.readouterr().out
        _handle_breaker_builtin("reset", _Db)
        assert breaker.tripped(_Db._conn) == []


class TestOrchestratorGate:
    def test_turns_3_stops_a_5_step_goal_at_3(self, tmp_path, capsys):
        """The roadmap gate: per_job.turns = 3, a 5-step goal stops at 3."""
        from unittest.mock import MagicMock

        from sable.agents.orchestrator import OrchestratorAgent
        from sable.llm.base import LLMResponse

        config = ShellConfig.defaults()
        config.tasks_base_dir = str(tmp_path / "tasks")
        config.per_job_budget = {"turns": 3}
        orch = OrchestratorAgent(goal="five steps", cwd=str(tmp_path), config=config,
                                 db_path=str(tmp_path / "s.db"), task_manager=MagicMock())
        calls = []
        orch._call_llm = lambda m: calls.append(1) or LLMResponse(
            command="true", explanation="step", safe=True, plan=None, action="run",
            prompt_tokens=1, completion_tokens=1, cost_usd=0.0, model="m")
        orch._handle_run = lambda action: None

        orch.run()

        assert len(calls) == 3
        assert "[breaker]" in capsys.readouterr().out
        with _conn(tmp_path) as c:
            assert breaker.tripped(c)[0]["reason"].startswith("turns")

    def test_an_open_worker_trip_does_not_block_a_typed_goal(self, tmp_path):
        from unittest.mock import MagicMock

        from sable.agents.orchestrator import OrchestratorAgent
        from sable.llm.base import LLMResponse

        worker = _b(tmp_path, turns=1)
        worker.record()
        assert worker.check()

        config = ShellConfig.defaults()
        config.tasks_base_dir = str(tmp_path / "tasks")
        orch = OrchestratorAgent(goal="one step", cwd=str(tmp_path), config=config,
                                 db_path=str(tmp_path / "s.db"), task_manager=MagicMock())
        calls = []
        orch._call_llm = lambda m: calls.append(1) or LLMResponse(
            command="", explanation="done", safe=True, plan=None, action="done",
            prompt_tokens=1, completion_tokens=1, cost_usd=0.0, model="m")
        orch._handle_done = lambda action: None

        orch.run()

        assert calls == [1]
        # ...while a worker is still held by the same trip.
        assert "/breaker reset" in _b(tmp_path).check()
