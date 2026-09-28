"""Two cancels in a row end the goal (Phase 4 gate finding).

After `q` cancelled a proposal the orchestrator proposed the same command
again, three times in the gate transcript. The second consecutive cancel
now stops the goal with a clear message instead of asking the model again.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def orch(tmp_path, monkeypatch):
    from sable.agents.orchestrator import OrchestratorAgent
    cfg = MagicMock(); cfg.tasks_base_dir = str(tmp_path); cfg.model_for.return_value = "m"
    agent = OrchestratorAgent(goal="g", cwd=str(tmp_path), config=cfg,
                              db_path=str(tmp_path / "s.db"), task_manager=MagicMock())
    agent._breaker = MagicMock(check=lambda: None)
    agent.calls = 0

    def call(messages):
        agent.calls += 1
        return MagicMock(prompt_tokens=0, completion_tokens=0, cost_usd=0.0)

    monkeypatch.setattr(agent, "_call_llm", call)
    monkeypatch.setattr(agent, "_record_turn", lambda *a: None)
    monkeypatch.setattr(agent, "_extract_raw", lambda r: json.dumps(
        {"action": "run", "command": "rm -rf build", "explanation": "e"}))
    return agent


def test_two_cancels_in_a_row_end_the_goal(orch, monkeypatch, capsys):
    monkeypatch.setattr(orch, "_confirm_command", lambda cmd, expl: None)
    monkeypatch.setattr(orch, "_run_command", lambda c, **k: pytest.fail("must not run"))
    orch.run()
    assert orch.calls == 2
    assert "cancelled twice" in capsys.readouterr().out


def test_a_run_between_cancels_resets_the_count(orch, monkeypatch):
    answers = iter([None, "rm -rf build", None, None])
    monkeypatch.setattr(orch, "_confirm_command", lambda cmd, expl: next(answers))
    monkeypatch.setattr(orch, "_run_command", lambda c, **k: "(no output; exit 0)")
    monkeypatch.setattr(orch, "_show_block", lambda *a: None)
    monkeypatch.setattr(orch, "_climb_ladder", lambda o: None)
    orch._guard = MagicMock(refuse=lambda k: None)
    orch.run()
    assert orch.calls == 4
