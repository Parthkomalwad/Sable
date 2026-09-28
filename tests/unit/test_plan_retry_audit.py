"""A retried plan step is gated and audited as its own run.

The retry used to call the runner directly, so the audit ledger kept only
the failed first attempt's exit code even when the retry succeeded, and a
retry skipped policy entirely.
"""
from __future__ import annotations

from sable.agents import planner


def test_retry_is_gated_again_and_records_its_own_exit_code(monkeypatch, tmp_path):
    answers = iter(["", "r"])            # run the plan, then retry the failed step
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    gated, finished = [], []
    monkeypatch.setattr(planner, "gate", lambda cmd, **kw: gated.append(cmd) or True)
    results = iter([(1, "boom"), (0, "ok")])
    monkeypatch.setattr(planner, "_run_step", lambda cmd, cwd: next(results))
    monkeypatch.setattr(planner.audit, "finish", lambda code=None, **kw: finished.append(code))

    assert planner.execute_plan(["make deploy"], str(tmp_path)) == 0
    assert gated == ["make deploy", "make deploy"]
    assert finished == [1, 0]


def test_a_retry_policy_refuses_does_not_run(monkeypatch, tmp_path):
    answers = iter(["", "r"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    decisions = iter([True, False])      # allowed first, refused on retry
    monkeypatch.setattr(planner, "gate", lambda cmd, **kw: next(decisions))
    runs = []
    monkeypatch.setattr(planner, "_run_step", lambda cmd, cwd: runs.append(cmd) or (1, "boom"))
    monkeypatch.setattr(planner.audit, "finish", lambda code=None, **kw: None)

    planner.execute_plan(["make deploy"], str(tmp_path))
    assert runs == ["make deploy"]
