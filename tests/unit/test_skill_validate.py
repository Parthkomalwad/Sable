"""Grading a skill's use: validators (B5).

A skill may declare `validate` in its frontmatter: a command that answers
"did this actually work?" more honestly than the agent's own say-so. After a
run that used the skill, the validator runs and its exit code decides
whether confidence goes up or down.

Two things these tests fix.

**The validator runs in the sandbox.** It is model-adjacent text that came
off disk, and a skill imported from elsewhere (B7) is text a stranger wrote.
Running it unwrapped would make `validate = "rm -rf ~"` a working exploit
against a user who approved a skill without reading its frontmatter.

**A missing validator is not a failure.** Most skills will not declare one.
Those fall back to the run's own outcome, so the absence of a validator
never costs a skill confidence it earned.

`agents/runtime.run_command` returns a string and never an exit code, so the
grader appends an exit-code marker and parses it back. That is a decision
this module makes rather than one the plan settled: the alternative was a
second pty runner that returns a code, and one runner with one timeout
policy is worth more than a tidier return type.
"""
from __future__ import annotations

import pytest

from sable.skills import validate as validate_module
from sable.skills.validate import Grade, grade_skill_use


class _Recorder:
    """Captures what would have been run, and returns a canned result."""

    def __init__(self, output: str = "ok\n__SABLE_VALIDATE__0"):
        self.calls: list[dict] = []
        self._output = output

    def __call__(self, command, cwd, timeout=120, wrap=None, on_timeout=None,
                 prefix="agent_"):
        self.calls.append({
            "command": command, "cwd": cwd, "timeout": timeout,
            "wrap": wrap, "prefix": prefix,
        })
        return self._output


@pytest.fixture
def runner(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(validate_module, "run_command", rec)
    return rec


class TestGradingOnExitCode:
    def test_exit_zero_is_success(self, monkeypatch):
        monkeypatch.setattr(
            validate_module, "run_command",
            _Recorder("all good\n__SABLE_VALIDATE__0"),
        )
        assert grade_skill_use("docker ps", cwd="/w").success is True

    def test_non_zero_is_failure(self, monkeypatch):
        monkeypatch.setattr(
            validate_module, "run_command",
            _Recorder("not running\n__SABLE_VALIDATE__1"),
        )
        assert grade_skill_use("docker ps", cwd="/w").success is False

    def test_the_marker_is_stripped_from_the_reported_output(self, monkeypatch):
        """The output is shown to a human; the marker is our bookkeeping."""
        monkeypatch.setattr(
            validate_module, "run_command",
            _Recorder("service is up\n__SABLE_VALIDATE__0"),
        )
        grade = grade_skill_use("docker ps", cwd="/w")
        assert "service is up" in grade.output
        assert "__SABLE_VALIDATE__" not in grade.output

    def test_a_missing_marker_grades_as_failure(self, monkeypatch):
        """No marker means the command never reached the echo.

        A killed or crashed validator has not demonstrated success, and
        guessing in its favour is how a broken skill keeps its confidence.
        """
        monkeypatch.setattr(
            validate_module, "run_command", _Recorder("segfault"),
        )
        grade = grade_skill_use("docker ps", cwd="/w")
        assert grade.success is False
        assert "no exit code" in grade.reason

    def test_a_timeout_grades_as_failure(self, monkeypatch):
        monkeypatch.setattr(
            validate_module, "run_command",
            _Recorder("\n[timeout after 30s]"),
        )
        grade = grade_skill_use("sleep 999", cwd="/w")
        assert grade.success is False
        assert "timed out" in grade.reason


class TestTheValidatorIsSandboxed:
    """A validator is text off disk. B7 makes it text a stranger wrote."""

    def test_the_wrap_callable_is_passed_through(self, runner):
        def wrap(cmd):
            return f"sandboxed: {cmd}"

        grade_skill_use("docker ps", cwd="/w", wrap=wrap)

        assert runner.calls[0]["wrap"] is wrap

    def test_it_runs_in_the_given_directory(self, runner):
        grade_skill_use("docker ps", cwd="/workspace")
        assert runner.calls[0]["cwd"] == "/workspace"

    def test_a_validator_gets_a_short_timeout(self, runner):
        """A check that hangs must not hold a worker open for 120s.

        The validator answers one question about state that already exists;
        it is not doing the work.
        """
        grade_skill_use("docker ps", cwd="/w")
        assert runner.calls[0]["timeout"] <= 30

    def test_the_command_carries_the_exit_marker(self, runner):
        grade_skill_use("docker ps", cwd="/w")
        assert "docker ps" in runner.calls[0]["command"]
        assert "__SABLE_VALIDATE__" in runner.calls[0]["command"]


class TestNoValidator:
    """Most skills declare none. See the module docstring."""

    def test_an_empty_validator_falls_back_to_the_run_outcome(self, runner):
        assert grade_skill_use("", cwd="/w", ran_ok=True).success is True
        assert grade_skill_use("", cwd="/w", ran_ok=False).success is False

    def test_no_command_is_run_when_there_is_no_validator(self, runner):
        grade_skill_use("", cwd="/w", ran_ok=True)
        assert runner.calls == []

    def test_the_reason_says_where_the_grade_came_from(self, runner):
        """`/skill stats` shows this, so it has to be legible."""
        grade = grade_skill_use("", cwd="/w", ran_ok=True)
        assert "no validator" in grade.reason


class TestTheGradeShape:
    def test_a_grade_carries_its_reason_and_output(self, monkeypatch):
        monkeypatch.setattr(
            validate_module, "run_command",
            _Recorder("up\n__SABLE_VALIDATE__0"),
        )
        grade = grade_skill_use("docker ps", cwd="/w")
        assert isinstance(grade, Grade)
        assert grade.success is True
        assert grade.reason
        assert grade.output
