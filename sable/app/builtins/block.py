"""`/block` and the typed-command block wrapper (Phase 4, G2).

`run_block` wraps one typed command in a numbered header and a footer and
records it; `/block` lists, shows, copies and re-runs past blocks. The store
and formatting live in `core/blocks.py`.
"""
from __future__ import annotations

import base64
import os
import sqlite3
import sys
import time

from sable.core import blocks
from sable.ui.console import out as _out

_USAGE = "usage: /block [N show|copy|rerun]"


def run_block(line: str, cwd: str, db, session_id: str, run=None) -> tuple[int, str]:
    """Run a typed command as block N. The caller has already called gate().

    A known full-screen program gets no header, since it takes the screen
    anyway; its alt-screen output is not stored, being screen paint rather
    than text. The footer is printed after it exits in either case.
    """
    if run is None:
        from sable.core.executor import execute_bash as run
    conn = getattr(db, "_conn", None)
    number = None
    if conn is not None:
        try:
            number = blocks.start(conn, session_id, cwd, line)
        except sqlite3.Error:
            number = None
    fullscreen = blocks.is_fullscreen(line)
    if number is not None and not fullscreen:
        _out(blocks.header(number, line))
    t0 = time.monotonic()
    exit_code, output = run(line, cwd)
    ms = int((time.monotonic() - t0) * 1000)
    if blocks.uses_alt_screen(output):
        fullscreen, output = True, ""
    tag = f"#{number}" if fullscreen and number is not None else ""
    _out(blocks.DIM + tag + blocks.RESET + blocks.footer(exit_code, ms))
    if number is None:
        return exit_code, output
    try:
        from sable.policy.engine import redact_text
        blocks.finish(conn, number, exit_code, ms, output, redact=redact_text)
    except sqlite3.Error:
        pass
    return exit_code, output


def _copy(command: str) -> None:
    """OSC 52 puts it on the terminal's clipboard (works over SSH); also print it.

    `ui/clipboard/` is Sable's snippet store, not the system clipboard, so it
    is not used here.
    """
    b64 = base64.b64encode(command.encode()).decode()
    sys.stdout.write(f"\x1b]52;c;{b64}\x07")
    _out(command)


def handle_block(argument: str, db, session_id: str) -> bool:
    """`/block`, `/block N show|copy|rerun`. Always True."""
    conn = getattr(db, "_conn", None)
    if conn is None:
        _out("blocks need the session database, which is unavailable")
        return True
    parts = argument.split()
    if not parts:
        for r in reversed(blocks.recent(conn)):
            code = "..." if r["exit_code"] is None else r["exit_code"]
            _out(f"  #{r['id']:<5} {str(code):>3}  {r['command']}")
        return True
    if len(parts) != 2 or not parts[0].isdigit() or parts[1] not in ("show", "copy", "rerun"):
        _out(_USAGE)
        return True
    row = blocks.get(conn, int(parts[0]))
    if row is None:
        _out(f"no block #{parts[0]}")
        return True
    action = parts[1]
    if action == "show":
        _out(blocks.header(row["id"], row["command"]))
        if row["output"]:
            _out(row["output"])
        _out(blocks.footer(row["exit_code"], row["duration_ms"] or 0, row["cost_usd"]))
    elif action == "copy":
        _copy(row["command"])
    else:
        _rerun(row, db, session_id)
    return True


def _rerun(row: dict, db, session_id: str) -> None:
    """Re-run through gate(), in the block's own cwd (the command may use
    relative paths); the current cwd if that directory is gone."""
    from sable.core import audit
    from sable.policy.engine import gate

    command = row["command"]
    if not gate(command, role="user"):
        _out(f"not run: {command}")
        return
    cwd = row["cwd"] if row["cwd"] and os.path.isdir(row["cwd"]) else os.getcwd()
    exit_code, _ = run_block(command, cwd, db, session_id)
    audit.finish(exit_code)
