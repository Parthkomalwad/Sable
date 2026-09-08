"""Tasks panel runs in the bottom tmux pane.

Polls the tasks table every 3 seconds and renders a live Rich table.
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
from rich import box as rich_box
from rich.text import Text

from shell.telemetry.db import DB_PATH

console = Console()

_STATUS = {
    "running":   ("●", "bright_green"),
    "done":      ("✓", "green"),
    "completed": ("✓", "green"),
    "paused":    ("⏸", "yellow"),
    "lost":      ("✗", "bright_red"),
    "starting":  ("◌", "cyan"),
}


def _build(conn: sqlite3.Connection) -> Panel:
    table = Table(
        box=rich_box.SIMPLE,
        show_header=True,
        header_style="bold color(245)",
        padding=(0, 2),
        expand=True,
        show_edge=False,
    )
    table.add_column("",      width=2,  no_wrap=True)
    table.add_column("TASK",  min_width=10, no_wrap=True)
    table.add_column("ST",    width=4,  no_wrap=True, justify="right")
    table.add_column("GOAL",  min_width=20, no_wrap=True)
    table.add_column("COST",  width=9,  no_wrap=True, justify="right")

    try:
        rows = conn.execute(
            """SELECT t.name, t.status, t.step_count, t.goal,
                      COALESCE(SUM(e.cost_usd), 0.0)
               FROM tasks t
               LEFT JOIN task_events e ON e.task_name = t.name
               GROUP BY t.name
               ORDER BY t.id DESC LIMIT 8"""
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []

    if not rows:
        table.add_row("", Text("no tasks yet", style="dim"), "", "", "")
    else:
        for i, (name, status, steps, goal, cost) in enumerate(rows):
            sym, col = _STATUS.get(status, ("?", "white"))
            goal_str = f'"{(goal or "")[:55]}"'
            cost_str = f"${cost:.4f}"

            # Alternate row shading
            row_style = "on color(234)" if i % 2 == 0 else "on color(236)"

            table.add_row(
                Text(sym, style=col),
                Text(name, style=f"bold color(75)"),
                Text(str(steps or 0), style="color(220)"),
                Text(goal_str, style="color(252)"),
                Text(cost_str, style="color(141)"),
                style=row_style,
            )

    return Panel(
        table,
        title="[bold color(75)] ◈ TASKS [/bold color(75)]",
        border_style="color(27)",
        padding=(0, 0),
    )


def main() -> None:
    # Clear the pane before starting so the startup command isn't visible
    os.system("clear")
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")

    with Live(console=console, refresh_per_second=1, screen=False) as live:
        while True:
            live.update(_build(conn))
            time.sleep(3)


if __name__ == "__main__":
    main()
