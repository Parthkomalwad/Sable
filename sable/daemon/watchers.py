"""Watchers: disk, file, log, http (Phase 5, E3).

Each check is a pure function over injected readers. A watcher fires once per
state change, not every tick: a full disk fires when it crosses the line and
again only after it has dropped back under. Tiers: `notify` publishes
`watch.fired`, `run` starts its approved plan through `jobs.run_plan` (so
policy still gates each step), `approve` queues its steps to the inbox.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import time
from types import SimpleNamespace

from sable.daemon import jobs, service
from sable.policy import queue
from sable.policy.engine import decide

KINDS = ("disk", "file", "log", "http")
TIERS = ("notify", "run", "approve")
EVERY_S = 30.0

_CREATE = """
CREATE TABLE IF NOT EXISTS watchers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    target      TEXT NOT NULL,
    arg         TEXT NOT NULL DEFAULT '',
    tier        TEXT NOT NULL DEFAULT 'notify',
    steps_json  TEXT NOT NULL DEFAULT '[]',
    last_state  TEXT,
    offset      INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
)
"""


def _http_status(url: str):
    import httpx
    try:
        return httpx.get(url, timeout=httpx.Timeout(30.0), follow_redirects=False).status_code
    except httpx.HTTPError as exc:
        return type(exc).__name__


def _mtime(path: str):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def _size(path: str) -> int:
    try:
        return os.stat(path).st_size
    except OSError:
        return 0


def _read(path: str, offset: int) -> bytes:
    # ponytail: reads everything new in one go; cap it if a log can grow
    # by gigabytes inside 30 s.
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            return fh.read()
    except OSError:
        return b""


READERS = SimpleNamespace(disk=shutil.disk_usage, mtime=_mtime, size=_size, read=_read,
                          http=_http_status)


def validate(kind: str, target: str, arg: str) -> None:
    """Raise ValueError naming what is wrong with a watcher definition."""
    from sable.agents.runtime import _local_url
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if kind == "disk":
        try:
            pct = float(arg)
        except ValueError:
            raise ValueError("disk needs a percentage, e.g. /watch add disk / 90") from None
        if not 0 < pct <= 100:
            raise ValueError("disk percentage must be between 0 and 100")
    elif kind == "log":
        try:
            re.compile(arg)
        except re.error as exc:
            raise ValueError(f"bad regex: {exc}") from None
    elif kind == "http":
        if not _local_url(target):
            raise ValueError("http watchers are limited to localhost/127.0.0.1 URLs")
        if not arg.isdigit():
            raise ValueError("http needs an expected status, e.g. 200")


def check(kind: str, target: str, arg: str, last: str | None, offset: int,
          r=READERS) -> tuple[str, int, str | None]:
    """(new state, new offset, what fired or None)."""
    if kind == "disk":
        total, used, _ = r.disk(target)
        pct = used * 100 / total if total else 0
        state = "over" if pct >= float(arg) else "ok"
        fired = f"{target} is {pct:.0f}% full (limit {arg}%)" if state == "over" and last != "over" else None
        return state, offset, fired
    if kind == "file":
        state = str(r.mtime(target))
        fired = f"{target} changed" if last is not None and state != last else None
        return state, offset, fired
    if kind == "log":
        size = r.size(target)
        if last is None:
            return "seen", size, None  # tail from now, not from the start of the file
        if size < offset:
            offset = 0  # truncated or rotated
        new = r.read(target, offset)
        pattern = re.compile(arg)
        hit = next((ln for ln in new.decode("utf-8", "replace").splitlines() if pattern.search(ln)), None)
        return "seen", offset + len(new), (f"{target}: {hit.strip()[:200]}" if hit else None)
    if kind == "http":
        got = r.http(target)
        state = "ok" if str(got) == arg else f"got {got}"
        fired = f"{target} returned {got}, expected {arg}" if state != "ok" and state != last else None
        return state, offset, fired
    raise ValueError(f"unknown watcher kind {kind!r}")


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE)
    conn.commit()


def add(conn: sqlite3.Connection, kind: str, target: str, arg: str, *,
        tier: str = "notify", steps: list[str] | None = None) -> int:
    validate(kind, target, arg)
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {', '.join(TIERS)}")
    if tier != "notify" and not steps:
        raise ValueError(f"tier {tier} needs a plan: --do \"<cmd>; <cmd>\"")
    ensure_table(conn)
    cur = conn.execute(
        "INSERT INTO watchers (kind, target, arg, tier, steps_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (kind, target, arg, tier, json.dumps(steps or []), time.time()),
    )
    conn.commit()
    return cur.lastrowid


_COLS = ("id", "kind", "target", "arg", "tier", "steps_json", "last_state", "offset")


def list_all(conn: sqlite3.Connection) -> list[dict]:
    ensure_table(conn)
    rows = conn.execute(f"SELECT {', '.join(_COLS)} FROM watchers ORDER BY id").fetchall()
    out = [dict(zip(_COLS, row)) for row in rows]
    for w in out:
        w["steps"] = json.loads(w.pop("steps_json"))
    return out


def remove(conn: sqlite3.Connection, wid: int) -> bool:
    ensure_table(conn)
    cur = conn.execute("DELETE FROM watchers WHERE id = ?", (wid,))
    conn.commit()
    _checked.pop(wid, None)
    return cur.rowcount > 0


def _publish(kind: str, payload: dict) -> None:
    jobs._publish(kind, payload)


def _fire(conn: sqlite3.Connection, w: dict, detail: str) -> None:
    _publish("watch.fired", {"watcher": w["id"], "kind": w["kind"], "tier": w["tier"], "detail": detail})
    agent = f"watch:{w['id']}"
    if w["tier"] == "run":
        jobs.run_plan(conn, agent, w["steps"], cwd=os.path.expanduser("~"))
    elif w["tier"] == "approve":
        for step in w["steps"]:
            queue.enqueue(conn, agent, step, decide(step))


_checked: dict[int, float] = {}


def run_due(conn: sqlite3.Connection, *, now: float | None = None, readers=READERS) -> None:
    """Check each watcher at most every EVERY_S seconds; fire on a state change."""
    now = time.time() if now is None else now
    for w in list_all(conn):
        if now - _checked.get(w["id"], float("-inf")) < EVERY_S:
            continue
        _checked[w["id"]] = now
        state, offset, fired = check(w["kind"], w["target"], w["arg"], w["last_state"], w["offset"], readers)
        conn.execute("UPDATE watchers SET last_state = ?, offset = ? WHERE id = ?", (state, offset, w["id"]))
        conn.commit()
        if fired:
            _fire(conn, w, fired)


@service.register
def watch_tick(conn: sqlite3.Connection) -> None:
    run_due(conn)
