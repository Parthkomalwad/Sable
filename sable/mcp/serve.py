"""`sable --mcp-serve`: Sable as an MCP server over stdio (Phase 6 Task 4, D2).

An external agent (Claude Code and the like) drives this, so everything it
sends is untrusted and Sable's policy holds exactly as it does for a worker:

- `run_command` goes through `policy.engine.decide`. `deny` is refused.
  `confirm` is queued to `policy_queue` for a human (`/inbox approve a<id>`)
  and runs once, on a later identical call, only after approval. `allow`
  runs in a pty and is audited under `mcp:<client>`.
- The client name comes from the client and is sanitised before it reaches
  the queue, the audit log or anything else.
- Every `tools/call` is audited with `write_action("mcp.serve", ...)`.
- stdout carries protocol messages only: `main()` moves the real stdin and
  stdout aside and points fds 0 and 1 at /dev/null and stderr, so nothing
  else (a child, a stray print) can read the protocol or corrupt it.
- Output returned to the client passes the secret redactor.

Spec notes are in `sable/mcp/client.py`. 2026-07-28 is stateless, so the
client name is read from `_meta` on each request, falling back to what a
legacy client sent in `initialize`.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from sable import __version__
from sable.agents import runtime
from sable.core import audit
from sable.policy import queue
from sable.policy.engine import decide, redact_text
from sable.policy.tiers import Tier

MODERN, LEGACY = "2026-07-28", "2025-06-18"
_INFO = {"name": "sable", "version": __version__}
_INSTRUCTIONS = ("Sable runs shell commands on this host under its policy. Commands that need "
                 "a human are queued, not run; retry the same call after they approve it.")
_CAP = 64 * 1024

_STR = {"type": "string"}
TOOLS = [
    {"name": "run_command", "description": "Run a shell command under Sable's policy. "
     "Denied commands are refused; ones that need a human are queued for approval.",
     "inputSchema": {"type": "object", "properties": {"command": _STR, "cwd": _STR},
                     "required": ["command"]}},
    {"name": "list_tasks", "description": "List Sable task agents and their status.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "spawn_task", "description": "Start a Sable task agent in a tmux window; "
     "poll it with list_tasks.",
     "inputSchema": {"type": "object", "properties": {"name": _STR, "goal": _STR},
                     "required": ["name", "goal"]}},
    {"name": "get_skill", "description": "Read an enabled Sable skill by name.",
     "inputSchema": {"type": "object", "properties": {"name": _STR}, "required": ["name"]}},
    {"name": "search_memory", "description": "Search Sable's saved session memory and skill names.",
     "inputSchema": {"type": "object", "properties": {"query": _STR}, "required": ["query"]}},
]


def sanitize(name) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "", str(name or ""))[:40] or "unknown"


@dataclass
class Context:
    conn: sqlite3.Connection
    run: Callable[[str, str, float], str] = field(
        default=lambda c, cwd, t: runtime.run_command(c, cwd, timeout=t, prefix="mcp_"))
    home: str = field(default_factory=lambda: str(Path.home()))
    spawn: Callable[[str, str], None] | None = None
    client: str = "unknown"  # from a legacy `initialize`


class _ArgError(ValueError):
    pass


def _arg(args: dict, key: str, required: bool = True) -> str | None:
    v = args.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or (required and not v.strip()):
        raise _ArgError(f"{key} must be a non-empty string")
    return v


def _result(text: str, error: bool = False) -> dict:
    raw = redact_text(text)
    if len(raw) > _CAP:
        raw = raw[:_CAP] + "\n[truncated]"
    return {"content": [{"type": "text", "text": raw}], "isError": error}


def _audit_call(client: str, tool: str, args: dict) -> None:
    audit.write_action("mcp.serve", redact_text(f"client={client} tool={tool} args={json.dumps(args)[:500]}"))


def _skills() -> list[dict]:
    from sable.skills.index import SkillIndex
    return SkillIndex().list_all()


def _enabled(entry: dict) -> bool:
    return entry.get("status", "enabled") == "enabled"


# --- tools --------------------------------------------------------------

def _run_command(args: dict, ctx: Context, client: str) -> dict:
    command = _arg(args, "command")
    cwd = os.path.expanduser(_arg(args, "cwd", required=False) or ctx.home)
    if not os.path.isdir(cwd):
        return _result(f"no such directory: {cwd}", True)
    agent = f"mcp:{client}"
    d = decide(command)
    if d.tier is Tier.DENY:
        return _result(f"refused by policy: {d.why or (d.rule.name if d.rule else 'deny')}", True)
    if d.tier is not Tier.ALLOW and not queue.take_approved(ctx.conn, agent, command):
        qid = queue.enqueue(ctx.conn, agent, command, d)
        return _result(f"queued as a{qid}; a human must approve it with /inbox approve a{qid} "
                       f"({d.why}). Call again with the same command once approved.")
    audit.write_command(agent, cwd, command)
    output = ctx.run(command, cwd, runtime.escalated_timeout(command))
    code = runtime.exit_code_of(output)
    return _result(f"{output}\n(exit status {code})" if code is None else output, code != 0)


def _list_tasks(args: dict, ctx: Context, client: str) -> dict:
    try:
        rows = ctx.conn.execute(
            "SELECT name, status, step_count, goal FROM tasks ORDER BY id DESC LIMIT 50").fetchall()
    except sqlite3.OperationalError:
        rows = []
    if not rows:
        return _result("no tasks")
    return _result("\n".join(f"{n}\t{s}\tsteps={c or 0}\t{g}" for n, s, c, g in rows))


def _default_spawn(name: str, goal: str) -> None:
    from sable.agents.manager import TaskManager
    from sable.core.config.schema import ShellConfig
    from sable.core.db import Database

    path = Path.home() / ".config" / "agentic-shell" / "config.json"
    try:
        config = ShellConfig.from_dict(json.loads(path.read_text()))
    except (OSError, ValueError):
        config = ShellConfig.defaults()
    TaskManager(config=config, db=Database()).spawn(name, goal)


def _spawn_task(args: dict, ctx: Context, client: str) -> dict:
    # The name becomes a directory under tasks_base and a tmux window name,
    # so no leading dots (no "..") and nothing outside the safe set.
    name = re.sub(r"[^A-Za-z0-9_.-]", "", _arg(args, "name")).lstrip(".")[:40]
    goal = _arg(args, "goal")
    if not name:
        return _result("name must contain letters, digits, _ . or -", True)
    if not os.environ.get("TMUX"):
        return _result("spawn_task needs the Sable tmux session", True)
    try:
        (ctx.spawn or _default_spawn)(name, goal)
    except (RuntimeError, OSError, sqlite3.Error) as e:
        return _result(f"could not spawn {name}: {e}", True)
    return _result(f"spawned {name}; poll it with list_tasks")


def _get_skill(args: dict, ctx: Context, client: str) -> dict:
    name = _arg(args, "name")
    for entry in _skills():
        if entry.get("name") == name and _enabled(entry):
            try:
                return _result(Path(entry["file"]).read_text(encoding="utf-8"))
            except (OSError, KeyError, UnicodeDecodeError) as e:
                return _result(f"could not read skill {name}: {e}", True)
    return _result(f"no enabled skill named {name}", True)


def _search_memory(args: dict, ctx: Context, client: str) -> dict:
    # ponytail: substring match over session memory and skill names; move to
    # FTS5 when memory grows past what LIKE scans comfortably.
    q = _arg(args, "query").lower()
    hits = []
    try:
        for ts, text in ctx.conn.execute(
                "SELECT created_at, compressed FROM session_memory WHERE lower(compressed) LIKE ? "
                "ORDER BY id DESC LIMIT 5", (f"%{q}%",)):
            hits.append(f"[memory {ts}] {text[:2000]}")
    except sqlite3.OperationalError:
        pass
    for e in _skills():
        words = " ".join([str(e.get("name", ""))] + [str(k) for k in e.get("keywords") or []]).lower()
        if _enabled(e) and q in words:
            hits.append(f"[skill] {e.get('name')} (read it with get_skill)")
    return _result("\n\n".join(hits) or f"nothing found for {q!r}")


_HANDLERS = {"run_command": _run_command, "list_tasks": _list_tasks, "spawn_task": _spawn_task,
             "get_skill": _get_skill, "search_memory": _search_memory}


# --- protocol -------------------------------------------------------------

def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle(msg: dict, ctx: Context) -> dict | None:
    """Answer one JSON-RPC message; None for a notification."""
    mid, method = msg.get("id"), msg.get("method")
    params = msg.get("params")
    if params is None:
        params = {}
    if not isinstance(method, str) or not isinstance(params, dict):
        return None if "id" not in msg else _error(mid, -32600, "invalid request")
    if "id" not in msg:
        return None  # notifications/initialized and friends need no answer
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    info = meta.get("io.modelcontextprotocol/clientInfo")
    client = sanitize(info.get("name")) if isinstance(info, dict) else ctx.client

    if method == "server/discover":
        result = {"supportedVersions": [MODERN, LEGACY], "capabilities": {"tools": {}},
                  "instructions": _INSTRUCTIONS, "_meta": {"io.modelcontextprotocol/serverInfo": _INFO}}
    elif method == "initialize":
        info = params.get("clientInfo")
        ctx.client = sanitize(info.get("name")) if isinstance(info, dict) else "unknown"
        asked = params.get("protocolVersion")
        result = {"protocolVersion": asked if asked in (MODERN, LEGACY) else LEGACY,
                  "capabilities": {"tools": {}}, "serverInfo": _INFO, "instructions": _INSTRUCTIONS}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in _HANDLERS or not isinstance(args, dict):
            return _error(mid, -32602, f"unknown tool or bad arguments: {str(name)[:60]}")
        _audit_call(client, name, args)
        try:
            result = _HANDLERS[name](args, ctx, client)
        except _ArgError as e:
            result = _result(str(e), True)
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as e:
            return _error(mid, -32603, f"{name} failed: {type(e).__name__}")
    else:
        return _error(mid, -32601, f"method not found: {method[:60]}")
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def handle_line(line: str, ctx: Context) -> dict | None:
    try:
        msg = json.loads(line)
    except ValueError:
        return _error(None, -32700, "parse error")
    if not isinstance(msg, dict):
        return _error(None, -32600, "invalid request (batches are not supported)")
    if "method" not in msg:
        return None if "id" not in msg else _error(msg.get("id"), -32600, "invalid request")
    return handle(msg, ctx)


def main() -> int:
    # Take the protocol pipes, then point fds 0 and 1 elsewhere so no child
    # process or stray write can read the protocol or corrupt it.
    proto_in = os.fdopen(os.dup(0), "r", encoding="utf-8", errors="replace")
    proto_out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    os.dup2(2, 1)
    sys.stdin, sys.stdout = open(os.devnull), sys.stderr

    from sable.core.db import Database
    ctx = Context(conn=Database()._conn)
    for line in proto_in:
        if not line.strip():
            continue
        reply = handle_line(line, ctx)
        if reply is not None:
            proto_out.write(json.dumps(reply) + "\n")
            proto_out.flush()
    return 0
