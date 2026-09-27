"""What a tool is: a name, an argument schema, a default tier and a function.

The schema is deliberately small, `{"arg": "type"}` with a trailing `?` for
optional arguments and types `string`, `integer`, `number`, `boolean`,
`object`, `array`. It is what a prompt can show a model in one line and what
validation can check without a JSON Schema dependency.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from sable.policy.tiers import Tier

ROLES = frozenset({"orchestrator", "worker"})

_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
}


class ToolError(Exception):
    """A tool failed in a way the model should read and recover from."""


@dataclass(frozen=True)
class ToolContext:
    role: str                  # "orchestrator" | "worker"
    cwd: str                   # the orchestrator's cwd, or the worker's workspace
    agent: str
    goal: str | None = None
    model: str | None = None
    tainted: bool = False


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str
    #: True when the output came from outside the workspace (a web page, a
    #: file elsewhere): the caller wraps it as untrusted and taints the agent.
    taints: bool = False


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict[str, str]
    tier: Tier                 # the floor: policy files can raise it, never lower it
    run: Callable[[dict, ToolContext], ToolResult]
    roles: frozenset[str] = field(default=ROLES)
    #: Optional human preview for the confirm block (fs.write shows a diff).
    #: May raise ToolError; the caller then shows the error instead.
    preview: Callable[[dict, ToolContext], str] | None = None

    def validate(self, args) -> str | None:
        """None if `args` fits the schema, else what is wrong, for the model."""
        if not isinstance(args, dict):
            return "args must be a JSON object"
        spec = {k.rstrip("?"): (t, not k.endswith("?")) for k, t in self.schema.items()}
        for name in args:
            if name not in spec:
                return f"unexpected argument {name!r}; expected {sorted(spec)}"
        for name, (typ, required) in spec.items():
            if name not in args:
                if required:
                    return f"missing argument {name!r} ({typ})"
                continue
            value = args[name]
            # bool is an int in Python; an integer argument must not accept True.
            if not isinstance(value, _TYPES[typ]) or (typ in ("integer", "number") and isinstance(value, bool)):
                return f"argument {name!r} must be {typ}"
        return None

    def signature(self) -> str:
        args = ", ".join(f"{k}: {t}" for k, t in self.schema.items())
        return f"{self.name}({args})"
