"""K2: `? explain  ! fix` after a failed command."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sable.app import explain, repl


@pytest.fixture(autouse=True)
def _reset():
    repl._last_failure = None
    yield
    repl._last_failure = None


def _run(exit_code, output="", capsys=None):
    with patch.object(repl, "_post_command"):
        repl._after_bash("make build", exit_code, output, "/tmp")


def test_hint_only_after_non_zero(capsys):
    _run(0, "ok")
    assert "? explain" not in capsys.readouterr().out
    assert repl._last_failure is None
    _run(2, "boom")
    out = capsys.readouterr().out
    assert "exit 2" in out and "? explain   ! fix" in out
    assert repl._last_failure == explain.Failure("make build", 2, "boom")


def test_other_input_is_not_taken():
    failure = explain.Failure("x", 1, "")
    assert not explain.handle("ls", failure, None, lambda g: None)
    assert not explain.handle("?", None, None, lambda g: None)


def test_question_sends_only_command_exit_and_40_redacted_lines():
    secret = "AKIAABCDEFGHIJKLMNOP"
    output = "\n".join(f"line {i}" for i in range(100)) + f"\naws key {secret}"
    failure = explain.Failure(f"deploy --key {secret}", 3, output)
    sent = {}

    def fake_call(backend, messages, system, **kw):
        sent["messages"] = messages
        return SimpleNamespace(explanation="the key is wrong", command="")

    with patch("sable.agents.runtime.call_llm", fake_call), \
         patch("sable.llm.registry.build_backend", lambda *a, **k: object()):
        assert explain.handle("?", failure, SimpleNamespace(), lambda g: None)

    assert len(sent["messages"]) == 1
    text = sent["messages"][0]["content"]
    assert secret not in text
    assert "Exit code: 3" in text
    assert "line 60" not in text and "line 61" in text
    tail = text.split("output:\n", 1)[1].splitlines()
    assert len(tail) == 40


def test_bang_goes_to_the_orchestrator_goal_path():
    failure = explain.Failure("pip install foo", 1, "error: no such package")
    goals = []
    assert explain.handle("!", failure, None, goals.append)
    assert "pip install foo" in goals[0] and "Exit code: 1" in goals[0]


def test_run_goal_uses_the_orchestrator(tmp_path):
    """The fix is a normal orchestrator goal, so its preview and gate() apply."""
    seen = {}

    class FakeAgent:
        def __init__(self, goal, **kw):
            seen["goal"] = goal

        def run(self):
            seen["ran"] = True

    cfg = SimpleNamespace(tasks_base_dir=str(tmp_path))
    # Stand-in modules: the real ones pull in ptyprocess, absent on Windows.
    fakes = {
        "sable.agents.orchestrator": SimpleNamespace(OrchestratorAgent=FakeAgent),
        "sable.agents.manager": SimpleNamespace(TaskManager=lambda **k: None),
        "sable.skills.index": SimpleNamespace(SkillIndex=lambda: None),
        "sable.skills.loader": SimpleNamespace(TaskSkillLoader=lambda *a: None),
    }
    with patch.dict(sys.modules, fakes), \
         patch.object(repl, "_crystalliser_or_none", lambda c: None), \
         patch.object(repl, "_save_turns_if_needed"):
        repl._run_goal("fix it", str(tmp_path), cfg, None, "s1", [])
    assert seen == {"goal": "fix it", "ran": True}
