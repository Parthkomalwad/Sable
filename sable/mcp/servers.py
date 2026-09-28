"""Configured MCP servers, and their tools as ordinary Sable tools (D1, D4).

config.json holds, under `mcp`:

    {"servers": {"fs": {"command": ["npx", "-y", "..."], "env": {"K": "$SECRET:k"}},
                 "gh": {"url": "https://...", "headers": {"Authorization": "$SECRET:gh"}}},
     "trusted": ["fs.list_directory"]}

`$SECRET:name` in `env` and `headers` is resolved from the keyring at connect
time and never written back. Each MCP tool becomes `mcp.<server>.<tool>`
(plan section 0.1): tier `confirm` unless trusted, every result tainted.
Servers are connected lazily, on the first use of the tool registry, so the
shell's cold start never imports this module.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import stat
from typing import Callable
from urllib.parse import urlsplit

from sable.mcp.client import Client
from sable.mcp.transports import HttpTransport, McpError, StdioTransport
from sable.policy.tiers import Tier
from sable.tools import registry
from sable.tools.base import Tool, ToolContext, ToolError, ToolResult

SERVER_NAME = re.compile(r"[A-Za-z0-9_-]+\Z")
_DESC_CAP = 300
_JSON_TYPES = {"string", "integer", "number", "boolean", "object", "array"}

#: Task 2 plugs the shell's elicitation prompt in here; None fails an
#: input_required call with "needs input; run it from the shell".
ON_INPUT: Callable[[dict], dict] | None = None

_clients: dict[str, Client] = {}
_failed: dict[str, str] = {}
_loaded = False


# --- config --------------------------------------------------------------

def _path(path=None):
    from sable.core.config.wizard import CONFIG_PATH
    return path or CONFIG_PATH


def load_config(path=None) -> dict:
    """The `mcp` section with both keys present; empty when absent."""
    p = _path(path)
    try:
        data = json.loads(p.read_text()) if p.exists() else {}
    except (OSError, ValueError):
        data = {}
    mcp = data.get("mcp") if isinstance(data.get("mcp"), dict) else {}
    return {"servers": dict(mcp.get("servers") or {}), "trusted": list(mcp.get("trusted") or [])}


def save_config(mcp: dict, path=None) -> None:
    """Rewrite config.json's `mcp` key, keeping every other key, mode 0600."""
    p = _path(path)
    data = json.loads(p.read_text()) if p.exists() else {}
    data["mcp"] = mcp
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)


def check_url(url: str) -> str | None:
    """None if `url` may be used, else why not: https, or http to localhost only."""
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return None
    if parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1"):
        return None
    return f"{url} must be https (plain http only for localhost)"


def _resolve(values: dict | None) -> dict:
    """`$SECRET:name` values resolved from the keyring. Raises McpError."""
    from sable.core.config import keyring
    from sable.policy.secrets import PLACEHOLDER, SERVICE_PREFIX

    out = {}
    for k, v in (values or {}).items():
        def sub(m):
            try:
                value = keyring.lookup(SERVICE_PREFIX + m.group(1))
            except keyring.KeyringUnavailable as exc:
                raise McpError(f"keyring unavailable for $SECRET:{m.group(1)} ({exc})") from exc
            if value is None:
                raise McpError(f"no secret named {m.group(1)!r} (add it with /secret add {m.group(1)})")
            return value
        out[str(k)] = PLACEHOLDER.sub(sub, str(v))
    return out


# --- connecting and registering ------------------------------------------

def connect(name: str, spec: dict) -> Client:
    """A discovered Client for server `name`. Raises McpError."""
    if spec.get("url"):
        problem = check_url(spec["url"])
        if problem:
            raise McpError(problem)
        transport = HttpTransport(spec["url"], headers=_resolve(spec.get("headers")))
    elif spec.get("command"):
        transport = StdioTransport(list(spec["command"]), env=_resolve(spec.get("env")))
    else:
        raise McpError(f"server {name} has neither a command nor a url")
    client = Client(transport)
    try:
        client.discover()
    except McpError:
        client.close()
        raise
    return client


def _clean(text: str, cap: int) -> str:
    """Untrusted text from a server: control characters out, length capped."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or "")).strip()
    return text[:cap]


def tool_key(server: str, tool_name: str) -> str:
    """`server.tool`, the form `/mcp trust` takes; names sanitized."""
    return f"{server}.{re.sub(r'[^A-Za-z0-9_.-]', '_', tool_name)}"


def _schema(input_schema) -> dict[str, str]:
    """JSON Schema properties/required to the registry's `{"arg": "type"}`."""
    props = (input_schema or {}).get("properties") or {}
    required = set((input_schema or {}).get("required") or [])
    out = {}
    for arg, spec in props.items():
        typ = spec.get("type") if isinstance(spec, dict) else None
        if isinstance(typ, list):  # ["string", "null"] and friends
            typ = next((t for t in typ if t != "null"), None) if len(typ) <= 2 else None
        out[arg if arg in required else arg + "?"] = typ if typ in _JSON_TYPES else "any"
    return out


def _runner(client: Client, mcp_name: str):
    def run(args: dict, ctx: ToolContext) -> ToolResult:
        try:
            r = client.call_tool(mcp_name, args, on_input=ON_INPUT)
        except (McpError, OSError) as exc:
            raise ToolError(str(exc)) from exc
        return ToolResult(ok=not r.is_error, output=r.text, taints=True)
    return run


def make_tools(server: str, client: Client, trusted) -> list[Tool]:
    tools = []
    for t in client.list_tools():
        key = tool_key(server, str(t.get("name", "")))
        tools.append(Tool(
            name="mcp." + key,
            description=_clean(t.get("description"), _DESC_CAP) or f"MCP tool from {server}",
            schema=_schema(t.get("inputSchema")),
            tier=Tier.ALLOW if key in trusted else Tier.CONFIRM,
            run=_runner(client, str(t.get("name", ""))),
        ))
    return tools


def register_server(name: str, client: Client, trusted) -> list[Tool]:
    """(Re)register every tool of a connected server. Raises McpError."""
    unregister_server(name, close=False)
    tools = make_tools(name, client, trusted)
    for tool in tools:
        registry.register(tool)
    _clients[name] = client
    _failed.pop(name, None)
    return tools


def unregister_server(name: str, close: bool = True) -> None:
    for tool in list(registry._TOOLS):
        if tool.startswith(f"mcp.{name}."):
            registry.unregister(tool)
    client = _clients.pop(name, None)
    if client is not None and close:
        client.close()


def set_tier(key: str, tier: Tier) -> bool:
    """Change a registered tool's tier in place; False if it is not loaded."""
    import dataclasses
    tool = registry._TOOLS.get("mcp." + key)
    if tool is not None:
        registry.register(dataclasses.replace(tool, tier=tier))
    return tool is not None


def load_all(report: Callable[[str], None] | None = None, path=None) -> None:
    """Connect and register every configured server, once per process.

    A server that fails is reported in one line and skipped.
    """
    global _loaded
    if _loaded:
        return
    _loaded = True
    cfg = load_config(path)
    for name, spec in cfg["servers"].items():
        if name in _clients:
            continue
        try:
            register_server(name, connect(name, spec), cfg["trusted"])
        except (McpError, OSError, ValueError, TypeError) as exc:
            _failed[name] = str(exc)
            if report is None:
                from sable.ui.console import out as report
            report(f"mcp: server {name} skipped: {exc}")


def status(name: str) -> str:
    if name in _clients:
        return "connected"
    if name in _failed:
        return f"failed: {_failed[name]}"
    return "not connected"


def close_all() -> None:
    for name in list(_clients):
        _clients.pop(name).close()


atexit.register(close_all)
