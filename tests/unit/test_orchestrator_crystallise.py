"""The orchestrator drafts a skill from a completed multi-step run (B3).

Phase 2 Task 6 built `SkillCrystalliser.from_run()` and tested it directly.
Nothing ever called it. `grep -rn from_run sable/` returned the definition and
no call site, in the orchestrator or anywhere else, so the post-task half of
B3 was dead from the day it shipped and the phase's gate line 1 ("after run 1:
/skill list shows draft 'deploy-api' pending") was unreachable by any code
path.

Found by the manual gate run, not by the suite: 18 unit tests drove `from_run`
as a unit and all passed, because a test that calls the function under test
directly cannot notice that production never does. This file tests the wiring
instead, which is the seam those tests skipped.

Three decisions, each mirroring one the skills loop already made.

**Injected, not imported.** `agents` sits below `skills` in the layering rule,
so constructing a crystalliser here would add a fourth `agents -> skills` edge
and flip `test_layering.py`'s strict xfail. The REPL owns it and hands it in,
exactly as it does `skill_loader`, `skill_index` and `corrections_db`.

**Drafted only on a real completion.** A `done` the guard already reads as a
refusal has demonstrated nothing worth keeping, and an edited run is the same
ambiguous evidence that `_grade_skills` refuses to grade on. Both are
withheld here for the reasons they are withheld there.

**Never fatal.** Drafting happens after the work is finished. `from_run`
already promises not to raise; this asserts the caller does not either, since
a crystalliser that throws would turn a completed goal into a failed one.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sable.agents.orchestrator import OrchestratorAgent


class _Crystalliser:
    """Records what the orchestrator asked it to draft."""

    def __init__(self, returns=None, raises=None):
        self.calls: list[dict] = []
        self._returns = returns
        self._raises = raises

    def from_run(self, goal: str, steps: list[dict], succeeded: bool):
        self.calls.append({"goal": goal, "steps": steps, "succeeded": succeeded})
        if self._raises is not None:
            raise self._raises
        return self._returns


@pytest.fixture
def config(tmp_path):
    cfg = MagicMock()
    cfg.tasks_base_dir = str(tmp_path / "tasks")
    cfg.model = "test-model"
    cfg.model_for.return_value = "test-model"
    return cfg


def _agent(config, tmp_path, crystalliser=None):
    return OrchestratorAgent(
        goal="deploy the api",
        cwd=str(tmp_path),
        config=config,
        db_path=str(tmp_path / "sessions.db"),
        task_manager=MagicMock(),
        crystalliser=crystalliser,
    )


def _ran(agent, command: str, output: str = "ok") -> None:
    """Record an executed step the way a real turn does.

    Both counters, because `_handle_run` moves both: `_commands_run` is what
    the refusal guard reads and `_steps` is what B3 summarises. Recording
    only the latter would leave the agent looking like it had refused.
    """
    agent._commands_run += 1
    agent._record_step(command, output)


class TestTheCallSiteExists:
    def test_a_completed_multi_step_run_drafts_a_skill(self, config, tmp_path):
        """The bug: this call never happened, so no draft was ever written."""
        crystalliser = _Crystalliser()
        agent = _agent(config, tmp_path, crystalliser=crystalliser)
        _ran(agent, "/root/api/deploy.sh")
        _ran(agent, "cat /root/api/build/STATUS")
        _ran(agent, "cat /root/api/build/app.txt")

        agent._handle_done({"action": "done", "explanation": "deployed"})

        assert len(crystalliser.calls) == 1
        call = crystalliser.calls[0]
        assert call["goal"] == "deploy the api"
        assert call["succeeded"] is True

    def test_the_steps_carry_the_commands_that_ran(self, config, tmp_path):
        """`from_run` asks the model for a procedure; it needs the commands."""
        crystalliser = _Crystalliser()
        agent = _agent(config, tmp_path, crystalliser=crystalliser)
        _ran(agent, "/root/api/deploy.sh")
        _ran(agent, "cat /root/api/build/STATUS")
        _ran(agent, "cat /root/api/build/app.txt")

        agent._handle_done({"action": "done", "explanation": "deployed"})

        commands = [s["command"] for s in crystalliser.calls[0]["steps"]]
        assert commands == [
            "/root/api/deploy.sh",
            "cat /root/api/build/STATUS",
            "cat /root/api/build/app.txt",
        ]

    def test_no_crystalliser_means_no_drafting(self, config, tmp_path):
        """Every existing caller passes nothing and must keep working."""
        agent = _agent(config, tmp_path, crystalliser=None)
        _ran(agent, "one")
        _ran(agent, "two")
        _ran(agent, "three")

        agent._handle_done({"action": "done", "explanation": "done"})  # must not raise


class TestWhatIsWithheld:
    def test_a_refusal_drafts_nothing(self, config, tmp_path):
        """`_commands_run == 0` already reads as a refusal. Nothing was shown."""
        crystalliser = _Crystalliser()
        agent = _agent(config, tmp_path, crystalliser=crystalliser)

        agent._handle_done({"action": "done", "explanation": "I will delegate this"})

        assert crystalliser.calls == []

    def test_an_edited_run_drafts_nothing(self, config, tmp_path):
        """The same evidence `_grade_skills` refuses to grade on.

        The human rewrote a command, so the procedure that ran is theirs and
        the model's account of it would be drafted as if it were the model's.
        """
        crystalliser = _Crystalliser()
        agent = _agent(config, tmp_path, crystalliser=crystalliser)
        _ran(agent, "one")
        _ran(agent, "two")
        _ran(agent, "three")
        agent._command_was_edited = True

        agent._handle_done({"action": "done", "explanation": "deployed"})

        assert crystalliser.calls == []


class TestFailureIsNotFatal:
    def test_a_crystalliser_that_raises_does_not_lose_the_goal(self, config, tmp_path):
        """Drafting runs after the work. It must never fail a finished goal."""
        crystalliser = _Crystalliser(raises=OSError("skills dir unwritable"))
        agent = _agent(config, tmp_path, crystalliser=crystalliser)
        _ran(agent, "one")
        _ran(agent, "two")
        _ran(agent, "three")

        agent._handle_done({"action": "done", "explanation": "deployed"})  # must not raise

    def test_a_draft_is_announced_so_the_user_can_approve_it(self, config, tmp_path, capsys):
        """A pending draft nobody hears about is a draft nobody approves."""
        drafted = tmp_path / "skills" / "deploy-api" / "SKILL.md"
        crystalliser = _Crystalliser(returns=drafted)
        agent = _agent(config, tmp_path, crystalliser=crystalliser)
        _ran(agent, "one")
        _ran(agent, "two")
        _ran(agent, "three")

        agent._handle_done({"action": "done", "explanation": "deployed"})

        out = capsys.readouterr().out
        assert "deploy-api" in out
        assert "/skill approve" in out
