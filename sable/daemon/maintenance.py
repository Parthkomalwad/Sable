"""Daily maintenance (Phase 7 Task 3, C3): palace consolidation at
`maintenance_time` (local, default 02:30), at most once per day even if the
daemon restarts within that minute."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from sable.daemon import service
from sable.memory import consolidate

DEFAULT_TIME = "02:30"


def _at() -> str:
    from sable.core.config.wizard import CONFIG_PATH
    try:
        data = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    except (OSError, ValueError):
        data = {}
    return data.get("maintenance_time") or DEFAULT_TIME


def due(conn: sqlite3.Connection, now: datetime, at: str) -> bool:
    """Run consolidation if `now` is the `at` minute and it has not run today."""
    if now.strftime("%H:%M") != at:
        return False
    conn.execute("CREATE TABLE IF NOT EXISTS maintenance (task TEXT PRIMARY KEY, last_run_date TEXT)")
    today = now.date().isoformat()
    row = conn.execute("SELECT last_run_date FROM maintenance WHERE task = 'consolidate'").fetchone()
    if row and row[0] == today:
        return False
    conn.execute("INSERT OR REPLACE INTO maintenance (task, last_run_date) VALUES ('consolidate', ?)", (today,))
    conn.commit()
    consolidate.run()
    return True


@service.register
def maintenance_tick(conn: sqlite3.Connection) -> None:
    due(conn, datetime.now(), _at())
