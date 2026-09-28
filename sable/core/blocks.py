"""Blocks in scrollback (Phase 4, G2).

Every command the REPL runs, typed or proposed by the orchestrator, is a
numbered block: a header before its output and a footer after. The terminal
keeps native scroll, search and copy; this module only numbers, formats and
remembers them, so `/block N show|copy|rerun` works on any past block.

Numbers are global (the table's rowid), not per session, so a number printed
yesterday still names the same block today.

`core` sits below `policy` in the layering rule, so the redactor is passed in
by the caller, the same seam `core/events/replay.py` uses.
"""
from __future__ import annotations

import os
import re
import shlex
import sqlite3
import time
from typing import Callable

TAIL_LINES = 200

_CREATE = """
CREATE TABLE IF NOT EXISTS blocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  REAL NOT NULL,
    session_id  TEXT,
    cwd         TEXT,
    command     TEXT NOT NULL,
    exit_code   INTEGER,
    duration_ms INTEGER,
    cost_usd    REAL,
    output      TEXT
)
"""

# Programs that take over the screen. A header printed before them is fine,
# but they are listed so the typed path does not print one at all; anything
# else that switches to the alternate screen is caught from its output.
FULLSCREEN = frozenset({
    "vim", "vi", "nvim", "nano", "emacs", "htop", "top", "btop", "less", "more",
    "man", "ssh", "tmux", "screen", "watch",
})

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\r")

DIM = "\033[2;37m"
BOLD = "\033[1;37m"
RED = "\033[38;5;203m"
RESET = "\033[0m"


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE)
    conn.commit()


def start(conn: sqlite3.Connection, session_id: str, cwd: str, command: str) -> int:
    """Open a block and return its number, before the command runs."""
    ensure_table(conn)
    cur = conn.execute(
        "INSERT INTO blocks (created_at, session_id, cwd, command) VALUES (?, ?, ?, ?)",
        (time.time(), session_id, cwd, command),
    )
    conn.commit()
    return cur.lastrowid


def finish(
    conn: sqlite3.Connection, number: int, exit_code: int | None, duration_ms: int,
    output: str, cost_usd: float | None = None,
    redact: Callable[[str], str] = lambda t: t,
) -> None:
    """Stamp a block with how it ended and the redacted tail of its output."""
    tail = "\n".join(_ANSI.sub("", output).splitlines()[-TAIL_LINES:])
    conn.execute(
        "UPDATE blocks SET exit_code = ?, duration_ms = ?, cost_usd = ?, output = ? "
        "WHERE id = ?",
        (exit_code, duration_ms, cost_usd, redact(tail), number),
    )
    conn.commit()


_COLS = ("id", "created_at", "session_id", "cwd", "command", "exit_code",
         "duration_ms", "cost_usd", "output")


def get(conn: sqlite3.Connection, number: int) -> dict | None:
    ensure_table(conn)
    row = conn.execute(f"SELECT {', '.join(_COLS)} FROM blocks WHERE id = ?",
                       (number,)).fetchone()
    return dict(zip(_COLS, row)) if row else None


def recent(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    ensure_table(conn)
    rows = conn.execute(f"SELECT {', '.join(_COLS)} FROM blocks ORDER BY id DESC LIMIT ?",
                        (limit,)).fetchall()
    return [dict(zip(_COLS, r)) for r in rows]


def uses_alt_screen(output: str) -> bool:
    """True when the program switched to the alternate screen (vim, htop...)."""
    return "\x1b[?1049h" in output or "\x1b[?47h" in output


def is_fullscreen(command: str) -> bool:
    """True when the command's program (after sudo/env) is a known full-screen one."""
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    while words and (words[0] in ("sudo", "env", "exec") or "=" in words[0]):
        words = words[1:]
    return bool(words) and os.path.basename(words[0]) in FULLSCREEN


def header(number: int, command: str, colour: str = BOLD) -> str:
    return f"{DIM}#{number}{RESET}  {colour}{command}{RESET}"


def _duration(ms: int) -> str:
    return f"{ms}ms" if ms < 1000 else f"{ms / 1000:.1f}s"


def footer(exit_code: int | None, duration_ms: int, cost_usd: float | None = None) -> str:
    code = "exit ?" if exit_code is None else f"exit {exit_code}"
    if exit_code:
        code = f"{RED}{code}{DIM}"
    parts = [code, _duration(duration_ms)]
    if cost_usd:
        parts.append(f"${cost_usd:.4f}")
    return DIM + "  " + " · ".join(parts) + RESET
