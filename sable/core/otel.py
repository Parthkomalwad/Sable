"""OpenTelemetry traces over OTLP/HTTP JSON (Phase 9, H4).

Off unless `otel.endpoint` is set; off means `span()` yields a throwaway dict
and no thread, queue or client exists. On, every finished span goes on a
queue and a daemon thread posts batches (5 s or 100 spans) to
`{endpoint}/v1/traces`. A failed post is dropped with at most one log line a
minute: telemetry is worth less than the goal it describes, so it never
raises into one and never blocks one.

A root span (no parent in this context) starts a new trace, so a goal is one
trace. String attributes pass through the redactor given to `configure`
(core may not import policy, the same arrangement as `events/replay.py`) and
are capped at `MAX_ATTR` characters.
"""
from __future__ import annotations

import atexit
import contextvars
import logging
import os
import queue
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import httpx

BATCH_SIZE = 100
FLUSH_S = 5.0
EXIT_WAIT_S = 2.0
MAX_ATTR = 512
_LOG_EVERY_S = 60.0
#: Keyring service prefix for `$SECRET:name`; mirrors policy/secrets.py,
#: which core may not import.
_SECRET_PREFIX = "secret:"

_log = logging.getLogger("sable.otel")
#: (trace_id, span_id) of the open span in this context, None at the root.
_current: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "sable_otel_span", default=None)
_exporter: "_Exporter | None" = None


def _identity(text: str) -> str:
    return text


def resolve_headers(headers: dict, lookup: Callable[[str], str | None] | None = None) -> dict:
    """Headers with `$SECRET:name` values read from the keyring.

    A secret that cannot be read drops that header (logged once) rather than
    sending the placeholder to a third party.
    """
    if lookup is None:
        from sable.core.config import keyring

        def lookup(service: str) -> str | None:
            try:
                return keyring.lookup(service)
            except keyring.KeyringUnavailable:
                return None
    out = {}
    for key, value in headers.items():
        if isinstance(value, str) and value.startswith("$SECRET:"):
            secret = lookup(_SECRET_PREFIX + value[len("$SECRET:"):])
            if secret is None:
                _log.warning("otel: header %s dropped, secret not found", key)
                continue
            value = secret
        out[key] = str(value)
    return out


class _Exporter:
    def __init__(self, endpoint: str, headers: dict, service_name: str,
                 redact: Callable[[str], str]) -> None:
        self.url = endpoint.rstrip("/") + "/v1/traces"
        self.headers = {"Content-Type": "application/json", **headers}
        self.service_name = service_name
        self.redact = redact
        self.queue: queue.Queue = queue.Queue()
        self.stop = threading.Event()
        self._last_log = 0.0
        self.thread = threading.Thread(target=self._loop, name="sable-otel", daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        while not self.stop.is_set():
            batch = self._take(FLUSH_S)
            if batch:
                self.post(batch)
        while rest := self._take(0):
            self.post(rest)

    def _take(self, wait: float) -> list[dict]:
        batch: list[dict] = []
        deadline = time.monotonic() + wait
        while len(batch) < BATCH_SIZE and not (wait and self.stop.is_set()):
            remaining = deadline - time.monotonic()
            try:
                batch.append(self.queue.get(timeout=remaining) if remaining > 0
                             else self.queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def post(self, spans: list[dict]) -> None:
        try:
            response = httpx.post(self.url, json=payload(spans, self.service_name),
                                  headers=self.headers, timeout=httpx.Timeout(30.0))
            response.raise_for_status()
        except httpx.HTTPError as exc:
            now = time.monotonic()
            if now - self._last_log >= _LOG_EVERY_S:
                self._last_log = now
                _log.warning("otel: %d spans dropped, %s", len(spans), exc)

    def close(self, wait: float = EXIT_WAIT_S) -> None:
        self.stop.set()
        self.thread.join(wait)


def configure(otel: dict | None, redact: Callable[[str], str] | None = None,
              lookup: Callable[[str], str | None] | None = None) -> bool:
    """Start exporting per the `otel` config block. True if on.

    Called once per process by a composition root. No endpoint, no thread.
    """
    global _exporter
    shutdown()
    otel = otel or {}
    if not otel.get("endpoint"):
        return False
    _exporter = _Exporter(otel["endpoint"], resolve_headers(otel.get("headers") or {}, lookup),
                          otel.get("service_name") or "sable", redact or _identity)
    return True


def shutdown(wait: float = EXIT_WAIT_S) -> None:
    """Flush what is queued (at most `wait` seconds) and stop exporting."""
    global _exporter
    if _exporter is not None:
        _exporter.close(wait)
        _exporter = None


atexit.register(shutdown)


def enabled() -> bool:
    return _exporter is not None


def _value(value) -> dict:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}  # int64 is a string in OTLP JSON
    if isinstance(value, float):
        return {"doubleValue": value}
    text = _exporter.redact(str(value)) if _exporter else str(value)
    return {"stringValue": text[:MAX_ATTR]}


def payload(spans: list[dict], service_name: str = "sable") -> dict:
    """The OTLP/HTTP JSON body (ExportTraceServiceRequest) for `spans`."""
    return {"resourceSpans": [{
        "resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": service_name}}]},
        "scopeSpans": [{"scope": {"name": "sable"}, "spans": spans}],
    }]}


def record(name: str, start_ns: int, end_ns: int, error: str | None = None,
           parent: tuple[str, str] | None = None, **attrs) -> None:
    """Queue one finished span under the current (or given) parent."""
    if _exporter is None:
        return
    parent = parent or _current.get()
    trace_id = parent[0] if parent else os.urandom(16).hex()
    _exporter.queue.put(_finished(name, trace_id, os.urandom(8).hex(), parent,
                                  start_ns, end_ns, error, attrs))


@contextmanager
def span(name: str, **attrs) -> Iterator[dict]:
    """Time a block as a span; the yielded dict takes attributes set inside.

    Off, this costs one dict. An exception escaping the block marks the span
    as an error and propagates unchanged.
    """
    if _exporter is None:
        yield attrs
        return
    parent = _current.get()
    trace_id = parent[0] if parent else os.urandom(16).hex()
    span_id = os.urandom(8).hex()
    token = _current.set((trace_id, span_id))
    start = time.time_ns()
    try:
        yield attrs
    finally:
        _current.reset(token)
        exc_type = sys.exc_info()[0]  # set while an exception is escaping
        error = exc_type.__name__ if exc_type else None
        if _exporter is not None:
            _exporter.queue.put(_finished(name, trace_id, span_id, parent, start, time.time_ns(), error, attrs))


def _finished(name, trace_id, span_id, parent, start, end, error, attrs) -> dict:
    span = {
        "traceId": trace_id, "spanId": span_id, "name": name, "kind": 1,
        "startTimeUnixNano": str(start), "endTimeUnixNano": str(end),
        "attributes": [{"key": k, "value": _value(v)} for k, v in attrs.items() if v is not None],
        "status": {"code": 2, "message": error[:MAX_ATTR]} if error else {"code": 1},
    }
    if parent:
        span["parentSpanId"] = parent[1]
    return span
