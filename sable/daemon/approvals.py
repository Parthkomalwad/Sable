"""Daemon pushes and single-use phone approvals (Phase 5, E5).

Every tick: push new `job.finished` and `watch.fired` bus events, push each
new pending `policy_queue` item with Approve / Reject buttons, and poll the
reply topic (outbound GET, no open port). A reply `yes|no <qid> <token>`
decides that one item only if the token matches that qid, is unused and
under an hour old, the item is still pending and policy does not now rate
it deny. The token is burned on use. Every outcome is audited and
published as `approval.remote` with its reason.

Phase 9 (K12) reuses the same tokens for `sable share --approve-only`: a
token row may carry a `share_id` and the `approver` name. A token only
answers on the reply topic it was issued for (the owner's, or that share's),
only while the share is active, and its audit line names the approver.
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time

import httpx

from sable.daemon import notify
from sable.daemon.service import register
from sable.policy import queue
from sable.policy.engine import decide
from sable.policy.tiers import Tier

TOKEN_TTL_S = 3600
#: Reply polls are every 30 s, not every 5 s tick, to stay inside public
#: ntfy.sh rate limits; an approval tap lands within half a minute.
POLL_EVERY_S = 30.0
_last_poll = 0.0
_REPLY = re.compile(r"(yes|no) ([0-9]{1,18}) ([A-Za-z0-9_-]{1,64})")
_CREATE = (
    """CREATE TABLE IF NOT EXISTS approval_tokens (
        token      TEXT PRIMARY KEY,
        qid        INTEGER NOT NULL,
        created_at REAL NOT NULL,
        used_at    REAL,
        share_id   TEXT,
        approver   TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS shares (
        id          TEXT PRIMARY KEY,
        mode        TEXT NOT NULL,              -- approve | read
        name        TEXT,
        topic       TEXT NOT NULL,
        reply_topic TEXT,
        created_at  REAL NOT NULL,
        expires_at  REAL NOT NULL,
        status      TEXT NOT NULL DEFAULT 'active'   -- active | stopped | expired
    )""",
    "CREATE TABLE IF NOT EXISTS notify_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
)


def ensure_tables(conn: sqlite3.Connection) -> None:
    queue.ensure_table(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(approval_tokens)")}
    if cols and "share_id" not in cols:
        # The Phase 5 table keyed tokens by qid, one per item. Tokens live an
        # hour at most, so dropping it costs only buttons already on a phone.
        conn.execute("DROP TABLE approval_tokens")
    for sql in _CREATE:
        conn.execute(sql)
    conn.commit()


def _get(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM notify_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _put(conn, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO notify_state (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()


def _audit(reason: str, qid: int | None, who: str = "") -> None:
    from sable.core.audit import write_action
    write_action("approval.remote", f"{reason} #{qid}{who}")


def _publish(payload: dict) -> None:
    from sable.core.events.bus import EventBus
    try:
        with EventBus() as bus:
            bus.publish("daemon", "approval.remote", payload)
    except sqlite3.Error:
        pass


def issue(conn: sqlite3.Connection, qid: int, share_id: str | None = None,
          approver: str | None = None) -> str:
    """A fresh single-use token for `qid` (and `share_id`), valid TOKEN_TTL_S."""
    token = secrets.token_urlsafe(16)
    conn.execute("INSERT INTO approval_tokens (token, qid, created_at, share_id, approver) "
                 "VALUES (?, ?, ?, ?, ?)", (token, qid, time.time(), share_id, approver))
    conn.commit()
    return token


def _check(conn, text: str, share_id: str | None = None) -> tuple[bool, str, int | None]:
    m = _REPLY.fullmatch(text.strip())
    if not m:
        return False, "malformed", None
    word, qid, given = m.group(1), int(m.group(2)), m.group(3)
    rows = conn.execute("SELECT token, created_at, used_at, share_id FROM approval_tokens "
                        "WHERE qid = ?", (qid,)).fetchall()
    if not rows:
        return False, "unknown", qid
    match = None
    for row in rows:  # every row compared, each in constant time
        if secrets.compare_digest(row[0].encode(), given.encode()):
            match = row
    if match is None:
        return False, "bad token", qid
    token, created_at, used_at, row_share = match
    if row_share != share_id:
        # A share's token never answers on the owner's topic, nor on another share's.
        return False, "wrong share", qid
    if share_id is not None:
        live = conn.execute("SELECT 1 FROM shares WHERE id = ? AND status = 'active' "
                            "AND expires_at > ?", (share_id, time.time())).fetchone()
        if live is None:
            return False, "share ended", qid
    if used_at is not None:
        return False, "replay", qid
    if time.time() - created_at > TOKEN_TTL_S:
        return False, "expired", qid
    item = conn.execute("SELECT command, status FROM policy_queue WHERE id = ?", (qid,)).fetchone()
    if item is None or item[1] != "pending":
        return False, "not pending", qid
    if decide(item[0]).tier is Tier.DENY:
        return False, "never tier", qid
    # Burn first, conditionally, so two replies racing cannot both pass.
    if conn.execute("UPDATE approval_tokens SET used_at = ? WHERE token = ? AND used_at IS NULL",
                    (time.time(), token)).rowcount != 1:
        conn.commit()
        return False, "replay", qid
    conn.commit()
    approve = word == "yes"
    if not queue.decide_request(conn, qid, approve=approve):
        return False, "not pending", qid
    return True, "approved" if approve else "rejected", qid


def handle_reply(conn: sqlite3.Connection, text: str,
                 share_id: str | None = None) -> tuple[bool, str]:
    ok, reason, qid = _check(conn, text, share_id)
    if share_id is None:
        _audit(reason, qid)
        _publish({"ok": ok, "reason": reason, "qid": qid})
        return ok, reason
    row = conn.execute("SELECT name FROM shares WHERE id = ?", (share_id,)).fetchone()
    approver = (row[0] if row else None) or "unknown"
    _audit(reason, qid, f" approver={approver} share={share_id}")
    _publish({"ok": ok, "reason": reason, "qid": qid, "approver": approver, "share": share_id})
    return ok, reason


# ── the tick ────────────────────────────────────────────────────────────

def _events(cursor: int | None):
    """(events after cursor, new cursor). A first run starts at the head."""
    from sable.core.events.bus import EventBus
    with EventBus() as bus:
        if cursor is None:
            return [], bus.latest_id()
        evs = bus.since(cursor)
        return [e for e in evs if e.agent == "daemon"], max([cursor, *(e.id for e in evs)])


def _push_events(conn) -> None:
    cur = _get(conn, "bus_id")
    evs, head = _events(None if cur is None else int(cur))
    _put(conn, "bus_id", head)
    for e in evs:
        p = e.payload
        if e.kind == "job.finished":
            notify.send(f"job {p.get('job')} {p.get('status')}", f"run #{p.get('run')}: {p.get('status')}")
        elif e.kind == "watch.fired":
            extra = "\na runbook exists: run it from /inbox" if p.get("runbook") else ""
            notify.send("watch fired", json.dumps(p) + extra)


def _push_approvals(conn, s: dict) -> None:
    last = int(_get(conn, "queue_id") or 0)
    for item in queue.pending(conn):
        if item["id"] <= last:
            continue
        actions = []
        if s.get("reply_topic"):
            token = issue(conn, item["id"])
            url = f"{s['server']}/{s['reply_topic']}"
            for word, label in (("yes", "Approve"), ("no", "Reject")):
                # No access token in the button: anyone who can read the push
                # would get it. The reply topic takes anonymous writes; the
                # single-use token in the body is what authorises a reply.
                actions.append({"action": "http", "label": label, "url": url, "method": "POST",
                                "body": f"{word} {item['id']} {token}", "clear": True})
        if not notify.send(f"Approve #{item['id']}? ({item['agent']})",
                           f"$ {item['command']}\n{item['rule']}: {item['why']}", actions):
            return  # not marked sent: the next tick tries again, in order
        _put(conn, "queue_id", item["id"])


def _poll(conn, s: dict, topic: str | None = None, key: str = "reply_since",
          share_id: str | None = None) -> None:
    since = _get(conn, key)
    if since is None:
        _put(conn, key, int(time.time()))
        return
    r = notify._client().get(f"{s['server']}/{topic or s['reply_topic']}/json",
                             params={"poll": "1", "since": since},
                             headers=notify.auth_headers(), timeout=notify.TIMEOUT)
    r.raise_for_status()
    for line in r.text.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict) or msg.get("event") != "message":
            continue
        if isinstance(msg.get("id"), str) and re.fullmatch(r"[A-Za-z0-9]{1,32}", msg["id"]):
            _put(conn, key, msg["id"])
        handle_reply(conn, str(msg.get("message", "")), share_id)


@register
def tick(conn: sqlite3.Connection) -> None:
    s = notify.settings()
    if not s.get("topic"):
        return
    ensure_tables(conn)
    _push_events(conn)
    _push_approvals(conn, s)
    global _last_poll
    if s.get("reply_topic") and time.monotonic() - _last_poll >= POLL_EVERY_S:
        _last_poll = time.monotonic()
        try:
            _poll(conn, s)
        except httpx.HTTPError as exc:
            notify.log.warning("ntfy reply poll failed: %s", exc)
