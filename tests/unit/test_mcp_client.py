"""MCP client core: stdio against tests/fixtures/mock_mcp_server.py, HTTP on MockTransport."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

from sable.mcp.client import RESULT_CAP, Client, McpError, format_content
from sable.mcp.transports import HttpTransport, StdioTransport

SERVER = str(Path(__file__).resolve().parents[1] / "fixtures" / "mock_mcp_server.py")


@pytest.fixture
def stdio():
    made = []

    def make(mode, timeout=5.0):
        c = Client(StdioTransport([sys.executable, SERVER, mode], timeout=timeout))
        made.append(c)
        return c
    yield make
    for c in made:
        c.close()


def test_discover_new_spec(stdio):
    info = stdio("new-spec").discover()
    assert (info.era, info.version) == ("modern", "2026-07-28")
    assert info.server_info["name"] == "new" and info.instructions == "be nice"


def test_initialize_fallback_old_spec(stdio):
    c = stdio("old-spec")
    info = c.discover()
    assert (info.era, info.version, info.server_info["name"]) == ("legacy", "2025-06-18", "old")
    assert [t["name"] for t in c.list_tools()] == ["t0", "t1", "t2"]


def test_list_tools_follows_cursor(stdio):
    assert [t["name"] for t in stdio("new-spec").list_tools()] == ["t0", "t1", "t2"]


def test_call_tool_text_and_image_summary(stdio):
    r = stdio("new-spec").call_tool("echo", {"a": 1})
    assert r.text == "echo {'a': 1}\n[image, 12 KB]" and not r.is_error
    assert stdio("new-spec").call_tool("bad").is_error


def test_jsonrpc_error_maps_to_mcperror(stdio):
    with pytest.raises(McpError) as e:
        stdio("new-spec").call_tool("fail")
    assert e.value.code == -32602 and "bad arguments" in str(e.value)


def test_mrtr_retries_once_with_answers(stdio):
    seen = []

    def on_input(reqs):
        seen.append(reqs)
        return {"who": {"action": "accept", "content": {"name": "ada"}}}
    r = stdio("input-required").call_tool("ask", on_input=on_input)
    assert r.text == "hi ada state=s1"
    assert seen[0]["who"]["method"] == "elicitation/create"


def test_mrtr_without_on_input_says_so(stdio):
    with pytest.raises(McpError, match="needs input; run it from the shell"):
        stdio("input-required").call_tool("ask")


def test_server_dies_mid_call(stdio):
    c = stdio("crash")
    with pytest.raises(McpError, match=r"exited \(code 3\): boom"):
        c.call_tool("echo")
    with pytest.raises(McpError):  # and stays dead, fast
        c.call_tool("echo")


def test_timeout_never_hangs(stdio):
    c = stdio("hang", timeout=0.3)
    c.discover()
    with pytest.raises(McpError, match="no answer") as e:
        c.call_tool("echo")
    assert e.value.kind == "timeout"


def test_missing_command_is_mcperror():
    with pytest.raises(McpError, match="could not start"):
        StdioTransport(["definitely-not-a-real-mcp-server-xyz"])


def test_content_cap_and_resources():
    assert format_content([{"type": "resource", "resource": {"uri": "file:///a", "text": "x" * 3000}},
                           {"type": "resource_link", "uri": "file:///b"}]) == \
        "[resource file:///a, 3 KB]\n[resource link file:///b]"
    from sable.mcp.client import _cap
    out = _cap("y" * (RESULT_CAP + 10))
    assert out.startswith("y" * 100) and "truncated" in out and len(out) < RESULT_CAP + 100


# --- Streamable HTTP ----------------------------------------------------------

def _http(handler):
    return Client(HttpTransport("http://mcp.test/mcp", http=httpx.Client(transport=httpx.MockTransport(handler))))


def test_http_new_spec_json_and_sse():
    seen = []

    def handler(req):
        msg = json.loads(req.content)
        seen.append(req.headers)
        if msg["method"] == "server/discover":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {
                "resultType": "complete", "supportedVersions": ["2026-07-28"], "capabilities": {}}})
        body = (": keepalive\n\n"
                'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n'
                "data: " + json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {
                    "content": [{"type": "text", "text": "ok"}]}}) + "\n\n")
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    c = _http(handler)
    assert c.call_tool("go").text == "ok"
    assert seen[-1]["mcp-method"] == "tools/call" and seen[-1]["mcp-name"] == "go"
    assert seen[-1]["mcp-protocol-version"] == "2026-07-28"


def test_http_legacy_fallback_keeps_session_id():
    seen = []

    def handler(req):
        msg = json.loads(req.content)
        seen.append((msg.get("method"), req.headers.get("mcp-session-id")))
        if msg["method"] == "server/discover":
            return httpx.Response(400, text="bad request")
        if msg["method"] == "initialize":
            return httpx.Response(200, headers={"Mcp-Session-Id": "abc"}, json={
                "jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18",
                                                              "capabilities": {}}})
        if "id" not in msg:
            return httpx.Response(202)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": []}})
    c = _http(handler)
    assert c.discover().era == "legacy"
    assert c.list_tools() == []
    assert seen[-1] == ("tools/list", "abc") and seen[-2][0] == "notifications/initialized"


def test_http_modern_error_is_not_a_fallback():
    def handler(req):
        msg = json.loads(req.content)
        return httpx.Response(400, json={"jsonrpc": "2.0", "id": msg["id"], "error": {
            "code": -32022, "message": "Unsupported protocol version"}})
    with pytest.raises(McpError) as e:
        _http(handler).discover()
    assert e.value.code == -32022


def test_http_timeout_is_mcperror():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)
    with pytest.raises(McpError) as e:
        _http(handler).call_tool("x")
    assert e.value.kind == "timeout"
