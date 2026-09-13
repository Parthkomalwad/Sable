"""Drafting a skill from a completed run (B3).

Before this, a skill could only come from `PatternWatcher` noticing the same
commands three times across sessions, at `/exit`. That misses the case the
roadmap actually cares about: a multi-step goal that worked, once, and whose
procedure is worth keeping before it is ever repeated.

The rules here were decided before implementation, and each costs something
if broken:

**Only a successful run with at least three executed steps.** A failed run
teaches a procedure that does not work. A one or two step run is not a
procedure. Both would cost a summariser call per run to produce a skill
nobody wants.

**The draft lands pending.** Phase 2's gate says a skill is never enabled
without a human, and this is the path that would otherwise enable one
silently, moments after a goal finished, while the user is still reading the
output.

**The summariser role, not the orchestrator's.** This is summarising, not
reasoning, and A5 exists so that judgement is a config change rather than a
code change.

**A "no" writes nothing.** The model is asked whether the work is reusable
at all, and its refusal has to be honoured, or the question is theatre.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from sable.llm.base import LLMResponse
from sable.skills import crystalliser as crystalliser_module
from sable.skills.crystalliser import SkillCrystalliser
from sable.skills.index import SkillIndex
from sable.skills.model import parse_skill

_CREATE_SKILL_PATTERNS = """
CREATE TABLE IF NOT EXISTS skill_patterns (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_hash     TEXT NOT NULL UNIQUE,
    repo_path        TEXT NOT NULL,
    command_sequence TEXT NOT NULL,
    intent_keywords  TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    crystallised     INTEGER NOT NULL DEFAULT 0,
    last_seen        TEXT NOT NULL
)
"""

STEPS = [
    {"command": "docker compose build api", "explanation": "build the image"},
    {"command": "docker compose up -d api", "explanation": "start it"},
    {"command": "curl -sf localhost:8080/health", "explanation": "check it"},
]

_YES = json.dumps({
    "reusable": True,
    "name": "deploy-api",
    "description": "Build and restart the API container",
    "triggers": ["deploy the api", "restart the api"],
    "validate": "docker compose ps api",
    "body": "# Deploy the API\n\n## Steps\n1. Build.\n2. Start.\n3. Check.\n",
})

_NO = json.dumps({
    "reusable": False,
    "description": "one-off exploration of a log file",
})


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "sessions.db")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_SKILL_PATTERNS)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def skills_root(tmp_path, monkeypatch):
    """Redirect the skills root and the index into tmp_path."""
    root = tmp_path / "skills"
    monkeypatch.setattr(crystalliser_module, "SKILLS_ROOT", root)

    index_path = tmp_path / "skills" / "skills_index.json"
    real_init = SkillIndex.__init__

    def _init(self, index_path_arg=None):
        real_init(self, index_path=str(index_path))

    monkeypatch.setattr(SkillIndex, "__init__", _init)
    return root


def _backend(answer: str):
    backend = MagicMock()

    async def _complete(messages, system):
        return LLMResponse(
            command="", explanation=answer, safe=True, plan=None,
            prompt_tokens=10, completion_tokens=20, cost_usd=0.0,
        )

    backend.complete = _complete
    backend.captured = {"messages": None, "system": None}
    return backend


def _from_run(db_path, backend, *, goal="deploy the api", steps=None,
              succeeded=True, role_sink=None):
    def _build(config, role=None, **kwargs):
        if role_sink is not None:
            role_sink.append(role)
        return backend

    with patch("sable.llm.registry.build_backend", _build):
        return SkillCrystalliser(config=MagicMock(), db_path=db_path).from_run(
            goal=goal,
            steps=STEPS if steps is None else steps,
            succeeded=succeeded,
        )


class TestWhenItRuns:
    def test_a_successful_three_step_run_drafts_a_skill(self, db_path, skills_root):
        assert _from_run(db_path, _backend(_YES)) is not None

    def test_a_failed_run_drafts_nothing(self, db_path, skills_root):
        """A failed run teaches a procedure that does not work."""
        backend = _backend(_YES)
        assert _from_run(db_path, backend, succeeded=False) is None

    def test_a_two_step_run_drafts_nothing(self, db_path, skills_root):
        assert _from_run(db_path, _backend(_YES), steps=STEPS[:2]) is None

    def test_a_run_with_no_steps_drafts_nothing(self, db_path, skills_root):
        assert _from_run(db_path, _backend(_YES), steps=[]) is None

    def test_the_model_is_not_asked_when_the_run_does_not_qualify(
        self, db_path, skills_root
    ):
        """The gate is arithmetic, so it must cost nothing when it fails.

        Asking first and discarding the answer would spend a summariser call
        on every short or failed run in the system.
        """
        calls = []
        backend = _backend(_YES)

        async def _complete(messages, system):
            calls.append(1)
            return LLMResponse(
                command="", explanation=_YES, safe=True, plan=None,
                prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
            )

        backend.complete = _complete

        _from_run(db_path, backend, steps=STEPS[:1])

        assert calls == []


class TestTheModelsAnswerIsHonoured:
    def test_a_refusal_writes_no_file(self, db_path, skills_root):
        assert _from_run(db_path, _backend(_NO)) is None
        assert not list(skills_root.glob("*/SKILL.md"))

    def test_a_refusal_adds_no_index_entry(self, db_path, skills_root):
        _from_run(db_path, _backend(_NO))
        assert SkillIndex().list_all() == []

    def test_an_unparseable_answer_writes_nothing(self, db_path, skills_root):
        """Unlike `crystallise()`, there is nothing to salvage here.

        That path writes a placeholder carrying the failure, because a
        pattern crossed a threshold and the evidence should not vanish. Here
        the model was asked a yes/no question and did not answer it, so the
        honest response is to draft nothing.
        """
        assert _from_run(db_path, _backend("I think maybe?")) is None
        assert not list(skills_root.glob("*/SKILL.md"))


class TestWhatIsWritten:
    def test_the_draft_is_pending(self, db_path, skills_root):
        """The load-bearing assertion: this path must not enable a skill."""
        path = _from_run(db_path, _backend(_YES))
        assert parse_skill(path.read_text(encoding="utf-8")).status == "pending"

    def test_the_index_entry_is_pending_too(self, db_path, skills_root):
        _from_run(db_path, _backend(_YES))
        assert [e["status"] for e in SkillIndex().list_all()] == ["pending"]

    def test_it_is_sourced_as_crystallised(self, db_path, skills_root):
        """Phase 8's K8 sets a policy floor by source, so this is not cosmetic."""
        path = _from_run(db_path, _backend(_YES))
        assert parse_skill(path.read_text(encoding="utf-8")).source == "crystallised"

    def test_the_frontmatter_carries_the_models_fields(self, db_path, skills_root):
        path = _from_run(db_path, _backend(_YES))
        skill = parse_skill(path.read_text(encoding="utf-8"))
        assert skill.name == "deploy-api"
        assert skill.description == "Build and restart the API container"
        assert skill.triggers == ["deploy the api", "restart the api"]
        assert skill.validate == "docker compose ps api"

    def test_it_lands_in_a_folder(self, db_path, skills_root):
        path = _from_run(db_path, _backend(_YES))
        assert path == skills_root / "deploy-api" / "SKILL.md"

    def test_the_body_is_the_models_body(self, db_path, skills_root):
        path = _from_run(db_path, _backend(_YES))
        assert "1. Build." in parse_skill(path.read_text(encoding="utf-8")).body

    def test_an_existing_skill_of_the_same_name_is_not_overwritten(
        self, db_path, skills_root
    ):
        """A skill the user approved and possibly edited outranks a new draft.

        Silently replacing it would discard their edits and reset a
        confidence they had earned.
        """
        folder = skills_root / "deploy-api"
        folder.mkdir(parents=True)
        existing = (
            '+++\nname = "deploy-api"\ndescription = "Mine"\n'
            'status = "enabled"\nsource = "user"\n+++\n\n# Mine\n'
        )
        (folder / "SKILL.md").write_text(existing, encoding="utf-8")

        assert _from_run(db_path, _backend(_YES)) is None
        assert (folder / "SKILL.md").read_text(encoding="utf-8") == existing


class TestTheSummariserRole:
    def test_the_summariser_model_is_used(self, db_path, skills_root):
        """A5: this is summarising, not reasoning. Cheap model by default."""
        roles = []
        _from_run(db_path, _backend(_YES), role_sink=roles)
        assert roles == ["summariser"]


class TestWhatIsAsked:
    def test_the_goal_and_the_commands_reach_the_model(self, db_path, skills_root):
        captured = {}
        backend = MagicMock()

        async def _complete(messages, system):
            captured["messages"] = messages
            captured["system"] = system
            return LLMResponse(
                command="", explanation=_YES, safe=True, plan=None,
                prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
            )

        backend.complete = _complete

        _from_run(db_path, backend)

        text = " ".join(m["content"] for m in captured["messages"])
        assert "deploy the api" in text
        assert "docker compose build api" in text

    def test_failures_are_not_fatal(self, db_path, skills_root):
        """Drafting runs after a goal finished. It must not undo it."""
        backend = MagicMock()

        async def _complete(messages, system):
            raise OSError("backend unreachable")

        backend.complete = _complete

        assert _from_run(db_path, backend) is None
