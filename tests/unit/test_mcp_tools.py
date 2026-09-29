"""/mcp and MCP tools as Sable tools, against tests/fixtures/mock_mcp_server.py."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sable.app.builtins import mcp as builtin
from sable.core.config import keyring
from sable.mcp import servers
from sable.policy.tiers import Tier
from sable.tools import registry
from sable.tools.base import ToolContext

ROOT = Path(__file__).resolve().parents[2]
SERVER = str(ROOT / "tests" / "fixtures" / "mock_mcp_server.py")
CTX = ToolContext(role="orchestrator", cwd=".", agent="t")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"backend": "ollama", "theme": "mono"}))
    lines = []
    monkeypatch.setattr(builtin, "_out", lambda s: lines.append(str(s)))
    monkeypatch.setattr(servers, "_loaded", False)
    monkeypatch.setattr(registry, "_loaded", True)  # builtins not needed here
    yield path, lines
    for name in list(servers._clients):
        servers.unregister_server(name)
    servers._failed.clear()


def _mcp(path, line):
    return builtin.handle_mcp(line, path=path)


def test_add_list_trust_remove_round_trip(cfg):
    path, lines = cfg
    _mcp(path, f'add mock "{sys.executable}" "{SERVER}" new-spec')
    assert "added mock: 3 tools" in lines
    data = json.loads(path.read_text())
    assert data["theme"] == "mono" and data["mcp"]["servers"]["mock"]["command"][-1] == "new-spec"
    assert servers.label(registry.get("mcp.mock.t0")) == "preview"
    assert set(registry.get("mcp.mock.t0").roles) == {"orchestrator"}

    _mcp(path, "trust mock.t1")
    assert servers.label(registry.get("mcp.mock.t1")) == "trusted"
    assert json.loads(path.read_text())["mcp"]["trusted"] == ["mock.t1"]
    lines.clear()
    _mcp(path, "list")
    assert any("mcp.mock.t1" in s and "trusted" in s for s in lines)
    _mcp(path, "untrust mock.t1")
    assert servers.label(registry.get("mcp.mock.t1")) == "preview"

    _mcp(path, "remove mock")
    assert registry.get("mcp.mock.t0") is None
    assert json.loads(path.read_text())["mcp"]["servers"] == {}


def test_a_call_returns_output_and_always_taints(cfg):
    path, _ = cfg
    _mcp(path, f'add mock "{sys.executable}" "{SERVER}" new-spec')
    r = registry.get("mcp.mock.t0").run({}, CTX)
    assert r.ok and "echo" in r.output and r.taints


def test_trusted_tool_registers_as_allow_on_load(cfg):
    path, _ = cfg
    servers.save_config({"servers": {"m": {"command": [sys.executable, SERVER, "new-spec"]}},
                         "trusted": ["m.t2"]}, path)
    servers.load_all(report=lambda s: None, path=path)
    assert servers.label(registry.get("mcp.m.t2")) == "trusted"
    assert servers.label(registry.get("mcp.m.t0")) == "preview"


def test_broken_server_is_reported_once_and_skipped(cfg):
    path, _ = cfg
    servers.save_config({"servers": {
        "bad": {"command": [str(ROOT / "no-such-binary")]},
        "ok": {"command": [sys.executable, SERVER, "new-spec"]}}}, path)
    said = []
    servers.load_all(report=said.append, path=path)
    servers.load_all(report=said.append, path=path)
    assert len(said) == 1 and "bad" in said[0]
    assert registry.get("mcp.ok.t0") is not None
    assert servers.status("bad").startswith("failed")


def test_secret_env_is_resolved_but_never_stored(cfg, monkeypatch):
    path, _ = cfg
    monkeypatch.setattr(keyring, "lookup", {"secret:tok": "s3cret"}.get)
    spec = {"command": [sys.executable, SERVER, "new-spec"], "env": {"MCP_TEST": "$SECRET:tok"}}
    servers.save_config({"servers": {"m": spec}, "trusted": []}, path)
    client = servers.connect("m", spec)
    try:
        assert client.call_tool("env").text == "s3cret"
    finally:
        client.close()
    assert "s3cret" not in path.read_text()


def test_schema_mapping_and_untrusted_description():
    schema = {"properties": {"p": {"type": "string"}, "n": {"type": "integer"},
                             "o": {"type": "object"}, "u": {"anyOf": []},
                             "x": {"type": ["number", "null"]}}, "required": ["p"]}
    assert servers._schema(schema) == {"p": "string", "n?": "integer", "o?": "object",
                                       "u?": "any", "x?": "number"}
    assert servers._clean("a\x1b[31mb\nc" + "z" * 400, 300) == "a [31mb c" + "z" * 291
    assert servers.tool_key("s", "we ird/name") == "s.we_ird_name"


def test_urls_must_be_https_or_local_http():
    assert servers.check_url("https://x.example/mcp") is None
    assert servers.check_url("http://localhost:3000/mcp") is None
    assert servers.check_url("http://x.example/mcp")


def test_npx_missing_warns(cfg, monkeypatch):
    path, lines = cfg
    monkeypatch.setattr(builtin.shutil, "which", lambda _: None)
    _mcp(path, "add fs npx -y @modelcontextprotocol/server-filesystem /app")
    assert "Node.js" in lines[-1] and "http servers" in lines[-1]


def test_cold_start_does_not_import_mcp():
    code = "import sys, sable.app.main, sable.app.repl; print('sable.mcp' in sys.modules)"
    env = {**os.environ, "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, env=env)
    assert out.stdout.strip() == "False", out.stderr[-500:]


def test_a_tool_written_as_a_call_is_caught(monkeypatch):
    from sable.tools import registry
    from sable.tools.base import Tool, ToolResult
    from sable.policy.tiers import Tier
    monkeypatch.setitem(registry._TOOLS, "mcp.x.t0", Tool(
        name="mcp.x.t0", description="d", schema={}, tier=Tier.CONFIRM, run=lambda a, c: ToolResult(True, "")))
    assert registry.tool_as_command("mcp.x.t0()") == "mcp.x.t0"
    assert registry.tool_as_command('mcp.x.t0({"a": 1})') == "mcp.x.t0"


def test_mcp_tools_are_listed_under_their_own_heading(monkeypatch):
    from sable.tools import registry
    from sable.tools.base import Tool, ToolResult
    from sable.policy.tiers import Tier
    monkeypatch.setitem(registry._TOOLS, "mcp.files.read", Tool(
        name="mcp.files.read", description="read a file", schema={}, tier=Tier.CONFIRM,
        run=lambda a, c: ToolResult(True, "")))
    text = registry.describe("orchestrator")
    head = text.index("From MCP servers")
    assert text.index("mcp.files.read") > head
    assert all(text.index(f"- {t.signature()}") < head
               for t in registry.for_role("orchestrator") if not t.name.startswith("mcp."))


def test_untrusted_tools_are_shell_only_and_need_no_yes(cfg):
    """A preview and Enter, not a typed YES; workers cannot call them."""
    from sable.policy.tiers import Tier
    path, _ = cfg
    _mcp(path, f'add mock "{sys.executable}" "{SERVER}" new-spec')
    t = registry.get("mcp.mock.t0")
    assert t.tier is Tier.ALLOW and "worker" not in t.roles and "orchestrator" in t.roles
    assert "mcp.mock.t0" not in [x.name for x in registry.for_role("worker")]
