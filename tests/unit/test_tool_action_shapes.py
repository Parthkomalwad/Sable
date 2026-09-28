"""Tool calls in the shapes models actually send (Phase 3.5 gate finding).

gpt-4o-mini sent `{"action": "fs.read", "path": ...}`: the tool's name as the
action, arguments flat. Sable turned it into a silent `done` and reported
"the model declined this goal". Both shapes now reach the same call, and an
action nobody knows is reported, never passed off as done.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from sable.agents import runtime
from sable.tools import registry


def _resp(raw: dict):
    return SimpleNamespace(action=raw.get("action", ""), command="", explanation=raw.get("explanation", ""),
                           done=False, spawn=None, raw=json.dumps(raw))


def test_contract_shape_is_unchanged():
    call = registry.normalize_action({"action": "tool", "name": "echo", "args": {"text": "x"}, "explanation": "e"})
    assert call == {"action": "tool", "name": "echo", "args": {"text": "x"}, "explanation": "e"}


def test_tool_name_as_action_with_flat_args():
    call = registry.normalize_action({"action": "echo", "text": "hi", "explanation": "e", "verify": {"exit": 0}})
    assert call["name"] == "echo" and call["args"] == {"text": "hi"} and call["verify"] == {"exit": 0}


def test_tool_name_as_action_with_nested_args():
    call = registry.normalize_action({"action": "echo", "args": {"text": "hi"}})
    assert call["args"] == {"text": "hi"}


def test_unknown_action_is_not_a_tool_call():
    assert registry.normalize_action({"action": "dance"}) is None


def test_orchestrator_turns_the_flat_shape_into_a_tool_call():
    from sable.agents.orchestrator import OrchestratorAgent
    raw = OrchestratorAgent._extract_raw(None, _resp({"action": "echo", "text": "hi", "explanation": "e"}))
    assert json.loads(raw) == {"action": "tool", "name": "echo", "args": {"text": "hi"}, "explanation": "e"}


def test_orchestrator_reports_an_unknown_action_instead_of_done():
    from sable.agents.orchestrator import OrchestratorAgent
    raw = json.loads(OrchestratorAgent._extract_raw(None, _resp({"action": "dance", "explanation": "e"})))
    assert raw["action"] == "dance"   # _parse_action then rejects it by name


def test_worker_turns_the_flat_shape_into_a_tool_call():
    from sable.agents.worker import TaskAgent
    parsed = TaskAgent._parse_typed(_resp({"action": "echo", "text": "hi", "explanation": "e"}))
    assert parsed["tool"] == {"name": "echo", "args": {"text": "hi"}} and parsed["command"] == ""


def test_prompt_says_when_to_use_tools():
    from sable.agents.orchestrator import _system_prompt
    text = _system_prompt()
    assert "web.search" in text and "fs.patch" in text and "sed -i" in text


@pytest.mark.parametrize("given,want", [
    ("exit: 0", {"exit": 0}),
    ("exit == 0", {"exit": 0}),
    ("EXIT=2", {"exit": 2}),
    ("stdout_contains: healthy", {"stdout_contains": "healthy"}),
    ("file_exists: 'out.txt'", {"file_exists": "out.txt"}),
    ("docker compose config -q", "docker compose config -q"),   # a real command stays one
    ({"exit": 0}, {"exit": 0}),
])
def test_string_spellings_of_structured_verify(given, want):
    """Found by the Phase 3.5 gate: `"verify": "exit: 0"` ran as a shell
    command, failed every time, and a successful fs.read was retried away."""
    assert runtime._structured_verify(given) == want


def test_exit_colon_zero_passes_on_a_successful_action():
    ran = []
    assert runtime.run_verify("exit: 0", output="services:\n  web: {}\n", cwd=".",
                              run=lambda c: ran.append(c) or "") is None
    assert ran == []   # never sent to a shell


@pytest.mark.parametrize("command,tool", [
    ("fs.tree .", "fs.tree"),
    ("web.search nginx cve", "web.search"),
    ("docs.help curl", "docs.help"),
    ("ls -la", None),
    ("python3 -m http.server", None),       # a dotted word that is not a tool
    ("echo hi", None),                      # `echo` is a tool name but also a real program
])
def test_a_tool_used_as_a_shell_command_is_caught(command, tool):
    """Found by the Phase 2 turns run: `{"action": "run", "command": "fs.tree ."}`
    went to bash and came back `command not found`."""
    assert registry.tool_as_command(command) == tool


def test_the_reply_says_how_to_call_it():
    text = registry.tool_as_command_reply("fs.tree")
    assert '"action": "tool"' in text and "fs.tree(" in text and "nothing was run" in text
