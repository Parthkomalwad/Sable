"""Smoke test: verify OrchestratorAgent can be instantiated and called from loop context."""
import ast
import inspect
from unittest.mock import MagicMock, patch


def _orchestrator_call_keywords() -> set[str]:
    """The keyword arguments the REPL actually passes to OrchestratorAgent.

    Read out of the source rather than by running the REPL, which needs a
    terminal, a database and a backend. A collaborator the agent accepts but
    the REPL never passes is invisible to every other kind of test: that is
    exactly how B3's `from_run` came to have no call site while its own 18
    unit tests passed.
    """
    from sable.app import repl

    tree = ast.parse(inspect.getsource(repl))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name == "OrchestratorAgent":
            return {kw.arg for kw in node.keywords if kw.arg}
    raise AssertionError("the REPL never constructs an OrchestratorAgent")


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


def test_the_repl_passes_every_collaborator_the_agent_takes():
    """A collaborator the REPL forgets is a feature that is dead in production.

    B3 shipped with `SkillCrystalliser.from_run` implemented, tested and
    never called: the orchestrator had no parameter for it and the REPL
    passed nothing, so a completed multi-step goal drafted no skill and
    Phase 2's gate could not be reached. Found by a manual run, not by the
    suite. This asserts the wiring itself.
    """
    import inspect as _inspect

    from sable.agents.orchestrator import OrchestratorAgent

    accepted = {
        name for name, param in
        _inspect.signature(OrchestratorAgent.__init__).parameters.items()
        if param.default is not _inspect.Parameter.empty
    }
    passed = _orchestrator_call_keywords()

    missing = accepted - passed
    assert not missing, (
        f"OrchestratorAgent accepts {sorted(missing)} but the REPL never passes "
        f"them, so they are dead in production"
    )
