"""`/mcp add|list|remove|trust|untrust` (Phase 6, D1 D4).

Servers live in config.json under `mcp` (see sable/mcp/servers.py).
`/mcp search` is routed to `mcp_search.py` before this handler.
"""
from __future__ import annotations

import shlex
import shutil

from sable.ui.console import out as _out

_USAGE = ("usage: /mcp add <name> <command...> | /mcp add <name> <url> | /mcp list | "
          "/mcp remove <name> | /mcp trust <server>.<tool> | /mcp untrust <server>.<tool>")
_NODE = ("npx", "node", "npm")


def _add(name: str, rest: list[str], path=None) -> None:
    from sable.mcp import servers

    if not servers.SERVER_NAME.match(name) or not rest:
        _out(_USAGE)
        return
    if len(rest) == 1 and rest[0].startswith(("http://", "https://")):
        problem = servers.check_url(rest[0])
        if problem:
            _out(problem)
            return
        spec = {"url": rest[0]}
    else:
        if rest[0] in _NODE and shutil.which(rest[0]) is None:
            _out(f"{rest[0]} is not on PATH: install Node.js (https://nodejs.org, or "
                 f"`apt install nodejs npm`) to use this server; http servers work without it")
            return
        spec = {"command": rest}
    cfg = servers.load_config(path)
    try:
        client = servers.connect(name, spec)
        tools = servers.register_server(name, client, cfg["trusted"])
    except (servers.McpError, OSError) as exc:
        _out(f"could not connect to {name}: {exc}")
        return
    cfg["servers"][name] = spec
    try:
        servers.save_config(cfg, path)
    except (OSError, ValueError) as exc:
        _out(f"could not write config.json: {exc}")
        return
    _out(f"added {name}: {len(tools)} tool{'s' if len(tools) != 1 else ''}")
    for t in tools:
        _out(f"  {t.name:<40} {t.tier.value:<8} {t.description}")


def _list(path=None) -> None:
    from sable.mcp import servers
    from sable.tools import registry

    cfg = servers.load_config(path)
    if not cfg["servers"]:
        _out("no MCP servers; add one with /mcp add <name> <command...>|<url>")
        return
    servers.load_all(report=_out, path=path)
    for name, spec in cfg["servers"].items():
        where = spec.get("url") or " ".join(spec.get("command") or [])
        _out(f"{name}  {servers.status(name)}  {where}")
        for t in registry.for_role("orchestrator"):
            if t.name.startswith(f"mcp.{name}."):
                _out(f"  {t.name:<40} {t.tier.value:<8} {t.description}")


def _remove(name: str, path=None) -> None:
    from sable.mcp import servers

    cfg = servers.load_config(path)
    if cfg["servers"].pop(name, None) is None:
        _out(f"no MCP server {name!r}")
        return
    cfg["trusted"] = [k for k in cfg["trusted"] if not k.startswith(name + ".")]
    servers.save_config(cfg, path)
    servers.unregister_server(name)
    _out(f"removed {name}")


def _trust(key: str, trust: bool, path=None) -> None:
    from sable.mcp import servers
    from sable.policy.tiers import Tier

    cfg = servers.load_config(path)
    if "." not in key or key.split(".", 1)[0] not in cfg["servers"]:
        _out(f"usage: /mcp {'trust' if trust else 'untrust'} <server>.<tool> (a configured server)")
        return
    trusted = [k for k in cfg["trusted"] if k != key] + ([key] if trust else [])
    cfg["trusted"] = trusted
    servers.save_config(cfg, path)
    servers.set_tier(key, Tier.ALLOW if trust else Tier.CONFIRM)
    _out(f"mcp.{key}: {'allow (policy rules can still raise it)' if trust else 'confirm'}")


def handle_mcp(argument: str, path=None) -> bool:
    try:
        parts = shlex.split(argument)
    except ValueError as exc:
        _out(f"/mcp: {exc}")
        return True
    sub, args = (parts[0], parts[1:]) if parts else ("list", [])
    try:
        if sub == "add" and args:
            _add(args[0], args[1:], path)
        elif sub == "list" and not args:
            _list(path)
        elif sub == "remove" and len(args) == 1:
            _remove(args[0], path)
        elif sub in ("trust", "untrust") and len(args) == 1:
            _trust(args[0], sub == "trust", path)
        else:
            _out(_USAGE)
    except (OSError, ValueError) as exc:
        _out(f"/mcp: {exc}")
    return True
