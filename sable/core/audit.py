"""The audit ledger: what ran, when, for whom.

Extracted from `app/repl.py` in Phase 0.5 step 3 (docs/structure.md §5).
`agents/orchestrator.py` imported `_write_audit_log` from the REPL, which
made `agents` depend on `app` and violated the layering rule. Writing a line
to a log file needs nothing from the REPL, so it belongs in `core/`.

Two formats are written, both pre-existing and both kept as they are:

  - `write_command()` -> AUDIT_LOG_PATH, tab separated, consumed by the skills
    PatternWatcher, which parses it to find repeated command clusters.
    Changing this format silently breaks skill crystallisation.
  - `write_action()` -> /var/log/agentic-shell/audit.log, key=value, the
    system-wide ledger.

Both swallow permission errors: an unwritable audit log must never take the
shell down.

Phase 3 (F4) adds a third, the provenance ledger: `record()` writes one
`audit_ledger` row per `policy.engine.gate()` decision (who: uid, agent,
model; why: goal, tier, rule; what: command, cwd; outcome), and `finish()`
fills in exit code and duration once the call site has run the command.
`core` sits below `policy`, so the secret redactor is passed in, the same
inversion `core/events/replay.py` uses. Writing never raises.
"""
from __future__ import annotations

import datetime
import getpass
import os
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

#: (row id, monotonic start) of the last `record()` in this process.
# ponytail: one slot per process. Fine because gate() and the run that follows
# it are sequential in the REPL, the orchestrator and each worker process;
# return the id from gate() if commands ever run concurrently in one process.
_last: tuple[int, float] | None = None

_COLUMNS = ("id", "ts", "uid", "agent", "role", "model", "goal", "tier", "rule",
            "why", "command", "cwd", "outcome", "exit_code", "duration_ms")


def write_command(session_id: str, cwd: str, command: str) -> None:
    """Append one command to the session audit log.

    Format: ISO8601 \t session_id \t cwd \t command

    Parsed by `skills/watcher.py`. Keep the field order and the tab
    separator, or pattern detection stops finding anything.
    """
    from sable.core.db import AUDIT_LOG_PATH

    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    line = f"{stamp}\t{session_id}\t{cwd}\t{command}\n"
    try:
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(line)
    except (PermissionError, OSError):
        pass  # an unwritable audit log is not worth failing a command over


def write_action(action: str, command: str, exit_code: int | None = None) -> None:
    """Append one action to the system-wide ledger.

    Format: ISO8601 user=… action=… cmd=… exit=…
    """
    log_path = "/var/log/agentic-shell/audit.log"
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        # getuser() raises when there is no passwd entry for the uid, which
        # happens in containers run with --user.
        user = "unknown"
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    line = f"{stamp} user={user} action={action} cmd={command!r} exit={exit_code}\n"
    try:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(line)
    except (PermissionError, OSError):
        pass


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    from sable.core.db import _CREATE_AUDIT_LEDGER, DB_PATH

    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(_CREATE_AUDIT_LEDGER)
    return conn


def record(
    command: str,
    *,
    agent: str,
    outcome: str,
    role: str | None = None,
    model: str | None = None,
    goal: str | None = None,
    tier: str | None = None,
    rule: str | None = None,
    why: str | None = None,
    cwd: str | None = None,
    redact: Callable[[str], str] | None = None,
    db_path: str | Path | None = None,
) -> None:
    """Append one ledger row. `redact` runs over command and goal first."""
    global _last
    scrub = redact or (lambda text: text)
    try:
        cwd = cwd if cwd is not None else os.getcwd()
    except OSError:
        cwd = None
    row = (
        datetime.datetime.now(datetime.timezone.utc).isoformat(),
        os.getuid() if hasattr(os, "getuid") else None,
        agent, role, model, scrub(goal) if goal else goal, tier, rule, why,
        scrub(command), cwd, outcome,
    )
    try:
        conn = _connect(db_path)
        try:
            cur = conn.execute(
                "INSERT INTO audit_ledger (ts, uid, agent, role, model, goal, tier, "
                "rule, why, command, cwd, outcome) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                row,
            )
            conn.commit()
            _last = (cur.lastrowid, time.monotonic())
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        _last = None  # an unwritable ledger must not stop a command


def finish(exit_code: int | None = None, db_path: str | Path | None = None) -> None:
    """Stamp exit code and duration on the row the last `record()` wrote."""
    global _last
    if _last is None:
        return
    row_id, started = _last
    _last = None
    try:
        conn = _connect(db_path)
        try:
            conn.execute(
                "UPDATE audit_ledger SET exit_code = ?, duration_ms = ? WHERE id = ?",
                (exit_code, int((time.monotonic() - started) * 1000), row_id),
            )
            conn.commit()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        pass


def query(
    since_seconds: int | None = None,
    agent: str | None = None,
    limit: int = 500,
    db_path: str | Path | None = None,
) -> list[dict]:
    """Ledger rows, oldest first, optionally filtered. Backs `/audit`."""
    sql, params = f"SELECT {', '.join(_COLUMNS)} FROM audit_ledger WHERE 1=1", []
    if since_seconds is not None:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=since_seconds)
        sql += " AND ts >= ?"
        params.append(cutoff.isoformat())
    if agent is not None:
        sql += " AND agent = ?"
        params.append(agent)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    conn = _connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(zip(_COLUMNS, row)) for row in reversed(rows)]
