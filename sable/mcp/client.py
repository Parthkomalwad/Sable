"""MCP client: JSON-RPC over a transport, discovery, tools/list, tools/call.

Spec notes, checked against modelcontextprotocol.io on 2026-09-28, and where
they differ from docs/plans/2026-09-28-phase-6-mcp.md section 0.2:

- 2026-07-28 is stateless. Every request carries `_meta` with
  `io.modelcontextprotocol/protocolVersion` and
  `io.modelcontextprotocol/clientCapabilities` (both required) and
  `io.modelcontextprotocol/clientInfo`. `server/discover` returns
  `supportedVersions`, `capabilities`, `instructions` and
  `_meta["io.modelcontextprotocol/serverInfo"]`.
- Fallback: the plan says fall back to `initialize` on -32601. The spec says
  fall back on ANY error that is not a recognised modern error (codes -32020
  to -32022), on a timeout (a legacy stdio server may stay silent), and on
  HTTP on a 4xx without a modern error body. We follow the spec.
- MRTR: `inputRequests` is a map keyed by server-chosen ids (not a list),
  `inputResponses` a map with the same keys, and an opaque `requestState`
  that must be echoed back unchanged on the retry. The retry uses a new id.
- An absent `resultType` means "complete" (legacy servers).
- HTTP requests carry `MCP-Protocol-Version`, `Mcp-Method` and `Mcp-Name`.
  `Mcp-Session-Id` is gone in 2026-07-28; kept only for legacy servers.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable

from sable import __version__
from sable.mcp.transports import McpError

MODERN = "2026-07-28"
LEGACY = "2025-06-18"
_MODERN_ERRORS = {-32020, -32021, -32022}
_PROBE_TIMEOUT = 5.0
RESULT_CAP = 64 * 1024
_CLIENT_INFO = {"name": "sable", "version": __version__}

__all__ = ["Client", "McpError", "ToolResult", "format_content"]


@dataclass
class ToolResult:
    text: str
    is_error: bool = False


@dataclass
class ServerInfo:
    era: str  # "modern" or "legacy"
    version: str
    capabilities: dict = field(default_factory=dict)
    server_info: dict = field(default_factory=dict)
    instructions: str = ""


def _kb(n: int) -> str:
    return f"{max(1, round(n / 1024))} KB"


def _b64_size(data: str) -> int:
    return len(data) * 3 // 4


def format_content(parts: list) -> str:
    """Text parts joined; images, audio and resources summarised, not inlined."""
    out = []
    for p in parts or []:
        if not isinstance(p, dict):
            continue
        kind = p.get("type")
        if kind == "text":
            out.append(str(p.get("text", "")))
        elif kind in ("image", "audio"):
            out.append(f"[{kind}, {_kb(_b64_size(p.get('data', '')))}]")
        elif kind == "resource":
            res = p.get("resource") or {}
            if "text" in res:
                size = len(str(res["text"]).encode("utf-8"))
            else:
                size = _b64_size(res.get("blob", ""))
            out.append(f"[resource {res.get('uri', '?')}, {_kb(size)}]")
        elif kind == "resource_link":
            out.append(f"[resource link {p.get('uri', '?')}]")
        else:
            out.append(f"[{kind}]")
    return "\n".join(out)


def _cap(text: str) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= RESULT_CAP:
        return text
    head = raw[:RESULT_CAP].decode("utf-8", "ignore")
    return head + f"\n[truncated: {_kb(len(raw))} total, first 64 KB shown]"


class Client:
    def __init__(self, transport):
        self.transport = transport
        self.info: ServerInfo | None = None
        self._ids = itertools.count(1)

    def _rpc(self, method: str, params: dict | None = None, timeout: float | None = None,
             capabilities: dict | None = None) -> dict:
        params = dict(params or {})
        if self.info is None or self.info.era == "modern":
            params["_meta"] = {
                "io.modelcontextprotocol/protocolVersion": MODERN,
                "io.modelcontextprotocol/clientInfo": _CLIENT_INFO,
                "io.modelcontextprotocol/clientCapabilities": capabilities or {},
            }
        msg = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params}
        reply = self.transport.request(msg, timeout=timeout)
        if "error" in reply:
            err = reply["error"] or {}
            raise McpError(f"{method}: {err.get('message', 'error')}",
                           code=err.get("code"), data=err.get("data"))
        result = reply.get("result")
        if not isinstance(result, dict):
            raise McpError(f"{method}: malformed result")
        return result

    def discover(self) -> ServerInfo:
        self.transport.protocol_version = MODERN
        try:
            r = self._rpc("server/discover", timeout=_PROBE_TIMEOUT)
        except McpError as e:
            if e.code in _MODERN_ERRORS:
                raise
            if e.code is None and e.kind not in ("timeout", "http4xx"):
                raise  # a dead server or a network failure, not a legacy answer
            return self._initialize()
        versions = r.get("supportedVersions") or []
        if MODERN not in versions:
            raise McpError(f"server speaks {', '.join(versions) or 'no version'}, not {MODERN}")
        self.info = ServerInfo(
            era="modern", version=MODERN, capabilities=r.get("capabilities") or {},
            server_info=(r.get("_meta") or {}).get("io.modelcontextprotocol/serverInfo") or {},
            instructions=r.get("instructions") or "")
        return self.info

    def _initialize(self) -> ServerInfo:
        self.info = ServerInfo(era="legacy", version=LEGACY)
        self.transport.protocol_version = None  # not sent on initialize itself
        r = self._rpc("initialize", {"protocolVersion": LEGACY, "capabilities": {},
                                     "clientInfo": _CLIENT_INFO})
        self.info.version = r.get("protocolVersion") or LEGACY
        self.info.capabilities = r.get("capabilities") or {}
        self.info.server_info = r.get("serverInfo") or {}
        self.info.instructions = r.get("instructions") or ""
        self.transport.protocol_version = self.info.version
        self.transport.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self.info

    def list_tools(self) -> list[dict]:
        if self.info is None:
            self.discover()
        tools, cursor, seen = [], None, set()
        while True:
            r = self._rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(t for t in r.get("tools") or [] if isinstance(t, dict))
            cursor = r.get("nextCursor")
            if not cursor or cursor in seen:
                return tools
            seen.add(cursor)

    def call_tool(self, name: str, args: dict | None = None,
                  on_input: Callable[[dict], dict] | None = None) -> ToolResult:
        if self.info is None:
            self.discover()
        params = {"name": name, "arguments": args or {}}
        caps = {"elicitation": {}} if on_input else {}
        r = self._rpc("tools/call", params, capabilities=caps)
        if r.get("resultType") == "input_required":
            if on_input is None:
                raise McpError(f"{name} needs input; run it from the shell")
            retry = {**params, "inputResponses": on_input(r.get("inputRequests") or {})}
            if "requestState" in r:
                retry["requestState"] = r["requestState"]
            r = self._rpc("tools/call", retry, capabilities=caps)
            if r.get("resultType") == "input_required":
                raise McpError(f"{name} asked for input again; stopping after one retry")
        if r.get("resultType", "complete") != "complete":
            raise McpError(f"{name}: unknown resultType {r.get('resultType')!r}")
        return ToolResult(text=_cap(format_content(r.get("content"))),
                          is_error=bool(r.get("isError")))

    def close(self) -> None:
        self.transport.close()
