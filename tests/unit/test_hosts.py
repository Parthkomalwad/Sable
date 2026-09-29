"""Multi-host (Phase 9 Task 5, H5): /host and @NAME, no real ssh.

The transport is replaced by tests/fixtures/mock_mcp_server.py over pipes, or
by a fake client for the queued (confirm-tier) answer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from sable.app.builtins import hosts
from sable.mcp.client import Client, ToolResult
from sable.mcp.transports import StdioTransport

SERVER = str(Path(__file__).resolve().parents[1] / "fixtures" / "mock_mcp_server.py")


@pytest.fixture
def env(tmp_path, monkeypatch):
    lines, audit = [], []
    monkeypatch.setattr(hosts, "_out", lambda s="": lines.append(str(s)))
    monkeypatch.setattr("sable.core.audit.write_action", lambda a, c, e=None: audit.append((a, c)))
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"backend": "ollama"}))
    monkeypatch.setattr(hosts, "_path", lambda path=None: cfg)
    sent = []
    monkeypatch.setattr(hosts, "connect", lambda spec: sent.append(spec) or Client(
        StdioTransport([sys.executable, SERVER, "new-spec"], timeout=5.0)))
    return {"lines": lines, "audit": audit, "cfg": cfg, "sent": sent}


@pytest.mark.parametrize("name,target,port,sable", [
    ("Web", "u@h", 22, "sable"),            # uppercase name
    ("a" * 33, "u@h", 22, "sable"),
    ("all", "u@h", 22, "sable"),            # reserved
    ("b", "-oProxyCommand=x@h", 22, "sable"),
    ("b", "u@-oProxyCommand=x", 22, "sable"),
    ("b", "u@h;rm -rf /", 22, "sable"),
    ("b", "u@h $(id)", 22, "sable"),
    ("b", "u@h`id`", 22, "sable"),
    ("b", "host-without-user", 22, "sable"),
    ("b", "u@h", 0, "sable"),
    ("b", "u@h", 70000, "sable"),
    ("b", "u@h", 22, "sable;id"),
    ("b", "u@h", 22, "-x"),
    ("b", "u@h", 22, "/opt/sable $(id)"),
])
def test_check_refuses_injection(name, target, port, sable):
    assert hosts.check(name, target, port, sable)


def test_check_accepts_normal():
    assert hosts.check("web-1", "deploy@10.0.0.5", 2222, "/opt/sable/bin/sable") is None


def test_ssh_argv_exact():
    assert hosts.ssh_argv({"target": "u@b.example", "port": 2222, "sable": "/opt/sable"}) == [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-p", "2222", "--",
        "u@b.example", "/opt/sable", "--mcp-serve"]


def test_add_list_rm_keeps_other_keys(env):
    hosts.handle_host("add b u@b.example --port 2222")
    hosts.handle_host("add bad 'u@h;id'")
    data = json.loads(env["cfg"].read_text())
    assert data["backend"] == "ollama"
    assert data["hosts"] == {"b": {"target": "u@b.example", "port": 2222, "sable": "sable"}}
    assert any("target must be user@host" in s for s in env["lines"])
    hosts.handle_host("list")
    assert "b  u@b.example  port 2222  sable" in env["lines"]
    hosts.handle_host("rm b")
    assert json.loads(env["cfg"].read_text())["hosts"] == {}


def test_hand_edited_bad_host_is_ignored(env):
    env["cfg"].write_text(json.dumps({"hosts": {"x": {"target": "-oProxyCommand=id@h"}}}))
    assert hosts.load() == {}


def test_test_lists_remote_tools(env):
    hosts.handle_host("add b u@b")
    hosts.handle_host("test b")
    assert env["lines"][-1] == "b: 3 tools: t0, t1, t2"


def test_at_previews_then_runs_and_audits(env):
    hosts.handle_host("add b u@b")
    asked = []
    assert hosts.handle_at("@b df -h", prompt=lambda p: asked.append(p) or "")
    assert "on b: $ df -h" in env["lines"] and "q cancel" in asked[0]
    assert any("echo {'command': 'df -h'}" in s for s in env["lines"])
    assert env["audit"] == [("host", "b: df -h -> ok")]


def test_at_cancel_sends_nothing(env):
    hosts.handle_host("add b u@b")
    assert hosts.handle_at("@b df -h", prompt=lambda p: "q")
    assert env["sent"] == [] and env["lines"][-1] == "cancelled" and env["audit"] == []


def test_at_goal_is_not_sent(env):
    hosts.handle_host("add b u@b")
    assert hosts.handle_at("@b check why the disk is full", prompt=lambda p: "")
    assert env["sent"] == [] and "remote goals are not supported yet" in env["lines"][-1]


def test_at_unknown_host_and_non_host_lines(env):
    assert hosts.handle_at("@nope ls", prompt=lambda p: "")
    assert env["lines"][-1] == "no host 'nope'; /host list"
    assert not hosts.handle_at("@", prompt=lambda p: "")
    assert not hosts.handle_at("email@x", prompt=lambda p: "")


def test_at_all_grouped_per_host(env):
    hosts.handle_host("add a u@a")
    hosts.handle_host("add b u@b")
    hosts.handle_at("@all uptime", prompt=lambda p: "")
    heads = [i for i, s in enumerate(env["lines"]) if s.startswith("── ")]
    assert [env["lines"][i] for i in heads] == ["── a ──", "── b ──"]
    assert [s["target"] for s in env["sent"]] == ["u@a", "u@b"]
    assert [c for _, c in env["audit"]] == ["a: uptime -> ok", "b: uptime -> ok"]


def test_queued_result_says_approve_on_that_host(env, monkeypatch):
    class Fake:
        def call_tool(self, name, args):
            assert (name, args) == ("run_command", {"command": "systemctl restart nginx"})
            return ToolResult("queued as a7; a human must approve it with /inbox approve a7 (x).")

        def close(self):
            pass
    monkeypatch.setattr(hosts, "connect", lambda spec: Fake())
    hosts.handle_host("add b u@b")
    hosts.handle_at("@b systemctl restart nginx", prompt=lambda p: "")
    assert "  queued on b as a7; approve it on that host" in env["lines"]
    assert env["audit"] == [("host", "b: systemctl restart nginx -> queued a7")]


def test_audit_redacts_secrets(env):
    hosts.handle_host("add b u@b")
    hosts.handle_at("@b echo AKIAIOSFODNN7EXAMPLE", prompt=lambda p: "")
    assert "AKIAIOSFODNN7EXAMPLE" not in env["audit"][0][1]
    assert "[REDACTED]" in env["audit"][0][1]


def test_unreachable_host(env, monkeypatch):
    monkeypatch.setattr(hosts, "connect", lambda spec: Client(
        StdioTransport([sys.executable, SERVER, "crash"], timeout=5.0)))
    hosts.handle_host("add b u@b")
    hosts.handle_at("@b ls", prompt=lambda p: "")
    assert any(s.startswith("  could not reach b:") for s in env["lines"])
    assert env["audit"] == [("host", "b: ls -> unreachable")]


def test_dispatch_routes_at_and_host(env, monkeypatch):
    from sable.app.builtins import dispatch
    from sable.core.config.schema import ShellConfig
    seen = []
    monkeypatch.setattr(hosts, "handle_at", lambda line: seen.append(line) or True)
    assert dispatch.handle_builtin("@b ls", None, "s", ShellConfig.defaults())
    assert seen == ["@b ls"]


def test_config_keeps_hosts():
    from sable.core.config.schema import ShellConfig
    cfg = ShellConfig.from_dict({"backend": "ollama", "model": "m", "hosts": {"b": {"target": "u@b"}}})
    assert cfg.to_dict()["hosts"] == {"b": {"target": "u@b"}}
