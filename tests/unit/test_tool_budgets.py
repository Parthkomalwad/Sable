"""Phase 3.5 Task 6 (J12): per-tool budgets.

A budget is counted per goal (per task for a worker) by the agent's Breaker,
handed to `registry.call()` through `ToolContext.budget`. A call that would go
past `max_calls_per_goal` never runs; the job's breaker trips with the tool
named, and the trip is a `breaker_trips` row like any other.
"""
from __future__ import annotations

import sqlite3

import pytest

from sable.core.config.schema import ShellConfig
from sable.policy import breaker
from sable.policy.tiers import Tier
from sable.tools import registry
from sable.tools.base import Tool, ToolContext, ToolResult


@pytest.fixture
def web(tmp_path):
    """Fake `web.search` and `web.fetch`: Task 2's tools may not exist yet."""
    calls = []

    def make(name, output):
        def run(args, ctx):
            calls.append(name)
            return ToolResult(ok=True, output=output, cost_usd=0.01)
        return Tool(name=name, description="fake", schema={"q?": "string"}, tier=Tier.ALLOW, run=run)

    # Stand in for the real tools (no network), then put them back.
    real = {n: registry.get(n) for n in ("web.search", "web.fetch")}
    for tool in (make("web.search", "results"), make("web.fetch", "x" * 100)):
        registry.register(tool)
    yield calls
    for name, tool in real.items():
        if tool is None:
            registry.unregister(name)
        else:
            registry.register(tool)


def _breaker(tmp_path, tools):
    return breaker.Breaker("goal-1", breaker.Limits(tools=tools), str(tmp_path / "s.db"))


def _ctx(tmp_path, b):
    return ToolContext(role="worker", cwd=str(tmp_path), agent="t", goal="g", budget=b)


def _trips(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "s.db"))
    try:
        return breaker.tripped(conn)
    finally:
        conn.close()


class TestCalls:
    def test_gate_line_third_web_call_trips(self, tmp_path, web):
        """Roadmap gate: tools.web.max_calls_per_goal = 2, the third call trips."""
        b = _breaker(tmp_path, {"web": {"max_calls_per_goal": 2}})
        ctx = _ctx(tmp_path, b)
        assert registry.call("web.search", {}, ctx).ok
        assert registry.call("web.fetch", {}, ctx).ok
        assert b.check() is None

        third = registry.call("web.search", {}, ctx)

        assert not third.ok and "breaker" in third.output and "web.search" in third.output
        assert web == ["web.search", "web.fetch"]           # the third never ran
        trips = _trips(tmp_path)
        assert len(trips) == 1 and trips[0]["job"] == "goal-1"
        assert "web.search" in trips[0]["reason"] and "max_calls_per_goal" in trips[0]["reason"]
        assert b.check()                                    # the job stops next turn
        assert len(_trips(tmp_path)) == 1                   # and is not recorded twice

    def test_prefix_does_not_match_a_lookalike(self, tmp_path, web):
        registry.register(Tool(name="webby", description="", schema={}, tier=Tier.ALLOW,
                               run=lambda a, c: ToolResult(ok=True, output="")))
        try:
            b = _breaker(tmp_path, {"web": {"max_calls_per_goal": 1}})
            ctx = _ctx(tmp_path, b)
            for _ in range(3):
                assert registry.call("webby", {}, ctx).ok
        finally:
            registry.unregister("webby")

    def test_exact_name_budget_leaves_siblings_alone(self, tmp_path, web):
        b = _breaker(tmp_path, {"web.fetch": {"max_calls_per_goal": 1}})
        ctx = _ctx(tmp_path, b)
        assert registry.call("web.fetch", {}, ctx).ok
        assert registry.call("web.search", {}, ctx).ok
        assert not registry.call("web.fetch", {}, ctx).ok

    def test_unlimited_by_default(self, tmp_path, web):
        b = breaker.Breaker("g", breaker.Limits.from_config(ShellConfig.defaults()), str(tmp_path / "s.db"))
        ctx = _ctx(tmp_path, b)
        for _ in range(20):
            assert registry.call("web.search", {}, ctx).ok
        assert b.check() is None and _trips(tmp_path) == []

    def test_a_new_breaker_starts_at_zero(self, tmp_path, web):
        tools = {"web": {"max_calls_per_goal": 1}}
        first = _breaker(tmp_path, tools)
        assert registry.call("web.search", {}, _ctx(tmp_path, first)).ok
        assert not registry.call("web.search", {}, _ctx(tmp_path, first)).ok
        assert registry.call("web.search", {}, _ctx(tmp_path, _breaker(tmp_path, tools))).ok


class TestBytesAndCost:
    def test_crossing_call_is_truncated_and_trips(self, tmp_path, web):
        b = _breaker(tmp_path, {"web": {"max_bytes": 150}})
        ctx = _ctx(tmp_path, b)
        assert registry.call("web.fetch", {}, ctx).output == "x" * 100
        r = registry.call("web.fetch", {}, ctx)
        assert r.output.startswith("x" * 50 + "\n[breaker:") and "max_bytes" in r.output
        assert "web.fetch" in _trips(tmp_path)[0]["reason"]
        assert not registry.call("web.search", {}, ctx).ok  # budget spent: refused

    def test_cost_trips_after_the_call(self, tmp_path, web):
        b = _breaker(tmp_path, {"web": {"max_cost": 0.02}})
        ctx = _ctx(tmp_path, b)
        assert registry.call("web.search", {}, ctx).output == "results"
        r = registry.call("web.search", {}, ctx)
        assert r.output.startswith("results\n[breaker:") and "max_cost" in r.output


class TestConfig:
    def test_old_config_loads_and_round_trips(self):
        assert ShellConfig.from_dict({"model": "m"}).tool_budgets == {}
        cfg = ShellConfig.from_dict({"model": "m", "tool_budgets": {
            "web": {"max_calls_per_goal": 2, "max_bytes": None, "max_cost": 0.5}}})
        assert cfg.tool_budgets == {"web": {"max_calls_per_goal": 2, "max_cost": 0.5}}
        assert ShellConfig.from_dict(cfg.to_dict()).tool_budgets == cfg.tool_budgets
        assert breaker.Limits.from_config(cfg).tools == cfg.tool_budgets

    @pytest.mark.parametrize("budgets", [
        "web",
        {"web": 2},
        {"": {"max_calls_per_goal": 1}},
        {"web": {"bogus": 1}},
        {"web": {"max_calls_per_goal": 0}},
        {"web": {"max_calls_per_goal": 1.5}},
        {"web": {"max_bytes": True}},
        {"web": {"max_cost": -1}},
    ])
    def test_bad_budgets_raise(self, budgets):
        with pytest.raises(ValueError):
            ShellConfig.from_dict({"model": "m", "tool_budgets": budgets})
