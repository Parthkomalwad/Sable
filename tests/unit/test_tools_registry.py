"""Phase 3.5 Task 1 (J1): the tool registry and the `tool` action.

A tool call is a command in a different shape: it goes through `gate()`, is
tiered by policy with the tool's own tier as a floor, writes an audit row and
a bus event, and a failure comes back to the model as text, never a crash.
"""
from __future__ import annotations

import json

import pytest

from sable.policy import engine
from sable.policy.tiers import Tier
from sable.tools import registry
from sable.tools.base import Tool, ToolContext, ToolError, ToolResult


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(role="worker", cwd=str(tmp_path), agent="t", goal="g", model="m")


@pytest.fixture
def scratch_tool():
    calls = []

    def run(args, ctx):
        calls.append(args)
        if args.get("boom"):
            raise ToolError("it broke")
        return ToolResult(ok=True, output=f"got {args['text']}")

    tool = Tool(name="test.scratch", description="records calls",
                schema={"text": "string", "boom?": "boolean"}, tier=Tier.ALLOW, run=run)
    registry.register(tool)
    yield tool, calls
    registry.unregister(tool.name)


class TestCall:
    def test_runs_an_allowed_tool(self, ctx, scratch_tool):
        _, calls = scratch_tool
        r = registry.call("test.scratch", {"text": "hi"}, ctx)
        assert r.ok and r.output == "got hi" and calls == [{"text": "hi"}]

    def test_unknown_tool_is_an_error_the_model_reads(self, ctx):
        r = registry.call("no.such", {}, ctx)
        assert not r.ok and "unknown tool" in r.output and "echo" in r.output

    @pytest.mark.parametrize("args,why", [
        ({}, "missing"),
        ({"text": 3}, "text"),
        ({"text": "x", "extra": 1}, "unexpected"),
        ("not a dict", "object"),
    ])
    def test_bad_args_never_reach_the_tool(self, ctx, scratch_tool, args, why):
        _, calls = scratch_tool
        r = registry.call("test.scratch", args, ctx)
        assert not r.ok and why in r.output and calls == []

    def test_tool_error_is_reported_not_raised(self, ctx, scratch_tool):
        r = registry.call("test.scratch", {"text": "x", "boom": True}, ctx)
        assert not r.ok and "it broke" in r.output

    def test_tool_tier_is_a_floor_policy_cannot_lower(self, ctx, monkeypatch):
        tool = Tool(name="test.writer", description="w", schema={}, tier=Tier.CONFIRM,
                    run=lambda a, c: ToolResult(ok=True, output="wrote"))
        registry.register(tool)
        try:
            # A worker cannot answer confirm, so the floor alone refuses it.
            r = registry.call("test.writer", {}, ctx)
            assert not r.ok and "blocked" in r.output
        finally:
            registry.unregister("test.writer")

    def test_policy_can_deny_a_tool(self, ctx, scratch_tool, tmp_path, monkeypatch):
        user = tmp_path / "user.toml"
        user.write_text("[[rule]]\nname = 'no-scratch'\npattern = '^tool:test[.]scratch'\ntier = 'deny'\n",
                        encoding="utf-8")
        monkeypatch.setattr(engine.rules, "USER_POLICY_PATH", user)
        engine.rules.load.cache_clear()
        _, calls = scratch_tool
        r = registry.call("test.scratch", {"text": "x"}, ctx)
        assert not r.ok and calls == []

    def test_role_allowlist(self, tmp_path):
        tool = Tool(name="test.orch_only", description="o", schema={}, tier=Tier.ALLOW,
                    run=lambda a, c: ToolResult(ok=True, output="ok"), roles=frozenset({"orchestrator"}))
        registry.register(tool)
        try:
            worker = ToolContext(role="worker", cwd=str(tmp_path), agent="w")
            assert not registry.call("test.orch_only", {}, worker).ok
            assert "test.orch_only" not in [t.name for t in registry.for_role("worker")]
            assert "test.orch_only" in [t.name for t in registry.for_role("orchestrator")]
        finally:
            registry.unregister("test.orch_only")

    def test_call_is_audited_as_tool_text(self, ctx, scratch_tool, monkeypatch):
        seen = []
        real = engine.gate
        monkeypatch.setattr(engine, "gate", lambda cmd, **kw: seen.append((cmd, kw)) or real(cmd, **kw))
        registry.call("test.scratch", {"text": "hi"}, ctx)
        cmd, kw = seen[0]
        assert cmd == 'tool:test.scratch {"text": "hi"}'
        assert kw["role"] == "worker" and kw["floor"] is Tier.ALLOW


def test_echo_is_built_in(ctx):
    r = registry.call("echo", {"text": "ping"}, ctx)
    assert r.ok and r.output == "ping"


def test_describe_lists_tools_for_the_prompt():
    text = registry.describe("worker")
    assert "echo" in text and '"action": "tool"' in text


def test_orchestrator_accepts_the_tool_action():
    from sable.agents.orchestrator import ORCHESTRATOR_ACTIONS
    assert "tool" in ORCHESTRATOR_ACTIONS


def test_orchestrator_rebuilds_a_tool_action_from_raw():
    from types import SimpleNamespace
    from sable.agents.orchestrator import OrchestratorAgent
    raw = json.dumps({"action": "tool", "name": "echo", "args": {"text": "x"}, "explanation": "e"})
    resp = SimpleNamespace(action="tool", command="", explanation="e", done=False, spawn=None, raw=raw)
    assert json.loads(OrchestratorAgent._extract_raw(None, resp))["name"] == "echo"


def test_worker_parses_a_tool_action():
    from types import SimpleNamespace
    from sable.agents.worker import TaskAgent
    raw = json.dumps({"action": "tool", "name": "echo", "args": {"text": "x"}, "explanation": "e"})
    resp = SimpleNamespace(action="tool", command="", explanation="e", done=False, raw=raw)
    parsed = TaskAgent._parse_response(None, resp)
    assert parsed["tool"] == {"name": "echo", "args": {"text": "x"}} and parsed["command"] == ""


def test_floor_raises_decide_but_never_lowers():
    assert engine.decide("tool:x {}", floor=Tier.CONFIRM).tier is Tier.CONFIRM
    assert engine.decide("rm -rf /tmp/x", floor=Tier.ALLOW).tier is Tier.CONFIRM


def test_tools_layer_sits_between_policy_and_agents():
    from tests.unit.test_layering import DEPTH
    assert DEPTH["policy"] < DEPTH["tools"] < DEPTH["agents"]


def test_tools_builtin_lists_tools_and_tiers(capsys):
    from sable.app.builtins.dispatch import handle_builtin
    assert handle_builtin("/tools", None, "s", None) is True
    out = capsys.readouterr().out
    assert "orchestrator:" in out and "worker:" in out and "echo(text: string)" in out and "allow" in out


def test_a_goal_finished_by_a_tool_is_not_reported_as_declined(tmp_path, monkeypatch):
    """Found by the live J1 smoke run: `_commands_run` counted only shell
    commands, so a goal done with a tool ended "the model declined"."""
    from unittest.mock import MagicMock
    from sable.agents.orchestrator import OrchestratorAgent
    cfg = MagicMock(); cfg.tasks_base_dir = str(tmp_path); cfg.model_for.return_value = "m"
    agent = OrchestratorAgent(goal="say hi", cwd=str(tmp_path), config=cfg,
                              db_path=str(tmp_path / "s.db"), task_manager=MagicMock())
    monkeypatch.setattr(agent, "_confirm_tool", lambda *a: True)
    agent._handle_tool({"action": "tool", "name": "echo", "args": {"text": "hi"}, "explanation": "e"})
    assert agent._commands_run == 1
