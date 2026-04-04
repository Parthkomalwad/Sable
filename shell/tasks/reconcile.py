"""Reconcile — mark tasks whose tmux window is gone as 'lost'.

Called once from main.py after config loads.
Resolves the tmux session internally — main.py does not hold a session object.
"""
from __future__ import annotations

import sqlite3

import libtmux


def reconcile(db_path: str) -> list[str]:
    """Check all running/starting/paused tasks; mark lost if tmux window missing.

    Returns list of lost task names for main.py to print.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")

    try:
        rows = conn.execute(
            """SELECT name, tmux_window_id FROM tasks
               WHERE status IN ('running', 'starting', 'paused')"""
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return []

    if not rows:
        conn.close()
        return []

    server = libtmux.Server()

    session_name = None
    try:
        import subprocess
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#{session_name}"],
            capture_output=True, text=True,
        )
        session_name = result.stdout.strip() or None
    except Exception:
        pass

    session = None
    if session_name:
        session = server.find_where({"session_name": session_name})

    lost: list[str] = []
    for name, window_id in rows:
        if window_id is None:
            lost.append(name)
            continue
        alive = False
        if session:
            alive = session.find_where({"window_id": window_id}) is not None
        if not alive:
            conn.execute("UPDATE tasks SET status='lost' WHERE name=?", (name,))
            lost.append(name)

    conn.commit()
    conn.close()
    return lost
