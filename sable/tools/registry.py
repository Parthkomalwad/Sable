"""The tool registry and the one function agents call: `call()`.

A call is checked in this order, and each step can end it with a result the
model reads rather than an exception: the tool exists, the role may use it,
the arguments fit, the agent's per-tool budget (J12, `ctx.budget`) has
room, policy allows it (`gate()` with the tool's tier as a
floor, which writes the audit row), then it runs. Every call that reaches
`run` publishes one `tool` event on the bus.
"""
from __future__ import annotations

import dataclasses
import json
import time

from sable.core import audit
from sable.policy import engine
from sable.tools.base import Tool, ToolContext, ToolError, ToolResult

_TOOLS: dict[str, Tool] = {}
_loaded = False


def _load_builtins() -> None:
    global _loaded
    if not _loaded:
        _loaded = True
        from sable.tools import builtin  # noqa: F401  registers on import


def register(tool: Tool) -> None:
    _TOOLS[tool.name] = tool


def unregister(name: str) -> None:
    _TOOLS.pop(name, None)


def get(name: str) -> Tool | None:
    _load_builtins()
    return _TOOLS.get(name)


def for_role(role: str) -> list[Tool]:
    _load_builtins()
    return sorted((t for t in _TOOLS.values() if role in t.roles), key=lambda t: t.name)


def describe(role: str) -> str:
    """The prompt section that tells a model which tools it has."""
    lines = [
        "Tools: call one instead of a shell command when it fits, with",
        '{"action": "tool", "name": "<tool>", "args": {...}, "explanation": "<one sentence>"}',
        "Available:",
    ]
    lines += [f"- {t.signature()}: {t.description}" for t in for_role(role)]
    return "\n".join(lines)


def as_command(name: str, args) -> str:
    """The text policy rules match and the audit ledger stores for a call."""
    return f"tool:{name} {json.dumps(args, sort_keys=True)}"


def call(name: str, args, ctx: ToolContext, *, publish=None) -> ToolResult:
    """Run tool `name` for `ctx`. Never raises for anything the model did.

    `publish(kind, payload)` sends the bus event; agents pass their own so the
    event carries their name. Omitted, nothing is published.
    """
    tool = get(name)
    if tool is None:
        known = ", ".join(t.name for t in for_role(ctx.role)) or "none"
        return ToolResult(ok=False, output=f"unknown tool {name!r}; available: {known}")
    if ctx.role not in tool.roles:
        return ToolResult(ok=False, output=f"tool {name!r} is not available to a {ctx.role}")
    problem = tool.validate(args)
    if problem:
        return ToolResult(ok=False, output=f"invalid args for {name}: {problem}")

    if ctx.budget is not None:
        refused = ctx.budget.before_tool(name)
        if refused:
            return ToolResult(ok=False, output=refused)

    command = as_command(name, args)
    # Taint bumps every tier one step, except for a call its tool declares
    # safe to make while tainted (safe research, `web.py`).
    tainted = ctx.tainted and not (tool.taint_exempt and tool.taint_exempt(args, ctx))
    if not engine.gate(command, role=ctx.role, tainted=tainted, agent=ctx.agent,
                       model=ctx.model, goal=ctx.goal, floor=tool.tier):
        return ToolResult(ok=False, output="[blocked: refused by policy or not confirmed by the user]")

    started = time.monotonic()
    try:
        result = tool.run(args, ctx)
    except ToolError as exc:
        result = ToolResult(ok=False, output=f"{name} failed: {exc}")
    duration_ms = int((time.monotonic() - started) * 1000)
    audit.finish(0 if result.ok else 1)
    if ctx.budget is not None:
        keep, notice = ctx.budget.after_tool(name, len(result.output.encode()), result.cost_usd)
        if notice:
            output = result.output
            if keep is not None:
                output = output.encode()[:keep].decode(errors="ignore")
            result = dataclasses.replace(result, output=f"{output}\n{notice}")
    if publish is not None:
        publish("tool", {
            "name": name, "args": args, "ok": result.ok, "duration_ms": duration_ms,
            "result_bytes": len(result.output.encode()), "taints": result.taints,
        })
    return result
