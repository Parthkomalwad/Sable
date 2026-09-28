"""MCP transports: stdio (a protocol pipe) and Streamable HTTP.

A transport does one thing: send a JSON-RPC message and hand back the response
with the same id. It knows nothing about MCP methods beyond the HTTP headers
the spec asks for. Every failure is raised as McpError, never a hang.
"""
from __future__ import annotations

import collections
import json
import queue
import subprocess
import threading

import httpx
from httpx_sse import SSEError, connect_sse

TIMEOUT = 30.0
_STDERR_LINES = 200


class McpError(Exception):
    """Any MCP failure: a JSON-RPC error, a dead server, a timeout, bad data."""

    def __init__(self, message: str, code: int | None = None, data: object = None,
                 kind: str = "rpc"):
        super().__init__(message)
        self.code = code  # the JSON-RPC error code, when the server sent one
        self.data = data
        self.kind = kind  # rpc, timeout, dead, http4xx, http


class StdioTransport:
    """A server spawned as a child process, newline-delimited JSON on its pipes.

    Not a pty: this is a protocol pipe, not a user command.
    """

    def __init__(self, argv: list[str], env: dict | None = None, cwd: str | None = None,
                 timeout: float = TIMEOUT):
        self.timeout = timeout
        self.protocol_version: str | None = None  # unused on stdio, set by the client
        self._stderr: collections.deque[str] = collections.deque(maxlen=_STDERR_LINES)
        self._inbox: queue.Queue = queue.Queue()
        self._write_lock = threading.Lock()
        try:
            self._proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env, cwd=cwd,
            )
        except OSError as e:
            raise McpError(f"could not start {argv[0]}: {e}", kind="dead") from e
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        for raw in self._proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                self._stderr.append("[non-JSON on stdout] " + raw.decode("utf-8", "replace").rstrip())
                continue
            if not isinstance(msg, dict):
                continue
            if "method" in msg and "id" in msg:
                # A legacy server asking us something (sampling, elicitation,
                # roots). We do not serve those: say so rather than leave it waiting.
                self._send({"jsonrpc": "2.0", "id": msg["id"],
                            "error": {"code": -32601, "message": "not supported by this client"}})
                continue
            if "id" in msg:
                self._inbox.put(msg)
        self._inbox.put(None)  # EOF: the server is gone

    def _read_stderr(self) -> None:
        for raw in self._proc.stderr:
            self._stderr.append(raw.decode("utf-8", "replace").rstrip())

    def stderr_tail(self) -> list[str]:
        return list(self._stderr)

    def _dead(self) -> McpError:
        try:
            code = self._proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            code = None
        last = self._stderr[-1] if self._stderr else ""
        msg = f"server exited (code {code})" if code is not None else "server closed its output"
        return McpError(msg + (f": {last}" if last else ""), kind="dead")

    def _send(self, msg: dict) -> None:
        data = (json.dumps(msg) + "\n").encode("utf-8")
        try:
            with self._write_lock:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise self._dead() from e

    def notify(self, msg: dict) -> None:
        self._send(msg)

    def request(self, msg: dict, timeout: float | None = None) -> dict:
        self._send(msg)
        wait = self.timeout if timeout is None else timeout
        while True:
            try:
                reply = self._inbox.get(timeout=wait)
            except queue.Empty:
                raise McpError(f"no answer to {msg.get('method')} in {wait:g} s", kind="timeout") from None
            if reply is None:
                self._inbox.put(None)  # keep later calls failing fast
                raise self._dead()
            if reply.get("id") == msg["id"]:
                return reply
            # a late answer to an earlier, timed-out request: drop it

    def close(self) -> None:
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        self._proc.kill()
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


class HttpTransport:
    """Streamable HTTP: one POST per message; the answer is JSON or an SSE stream."""

    def __init__(self, url: str, headers: dict | None = None, timeout: float = TIMEOUT,
                 http: httpx.Client | None = None):
        self.url = url
        self.protocol_version: str | None = None
        self.session_id: str | None = None  # legacy (2025-06-18) servers only
        self._headers = dict(headers or {})
        self._http = http or httpx.Client(timeout=httpx.Timeout(timeout))

    def _headers_for(self, msg: dict) -> dict:
        h = {**self._headers, "Accept": "application/json, text/event-stream",
             "Content-Type": "application/json", "Mcp-Method": msg.get("method", "")}
        if self.protocol_version:
            h["MCP-Protocol-Version"] = self.protocol_version
        name = (msg.get("params") or {}).get("name") or (msg.get("params") or {}).get("uri")
        if isinstance(name, str) and name.isascii() and name.isprintable():
            h["Mcp-Name"] = name
        # ponytail: non-ASCII names skip Mcp-Name (spec wants base64), add when a server needs it
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def notify(self, msg: dict) -> None:
        try:
            r = self._http.post(self.url, json=msg, headers=self._headers_for(msg))
        except httpx.HTTPError as e:
            raise McpError(f"{self.url}: {e}", kind="http") from e
        if r.status_code >= 400:
            raise McpError(f"{self.url}: HTTP {r.status_code} on {msg.get('method')}")

    def request(self, msg: dict, timeout: float | None = None) -> dict:
        try:
            with connect_sse(self._http, "POST", self.url, json=msg,
                             headers=self._headers_for(msg)) as es:
                r = es.response
                if sid := r.headers.get("Mcp-Session-Id"):
                    self.session_id = sid
                ctype = r.headers.get("content-type", "")
                if r.status_code < 400 and ctype.startswith("text/event-stream"):
                    for ev in es.iter_sse():
                        try:
                            reply = json.loads(ev.data)
                        except ValueError:
                            continue
                        if isinstance(reply, dict) and reply.get("id") == msg["id"]:
                            return reply
                    raise McpError(f"{self.url}: stream ended without an answer to {msg.get('method')}")
                r.read()
        except httpx.TimeoutException as e:
            raise McpError(f"{self.url}: no answer to {msg.get('method')} in time", kind="timeout") from e
        except (httpx.HTTPError, SSEError) as e:
            raise McpError(f"{self.url}: {e}", kind="http") from e
        try:
            reply = r.json()
        except ValueError:
            reply = None
        if isinstance(reply, dict) and ("result" in reply or "error" in reply):
            return reply  # a 4xx with a JSON-RPC error body is still an answer
        kind = "http4xx" if 400 <= r.status_code < 500 else "http"
        raise McpError(f"{self.url}: HTTP {r.status_code} on {msg.get('method')}", kind=kind)

    def close(self) -> None:
        self._http.close()
