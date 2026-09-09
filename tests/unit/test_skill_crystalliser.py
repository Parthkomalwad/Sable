"""Unit tests for SkillCrystalliser (shell/skills/crystalliser.py).

The LLM backend is mocked throughout: no network, no API key needed.
SKILLS_DIR and the SkillIndex path are redirected into tmp_path so nothing
touches the real ~/skills.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from shell.llm.base import LLMResponse
from shell.skills import crystalliser as crystalliser_module
from shell.skills.crystalliser import SkillCrystalliser, _slugify
from shell.skills.index import SkillIndex

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

PATTERN = {
    "pattern_hash": "abc123",
    "repo_path": "/srv/api",
    "command_sequence": "docker|docker|make",
    "intent_keywords": ["docker", "deploy", "api"],
    "occurrence_count": 3,
}


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "sessions.db")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_SKILL_PATTERNS)
    conn.execute(
        "INSERT INTO skill_patterns (pattern_hash, repo_path, command_sequence,"
        " intent_keywords, occurrence_count, crystallised, last_seen)"
        " VALUES ('abc123', '/srv/api', 'docker|docker|make', '[]', 3, 0, 'now')"
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def skills_dir(tmp_path, monkeypatch):
    """Redirect the module-level SKILLS_DIR and the index into tmp_path."""
    directory = tmp_path / "skills" / "instructions"
    monkeypatch.setattr(crystalliser_module, "SKILLS_DIR", directory)

    index_path = tmp_path / "skills" / "skills_index.json"
    real_init = SkillIndex.__init__

    def _init(self, index_path_arg=None):
        real_init(self, index_path=str(index_path))

    monkeypatch.setattr(SkillIndex, "__init__", _init)
    return directory


def _mock_backend(explanation: str = "# deploy-api\n\n## When to use\nDeploying.\n"):
    backend = MagicMock()

    async def _complete(messages, system):
        return LLMResponse(
            command="",
            explanation=explanation,
            safe=True,
            plan=None,
            prompt_tokens=10,
            completion_tokens=20,
            cost_usd=0.0,
        )

    backend.complete = _complete
    return backend


def _crystallise(pattern, db_path, backend):
    with patch("shell.loop._build_backend", return_value=backend):
        return SkillCrystalliser(config=MagicMock(), db_path=db_path).crystallise(pattern)


class TestSlugify:
    def test_lowercases_and_hyphenates(self):
        assert _slugify("Deploy The API") == "deploy-the-api"

    def test_strips_punctuation_and_edges(self):
        assert _slugify("  docker/compose!  ") == "docker-compose"

    def test_truncates_to_forty_characters(self):
        assert len(_slugify("x" * 80)) == 40


class TestCrystallise:
    def test_writes_skill_file_named_for_first_keyword(self, db_path, skills_dir):
        path = _crystallise(PATTERN, db_path, _mock_backend())

        assert path == skills_dir / "docker.md"
        assert path.exists()

    def test_file_holds_the_model_output(self, db_path, skills_dir):
        body = "# deploy-api\n\n## Steps\n1. build\n"
        path = _crystallise(PATTERN, db_path, _mock_backend(body))

        assert path.read_text() == body

    def test_prompt_carries_repo_commands_and_keywords(self, db_path, skills_dir):
        seen = {}
        backend = MagicMock()

        async def _complete(messages, system):
            seen["prompt"] = messages[0]["content"]
            seen["system"] = system
            return LLMResponse(command="", explanation="# s", safe=True, plan=None)

        backend.complete = _complete
        _crystallise(PATTERN, db_path, backend)

        assert "/srv/api" in seen["prompt"]
        assert "docker" in seen["prompt"]
        assert "deploy" in seen["prompt"]
        assert "skill" in seen["system"].lower()

    def test_adds_an_auto_generated_index_entry(self, db_path, skills_dir):
        _crystallise(PATTERN, db_path, _mock_backend())

        entry = SkillIndex().list_all()[0]
        assert entry["name"] == "docker"
        assert entry["auto_generated"] is True
        assert entry["confidence"] == 0.5
        assert entry["keywords"] == PATTERN["intent_keywords"]

    def test_marks_the_pattern_crystallised(self, db_path, skills_dir):
        _crystallise(PATTERN, db_path, _mock_backend())

        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT crystallised FROM skill_patterns WHERE pattern_hash='abc123'"
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == 1

    def test_llm_failure_still_writes_a_placeholder_file(self, db_path, skills_dir):
        backend = MagicMock()

        async def _boom(messages, system):
            raise RuntimeError("backend unreachable")

        backend.complete = _boom

        path = _crystallise(PATTERN, db_path, backend)

        assert path.exists()
        assert "generation failed" in path.read_text()

    def test_pattern_without_keywords_falls_back_to_skill(self, db_path, skills_dir):
        pattern = {**PATTERN, "intent_keywords": []}

        path = _crystallise(pattern, db_path, _mock_backend())

        assert path.name == "skill.md"


class TestUpdate:
    def test_update_reruns_generation_and_recrystallises(self, db_path, skills_dir):
        _crystallise(PATTERN, db_path, _mock_backend("# first\n"))

        with patch("shell.loop._build_backend", return_value=_mock_backend("# second\n")):
            path = SkillCrystalliser(config=MagicMock(), db_path=db_path).update(PATTERN)

        assert path.read_text() == "# second\n"
        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute(
                "SELECT crystallised FROM skill_patterns WHERE pattern_hash='abc123'"
            ).fetchone()[0] == 1
        finally:
            conn.close()
