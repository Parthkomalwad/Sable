"""Unit tests for TaskMemory (shell/tasks/memory.py).

Covers the pinned goal, step summarisation, versioned snapshots on disk,
the task_memory DB row, and skill content hashing. No LLM calls.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from shell.tasks.memory import TaskMemory

_CREATE_TASK_MEMORY = """
CREATE TABLE IF NOT EXISTS task_memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name   TEXT NOT NULL,
    version     INTEGER NOT NULL,
    path        TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
)
"""


class _DB:
    """Minimal stand-in for telemetry.Database: TaskMemory only uses _conn."""

    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_TASK_MEMORY)
        self._conn.commit()


@pytest.fixture
def db(tmp_path):
    return _DB(str(tmp_path / "sessions.db"))


@pytest.fixture
def memory(tmp_path, db):
    return TaskMemory("my-task", str(tmp_path / "tasks"), db=db)


def test_creates_memory_directory(tmp_path, db):
    TaskMemory("my-task", str(tmp_path / "tasks"), db=db)
    assert (tmp_path / "tasks" / "my-task" / ".agentic" / "memory").is_dir()


class TestBuildContext:
    def test_goal_is_pinned_at_position_zero(self, memory):
        memory.set_goal("deploy the api")
        memory.add_turns([{"role": "assistant", "content": "step one"}])

        context = memory.build_context()

        assert context[0]["role"] == "system"
        assert context[0]["content"] == "[GOAL] deploy the api"

    def test_turns_follow_the_goal_in_order(self, memory):
        memory.set_goal("g")
        memory.add_turns([{"role": "assistant", "content": "one"}])
        memory.add_turns([{"role": "user", "content": "two"}])

        context = memory.build_context()

        assert [t["content"] for t in context[1:]] == ["one", "two"]

    def test_goal_survives_summarisation(self, memory):
        memory.set_goal("keep me")
        memory.add_turns([{"role": "assistant", "content": "noise"}])

        memory.summarise_last_step("did the thing")
        context = memory.build_context()

        assert context[0]["content"] == "[GOAL] keep me"
        assert len(context) == 2
        assert context[1] == {"role": "summary", "content": "did the thing"}


class TestSnapshots:
    def test_versions_increment_from_one(self, memory):
        memory.set_goal("g")
        assert memory.save_snapshot() == 1
        assert memory.save_snapshot() == 2

    def test_snapshot_file_holds_goal_and_turns(self, tmp_path, memory):
        memory.set_goal("build it")
        memory.add_turns([{"role": "assistant", "content": "ran make"}])

        version = memory.save_snapshot()

        path = tmp_path / "tasks" / "my-task" / ".agentic" / "memory" / f"v{version}.json"
        snap = json.loads(path.read_text())
        assert snap["version"] == 1
        assert snap["goal"] == "build it"
        assert snap["turns"] == [{"role": "assistant", "content": "ran make"}]
        assert snap["created_at"]

    def test_load_snapshot_round_trips(self, memory):
        memory.set_goal("g")
        memory.add_turns([{"role": "user", "content": "hello"}])
        version = memory.save_snapshot()

        assert memory.load_snapshot(version)["turns"] == [
            {"role": "user", "content": "hello"}
        ]

    def test_load_missing_snapshot_raises(self, memory):
        with pytest.raises(OSError):
            memory.load_snapshot(99)

    def test_snapshot_writes_a_task_memory_row(self, memory, db):
        memory.set_goal("g")
        version = memory.save_snapshot()

        row = db._conn.execute(
            "SELECT task_name, version, path FROM task_memory"
        ).fetchone()
        assert row[0] == "my-task"
        assert row[1] == version
        assert row[2].endswith(f"v{version}.json")

    def test_later_snapshot_reflects_summarisation(self, memory):
        memory.set_goal("g")
        memory.add_turns([{"role": "assistant", "content": "verbose"}])
        memory.save_snapshot()

        memory.summarise_last_step("short")
        second = memory.load_snapshot(memory.save_snapshot())

        assert second["turns"] == [{"role": "summary", "content": "short"}]


class TestSkillHashing:
    def test_same_content_hashes_equal(self, memory):
        first = memory.register_skill_hash("a", "# skill body")
        second = memory.register_skill_hash("b", "# skill body")
        assert first == second

    def test_different_content_hashes_differ(self, memory):
        assert memory.register_skill_hash("a", "one") != memory.register_skill_hash(
            "a", "two"
        )

    def test_registered_hash_is_seen(self, memory):
        content_hash = memory.register_skill_hash("a", "body")
        assert memory.is_skill_seen(content_hash) is True

    def test_unregistered_hash_is_not_seen(self, memory):
        assert memory.is_skill_seen("deadbeef") is False
