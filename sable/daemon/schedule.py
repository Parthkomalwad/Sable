"""Scheduled plans (Phase 5, E2).

A row is an approved plan and a cron expression. Once per minute the daemon
runs each due, unpaused row through `jobs.run_plan`, verbatim and under
policy; no model is called at run time (plan section 0.1).
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime

from sable.daemon import cron, jobs, service

_CREATE = """
CREATE TABLE IF NOT EXISTS schedules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    cron            TEXT NOT NULL,
    steps_json      TEXT NOT NULL,
    summary         TEXT NOT NULL,
    cwd             TEXT NOT NULL,
    paused          INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    last_run_minute TEXT
)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE)
    conn.commit()


def job_name(sid: int) -> str:
    return f"schedule-{sid}"


def add(conn, expr: str, steps: list[str], summary: str, cwd: str) -> int:
    cron.parse(expr)
    ensure_table(conn)
    cur = conn.execute(
        "INSERT INTO schedules (name, cron, steps_json, summary, cwd, created_at) VALUES ('', ?, ?, ?, ?, ?)",
        (expr, json.dumps(steps), summary, cwd, time.time()))
    conn.execute("UPDATE schedules SET name = ? WHERE id = ?", (job_name(cur.lastrowid), cur.lastrowid))
    conn.commit()
    return cur.lastrowid


def list_all(conn) -> list[dict]:
    ensure_table(conn)
    cur = conn.execute("SELECT * FROM schedules ORDER BY id")
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur]
    for r in rows:
        r["steps"] = json.loads(r["steps_json"])
    return rows


def get(conn, sid: int) -> dict | None:
    return next((r for r in list_all(conn) if r["id"] == sid), None)


def set_paused(conn, sid: int, paused: bool) -> bool:
    ensure_table(conn)
    cur = conn.execute("UPDATE schedules SET paused = ? WHERE id = ?", (int(paused), sid))
    conn.commit()
    return cur.rowcount > 0


def remove(conn, sid: int) -> bool:
    ensure_table(conn)
    cur = conn.execute("DELETE FROM schedules WHERE id = ?", (sid,))
    conn.commit()
    return cur.rowcount > 0


def _busy(conn, job: str) -> bool:
    jobs.ensure_table(conn)
    row = conn.execute("SELECT status FROM job_runs WHERE job = ? ORDER BY id DESC LIMIT 1",
                       (job,)).fetchone()
    return row is not None and row[0] in ("running", "waiting")


def run_now(conn, sid: int) -> int | None:
    """Start one run now, in this process; None if no such schedule or a run is in flight."""
    row = get(conn, sid)
    if row is None or _busy(conn, row["name"]):
        return None
    return jobs.run_plan(conn, row["name"], row["steps"], cwd=row["cwd"])


def due(conn, now: datetime) -> None:
    """Run each due, unpaused row at most once per minute, never on top of
    its own previous run."""
    minute = now.strftime("%Y-%m-%d %H:%M")
    for row in list_all(conn):
        if row["paused"] or row["last_run_minute"] == minute or not cron.matches(row["cron"], now):
            continue
        conn.execute("UPDATE schedules SET last_run_minute = ? WHERE id = ?", (minute, row["id"]))
        conn.commit()
        if not _busy(conn, row["name"]):
            jobs.run_plan(conn, row["name"], row["steps"], cwd=row["cwd"], background=True)


@service.register
def schedule_tick(conn: sqlite3.Connection) -> None:
    due(conn, datetime.now())
