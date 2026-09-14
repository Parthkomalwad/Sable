"""The orchestrator retrieves, announces and grades skills.

Phase 2 shipped the skills loop into `agents/worker.py` only. That is the
*spawned sub-agent*: it exists when the orchestrator delegates a long job.
The orchestrator is what a typed goal actually reaches, so until this lands
an approved skill is used by the rarer path and ignored by the main one, and
the phase's own goal statement ("the shell gets measurably better at a task
the second and third time you do it") is untrue where a user would notice.

Three things differ from the worker, each for a reason.

**Retrieved once, not per turn.** The worker calls `load_relevant` inside
its step loop and leans on `TaskMemory`'s content-hash dedup. The
orchestrator has no `TaskMemory` and a 20-turn limit, so re-injecting skill
text every turn would spend the context it needs for the work. The goal does
not change mid-run, so once at the top is both correct and cheaper.

**Placed after the K11 project block.** Project instructions describe the
repo's conventions; a skill is procedural memory the user approved. The
model should read conventions first and procedure second, and neither may
displace the goal.

**Graded only when no command was edited.** Every orchestrator command is
human-confirmed, and `e` lets the human rewrite it. A run the human rescued
is at best ambiguous evidence for the skill and at worst positive credit for
a procedure that did not work, so an edited run grades nothing rather than
grading success.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sable.agents.orchestrator import OrchestratorAgent


class _Loader:
    def __init__(self, skills=None):
        self._skills = skills or []
        self.calls: list[str] = []

    def load_relevant(self, goal: str) -> list[dict]:
        self.calls.append(goal)
        return list(self._skills)


class _Index:
    def __init__(self):
        self.nudges: list[tuple[str, bool]] = []

    def nudge(self, name: str, success: bool) -> None:
        self.nudges.append((name, success))


def _skill(name="deploy-api", confidence=0.55, body="Steps.", validate=""):
    return {
        "name": name,
        "content": body,
        "hash": f"hash-of-{name}",
        "source": "global",
        "confidence": confidence,
        "validate": validate,
    }


@pytest.fixture
def config(tmp_path):
    cfg = MagicMock()
    cfg.tasks_base_dir = str(tmp_path / "tasks")
    cfg.model = "test-model"
    cfg.model_for.return_value = "test-model"
    return cfg


def _agent(config, tmp_path, loader=None, index=None):
    return OrchestratorAgent(
        goal="deploy the api",
        cwd=str(tmp_path),
        config=config,
        db_path=str(tmp_path / "sessions.db"),
        task_manager=MagicMock(),
        skill_loader=loader,
        skill_index=index,
    )


class TestRetrieval:
    def test_skills_are_loaded_for_the_goal(self, config, tmp_path):
        loader = _Loader([_skill()])

        agent = _agent(config, tmp_path, loader=loader)

        assert loader.calls == ["deploy the api"]
        assert [s["name"] for s in agent._skills] == ["deploy-api"]

    def test_retrieval_happens_once_not_per_turn(self, config, tmp_path):
        """A 20-turn run must not re-retrieve, or re-inject, 20 times."""
        loader = _Loader([_skill()])

        agent = _agent(config, tmp_path, loader=loader)
        agent._build_messages()
        agent._build_messages()
        agent._build_messages()

        assert len(loader.calls) == 1

    def test_no_loader_means_no_skills(self, config, tmp_path):
        """Every existing caller passes nothing and must keep working."""
        agent = _agent(config, tmp_path, loader=None)

        assert agent._skills == []

    def test_a_loader_that_fails_does_not_stop_the_run(self, config, tmp_path):
        """A broken skills directory must not cost the user their goal."""
        loader = MagicMock()
        loader.load_relevant.side_effect = OSError("skills unreadable")

        agent = _agent(config, tmp_path, loader=loader)

        assert agent._skills == []


class TestInjection:
    def test_the_skill_body_reaches_the_model(self, config, tmp_path):
        agent = _agent(config, tmp_path, loader=_Loader([_skill(body="Run the deploy.")]))

        text = " ".join(m["content"] for m in agent._build_messages())

        assert "Run the deploy." in text

    def test_skills_come_after_the_goal(self, config, tmp_path):
        """The goal stays primary. A skill informs how, never what."""
        agent = _agent(config, tmp_path, loader=_Loader([_skill(body="SKILLBODY")]))

        messages = agent._build_messages()
        goal_at = next(i for i, m in enumerate(messages) if "<goal>" in m["content"])
        skill_at = next(i for i, m in enumerate(messages) if "SKILLBODY" in m["content"])

        assert goal_at < skill_at

    def test_skills_come_after_project_instructions(self, config, tmp_path, monkeypatch):
        """K11 conventions first, procedure second."""
        from sable.agents import context

        monkeypatch.setattr(
            context, "build_context_message",
            lambda cwd: {"role": "user", "content": "PROJECTINSTRUCTIONS"},
        )
        agent = _agent(config, tmp_path, loader=_Loader([_skill(body="SKILLBODY")]))

        messages = agent._build_messages()
        project_at = next(i for i, m in enumerate(messages) if "PROJECTINSTRUCTIONS" in m["content"])
        skill_at = next(i for i, m in enumerate(messages) if "SKILLBODY" in m["content"])

        assert project_at < skill_at

    def test_history_comes_last(self, config, tmp_path):
        """Injected context is stable; history is what changes each turn."""
        agent = _agent(config, tmp_path, loader=_Loader([_skill(body="SKILLBODY")]))
        agent._history.append({"role": "user", "content": "HISTORYLINE"})

        messages = agent._build_messages()
        skill_at = next(i for i, m in enumerate(messages) if "SKILLBODY" in m["content"])
        history_at = next(i for i, m in enumerate(messages) if "HISTORYLINE" in m["content"])

        assert skill_at < history_at

    def test_nothing_is_injected_when_no_skill_matched(self, config, tmp_path):
        agent = _agent(config, tmp_path, loader=_Loader([]))

        messages = agent._build_messages()

        assert all("skill" not in m["content"].lower() for m in messages)


class TestAnnouncement:
    def test_the_first_turn_names_the_skill_and_confidence(self, config, tmp_path, capsys):
        """The gate's wording, on the path a typed goal actually takes."""
        agent = _agent(config, tmp_path, loader=_Loader([_skill(confidence=0.55)]))

        agent._announce_skills()

        assert "using skill deploy-api (0.55)" in capsys.readouterr().out

    def test_nothing_is_printed_without_skills(self, config, tmp_path, capsys):
        agent = _agent(config, tmp_path, loader=_Loader([]))

        agent._announce_skills()

        assert capsys.readouterr().out == ""

    def test_it_announces_once_not_per_turn(self, config, tmp_path, capsys):
        agent = _agent(config, tmp_path, loader=_Loader([_skill()]))

        agent._announce_skills()
        agent._announce_skills()

        assert capsys.readouterr().out.count("using skill") == 1


class TestADoneThatDidNothing:
    """A `done` before any command ran is a refusal, not a completion.

    Seen in the playground: asked to build a snake game inside the Sable
    repo, the model answered on turn one with

        {"action": "done",
         "explanation": "This task requires multiple steps and may take
                         longer than 30 seconds."}

    which is valid JSON and a valid action, so the loop accepted it and
    printed it with the same success mark a finished goal gets. Nothing
    ran, nothing was spawned, no tokens showed in the sidebar, and the
    user had to run `/why` to discover the model had declined rather than
    finished. A refusal that renders as a success is the same silent
    failure shape as a dropped panel or a discarded model answer.

    The model's own words are kept, because they are the useful part: they
    say what it thought was in the way.
    """

    def test_a_done_with_nothing_run_says_it_declined(self, config, tmp_path, capsys):
        agent = _agent(config, tmp_path)

        agent._handle_done({"explanation": "this would take too long"})

        out = capsys.readouterr().out.lower()
        assert "declined" in out or "did not run" in out
        assert "this would take too long" in out

    def test_it_does_not_use_the_success_mark(self, config, tmp_path, capsys):
        """The tick is what makes a refusal look like a finished goal."""
        agent = _agent(config, tmp_path)

        agent._handle_done({"explanation": "nope"})

        assert "✦" not in capsys.readouterr().out

    def test_a_done_after_a_command_is_a_normal_completion(self, config, tmp_path, capsys):
        agent = _agent(config, tmp_path)
        agent._commands_run = 1

        agent._handle_done({"explanation": "created the file"})

        out = capsys.readouterr().out
        assert "✦" in out
        assert "declined" not in out.lower()

    def test_a_done_after_only_a_spawn_is_a_completion(self, config, tmp_path, capsys):
        """Delegating the work is doing it. The sub-agent carries on."""
        agent = _agent(config, tmp_path)
        agent._commands_run = 1

        agent._handle_done({"explanation": "handed off to a worker"})

        assert "declined" not in capsys.readouterr().out.lower()

    def test_a_refusal_suggests_what_to_do(self, config, tmp_path, capsys):
        """A message with no next step is the thing that wasted the user's time."""
        agent = _agent(config, tmp_path)

        agent._handle_done({"explanation": "too vague"})

        assert "/why" in capsys.readouterr().out

    def test_a_refusal_grades_no_skills(self, config, tmp_path):
        """Nothing ran, so a skill has demonstrated nothing either way."""
        index = _Index()
        agent = _agent(config, tmp_path, loader=_Loader([_skill()]), index=index)

        agent._handle_done({"explanation": "declined"})

        assert index.nudges == []


class TestGrading:
    def test_a_finished_goal_nudges_the_skills_it_used(self, config, tmp_path):
        index = _Index()
        agent = _agent(config, tmp_path, loader=_Loader([_skill()]), index=index)
        agent._commands_run = 1      # a real completion, not a refusal

        agent._handle_done({"explanation": "deployed"})

        assert index.nudges == [("deploy-api", True)]

    def test_an_edited_run_grades_nothing(self, config, tmp_path):
        """The human rewrote a command, so the skill's procedure fell short.

        Crediting the skill for a run a human rescued is how a procedure
        that does not work climbs to high confidence.
        """
        index = _Index()
        agent = _agent(config, tmp_path, loader=_Loader([_skill()]), index=index)
        agent._command_was_edited = True

        agent._handle_done({"explanation": "deployed, after I fixed it"})

        assert index.nudges == []

    def test_a_run_with_no_skills_nudges_nothing(self, config, tmp_path):
        index = _Index()
        agent = _agent(config, tmp_path, loader=_Loader([]), index=index)

        agent._handle_done({"explanation": "done"})

        assert index.nudges == []

    def test_grading_never_raises(self, config, tmp_path):
        """Grading happens after the work. It must not undo it."""
        index = MagicMock()
        index.nudge.side_effect = OSError("index is read-only")
        agent = _agent(config, tmp_path, loader=_Loader([_skill()]), index=index)

        agent._handle_done({"explanation": "deployed"})

    def test_no_index_means_no_grading(self, config, tmp_path):
        agent = _agent(config, tmp_path, loader=_Loader([_skill()]), index=None)

        agent._handle_done({"explanation": "deployed"})
