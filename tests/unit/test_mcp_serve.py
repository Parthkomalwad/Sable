"""sable --mcp-serve: the stdio MCP server (Phase 6 Task 4, D2).

Unit tests drive `serve.handle` in-process with an injected runner, so no
command really runs. One end-to-end test spawns the real server over pipes.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from sable.mcp import serve
from sable.policy import queue
from sable.policy.tiers import Decision, Tier

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(serve, "_audit_call", lambda *a: calls.append(a))
    monkeypatch.setattr(serve.audit, "write_command", lambda *a: commands.append(a))
    calls, commands = [], []
    ran = []
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, name TEXT, goal TEXT, status TEXT,"
                 " step_count INTEGER, created_at TEXT, ended_at TEXT)")
    conn.execute("CREATE TABLE session_memory (id INTEGER PRIMARY KEY, compressed TEXT, created_at TEXT)")

    def run(command, cwd, timeout):
        ran.append((command, cwd))
        return "hi\n[exit 0]" if "fail" not in command else "boom\n[exit 2]"

    c = serve.Context(conn=conn, run=run, home=str(tmp_path))
    c.ran, c.calls, c.commands = ran, calls, commands
    return c


def _tier(monkeypatch, tier):
    monkeypatch.setattr(serve, "decide", lambda cmd: Decision(tier=tier, rule=None, why="because", source="test"))


def call(ctx, name, args, client="Claude Code!"):
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": name, "arguments": args,
                      "_meta": {"io.modelcontextprotocol/clientInfo": {"name": client}}}}
    return serve.handle(msg, ctx)["result"]


def text(result):
    return result["content"][0]["text"]


def test_discover(ctx):
    r = serve.handle({"jsonrpc": "2.0", "id": 7, "method": "server/discover"}, ctx)
    assert r["id"] == 7
    assert r["result"]["supportedVersions"] == ["2026-07-28", "2025-06-18"]
    assert "tools" in r["result"]["capabilities"]
    assert r["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "sable"


def test_legacy_initialize_and_notification(ctx):
    r = serve.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "clientInfo": {"name": "old/client"}}}, ctx)
    assert r["result"]["protocolVersion"] == "2025-06-18"
    assert r["result"]["serverInfo"]["name"] == "sable"
    assert ctx.client == "oldclient"
    assert serve.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, ctx) is None


def test_tools_list(ctx):
    tools = serve.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, ctx)["result"]["tools"]
    assert {t["name"] for t in tools} == {"run_command", "list_tasks", "spawn_task", "get_skill", "search_memory"}
    assert all(t["inputSchema"]["type"] == "object" for t in tools)


def test_unknown_method_and_bad_input(ctx):
    assert serve.handle({"jsonrpc": "2.0", "id": 1, "method": "nope"}, ctx)["error"]["code"] == -32601
    assert serve.handle_line("{not json", ctx)["error"]["code"] == -32700
    assert serve.handle_line("[1, 2]", ctx)["error"]["code"] == -32600
    assert serve.handle_line('{"id": 3}', ctx)["error"]["code"] == -32600
    r = serve.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "zzz"}}, ctx)
    assert r["error"]["code"] == -32602
    r = serve.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": "x"}, ctx)
    assert "error" in r


def test_client_name_sanitized():
    assert serve.sanitize("a; rm -rf /" + "x" * 60) == ("arm-rf" + "x" * 60)[:40]
    assert serve.sanitize(None) == "unknown"


def test_run_allow(ctx, monkeypatch):
    _tier(monkeypatch, Tier.ALLOW)
    r = call(ctx, "run_command", {"command": "echo hi"})
    assert not r["isError"] and "hi" in text(r) and "exit 0" in text(r)
    assert ctx.ran == [("echo hi", ctx.home)]
    assert ctx.commands[0][0] == "mcp:ClaudeCode"
    assert ctx.calls and ctx.calls[0][0] == "ClaudeCode"


def test_run_allow_nonzero_is_error(ctx, monkeypatch):
    _tier(monkeypatch, Tier.ALLOW)
    assert call(ctx, "run_command", {"command": "fail"})["isError"]


def test_run_bad_cwd(ctx, monkeypatch):
    _tier(monkeypatch, Tier.ALLOW)
    r = call(ctx, "run_command", {"command": "echo", "cwd": "/no/such/dir"})
    assert r["isError"] and not ctx.ran


def test_run_deny(ctx, monkeypatch):
    _tier(monkeypatch, Tier.DENY)
    r = call(ctx, "run_command", {"command": "rm -rf /"})
    assert r["isError"] and text(r).startswith("refused by policy: because")
    assert not ctx.ran and ctx.calls


def test_run_confirm_queues_then_runs_once_approved(ctx, monkeypatch):
    _tier(monkeypatch, Tier.CONFIRM)
    r = call(ctx, "run_command", {"command": "sudo ls"})
    assert "queued as a1" in text(r) and "/inbox approve a1" in text(r)
    assert not ctx.ran
    assert queue.pending(ctx.conn)[0]["agent"] == "mcp:ClaudeCode"
    # same call again while pending: still queued, same id
    assert "queued as a1" in text(call(ctx, "run_command", {"command": "sudo ls"}))
    queue.decide_request(ctx.conn, 1, approve=True)
    r = call(ctx, "run_command", {"command": "sudo ls"})
    assert not r["isError"] and ctx.ran == [("sudo ls", ctx.home)]
    # used once: the next identical call is queued afresh
    assert "queued as a2" in text(call(ctx, "run_command", {"command": "sudo ls"}))


def test_approval_is_per_client(ctx, monkeypatch):
    _tier(monkeypatch, Tier.CONFIRM)
    call(ctx, "run_command", {"command": "sudo ls"}, client="a")
    queue.decide_request(ctx.conn, 1, approve=True)
    call(ctx, "run_command", {"command": "sudo ls"}, client="b")
    assert not ctx.ran


def test_list_tasks(ctx):
    ctx.conn.execute("INSERT INTO tasks (name, goal, status, step_count) VALUES ('t1', 'do x', 'running', 3)")
    r = call(ctx, "list_tasks", {})
    assert "t1" in text(r) and "running" in text(r)


def test_spawn_task_needs_tmux(ctx, monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    r = call(ctx, "spawn_task", {"name": "x", "goal": "y"})
    assert r["isError"] and "tmux" in text(r)


def test_spawn_task_sanitizes_name(ctx, monkeypatch):
    monkeypatch.setenv("TMUX", "1")
    got = []
    ctx.spawn = lambda name, goal: got.append((name, goal))
    r = call(ctx, "spawn_task", {"name": "../evil name", "goal": "g"})
    assert not r["isError"] and got == [("evilname", "g")]
    assert call(ctx, "spawn_task", {"name": "///", "goal": "g"})["isError"]


def test_get_skill(ctx, tmp_path, monkeypatch):
    f = tmp_path / "SKILL.md"
    f.write_text("do the thing")
    monkeypatch.setattr(serve, "_skills", lambda: [
        {"name": "deploy", "file": str(f), "status": "enabled"},
        {"name": "draft", "file": str(f), "status": "pending"}])
    assert text(call(ctx, "get_skill", {"name": "deploy"})) == "do the thing"
    assert call(ctx, "get_skill", {"name": "draft"})["isError"]
    assert call(ctx, "get_skill", {"name": "nope"})["isError"]


def test_search_memory(ctx, monkeypatch):
    monkeypatch.setattr(serve, "_skills", lambda: [{"name": "nginx-reload", "keywords": ["nginx"], "status": "enabled"}])
    ctx.conn.execute("INSERT INTO session_memory (compressed, created_at) VALUES ('fixed the nginx config', '2026')")
    t = text(call(ctx, "search_memory", {"query": "nginx"}))
    assert "fixed the nginx config" in t and "nginx-reload" in t
    assert "nothing" in text(call(ctx, "search_memory", {"query": "zzzz"}))


def test_bad_arguments(ctx):
    assert call(ctx, "run_command", {})["isError"]
    assert call(ctx, "get_skill", {"name": 3})["isError"]


@pytest.mark.skipif(sys.platform == "win32", reason="run_command needs a pty")
def test_end_to_end_over_pipes(tmp_path):
    from sable.mcp.client import Client
    from sable.mcp.transports import StdioTransport

    t = StdioTransport([sys.executable, "-m", "sable.app.main", "--mcp-serve"], env={
        "PYTHONPATH": str(ROOT), "HOME": str(tmp_path), "SABLE_ALLOW_ROOT": "1"})
    c = Client(t)
    try:
        assert c.discover().era == "modern"
        assert len(c.list_tools()) == 5
        r = c.call_tool("run_command", {"command": "echo hi"})
        assert not r.is_error and "hi" in r.text
    finally:
        c.close()
    assert not [line for line in t.stderr_tail() if "non-JSON" in line]
