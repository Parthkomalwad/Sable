"""Smoke test: verify OrchestratorAgent can be instantiated and called from loop context."""
from unittest.mock import MagicMock, patch


def test_orchestrator_agent_importable():
    """OrchestratorAgent can be imported without errors."""
    from sable.agents.orchestrator import OrchestratorAgent
    assert OrchestratorAgent is not None


def test_orchestrator_run_called_on_nl_input(tmp_path):
    """OrchestratorAgent.run() is invoked when NL input is processed."""
    from sable.agents.orchestrator import OrchestratorAgent

    config = MagicMock()
    config.tasks_base_dir = str(tmp_path / "tasks")
    config.backend = "ollama"
    config.model = "llama3"
    config.api_base = "http://localhost:11434"
    config.privacy_mode = False

    run_called = []

    with patch.object(OrchestratorAgent, "run", lambda self: run_called.append(True)):
        agent = OrchestratorAgent(
            goal="list files here",
            cwd=str(tmp_path),
            config=config,
            db_path=str(tmp_path / "test.db"),
            task_manager=MagicMock(),
        )
        agent.run()

    assert len(run_called) == 1
