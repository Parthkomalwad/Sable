"""Incidents become runbooks (Phase 9, K9).

A goal that started from a failure signal (K2's `!` fix, a goal naming
`watcher #N`, or text like "nginx is down") and ended done with a passing
verify and at least one state-changing step is drafted into the palace
`incidents` room: symptom, checks (read-only steps), fix (state-changing
steps), verify. The structured parts ride in the fact's source, so the fix
steps are read back exactly rather than parsed out of prose.

When a watcher fires and a runbook matches it, the daemon files an offer;
`/inbox` lists it and approving runs the fix through `planner.execute_plan`
(rehearsed, policy-gated). Nothing here ever runs a step by itself.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time

from sable.memory import palace

_SIGNAL = re.compile(
    r"\bis down\b|\bfailing\b|\bfailed\b|\b502\b|\bnot responding\b|\bdisk (?:is )?full\b"
    r"|\bwatcher #\d+", re.IGNORECASE)
_WATCHER = re.compile(r"\bwatcher #(\d+)", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z0-9_]{3,}")
STRONG = 0.6  # share of an alert's words a runbook must contain to match it

_CREATE = """
CREATE TABLE IF NOT EXISTS runbook_offers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  REAL NOT NULL,
    watcher     INTEGER NOT NULL,
    fact_id     TEXT NOT NULL,
    steps_json  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
)
"""


def is_failure_signal(goal: str) -> bool:
    return bool(_SIGNAL.search(goal or ""))


def watcher_of(goal: str) -> int | None:
    m = _WATCHER.search(goal or "")
    return int(m.group(1)) if m else None


def draft(goal: str, steps: list[dict], verifies: list, source: dict, *,
          untrusted: bool = False) -> str | None:
    """File a runbook for this goal, or None when it does not qualify."""
    from sable.policy import blast

    if not is_failure_signal(goal) or not verifies:
        return None
    commands = [s["command"] for s in steps if not s["command"].startswith("plan: ")]
    for s in steps:  # a plan step is recorded as "plan: a; b"; its commands count too
        if s["command"].startswith("plan: "):
            commands += [c.strip() for c in s["command"][6:].split("; ") if c.strip()]
    checks = [c for c in commands if blast.classify(c) is blast.Level.READ_ONLY]
    fix = [c for c in commands if c not in checks]
    if not fix:
        return None
    symptom = " ".join(goal.split())[:300]
    verify = [v if isinstance(v, str) else json.dumps(v) for v in verifies]
    wid = watcher_of(goal)
    text = (f"runbook{f' for watcher #{wid}' if wid else ''}: symptom: {symptom} | "
            f"checks: {'; '.join(checks) or '(none)'} | fix: {'; '.join(fix)} | verify: {'; '.join(verify)}")
    src = dict(source, runbook={"symptom": symptom, "checks": checks, "fix": fix,
                                "verify": verify, "watcher": wid})
    return palace.remember(text, "incidents", src, tier="episodic", untrusted=untrusted)


def _book(f: palace.Fact) -> dict | None:
    return next((s["runbook"] for s in f.sources if isinstance(s, dict) and "runbook" in s), None)


def find(watcher: int, detail: str = "") -> tuple[str, list[str]] | None:
    """(fact id, fix steps) of the runbook for this watcher: one filed for its
    id, else one whose text holds most of the alert's words."""
    books = [(f, b) for f in palace.all_facts("incidents") if (b := _book(f))]
    for f, b in reversed(books):  # newest first
        if b.get("watcher") == watcher:
            return f.id, list(b["fix"])
    words = {w.lower() for w in _WORD.findall(detail or "")}
    if not words:
        return None
    for f in palace.recall(detail, "incidents", k=3):
        b = _book(f)
        have = {w.lower() for w in _WORD.findall(f.text)}
        if b and len(words & have) / len(words) >= STRONG:
            return f.id, list(b["fix"])
    return None


def offer(conn: sqlite3.Connection, watcher: int, detail: str) -> str | None:
    """File an inbox offer when a runbook matches. Returns its fact id."""
    hit = find(watcher, detail)
    if hit is None:
        return None
    conn.execute(_CREATE)
    conn.execute("INSERT INTO runbook_offers (created_at, watcher, fact_id, steps_json) VALUES (?, ?, ?, ?)",
                 (time.time(), watcher, hit[0], json.dumps(hit[1])))
    conn.commit()
    return hit[0]


def decide(conn: sqlite3.Connection, offer_id: int, approve: bool, cwd: str) -> str:
    """Approve (run the fix as a plan) or reject one offer."""
    conn.execute(_CREATE)
    row = conn.execute("SELECT fact_id, steps_json, watcher FROM runbook_offers "
                       "WHERE id = ? AND status = 'pending'", (offer_id,)).fetchone()
    if row is None:
        return f"runbook offer r{offer_id} was already decided"
    conn.execute("UPDATE runbook_offers SET status = ? WHERE id = ?",
                 ("approved" if approve else "rejected", offer_id))
    conn.commit()
    if not approve:
        return f"rejected: runbook {row[0]}"
    from sable.agents import planner

    code = planner.execute_plan(json.loads(row[1]), cwd, f"runbook {row[0]} for watcher #{row[2]}")
    return f"runbook {row[0]} finished (exit {code})"
