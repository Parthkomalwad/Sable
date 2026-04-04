"""Tasks panel — runs in tmux pane 2.

Polls the tasks table every 5 seconds and renders a single overwriting
status line using Rich. Handles missing table gracefully (first launch).
"""
from __future__ import annotations

import os
import sqlite3
import time

os.environ["PROMPT_TOOLKIT_NO_CPR"] = "1"

from rich.console import Console

from shell.telemetry.db import DB_PATH

console = Console()

_STATUS_SYMBOLS = {
    "running":   ("●", "green"),
    "done":      ("✓", "green"),
    "completed": ("✓", "green"),
    "paused":    ("⏸", "yellow"),
    "lost":      ("✗", "red"),
    "starting":  ("○", "dim white"),
}


def _render_tasks(conn: sqlite3.Connection) -> str:
    try:
        rows = conn.execute(
            "SELECT name, status FROM tasks ORDER BY id DESC LIMIT 10"
        ).fetchall()
    except sqlite3.OperationalError:
        return "[dim]no tasks[/dim]"

    if not rows:
        return "[dim]no tasks[/dim]"

    parts = []
    for name, status in rows:
        sym, color = _STATUS_SYMBOLS.get(status, ("?", "white"))
        parts.append(f"[{color}]{sym}[/{color}] {name}")
    return "  ".join(parts)


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")

    while True:
        line = _render_tasks(conn)
        console.print(f"[bold]tasks:[/bold] {line}", end="\r")
        time.sleep(5)


if __name__ == "__main__":
    main()
