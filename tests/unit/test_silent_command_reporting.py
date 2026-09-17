"""A command that printed nothing is reported as finished, not as silence.

The gate run found this. `deploy.sh` writes files and prints nothing, so the
orchestrator fed the model the literal string `(no output)`. The model read
that as "the command may still be running", applied the prompt's rule 2
(long-running work gets a sub-agent), and then emitted `done` instead of
`spawn`, abandoning the goal after one of three steps.

Reproduced 4 times out of 4. The only change needed to make it stop was
making `deploy.sh` echo a line: with output, the same goal on the same model
ran 4 turns and completed all three steps, twice out of twice. So the empty
output is the trigger, not the model being unreliable in general.

The prompt cannot fix this. Rule 6 already forbids exactly this behaviour, in
capitals, naming it a bug ("'I will delegate this' inside a done is a bug"),
and the model did it anyway every single time, in four different phrasings.
Rules 2 and 6 collide and rule 6 loses, so the fix belongs in what the
runtime says, not in what the prompt asks for.

`(no output)` is also simply untrue. The command finished, and its exit status
said whether it worked. That was collected by `_reap` and thrown away. Saying
"exit 0, no output" instead of "(no output)" replaces an ambiguous silence
with the fact the model needed.

The return type stays `str`. Both agents treat the return of `run_command` as
the text they hand to the model, and widening it to a tuple would touch every
call site for a value only this case needs.
"""
from __future__ import annotations

import pytest

from sable.agents import runtime


class TestExitStatusIsReported:
    def test_a_silent_success_says_so(self, tmp_path):
        """The bug: this used to reach the model as nothing at all."""
        out = runtime.run_command("true", cwd=str(tmp_path))

        assert "exit 0" in out
        assert out.strip() != ""

    def test_a_silent_failure_says_so(self, tmp_path):
        """A command that failed silently must not read as success."""
        out = runtime.run_command("exit 3", cwd=str(tmp_path))

        assert "exit 3" in out

    def test_a_command_that_printed_is_left_alone(self, tmp_path):
        """Output is what the model reasons about. Do not decorate it."""
        out = runtime.run_command("echo hello", cwd=str(tmp_path))

        assert "hello" in out
        assert "exit 0" not in out

    def test_a_command_that_printed_and_failed_keeps_its_output(self, tmp_path):
        """The output is the diagnosis; the status must not replace it."""
        out = runtime.run_command("echo broken; exit 1", cwd=str(tmp_path))

        assert "broken" in out

    def test_a_command_that_printed_and_failed_also_reports_the_failure(self, tmp_path):
        """Output is not an outcome, and the first fix read as if it were.

        The gate's break-on-purpose step exposed this. A deploy script that
        wrote "deploy failed: registry unreachable" to stderr and exited 1
        took the early return for commands that printed something, so its
        status was dropped. The model was told the deploy had failed and the
        *runtime* was not, so grading saw no failed step and nudged the
        skill's confidence up, on the run that was supposed to push it down.

        A failure has to be legible to both readers: as prose for the model
        and as a marker for the code that grades the run.
        """
        out = runtime.run_command("echo broken >&2; exit 1", cwd=str(tmp_path))

        assert "broken" in out
        assert "exit 1" in out

    def test_a_success_that_printed_is_not_annotated(self, tmp_path):
        """Only failure is worth interrupting the output to say."""
        out = runtime.run_command("echo fine", cwd=str(tmp_path))

        assert "fine" in out
        assert "exit" not in out

    def test_an_empty_command_is_unchanged(self, tmp_path):
        """Nothing ran, so there is no status to report."""
        out = runtime.run_command("   ", cwd=str(tmp_path))

        assert out == "(empty command)"


class TestTheTimeoutMarkerSurvives:
    def test_a_timeout_still_reports_its_marker(self, tmp_path):
        """Two callers grep for `[timeout after`; masking it would be worse.

        `orchestrator.py` delegates the remaining goal to a sub-agent when it
        sees this, and `skills/validate.py` refuses to grade a skill on it. A
        timed-out command prints nothing of its own, so it takes the same
        path as a silent one and must come back unchanged.
        """
        out = runtime.run_command("sleep 5", cwd=str(tmp_path), timeout=0.5)

        assert "[timeout after" in out
        assert "no output" not in out


class TestTheModelNeverSeesBareSilence:
    def test_the_orchestrator_no_longer_substitutes_no_output(self, tmp_path):
        """`(no output)` was what the model read as "still running".

        With the status reported, a silent command arrives as a fact rather
        than as an absence, and the substitution has nothing left to do.
        """
        out = runtime.run_command("true", cwd=str(tmp_path))

        assert out.strip()
