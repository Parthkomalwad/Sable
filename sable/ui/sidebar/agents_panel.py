"""Tasks panel runs in the bottom tmux pane.

Polls the tasks table every 3 seconds and renders a short list in the
sidebar's style: a quiet TASKS label, one line per task with a status dot,
and no box. With nothing running it is a single hint line, and the pane
shrinks to fit it; it grows back as tasks appear, up to MAX_ROWS.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import time

os.environ["PROMPT_TOOLKIT_NO_CPR"] = "1"

from rich.console import Console
from rich.live import Live
from rich.text import Text

from sable.core.db import DB_PATH

console = Console()

MAX_ROWS = 8

GREEN, PURPLE, AMBER, RED = "color(114)", "color(141)", "color(179)", "color(203)"
TEXT, DIM, FAINT = "color(252)", "color(245)", "color(240)"

#: tasks.status -> (dot colour, what a human reads, still live)
_STATUS = {
    "starting":  (PURPLE, "thinking", True),
    "running":   (GREEN, "running", True),
    "paused":    (AMBER, "paused", True),
    "completed": (DIM, "done", False),
    "done":      (DIM, "done", False),
    "failed":    (RED, "failed", False),
    "lost":      (RED, "lost", False),
}


def _fetch(conn: sqlite3.Connection) -> list[tuple]:
    try:
        return conn.execute(
            """SELECT t.name, t.status, t.step_count, t.goal,
                      COALESCE(SUM(e.cost_usd), 0.0)
               FROM tasks t
               LEFT JOIN task_events e ON e.task_name = t.name
               GROUP BY t.name
               ORDER BY t.id DESC LIMIT ?""", (MAX_ROWS,)
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def render(rows: list[tuple], width: int) -> Text:
    """The whole panel as one Text: a header line, then one line per task."""
    t = Text(no_wrap=True, overflow="ellipsis")
    t.append("TASKS", style=f"bold {DIM}")
    if not rows:
        t.append("   none · ", style=FAINT)
        t.append("/task new <name> <goal>", style=PURPLE)
        return t

    live = sum(1 for r in rows if _STATUS.get(r[1], (None, None, False))[2])
    t.append(f"   {len(rows)} shown · {live} running", style=FAINT)
    goal_width = max(10, width - 2 - 22 - 10 - 9 - 10)
    for name, status, steps, goal, cost in rows:
        colour, label, is_live = _STATUS.get(status, (DIM, status or "?", False))
        t.append("\n")
        t.append("● ", style=colour)
        t.append(f"{name[:20]:<22}", style=f"bold {TEXT}" if is_live else DIM)
        t.append(f"{label:<10}", style=colour if is_live else FAINT)
        t.append(f"step {steps or 0:<4}", style=FAINT)
        g = (goal or "").replace("\n", " ")
        g = g if len(g) <= goal_width else g[:goal_width - 1] + "…"
        t.append(f"{g:<{goal_width}}", style=DIM)
        t.append(f"{'$%.4f' % cost:>10}", style=PURPLE if cost else FAINT)
    return t


def pane_height(rows: list[tuple]) -> int:
    """One line for the hint when empty; header, rows and a spare line otherwise."""
    return 2 if not rows else min(len(rows), MAX_ROWS) + 2


def _fit_pane(height: int) -> None:
    """Resize this tmux pane to `height` if it is not already. Outside tmux, nothing.

    Reads the real height each time rather than remembering the last one: a
    client resize makes tmux re-split the window, and a remembered height
    would then never be put back.
    """
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return
    try:
        now = subprocess.run(["tmux", "display-message", "-p", "-t", pane, "#{pane_height}"],
                             capture_output=True, text=True, timeout=1).stdout.strip()
        if now != str(height):
            subprocess.run(["tmux", "resize-pane", "-t", pane, "-y", str(height)],
                           capture_output=True, timeout=1)
    except (OSError, subprocess.SubprocessError):
        pass


def main() -> None:
    # Clear the pane before starting so the startup command isn't visible
    console.clear()
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")

    with Live(console=console, refresh_per_second=1, screen=False) as live:
        while True:
            rows = _fetch(conn)
            _fit_pane(pane_height(rows))
            live.update(render(rows, console.width))
            time.sleep(3)


if __name__ == "__main__":
    main()
