"""Run an approved plan headless, under policy (Phase 5, E1).

A job runs its approved commands verbatim; no model is called at run time
(plan section 0.1). An `allow` step runs, a `confirm` step is queued to the
inbox and the run waits there, a `never` step fails the run.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Callable

from sable.agents import runtime
from sable.policy import queue
from sable.policy.engine import decide
from sable.policy.tiers import Tier

_CREATE = """
CREATE TABLE IF NOT EXISTS job_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job         TEXT NOT NULL,
    started_at  REAL NOT NULL,
    finished_at REAL,
    status      TEXT NOT NULL,   -- running | ok | failed | waiting | lost
    step        INTEGER NOT NULL DEFAULT 0,
    steps_json  TEXT NOT NULL,
    cwd         TEXT NOT NULL,
    output      TEXT NOT NULL DEFAULT ''
)
"""

Run = Callable[..., str]


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE)
    conn.commit()


def _agent(job: str) -> str:
    return f"daemon:{job}"


def _audit(cwd: str, command: str) -> None:
    from sable.core.audit import write_command
    write_command("daemon", cwd, command)


def _publish(kind: str, payload: dict) -> None:
    from sable.core.events.bus import EventBus
    try:
        with EventBus() as bus:
            bus.publish("daemon", kind, payload)
    except sqlite3.Error:
        pass


def _set(conn, rid: int, **cols) -> None:
    keys = ", ".join(f"{k} = ?" for k in cols)
    conn.execute(f"UPDATE job_runs SET {keys} WHERE id = ?", (*cols.values(), rid))
    conn.commit()


def _continue(conn, rid: int, job: str, steps: list[str], start: int, cwd: str, run: Run,
              output: str = "", approved: int | None = None) -> str:
    _set(conn, rid, status="running")
    for i in range(start, len(steps)):
        step = steps[i]
        d = decide(step)
        if d.tier is Tier.DENY:
            output += f"refused by policy: {step} ({d.why})\n"
            return _finish(conn, rid, job, "failed", i, output)
        if d.tier is not Tier.ALLOW and i != approved and not queue.take_approved(conn, _agent(job), step):
            queue.enqueue(conn, _agent(job), step, d)
            _set(conn, rid, status="waiting", step=i, output=output)
            _publish("job.queued", {"job": job, "run": rid, "command": step})
            return "waiting"
        _audit(cwd, step)
        out = run(step, cwd, prefix="daemon_")
        output += f"$ {step}\n{out}\n"
        if runtime.failed_output(out):
            return _finish(conn, rid, job, "failed", i, output)
    return _finish(conn, rid, job, "ok", len(steps), output)


def _finish(conn, rid: int, job: str, status: str, step: int, output: str) -> str:
    _set(conn, rid, status=status, step=step, output=output[-8000:], finished_at=time.time())
    _publish("job.finished", {"job": job, "run": rid, "status": status})
    return status


def run_plan(conn: sqlite3.Connection, job: str, steps: list[str], *, cwd: str,
             run: Run = runtime.run_command) -> int:
    """Start one run of `steps` and return its `job_runs` id."""
    ensure_table(conn)
    cur = conn.execute(
        "INSERT INTO job_runs (job, started_at, status, steps_json, cwd) VALUES (?, ?, 'running', ?, ?)",
        (job, time.time(), json.dumps(steps), cwd),
    )
    conn.commit()
    _publish("job.started", {"job": job, "run": cur.lastrowid})
    _continue(conn, cur.lastrowid, job, steps, 0, cwd, run)
    return cur.lastrowid


def start_waiting(conn: sqlite3.Connection, job: str, steps: list[str], *, cwd: str) -> int:
    """A run that waits for a human before its first step, whatever its tier.
    Approving it in /inbox lets the next tick run the plan, still under policy."""
    ensure_table(conn)
    cur = conn.execute(
        "INSERT INTO job_runs (job, started_at, status, steps_json, cwd) VALUES (?, ?, 'waiting', ?, ?)",
        (job, time.time(), json.dumps(steps), cwd),
    )
    conn.commit()
    queue.enqueue(conn, _agent(job), steps[0], decide(steps[0]))
    _publish("job.queued", {"job": job, "run": cur.lastrowid, "command": steps[0]})
    return cur.lastrowid


def resume_waiting(conn: sqlite3.Connection, *, cwd: str | None = None,
                   run: Run = runtime.run_command) -> None:
    """Resume each waiting run whose queued step has been approved (or rejected)."""
    ensure_table(conn)
    queue.ensure_table(conn)
    rows = conn.execute(
        "SELECT id, job, step, steps_json, cwd, output FROM job_runs WHERE status = 'waiting'"
    ).fetchall()
    for rid, job, step, steps_json, run_cwd, output in rows:
        steps = json.loads(steps_json)
        status = conn.execute(
            "SELECT status FROM policy_queue WHERE agent = ? AND command = ? ORDER BY id DESC LIMIT 1",
            (_agent(job), steps[step]),
        ).fetchone()
        if status is None or status[0] == "pending":
            continue
        if status[0] == "rejected":
            _finish(conn, rid, job, "failed", step, output + f"rejected: {steps[step]}\n")
            continue
        # Consume the approval here, so it covers exactly this step once and
        # never lingers for a later identical command.
        queue.take_approved(conn, _agent(job), steps[step])
        _continue(conn, rid, job, steps, step, cwd or run_cwd, run, output, approved=step)


def mark_lost(conn: sqlite3.Connection) -> int:
    """Runs a dead daemon left `running` can never finish; say so."""
    ensure_table(conn)
    cur = conn.execute("UPDATE job_runs SET status = 'lost', finished_at = ? WHERE status = 'running'",
                       (time.time(),))
    conn.commit()
    return cur.rowcount
