"""Tasks panel — runs in the bottom tmux pane (30% height).

Polls the tasks table every 3 seconds and renders a live table using Rich.
Shows: status symbol, name, step count, goal (truncated), cumulative cost.
Handles missing table gracefully (first launch before DB init).
"""
from __future__ import annotations

import os
import sqlite3
import time

os.environ["PROMPT_TOOLKIT_NO_CPR"] = "1"

from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.text import Text

from shell.telemetry.db import DB_PATH

console = Console()

_STATUS_SYMBOLS = {
    "running":   ("●", "bright_green"),
    "done":      ("✓", "green"),
    "completed": ("✓", "green"),
    "paused":    ("⏸", "yellow"),
    "lost":      ("✗", "red"),
    "starting":  ("○", "dim white"),
}


def _build_table(conn: sqlite3.Connection) -> Table:
    table = Table(
        box=None,
        show_header=True,
        header_style="bold dim",
        padding=(0, 1),
        expand=True,
    )
    table.add_column("", width=2, no_wrap=True)          # status symbol
    table.add_column("task", min_width=10, no_wrap=True)
    table.add_column("step", width=5, no_wrap=True, justify="right")
    table.add_column("goal", ratio=1)
    table.add_column("cost", width=8, no_wrap=True, justify="right")

    try:
        rows = conn.execute(
            """SELECT t.name, t.status, t.step_count, t.goal,
                      COALESCE(SUM(e.cost_usd), 0.0) as cost
               FROM tasks t
               LEFT JOIN task_events e ON e.task_name = t.name
               GROUP BY t.name
               ORDER BY t.id DESC
               LIMIT 12"""
        ).fetchall()
    except sqlite3.OperationalError:
        table.add_row("", "[dim]no tasks yet[/dim]", "", "", "")
        return table

    if not rows:
        table.add_row("", "[dim]no tasks yet[/dim]", "", "", "")
        return table

    for name, status, step_count, goal, cost in rows:
        sym, color = _STATUS_SYMBOLS.get(status, ("?", "white"))
        goal_short = (goal or "")[:60] + ("…" if len(goal or "") > 60 else "")
        cost_str = f"${cost:.4f}" if cost else "$0.0000"
        table.add_row(
            f"[{color}]{sym}[/{color}]",
            f"[bold]{name}[/bold]",
            str(step_count or 0),
            f"[dim]{goal_short}[/dim]",
            f"[dim]{cost_str}[/dim]",
        )

    return table


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")

    with Live(console=console, refresh_per_second=1, screen=False) as live:
        while True:
            live.update(_build_table(conn))
            time.sleep(3)


if __name__ == "__main__":
    main()
