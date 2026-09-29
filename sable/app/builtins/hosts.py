"""`/host add|list|rm|test` and `@NAME <command>` (Phase 9 Task 5, H5).

A host is reached by SSH to its own `sable --mcp-serve`, so the remote
host's policy, audit and inbox decide what runs there. We only preview,
send `run_command`, and record locally what was sent and what came back.

Security: the target and the sable path go into an ssh argv, and ssh joins
the remote part into a string for the remote shell, so both are held to a
strict character set. The command itself travels as JSON over the pipe,
never through a shell on this side.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import stat

from sable.ui.console import out as _out

NAME = re.compile(r"[a-z0-9-]{1,32}")
_TARGET = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,63}@[A-Za-z0-9][A-Za-z0-9.-]{0,252}")
_SABLE = re.compile(r"[A-Za-z0-9_~/][A-Za-z0-9._/~-]{0,255}")
_QUEUED = re.compile(r"queued as (a\d+)")
_USAGE = ("usage: /host add NAME user@host [--port N] [--sable PATH] | /host list | "
          "/host rm NAME | /host test NAME")


def check(name: str, target: str, port: int = 22, sable: str = "sable") -> str | None:
    """None if the host spec is safe to put in an ssh argv, else why not."""
    if not NAME.fullmatch(name) or name == "all":
        return "NAME must be 1-32 of a-z, 0-9, - (and not 'all')"
    if not _TARGET.fullmatch(target):
        return "target must be user@host: letters, digits, . _ - only, not starting with -"
    if not (isinstance(port, int) and 1 <= port <= 65535):
        return "port must be 1-65535"
    if not _SABLE.fullmatch(sable):
        return "--sable must be a path of letters, digits, . _ / ~ - only, not starting with -"
    return None


def ssh_argv(spec: dict) -> list[str]:
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-p", str(spec.get("port", 22)), "--", spec["target"],
            spec.get("sable", "sable"), "--mcp-serve"]


def connect(spec: dict):
    """A Client talking to the remote Sable. Tests replace this."""
    from sable.mcp.client import Client
    from sable.mcp.transports import StdioTransport
    return Client(StdioTransport(ssh_argv(spec)))


# --- config --------------------------------------------------------------

def _path(path=None):
    from sable.core.config.wizard import CONFIG_PATH
    return path or CONFIG_PATH


def load(path=None) -> dict:
    p = _path(path)
    try:
        data = json.loads(p.read_text()) if p.exists() else {}
    except (OSError, ValueError):
        data = {}
    hosts = data.get("hosts") if isinstance(data.get("hosts"), dict) else {}
    # Re-check on load: a hand-edited config.json must not reach an argv unchecked.
    return {n: s for n, s in hosts.items() if isinstance(s, dict) and not check(
        n, str(s.get("target", "")), s.get("port", 22), str(s.get("sable", "sable")))}


def _save(hosts: dict, path=None) -> None:
    p = _path(path)
    data = json.loads(p.read_text()) if p.exists() else {}
    data["hosts"] = hosts
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)


# --- /host ----------------------------------------------------------------

def _add(args: list[str], path=None) -> None:
    if len(args) < 2:
        _out(_USAGE)
        return
    name, target, rest = args[0], args[1], args[2:]
    port, sable = 22, "sable"
    while rest:
        flag, value = rest[0], (rest[1] if len(rest) > 1 else None)
        if flag == "--port" and value is not None and value.isdigit():
            port = int(value)
        elif flag == "--sable" and value is not None:
            sable = value
        else:
            _out(_USAGE)
            return
        rest = rest[2:]
    problem = check(name, target, port, sable)
    if problem:
        _out(f"/host add: {problem}")
        return
    hosts = load(path)
    hosts[name] = {"target": target, "port": port, "sable": sable}
    _save(hosts, path)
    _out(f"added {name}: {target} port {port}; try /host test {name}")


def _hint(exc, spec: dict) -> str:
    """BatchMode refuses a host key it has never seen; say how to fix it."""
    text = str(exc).lower()
    if "host key" in text or "authenticity" in text:
        return f"\n  first connect once by hand to accept its key: ssh {spec.get('target', '')}"
    return ""


def _test(name: str, path=None) -> None:
    from sable.mcp.client import McpError
    spec = load(path).get(name)
    if spec is None:
        _out(f"no host {name!r}; /host list")
        return
    try:
        client = connect(spec)
    except McpError as exc:
        _out(f"{name}: {exc}{_hint(exc, spec)}")
        return
    try:
        tools = client.list_tools()
    except McpError as exc:
        _out(f"{name}: {exc}")
        return
    finally:
        client.close()
    _out(f"{name}: {len(tools)} tools: " + ", ".join(str(t.get("name")) for t in tools))


def handle_host(argument: str, path=None) -> bool:
    try:
        parts = shlex.split(argument)
    except ValueError as exc:
        _out(f"/host: {exc}")
        return True
    sub, args = (parts[0], parts[1:]) if parts else ("list", [])
    if sub == "add":
        _add(args, path)
    elif sub == "list" and not args:
        hosts = load(path)
        if not hosts:
            _out("no hosts; add one with /host add NAME user@host")
        for n, s in hosts.items():
            _out(f"{n}  {s['target']}  port {s.get('port', 22)}  {s.get('sable', 'sable')}")
    elif sub == "rm" and len(args) == 1:
        hosts = load(path)
        if hosts.pop(args[0], None) is None:
            _out(f"no host {args[0]!r}")
        else:
            _save(hosts, path)
            _out(f"removed {args[0]}")
    elif sub == "test" and len(args) == 1:
        _test(args[0], path)
    else:
        _out(_USAGE)
    return True


# --- @NAME <command> ------------------------------------------------------

def _run_one(name: str, spec: dict, command: str) -> str:
    """Send one command; return the outcome word for the audit line."""
    from sable.core.audit import write_action
    from sable.mcp.client import McpError
    from sable.policy.engine import redact_text

    try:
        client = connect(spec)
        try:
            result = client.call_tool("run_command", {"command": command})
        finally:
            client.close()
    except McpError as exc:
        _out(f"  could not reach {name}: {exc}{_hint(exc, spec)}")
        outcome = "unreachable"
    else:
        queued = _QUEUED.search(result.text)
        if queued:
            _out(f"  queued on {name} as {queued.group(1)}; approve it on that host")
            outcome = f"queued {queued.group(1)}"
        else:
            _out(redact_text(result.text))
            outcome = "error" if result.is_error else "ok"
    write_action("host", f"{name}: {redact_text(command)} -> {outcome}")
    return outcome


def handle_at(line: str, path=None, prompt=input) -> bool:
    """`@NAME <line>` / `@all <line>`. False if the line is not addressed to a host."""
    m = re.fullmatch(r"@([a-z0-9-]{1,32})\s+(.+)", line.strip(), re.S)
    if not m:
        return False
    name, command = m.group(1), m.group(2).strip()
    hosts = load(path)
    if name == "all":
        targets = list(hosts.items())
    elif name in hosts:
        targets = [(name, hosts[name])]
    else:
        _out(f"no host {name!r}; /host list")
        return True
    if not targets:
        _out("no hosts; add one with /host add NAME user@host")
        return True
    from sable.agents.router import Route, classify
    if classify(command, mode="auto") is Route.AGENTIC:
        # ponytail: remote goals need a remote orchestrator call; commands only for now.
        _out("remote goals are not supported yet; send a shell command, e.g. "
             f"@{name} df -h")
        return True
    for n, _ in targets:
        _out(f"on {n}: $ {command}")
    try:
        answer = prompt("  ↵ run   q cancel  › ").strip()
    except (EOFError, KeyboardInterrupt):
        answer = "q"
    if answer:
        _out("cancelled")
        return True
    for n, spec in targets:  # sequential on purpose: output stays grouped per host
        _out(f"── {n} ──")
        _run_one(n, spec, command)
    return True
