"""`/mcp search` over the MCP Registry (D3). No network: httpx.MockTransport."""
import httpx
import pytest

from sable.app.builtins import mcp_search
from sable.mcp import registry_search as rs

# Mirrors a real v0.1 response (2026-09-28), trimmed.
BODY = {
    "servers": [
        {"server": {"name": "com.pulsemcp/remote-filesystem",
                    "description": "Remote \x1b[31mfilesystem\x07 ops\n" + "x" * 300,
                    "version": "0.1.3",
                    "packages": [{"registryType": "npm", "identifier": "remote-filesystem-mcp-server",
                                  "transport": {"type": "stdio"},
                                  "environmentVariables": [
                                      {"name": "GCS_BUCKET", "isRequired": True},
                                      {"name": "GCS_ROOT_PATH"}]}]},
         "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": True}}},
        {"server": {"name": "com.mcparmory/slack", "description": "Slack",
                    "packages": [{"registryType": "pypi", "identifier": "mcparmory-slack",
                                  "transport": {"type": "stdio"}},
                                 {"registryType": "oci", "identifier": "ghcr.io/mcparmory/slack:1.0.1"}]}},
        {"server": {"name": "io.github.x/shibayu", "description": "d",
                    "packages": [{"registryType": "oci", "identifier": "ghcr.io/shibayu36/slack-explorer-mcp:0.12.0",
                                  "transport": {"type": "stdio"}}]}},
        {"server": {"name": "ai.waystation/slack", "description": "d",
                    "remotes": [{"type": "streamable-http", "url": "https://waystation.ai/slack/mcp"}]}},
        {"server": {"name": "ai.smithery/slack", "description": "d",
                    "remotes": [{"type": "streamable-http", "url": "https://server.smithery.ai/slack/mcp",
                                 "headers": [{"name": "Authorization", "isRequired": True}]}]}},
        {"server": {"name": "io.github.y/tg", "description": "d",
                    "packages": [{"registryType": "npm", "identifier": "telegram-slack-mcp",
                                  "packageArguments": [{"type": "positional", "name": "server", "isRequired": True}]}]}},
        {"server": {"name": "io.github.z/bundle", "description": "d",
                    "packages": [{"registryType": "mcpb", "identifier": "https://x/y.mcpb"}]}},
        {"server": {"name": "evil/pkg", "description": "d",
                    "packages": [{"registryType": "npm", "identifier": "a; rm -rf ~"}]}},
    ],
    "metadata": {"count": 8},
}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok(seen=None):
    def handler(req):
        if seen is not None:
            seen.append(req)
        return httpx.Response(200, json=BODY)
    return _client(handler)


def test_query_hits_v0_1_latest():
    seen = []
    rs.search("slack", limit=5, client=_ok(seen))
    url = seen[0].url
    assert url.path == "/v0.1/servers"
    assert url.params["search"] == "slack" and url.params["version"] == "latest"


def test_add_lines_per_kind():
    lines = [h.add_line for h in rs.search("x", limit=20, client=_ok())]
    assert lines == [
        "/mcp add remote-filesystem npx -y remote-filesystem-mcp-server",
        "/mcp add slack uvx mcparmory-slack",
        "/mcp add shibayu docker run -i --rm ghcr.io/shibayu36/slack-explorer-mcp:0.12.0",
        "/mcp add slack https://waystation.ai/slack/mcp",
        "/mcp add slack https://server.smithery.ai/slack/mcp",
        "/mcp add tg npx -y telegram-slack-mcp <server>",
    ]  # mcpb bundle and the injection identifier are dropped


def test_untrusted_description_and_needs():
    hits = rs.search("x", limit=20, client=_ok())
    d = hits[0].description
    assert "\x1b" not in d and "\x07" not in d and "\n" not in d and len(d) <= 160
    assert hits[0].needs == ["GCS_BUCKET=<value>"]
    assert hits[0].transport == "stdio"
    assert hits[4].needs == ["header Authorization"]


def test_limit():
    assert len(rs.search("x", limit=2, client=_ok())) == 2


@pytest.mark.parametrize("handler,word", [
    (lambda r: httpx.Response(429), "429"),
    (lambda r: httpx.Response(500), "500"),
    (lambda r: httpx.Response(200, text="<html>"), "not JSON"),
])
def test_bad_answers_raise_one_line(handler, word):
    with pytest.raises(rs.RegistryError, match=word):
        rs.search("x", client=_client(handler))


def _raise(exc):
    def handler(req):
        raise exc
    return handler


def test_offline_and_timeout():
    with pytest.raises(rs.RegistryError, match="unreachable"):
        rs.search("x", client=_client(_raise(httpx.ConnectError("no route"))))
    with pytest.raises(rs.RegistryError, match="timed out"):
        rs.search("x", client=_client(_raise(httpx.ReadTimeout("slow"))))


def test_builtin_offline_prints_one_line(capsys):
    def boom(q):
        raise rs.RegistryError("MCP Registry unreachable (offline?)")
    assert mcp_search.handle_mcp_search(" slack", search=boom)
    assert capsys.readouterr().out.strip() == "mcp search: MCP Registry unreachable (offline?)"


def test_builtin_table_and_add(capsys, monkeypatch):
    from sable.app.builtins import mcp
    monkeypatch.setenv("COLUMNS", "300")
    added = []
    monkeypatch.setattr(mcp, "handle_mcp", lambda a: added.append(a) or True)
    hits = rs.search("x", limit=20, client=_ok())
    mcp_search.handle_mcp_search("slack", search=lambda q: hits)
    assert "remote-filesystem" in capsys.readouterr().out
    mcp_search.handle_mcp_search("slack --add 2", search=lambda q: hits, confirm=lambda: False)
    assert "/mcp add slack uvx mcparmory-slack" in capsys.readouterr().out and added == []
    mcp_search.handle_mcp_search("slack --add 2", search=lambda q: hits, confirm=lambda: True)
    assert added == ["add slack uvx mcparmory-slack"]


def test_dispatch_routes_search(monkeypatch):
    from sable.app.builtins import dispatch
    got = []
    monkeypatch.setattr(mcp_search, "handle_mcp_search", lambda a: got.append(a) or True)
    assert dispatch.handle_builtin("/mcp search fs", None, "s", None)
    assert got == [" fs"]
