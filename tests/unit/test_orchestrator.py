import json
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path


def _make_orchestrator(tmp_path, goal="list files here"):
    from sable.agents.orchestrator import OrchestratorAgent
    config = MagicMock()
    config.tasks_base_dir = str(tmp_path / "tasks")
    config.backend = "ollama"
    config.model = "llama3"
    config.api_base = "http://localhost:11434"
    config.privacy_mode = False
    db_path = str(tmp_path / "test.db")
    task_manager = MagicMock()
    return OrchestratorAgent(
        goal=goal,
        cwd=str(tmp_path),
        config=config,
        db_path=db_path,
        task_manager=task_manager,
    )


def test_slug_generation(tmp_path):
    orch = _make_orchestrator(tmp_path, goal="build a react app with docker")
    assert "build-a-react-app" in orch._slug
    assert len(orch._slug) > 8


def test_slug_sanitizes_special_chars(tmp_path):
    orch = _make_orchestrator(tmp_path, goal="deploy to prod! (urgent)")
    assert "!" not in orch._slug
    assert "(" not in orch._slug


def test_task_folder_not_created_on_init(tmp_path):
    orch = _make_orchestrator(tmp_path, goal="list files")
    tasks_dir = tmp_path / "tasks"
    assert not (tasks_dir / orch._slug).exists()


def test_parse_action_run(tmp_path):
    orch = _make_orchestrator(tmp_path)
    result = orch._parse_action('{"action": "run", "command": "ls -la", "explanation": "check files"}')
    assert result["action"] == "run"
    assert result["command"] == "ls -la"
    assert result["explanation"] == "check files"


def test_parse_action_spawn(tmp_path):
    orch = _make_orchestrator(tmp_path)
    result = orch._parse_action('{"action": "spawn", "name": "frontend", "goal": "build react app", "explanation": "long task"}')
    assert result["action"] == "spawn"
    assert result["name"] == "frontend"
    assert result["goal"] == "build react app"


def test_parse_action_done(tmp_path):
    orch = _make_orchestrator(tmp_path)
    result = orch._parse_action('{"action": "done", "explanation": "all done"}')
    assert result["action"] == "done"


def test_parse_action_strips_markdown_fences(tmp_path):
    orch = _make_orchestrator(tmp_path)
    raw = '```json\n{"action": "run", "command": "pwd", "explanation": "check cwd"}\n```'
    result = orch._parse_action(raw)
    assert result["action"] == "run"


def test_parse_action_invalid_returns_done(tmp_path):
    orch = _make_orchestrator(tmp_path)
    result = orch._parse_action("not valid json at all")
    assert result["action"] == "done"


def test_build_messages_pins_goal(tmp_path):
    orch = _make_orchestrator(tmp_path, goal="list all python files")
    messages = orch._build_messages()
    assert any("list all python files" in m["content"] for m in messages)


def test_collect_agent_status_empty(tmp_path):
    orch = _make_orchestrator(tmp_path)
    summaries = orch._collect_agent_statuses()
    assert summaries == []


def test_extract_raw_with_action_field(tmp_path):
    """When LLMResponse has action field set, _extract_raw uses it directly."""
    from sable.llm.base import LLMResponse
    orch = _make_orchestrator(tmp_path)
    response = LLMResponse(
        command="ls -la", explanation="list files", safe=True, plan=None,
        prompt_tokens=10, completion_tokens=5, cost_usd=0.0,
        action="run",
    )
    result = orch._extract_raw(response)
    parsed = json.loads(result)
    assert parsed["action"] == "run"
    assert parsed["command"] == "ls -la"


def test_extract_raw_spawn_action(tmp_path):
    """_extract_raw correctly handles spawn action."""
    from sable.llm.base import LLMResponse
    orch = _make_orchestrator(tmp_path)
    response = LLMResponse(
        command="", explanation="delegating", safe=True, plan=None,
        prompt_tokens=10, completion_tokens=5, cost_usd=0.0,
        action="spawn",
        spawn={"name": "frontend", "goal": "build react app"},
    )
    result = orch._extract_raw(response)
    parsed = json.loads(result)
    assert parsed["action"] == "spawn"
    assert parsed["name"] == "frontend"
    assert parsed["goal"] == "build react app"


def test_extract_raw_fallback_to_done(tmp_path):
    """_extract_raw falls back to done when no meaningful fields set."""
    from sable.llm.base import LLMResponse
    orch = _make_orchestrator(tmp_path)
    response = LLMResponse(
        command="", explanation="", safe=True, plan=None,
        prompt_tokens=10, completion_tokens=5, cost_usd=0.0,
    )
    result = orch._extract_raw(response)
    parsed = json.loads(result)
    assert parsed["action"] == "done"


def test_handle_spawn_creates_task_dir(tmp_path):
    """_handle_spawn creates the shared task dir lazily."""
    orch = _make_orchestrator(tmp_path, goal="build an app")
    orch._task_manager.spawn = MagicMock()

    action = {"action": "spawn", "name": "frontend", "goal": "build react app", "explanation": "long task"}
    orch._handle_spawn(action)

    assert orch._task_dir is not None
    assert orch._task_dir.exists()
    assert "frontend" in orch._spawned


def test_handle_spawn_skips_invalid_name(tmp_path):
    """_handle_spawn skips actions with degenerate name (no alphanumeric chars)."""
    orch = _make_orchestrator(tmp_path, goal="build an app")
    orch._task_manager.spawn = MagicMock()

    action = {"action": "spawn", "name": "---", "goal": "some goal", "explanation": "x"}
    orch._handle_spawn(action)

    orch._task_manager.spawn.assert_not_called()


def test_collect_agent_statuses_consumes_result(tmp_path):
    """_collect_agent_statuses reads result.md, removes it, and removes agent from _spawned."""
    orch = _make_orchestrator(tmp_path, goal="build an app")
    # Manually set up task dir and a fake completed agent
    orch._task_dir = tmp_path / "tasks" / orch._slug
    agent_agentic = orch._task_dir / "frontend" / ".agentic"
    agent_agentic.mkdir(parents=True)
    (agent_agentic / "result.md").write_text("# Done\nBuilt react app")
    orch._spawned = ["frontend"]

    statuses = orch._collect_agent_statuses()

    assert len(statuses) == 1
    assert "COMPLETED" in statuses[0]
    assert not (agent_agentic / "result.md").exists()  # consumed
    assert "frontend" not in orch._spawned


def test_handle_done_writes_result(tmp_path):
    """_handle_done writes result.md when task_dir exists."""
    orch = _make_orchestrator(tmp_path, goal="build an app")
    orch._task_dir = tmp_path / "tasks" / orch._slug
    (orch._task_dir / ".agentic").mkdir(parents=True)

    action = {"action": "done", "explanation": "all done, react app is running"}
    orch._handle_done(action)

    result_path = orch._task_dir / ".agentic" / "result.md"
    assert result_path.exists()
    assert "all done" in result_path.read_text()
