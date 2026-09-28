"""Answer an MCP server's questions in the shell (MRTR, Phase 6 Task 2).

A tool call can come back `input_required` with `inputRequests`: a map of
request ids to `elicitation/create` requests, each a message and a flat
JSON Schema. The user answers each field at a prompt, or `q` to decline;
the answers go back once as `inputResponses`. Only the orchestrator in an
interactive shell asks: a worker or the daemon has nobody at the keyboard.
"""
from __future__ import annotations

import re
from typing import Callable

from rich.console import Console

_console = Console(markup=False, highlight=False)


def _clean(text, cap: int = 300) -> str:
    """A server's text is untrusted: no control characters, capped."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or "")).strip()[:cap]


def _convert(raw: str, spec: dict):
    """The typed value for one answer, or ValueError with what is wrong."""
    typ, enum = spec.get("type", "string"), spec.get("enum")
    if enum:
        if raw.isdigit() and 1 <= int(raw) <= len(enum):
            return enum[int(raw) - 1]
        if raw in [str(e) for e in enum]:
            return next(e for e in enum if str(e) == raw)
        raise ValueError(f"pick 1 to {len(enum)}")
    if typ == "boolean":
        if raw.lower() in ("y", "yes", "true"):
            return True
        if raw.lower() in ("n", "no", "false"):
            return False
        raise ValueError("answer y or n")
    if typ == "integer":
        return int(raw)
    if typ == "number":
        return float(raw)
    return raw


def _field(name: str, spec: dict, required: bool, prompt: Callable[[str], str]):
    """One field's value, None when skipped; raises EOFError on `q`."""
    label = _clean(spec.get("title") or name, 60)
    if spec.get("description"):
        _console.print(f"    {_clean(spec['description'])}")
    for i, option in enumerate(spec.get("enum") or [], 1):
        _console.print(f"    {i}. {_clean(option, 80)}")
    for _ in range(3):
        raw = prompt(f"  {label}{'' if required else ' (optional)'} › ").strip()
        if raw == "q":
            raise EOFError
        if not raw and not required:
            return None
        try:
            return _convert(raw, spec)
        except ValueError as exc:
            _console.print(f"    {exc}")
    raise EOFError


def _one(server: str, request: dict, prompt) -> dict:
    if request.get("method") != "elicitation/create":
        return {"action": "decline"}
    params = request.get("params") or {}
    schema = params.get("requestedSchema") or {}
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    _console.print(f"\n  [mcp {server}] asks: {_clean(params.get('message'))}   (q declines)")
    try:
        content = {}
        for name, spec in props.items():
            value = _field(name, spec if isinstance(spec, dict) else {}, name in required, prompt)
            if value is not None:
                content[name] = value
        if not props:  # no schema: a free-text answer
            content = {"answer": _field("answer", {}, True, prompt)}
    except (EOFError, KeyboardInterrupt):
        _console.print("  declined")
        return {"action": "decline"}
    return {"action": "accept", "content": content}


def ask(requests: dict, server: str, prompt: Callable[[str], str] = input) -> dict:
    """`inputResponses` for `inputRequests`, asked at the keyboard, audited."""
    from sable.core.audit import write_action
    from sable.policy.engine import redact_text

    responses = {key: _one(server, req if isinstance(req, dict) else {}, prompt)
                 for key, req in (requests or {}).items()}
    summary = ", ".join(f"{k}={r['action']}" for k, r in responses.items())
    answers = redact_text(str([r.get("content") for r in responses.values()]))
    write_action("mcp.elicit", f"{server}: {summary} {answers}"[:500])
    return responses
