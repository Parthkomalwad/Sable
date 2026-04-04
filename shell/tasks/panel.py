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
from rich.panel import Panel
from rich.box import SIMPLE_HEAVY, HORIZONTALS
from rich import box as rich_box

from shell.telemetry.db import DB_PATH

console = Console()

_STATUS_SYMBOLS = {
    "running":   ("●", "bright_green"),
    "done":      ("✓", "bright_green"),
    "completed": ("✓", "bright_green"),
    "paused":    ("⏸", "yellow"),
    "lost":      ("✗", "bright_red"),
    "starting":  ("○", "cyan"),
}


def _build_table(conn: sqlite3.Connection) -> Panel:
    table = Table(
        box=rich_box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold bright_white",
        border_style="bright_black",
        padding=(0, 1),
        expand=True,
        show_edge=True,
    )
    table.add_column("", width=2, no_wrap=True)
    table.add_column("TASK", min_width=12, no_wrap=True, style="bold cyan")
    table.add_column("STEPS", width=6, no_wrap=True, justify="right", style="bright_yellow")
    table.add_column("GOAL", ratio=1, style="white")
    table.add_column("COST", width=9, no_wrap=True, justify="right", style="bright_magenta")

    try:
        rows = conn.execute(
            """SELECT t.name, t.status, t.step_count, t.goal,
                      COALESCE(SUM(e.cost_usd), 0.0) as cost
               FROM tasks t
               LEFT JOIN task_events e ON e.task_name = t.name
               GROUP BY t.name
               ORDER BY t.id DESC
               LIMIT 8"""
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []

    if not rows:
        table.add_row("", "[dim]no tasks yet — use /task new <name> <goal>[/dim]", "", "", "")
    else:
        for name, status, step_count, goal, cost in rows:
            sym, color = _STATUS_SYMBOLS.get(status, ("?", "white"))
            goal_short = (goal or "")[:70] + ("…" if len(goal or "") > 70 else "")
            cost_str = f"${cost:.4f}"
            table.add_row(
                f"[{color}]{sym}[/{color}]",
                name,
                str(step_count or 0),
                goal_short,
                cost_str,
            )

    return Panel(
        table,
        title="[bold bright_white] ◈ TASKS [/bold bright_white]",
        border_style="bright_blue",
        padding=(0, 0),
    )


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")

    with Live(console=console, refresh_per_second=1, screen=False) as live:
        while True:
            live.update(_build_table(conn))
            time.sleep(3)


if __name__ == "__main__":
    main()
