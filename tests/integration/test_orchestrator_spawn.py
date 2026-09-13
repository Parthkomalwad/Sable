"""Integration test: orchestrator spawns one sub-agent and folds its result back.

Exercises the real OrchestratorAgent loop against the mock LLM backend's
orchestrator script (run, run, spawn, run, done). What is faked is only the
boundary the test cannot own: tmux (via TaskManager.spawn) and command
execution. Everything between, the turn loop, action parsing, the handoff
file, the bus drain and the context rebuild, is the real code.

Runs anywhere: no Docker, no tmux, no API key.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration

_CREATE_TASKS = """
CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    goal         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'starting',
    tmux_window_id TEXT,
    pid          INTEGER,
    step_count   INTEGER NOT NULL DEFAULT 0,
    last_output  TEXT,
    created_at   TEXT NOT NULL,
    ended_at     TEXT
)
"""


@pytest.fixture(autouse=True)
def mock_libtmux():
    with patch.dict(sys.modules, {"libtmux": MagicMock()}):
        yield


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "sessions.db")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_TASKS)
    conn.commit()
    conn.close()
    return path


class _RecordingTaskManager:
    """Stands in for TaskManager.spawn, which would need a live tmux server.

    Records the spawn and then does what a real TaskAgent does: publishes its
    progress to the event bus, and writes status.md / result.md as the
    human-readable mirrors. Since Phase 1 the orchestrator reads the events
    and not the files, so the publish is what this test actually exercises;
    the files are written because a real worker still writes them.
    """

    def __init__(self, db_path: str) -> None:
        self.spawned: list[dict] = []
        self._db_path = db_path

    def spawn(self, name: str, goal: str, context: str = "",
              task_base_dir: str | None = None) -> None:
        self.spawned.append(
            {"name": name, "goal": goal, "context": context,
             "task_base_dir": task_base_dir}
        )

        result_body = (
            f"# Task: {name}\n**Goal**: {goal}\n\n"
            "## Files created\n```\n./hello.txt\n```\n\n"
            "## Steps taken (2)\n1. `ls hello.txt`\n2. `cat hello.txt`\n"
        )

        from sable.core.events.bus import EventBus
        from sable.core.events.types import EventKind

        bus = EventBus(db_path=self._db_path)
        bus.publish(name, EventKind.STARTED, {"goal": goal})
        bus.publish(
            name, EventKind.STATUS,
            {"step": 1, "command": "ls hello.txt", "explanation": "check it exists"},
        )
        bus.publish(name, EventKind.COMPLETED, {"steps": 2, "result": result_body})
        bus.close()

        agentic = Path(task_base_dir) / name / ".agentic"
        agentic.mkdir(parents=True, exist_ok=True)
        (agentic / "status.md").write_text(
            f"# Agent: {name} (running step 1)\n**Goal**: {goal}\n"
        )
        (agentic / "result.md").write_text(result_body)


def _make_orchestrator(tmp_path, db_path, task_manager, goal="create a hello file"):
    from sable.core.config.schema import ShellConfig
    from sable.agents.orchestrator import OrchestratorAgent

    config = ShellConfig.defaults()
    config.tasks_base_dir = str(tmp_path / "tasks")
    return OrchestratorAgent(
        goal=goal,
        cwd=str(tmp_path),
        config=config,
        db_path=db_path,
        task_manager=task_manager,
    )


def _run(orchestrator, commands_seen: list[str]):
    """Run the loop with the mock backend, auto-confirming every command."""
    from tests.fixtures.mock_llm import MockLLMBackend

    def fake_run_command(command, timeout=120):
        commands_seen.append(command)
        return f"[output of {command}]"

    # A new backend per call, exactly as OrchestratorAgent._call_llm does it.
    # Returning one shared instance would hide any dependence on instance state.
    def fresh_backend(*args, **kwargs):
        return MockLLMBackend(mode="orchestrator")

    with patch("sable.llm.registry.build_backend", side_effect=fresh_backend), \
         patch.object(orchestrator, "_confirm_command", side_effect=lambda c, e: c), \
         patch.object(orchestrator, "_run_command", side_effect=fake_run_command):
        orchestrator.run()


class TestOrchestratorSpawn:
    def test_runs_commands_then_spawns_a_worker(self, tmp_path, db_path):
        manager = _RecordingTaskManager(db_path)
        orchestrator = _make_orchestrator(tmp_path, db_path, manager)
        commands: list[str] = []

        _run(orchestrator, commands)

        assert commands == [
            "echo hello > hello.txt",
            "cat hello.txt",
            "ls -l hello.txt",
        ]
        assert len(manager.spawned) == 1
        assert manager.spawned[0]["name"] == "verify-hello"

    def test_spawned_goal_carries_the_working_directory(self, tmp_path, db_path):
        manager = _RecordingTaskManager(db_path)
        orchestrator = _make_orchestrator(tmp_path, db_path, manager)

        _run(orchestrator, [])

        goal = manager.spawned[0]["goal"]
        assert goal.startswith("Working directory:")
        assert str(tmp_path) in goal

    def test_handoff_file_is_written_for_the_sub_agent(self, tmp_path, db_path):
        manager = _RecordingTaskManager(db_path)
        orchestrator = _make_orchestrator(tmp_path, db_path, manager)

        _run(orchestrator, [])

        handoff = (
            Path(manager.spawned[0]["task_base_dir"])
            / "verify-hello" / ".agentic" / "handoff.txt"
        )
        assert handoff.exists()
        body = handoff.read_text()
        assert "verify-hello" in body or "Verify hello.txt" in body
        assert "Parent goal: create a hello file" in body

    def test_sub_agent_result_flows_back_into_orchestrator_context(self, tmp_path, db_path):
        """The point of the test: the turn after the spawn sees the worker's
        result folded into the context the orchestrator sends to the model."""
        manager = _RecordingTaskManager(db_path)
        orchestrator = _make_orchestrator(tmp_path, db_path, manager)
        contexts: list[list[dict]] = []

        real_build = orchestrator._build_messages

        def capture():
            messages = real_build()
            contexts.append(messages)
            return messages

        with patch.object(orchestrator, "_build_messages", side_effect=capture):
            _run(orchestrator, [])

        folded = [
            m["content"]
            for messages in contexts
            for m in messages
            if "COMPLETED" in str(m.get("content", ""))
        ]
        assert folded, "worker result never reached the orchestrator context"
        assert "verify-hello" in folded[0]
        assert "hello.txt" in folded[0]

    def test_result_reaches_context_only_once(self, tmp_path, db_path):
        """A completion is folded in exactly once.

        The orchestrator advances its bus cursor past everything it drains,
        so a later turn cannot re-report the same completion. This used to
        be enforced by deleting result.md after reading it.
        """
        manager = _RecordingTaskManager(db_path)
        orchestrator = _make_orchestrator(tmp_path, db_path, manager)
        contexts: list[list[dict]] = []

        real_build = orchestrator._build_messages

        def capture():
            messages = real_build()
            contexts.append(messages)
            return messages

        with patch.object(orchestrator, "_build_messages", side_effect=capture):
            _run(orchestrator, [])

        completions = [
            m
            for messages in contexts
            for m in messages
            if "COMPLETED" in str(m.get("content", ""))
        ]
        assert len(completions) == 1

    def test_loop_ends_on_done_without_hitting_the_turn_limit(self, tmp_path, db_path):
        manager = _RecordingTaskManager(db_path)
        orchestrator = _make_orchestrator(tmp_path, db_path, manager)

        _run(orchestrator, [])

        # run, run, spawn, run, done = 5 turns, 2 history entries per turn.
        assert len(orchestrator._history) <= 10

    def test_result_file_records_the_outcome(self, tmp_path, db_path):
        manager = _RecordingTaskManager(db_path)
        orchestrator = _make_orchestrator(tmp_path, db_path, manager)

        _run(orchestrator, [])

        result = Path(manager.spawned[0]["task_base_dir"]) / ".agentic" / "result.md"
        assert result.exists()
        assert "create a hello file" in result.read_text()
