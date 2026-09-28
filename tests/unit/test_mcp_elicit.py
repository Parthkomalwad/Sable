"""Phase 6 Task 2: an MCP server's questions (MRTR input_required) in the shell."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sable.mcp import elicit, servers
from sable.mcp.client import Client
from sable.mcp.transports import StdioTransport
from sable.tools.base import ToolContext

SERVER = str(Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "mock_mcp_server.py")


@pytest.fixture(autouse=True)
def no_audit(monkeypatch):
    import sable.core.audit as audit
    monkeypatch.setattr(audit, "write_action", lambda *a, **k: None)


def _req(props, required=()):
    return {"method": "elicitation/create", "params": {
        "message": "Deploy?\x1b[31m", "requestedSchema": {
            "type": "object", "properties": props, "required": list(required)}}}


def _answers(*lines):
    it = iter(lines)
    return lambda _prompt: next(it)


def test_typed_fields_are_converted():
    props = {"env": {"type": "string", "enum": ["staging", "prod"]},
             "replicas": {"type": "integer"}, "force": {"type": "boolean"}, "note": {"type": "string"}}
    out = elicit.ask({"r1": _req(props, ["env", "replicas", "force"])}, "deploy",
                     prompt=_answers("2", "three", "3", "y", ""))
    assert out == {"r1": {"action": "accept", "content": {"env": "prod", "replicas": 3, "force": True}}}


def test_q_declines_and_unknown_methods_decline():
    out = elicit.ask({"a": _req({"x": {"type": "string"}}, ["x"]), "b": {"method": "sampling/createMessage"}},
                     "s", prompt=_answers("q"))
    assert out == {"a": {"action": "decline"}, "b": {"action": "decline"}}


def test_answers_go_back_to_the_server_once():
    c = Client(StdioTransport([sys.executable, SERVER, "input-required"]))
    try:
        r = c.call_tool("ask", {}, on_input=lambda reqs: elicit.ask(reqs, "mock", prompt=_answers("ada")))
    finally:
        c.close()
    assert r.text == "hi ada state=s1"


class TestWhoAnswers:
    def test_orchestrator_at_a_terminal_is_asked(self, monkeypatch):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
        assert servers._on_input("fs", ToolContext(role="orchestrator", cwd=".", agent="o")) is not None

    @pytest.mark.parametrize("role,tty", [("worker", True), ("orchestrator", False)])
    def test_nobody_to_ask_means_no_prompt(self, monkeypatch, role, tty):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: tty, raising=False)
        assert servers._on_input("fs", ToolContext(role=role, cwd=".", agent="w")) is None
