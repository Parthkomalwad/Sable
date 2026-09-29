"""`sable share` (Phase 9, K12): let a teammate approve, or watch, via ntfy.

A share is a row in `shares`: an id, the teammate's name, a topic, a reply
topic (approve-only) and an expiry. The daemon handler below does the work,
so it carries on after `sable share` returns:

  - approve-only: every pending inbox approval, existing and new, is pushed
    to the share topic with Approve / Reject buttons. Each carries a
    single-use token from `approvals.issue`, bound to this share and qid;
    `approvals._check` accepts it only on this share's reply topic and only
    while the share is active. Deny-tier commands are never offered.
  - read-only: a plain-text `/dash` snapshot every 5 minutes, redacted.

Nothing listens on the network: replies are polled, outbound, every 30 s.
"""
from __future__ import annotations

import re
import secrets
import sqlite3
import time

import httpx

from sable.daemon import approvals, notify
from sable.daemon.service import register
from sable.policy import queue
from sable.policy.engine import decide, redact_text
from sable.policy.tiers import Tier

SNAPSHOT_EVERY_S = 300
MAX_TTL_S = 24 * 3600
_TTL = re.compile(r"([0-9]{1,5})([smh])")
_NAME = re.compile(r"[A-Za-z0-9._-]{1,40}")
_TOPIC = re.compile(r"[A-Za-z0-9_-]{1,64}")
_last_poll: dict[str, float] = {}


def parse_ttl(text: str) -> int:
    """'90s', '30m', '1h' -> seconds; at most a day, never zero."""
    m = _TTL.fullmatch(text.strip())
    if not m:
        raise ValueError(f"bad ttl {text!r}, use e.g. 30m or 1h")
    secs = int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)]
    if not 0 < secs <= MAX_TTL_S:
        raise ValueError("ttl must be between 1s and 24h")
    return secs


def create(conn: sqlite3.Connection, mode: str, ttl_s: int, name: str | None = None,
           topic: str | None = None) -> dict:
    """Start a share; `mode` is 'approve' or 'read'. Topics default to random."""
    if mode not in ("approve", "read"):
        raise ValueError(f"bad mode {mode!r}")
    if name is not None and not _NAME.fullmatch(name):
        raise ValueError("name: letters, digits, . _ - only, up to 40")
    if topic is not None and not _TOPIC.fullmatch(topic):
        raise ValueError("topic: letters, digits, _ - only, up to 64")
    approvals.ensure_tables(conn)
    now = time.time()
    sh = {"id": secrets.token_hex(4), "mode": mode, "name": name,
          "topic": topic or f"sable-share-{secrets.token_urlsafe(12)}",
          "reply_topic": f"sable-reply-{secrets.token_urlsafe(12)}" if mode == "approve" else None,
          "created_at": now, "expires_at": now + ttl_s}
    conn.execute("INSERT INTO shares (id, mode, name, topic, reply_topic, created_at, expires_at) "
                 "VALUES (:id, :mode, :name, :topic, :reply_topic, :created_at, :expires_at)", sh)
    conn.commit()
    return sh


def list_active(conn: sqlite3.Connection) -> list[dict]:
    approvals.ensure_tables(conn)
    rows = conn.execute("SELECT id, mode, name, topic, reply_topic, expires_at FROM shares "
                        "WHERE status = 'active' AND expires_at > ? ORDER BY created_at",
                        (time.time(),)).fetchall()
    keys = ("id", "mode", "name", "topic", "reply_topic", "expires_at")
    return [dict(zip(keys, r)) for r in rows]


def stop(conn: sqlite3.Connection, sid: str | None = None) -> int:
    """Stop one share, or every active one. Its tokens are dead from now on."""
    approvals.ensure_tables(conn)
    sql, args = "UPDATE shares SET status = 'stopped' WHERE status = 'active'", ()
    if sid is not None:
        sql, args = sql + " AND id = ?", (sid,)
    n = conn.execute(sql, args).rowcount
    # _check refuses a stopped share already; burning its tokens is belt and braces.
    burn = "UPDATE approval_tokens SET used_at = ? WHERE used_at IS NULL AND share_id IS NOT NULL"
    burn += " AND share_id IN (SELECT id FROM shares WHERE status = 'stopped')"
    conn.execute(burn, (time.time(),))
    conn.commit()
    return n


def _one(conn, sql: str, args: tuple = ()):
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []  # a table not created yet is an empty section


def snapshot(conn: sqlite3.Connection) -> str:
    """What /dash shows, as plain text: agents, inbox count, cost today, lanes."""
    import json

    from sable.core.db import local_day_start_utc

    lines = [f"Sable status {time.strftime('%Y-%m-%d %H:%M')}"]
    agents = _one(conn, "SELECT name, status FROM tasks ORDER BY id DESC LIMIT 10")
    lines.append("agents: " + (", ".join(f"{n} ({s})" for n, s in agents) or "none"))
    waiting = sum(r[0] for r in _one(conn, "SELECT COUNT(*) FROM policy_queue WHERE status = 'pending'"))
    waiting += sum(r[0] for r in _one(conn, "SELECT COUNT(*) FROM breaker_trips WHERE status = 'tripped'"))
    lines.append(f"inbox: {waiting} waiting")
    cost = sum(r[0] or 0.0 for r in _one(
        conn, "SELECT SUM(cost_usd) FROM token_events WHERE timestamp >= ?", (local_day_start_utc(),)))
    lines.append(f"cost today: ${cost:.4f}")
    lanes, graph = {}, None
    for (payload,) in _one(conn, "SELECT payload_json FROM agent_events WHERE kind = 'graph.lane' "
                                 "ORDER BY id DESC LIMIT 200")[::-1]:
        try:
            d = json.loads(payload or "{}")
        except ValueError:
            continue
        if d.get("graph") != graph:
            lanes, graph = {}, d.get("graph")
        lanes[d.get("id", "?")] = d.get("status", "?")
    lines.append("lanes: " + (", ".join(f"{k} {v}" for k, v in lanes.items()) or "none"))
    return redact_text("\n".join(lines))


def _push_approvals(conn, s: dict, sh: dict) -> None:
    key = f"share:{sh['id']}:queue_id"
    last = int(approvals._get(conn, key) or 0)
    url = f"{s['server']}/{sh['reply_topic']}"
    for item in queue.pending(conn):
        if item["id"] <= last:
            continue
        if decide(item["command"]).tier is Tier.DENY:
            approvals._put(conn, key, item["id"])  # never offered
            continue
        token = approvals.issue(conn, item["id"], sh["id"], sh["name"])
        actions = [{"action": "http", "label": label, "url": url, "method": "POST",
                    "body": f"{word} {item['id']} {token}", "clear": True}
                   for word, label in (("yes", "Approve"), ("no", "Reject"))]
        if not notify.send(f"Approve #{item['id']}? ({item['agent']})",
                           f"$ {item['command']}\n{item['rule']}: {item['why']}", actions,
                           topic=sh["topic"]):
            return  # retried next tick, in order
        approvals._put(conn, key, item["id"])


@register
def share_tick(conn: sqlite3.Connection) -> None:
    approvals.ensure_tables(conn)
    now = time.time()
    conn.execute("UPDATE shares SET status = 'expired' WHERE status = 'active' AND expires_at <= ?",
                 (now,))
    conn.commit()
    s = notify.settings()
    for sh in list_active(conn):
        if sh["mode"] == "read":
            key = f"share:{sh['id']}:snap"
            if now - float(approvals._get(conn, key) or 0) >= SNAPSHOT_EVERY_S:
                if notify.send("Sable status", snapshot(conn), topic=sh["topic"]):
                    approvals._put(conn, key, now)
            continue
        _push_approvals(conn, s, sh)
        if time.monotonic() - _last_poll.get(sh["id"], -1e18) >= approvals.POLL_EVERY_S:
            _last_poll[sh["id"]] = time.monotonic()
            try:
                approvals._poll(conn, s, sh["reply_topic"], f"share:{sh['id']}:since", sh["id"])
            except httpx.HTTPError as exc:
                notify.log.warning("share %s reply poll failed: %s", sh["id"], exc)
