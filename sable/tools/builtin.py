"""Built-in tools, registered on import.

Task 1 ships only `echo`, so the whole path (action, validation, gate,
audit, event, result) is testable before any real tool exists. Tasks 2 to 5
add `web.*`, `fs.*` and `docs.*` here or in their own modules.
"""
from __future__ import annotations

from sable.policy.tiers import Tier
from sable.tools.base import Tool, ToolResult
from sable.tools.registry import register

register(Tool(
    name="echo",
    description="returns its text unchanged; for testing the tool path",
    schema={"text": "string"},
    tier=Tier.ALLOW,
    run=lambda args, ctx: ToolResult(ok=True, output=args["text"]),
))

from sable.tools import web  # noqa: E402,F401  registers web.search and web.fetch
