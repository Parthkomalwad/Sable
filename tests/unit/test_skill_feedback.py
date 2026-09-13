"""The confidence loop closing: a worker nudges the skills it used (B1).

Until this lands, `SkillIndex.nudge()` has no caller in the product and
confidence never moves outside its own unit tests. This is the wiring that
makes the Phase 2 gate's third run possible: use a skill, have it work,
confidence rises; break the thing on purpose, confidence falls.

Three properties these tests fix.

**Once per run, not once per step.** A worker injects the same skills on
every turn of a 20-step run. Nudging per step would take a skill from 0.5 to
1.0 on a single goal and make the number meaningless.

**Every exit path nudges.** The worker has four ways out: the step limit,
an unreachable LLM, an explicit `done`, and the loop condition going false.
A nudge on the `done` branch alone would only ever raise confidence, since
failures would never be recorded. That would be worse than not nudging at
all: the score would drift up forever and look like evidence.

**A run that used no skills does nothing.** No index write, no bus event.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class _FakeIndex:
    def __init__(self):
        self.nudges: list[tuple[str, bool]] = []

    def nudge(self, name: str, success: bool) -> None:
        self.nudges.append((name, success))


@pytest.fixture
def index():
    return _FakeIndex()


def _grade_run(worker_module, used, succeeded, index, validators=None):
    """Drive the module-level helper the worker calls at its terminal state."""
    return worker_module.grade_skills_used(
        used_skills=used,
        succeeded=succeeded,
        index=index,
        cwd="/w",
        wrap=None,
        validators=validators or {},
    )


class TestOncePerRun:
    def test_a_skill_used_on_many_steps_is_nudged_once(self, index):
        from sable.agents import worker

        _grade_run(worker, ["deploy-api", "deploy-api", "deploy-api"], True, index)

        assert index.nudges == [("deploy-api", True)]

    def test_several_distinct_skills_are_each_nudged_once(self, index):
        from sable.agents import worker

        _grade_run(worker, ["a", "b", "a", "b"], True, index)

        assert sorted(index.nudges) == [("a", True), ("b", True)]

    def test_a_run_that_used_no_skills_writes_nothing(self, index):
        from sable.agents import worker

        _grade_run(worker, [], True, index)

        assert index.nudges == []


class TestEveryExitPathNudges:
    """A nudge only on success would make confidence drift up forever."""

    def test_a_failed_run_lowers_confidence(self, index):
        from sable.agents import worker

        _grade_run(worker, ["deploy-api"], False, index)

        assert index.nudges == [("deploy-api", False)]

    def test_a_successful_run_raises_confidence(self, index):
        from sable.agents import worker

        _grade_run(worker, ["deploy-api"], True, index)

        assert index.nudges == [("deploy-api", True)]


class TestValidatorsDecideWhenPresent:
    """B5: a declared validator outranks the run's own outcome."""

    def test_a_passing_validator_makes_a_failed_run_a_success(self, index, monkeypatch):
        from sable.agents import worker
        from sable.skills.validate import Grade

        monkeypatch.setattr(
            worker, "grade_skill_use",
            lambda *a, **k: Grade(success=True, reason="validator exited 0"),
        )

        _grade_run(worker, ["deploy-api"], False, index,
                   validators={"deploy-api": "docker ps"})

        assert index.nudges == [("deploy-api", True)]

    def test_a_failing_validator_overrides_a_successful_run(self, index, monkeypatch):
        """The agent said done; the system disagrees. The system wins.

        This is the gate's "break the deploy on purpose" step: the agent
        believes it succeeded and the validator is what catches it.
        """
        from sable.agents import worker
        from sable.skills.validate import Grade

        monkeypatch.setattr(
            worker, "grade_skill_use",
            lambda *a, **k: Grade(success=False, reason="validator exited 1"),
        )

        _grade_run(worker, ["deploy-api"], True, index,
                   validators={"deploy-api": "docker ps"})

        assert index.nudges == [("deploy-api", False)]

    def test_a_skill_without_a_validator_uses_the_run_outcome(self, index, monkeypatch):
        from sable.agents import worker

        called = []
        monkeypatch.setattr(
            worker, "grade_skill_use",
            lambda *a, **k: called.append(1),
        )

        _grade_run(worker, ["no-validator"], True, index, validators={})

        assert called == []
        assert index.nudges == [("no-validator", True)]


class TestFailuresDoNotTakeTheWorkerDown:
    """Grading happens after the work is finished. It must never undo it."""

    def test_a_broken_index_does_not_raise(self, monkeypatch):
        from sable.agents import worker

        broken = MagicMock()
        broken.nudge.side_effect = OSError("index is read-only")

        # Must not raise: the run succeeded, and a bookkeeping failure
        # afterwards must not turn it into a crash the user sees.
        _grade_run(worker, ["deploy-api"], True, broken)

    def test_a_validator_that_explodes_does_not_raise(self, index, monkeypatch):
        from sable.agents import worker

        def explode(*a, **k):
            raise OSError("no pty available")

        monkeypatch.setattr(worker, "grade_skill_use", explode)

        _grade_run(worker, ["deploy-api"], True, index,
                   validators={"deploy-api": "docker ps"})

        # The run's own outcome stands when the validator cannot be run.
        assert index.nudges == [("deploy-api", True)]
