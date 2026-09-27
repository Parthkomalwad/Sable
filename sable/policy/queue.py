"""The approval queue for unattended agents (Phase 3, F1).

A worker runs in a tmux window nobody reads, so it can never be prompted for
YES. A command policy tiers `confirm` is queued here instead, the user
decides with `/approve`, and an approved command runs the next time that
worker proposes it, once. `deny` is never queued: nothing can approve it.

The state lives in a table now so Phase 5's `/inbox` can read it later
without a redesign, the same precedent as Phase 2's pending skills.
"""
from __future__ import annotations

import sqlite3
import time

from sable.policy.tiers import Decision

_CREATE = """
CREATE TABLE IF NOT EXISTS policy_queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    agent      TEXT NOT NULL,
    command    TEXT NOT NULL,
    rule       TEXT,
    why        TEXT,
    status     TEXT NOT NULL DEFAULT 'pending'   -- pending | approved | rejected | used
)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE)
    conn.commit()


def enqueue(conn: sqlite3.Connection, agent: str, command: str, decision: Decision) -> int:
    """Queue a request, or return the id of the same one already pending."""
    ensure_table(conn)
    row = conn.execute(
        "SELECT id FROM policy_queue WHERE agent = ? AND command = ? AND status = 'pending'",
        (agent, command),
    ).fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO policy_queue (created_at, agent, command, rule, why) VALUES (?, ?, ?, ?, ?)",
        (time.time(), agent, command, decision.rule.name if decision.rule else None, decision.why),
    )
    conn.commit()
    return cur.lastrowid


def pending(conn: sqlite3.Connection) -> list[dict]:
    ensure_table(conn)
    rows = conn.execute(
        "SELECT id, created_at, agent, command, rule, why FROM policy_queue "
        "WHERE status = 'pending' ORDER BY id"
    ).fetchall()
    keys = ("id", "created_at", "agent", "command", "rule", "why")
    return [dict(zip(keys, r)) for r in rows]


def decide_request(conn: sqlite3.Connection, qid: int, *, approve: bool) -> bool:
    """Approve or reject a pending request. False if there is none by that id."""
    ensure_table(conn)
    cur = conn.execute(
        "UPDATE policy_queue SET status = ? WHERE id = ? AND status = 'pending'",
        ("approved" if approve else "rejected", qid),
    )
    conn.commit()
    return cur.rowcount == 1


def take_approved(conn: sqlite3.Connection, agent: str, command: str) -> bool:
    """True, once, if the user approved exactly this command for this agent."""
    ensure_table(conn)
    cur = conn.execute(
        "UPDATE policy_queue SET status = 'used' WHERE id = ("
        "  SELECT id FROM policy_queue WHERE agent = ? AND command = ? AND status = 'approved'"
        "  ORDER BY id LIMIT 1)",
        (agent, command),
    )
    conn.commit()
    return cur.rowcount == 1
