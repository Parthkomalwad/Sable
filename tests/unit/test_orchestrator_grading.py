"""The orchestrator can grade a skill down, not only up.

`_grade_skills` passed a hardcoded `True` to every `nudge`, so confidence on
the orchestrator path could only ever rise. A number that cannot fall is not
evidence of anything, and Phase 2's gate line 3 ("break the deploy on purpose
-> confidence drops to 0.50") could not pass on the path a typed goal takes.

The worker already had the real thing: `grade_skills_used` runs a skill's
declared validator (B5) and nudges on what the system says rather than on
what the agent claims. Its own docstring names this as the gate's
break-on-purpose step. It is called from `worker.py` and nowhere else, which
makes this the third Phase 2 feature built, tested and wired into the
sub-agent while absent from the orchestrator.

**What this does and does not do.** It grades on the run's own outcome: a
command that exited non-zero means the run failed, and the skill that
informed it is nudged down. It deliberately does NOT run a skill's
`validate` command. The worker runs validators inside bwrap via
`Sandbox.wrap_command`; the orchestrator runs unsandboxed in the user's real
cwd, so executing a model-authored validator there would auto-run model
output with no confirmation and no sandbox. That belongs behind Phase 3's
policy tiers, and is left to it rather than bolted on here.

The consequence is honest and worth stating: this catches a run that failed
visibly, and does not catch a run the agent wrongly believes succeeded. The
latter is what validators are for, and it stays unavailable on this path
until Phase 3.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sable.agents.orchestrator import OrchestratorAgent


class _Index:
    def __init__(self):
        self.nudges: list[tuple[str, bool]] = []

    def nudge(self, name: str, success: bool) -> None:
        self.nudges.append((name, success))


class _Loader:
    def __init__(self, skills):
        self._skills = skills

    def load_relevant(self, goal: str) -> list[dict]:
        return list(self._skills)


def _skill(name="deploy-api", validate=""):
    return {
        "name": name,
        "content": "Steps.",
        "hash": f"hash-of-{name}",
        "source": "global",
        "confidence": 0.55,
        "validate": validate,
    }


@pytest.fixture
def config(tmp_path):
    cfg = MagicMock()
    cfg.tasks_base_dir = str(tmp_path / "tasks")
    cfg.model = "test-model"
    cfg.model_for.return_value = "test-model"
    return cfg


def _agent(config, tmp_path, index=None, skills=None):
    return OrchestratorAgent(
        goal="deploy the api",
        cwd=str(tmp_path),
        config=config,
        db_path=str(tmp_path / "sessions.db"),
        task_manager=MagicMock(),
        skill_loader=_Loader(skills if skills is not None else [_skill()]),
        skill_index=index,
    )


def _ran(agent, command="deploy.sh", output="ok") -> None:
    agent._commands_run += 1
    agent._record_step(command, output)


class TestAFailedRunGradesDown:
    def test_a_non_zero_exit_nudges_the_skill_down(self, config, tmp_path):
        """The bug: this nudged True, so confidence could only ever rise."""
        index = _Index()
        agent = _agent(config, tmp_path, index=index)
        _ran(agent, "deploy.sh", "(no output; exit 1)")

        agent._handle_done({"action": "done", "explanation": "deployed"})

        assert index.nudges == [("deploy-api", False)]

    def test_a_clean_run_still_grades_up(self, config, tmp_path):
        index = _Index()
        agent = _agent(config, tmp_path, index=index)
        _ran(agent, "deploy.sh", "(no output; exit 0)")

        agent._handle_done({"action": "done", "explanation": "deployed"})

        assert index.nudges == [("deploy-api", True)]

    def test_one_failed_step_among_several_fails_the_run(self, config, tmp_path):
        """A procedure whose middle step broke did not work, whatever the end said."""
        index = _Index()
        agent = _agent(config, tmp_path, index=index)
        _ran(agent, "deploy.sh", "(no output; exit 0)")
        _ran(agent, "cat STATUS", "(no output; exit 2)")
        _ran(agent, "cat app.txt", "v1 healthy")

        agent._handle_done({"action": "done", "explanation": "deployed"})

        assert index.nudges == [("deploy-api", False)]

    def test_a_command_that_printed_its_error_still_fails_the_run(self, config, tmp_path):
        """The case the live gate caught, where confidence went the wrong way.

        A deploy that wrote "registry unreachable" and exited 1 was graded a
        success, because the runtime returned only the prose and grading had
        no marker to find. Confidence rose 0.60 to 0.65 on the run that was
        meant to drop it to 0.50.
        """
        index = _Index()
        agent = _agent(config, tmp_path, index=index)
        _ran(agent, "deploy.sh", "deploy failed: registry unreachable\n[exit 1]")

        agent._handle_done({"action": "done", "explanation": "deployment failed"})

        assert index.nudges == [("deploy-api", False)]

    def test_a_blocked_command_fails_the_run(self, config, tmp_path):
        """`_run_command` returns this marker instead of running anything."""
        index = _Index()
        agent = _agent(config, tmp_path, index=index)
        _ran(agent, "rm -rf /", "[blocked: destructive command]")

        agent._handle_done({"action": "done", "explanation": "done"})

        assert index.nudges == [("deploy-api", False)]

    def test_a_timeout_fails_the_run(self, config, tmp_path):
        index = _Index()
        agent = _agent(config, tmp_path, index=index)
        _ran(agent, "sleep 999", "[timeout after 120s]")

        agent._handle_done({"action": "done", "explanation": "done"})

        assert index.nudges == [("deploy-api", False)]


class TestWhatIsStillWithheld:
    def test_an_edited_run_grades_nothing(self, config, tmp_path):
        """Unchanged: the human's fix must not be credited to the skill."""
        index = _Index()
        agent = _agent(config, tmp_path, index=index)
        _ran(agent, "deploy.sh", "(no output; exit 1)")
        agent._command_was_edited = True

        agent._handle_done({"action": "done", "explanation": "deployed"})

        assert index.nudges == []

    def test_a_refusal_grades_nothing(self, config, tmp_path):
        """Nothing ran, so the skill was neither helped nor hindered."""
        index = _Index()
        agent = _agent(config, tmp_path, index=index)

        agent._handle_done({"action": "done", "explanation": "I will delegate"})

        assert index.nudges == []


class TestOrdinaryOutputIsNotMisread:
    def test_the_word_error_in_output_is_not_a_failure(self, config, tmp_path):
        """Grading reads exit status, not prose. `grep error` is not a failure."""
        index = _Index()
        agent = _agent(config, tmp_path, index=index)
        _ran(agent, "grep error app.log", "2 errors found, all handled")

        agent._handle_done({"action": "done", "explanation": "checked"})

        assert index.nudges == [("deploy-api", True)]

    def test_a_zero_exit_marker_is_not_read_as_failure(self, config, tmp_path):
        """`exit 0` must not match a naive search for `exit `."""
        index = _Index()
        agent = _agent(config, tmp_path, index=index)
        _ran(agent, "true", "(no output; exit 0)")

        agent._handle_done({"action": "done", "explanation": "ok"})

        assert index.nudges == [("deploy-api", True)]
