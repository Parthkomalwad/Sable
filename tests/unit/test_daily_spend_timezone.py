"""Daily spend is counted from local midnight, whatever the UTC date is.

Events are stored with UTC timestamps. The daily budget used to match them
with `LIKE '<local date>%'`, so east of UTC the budget read zero spend for
part of every day (00:00 to 05:30 in IST). Found when a test started failing
after local midnight.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch

from sable.core import db as dbmod


def _insert(path, when: datetime, cost: float) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "INSERT INTO token_events (timestamp, session_id, action_type, cost_usd, total_tokens) "
        "VALUES (?, 's', 'nl', ?, 10)",
        (when.astimezone(timezone.utc).isoformat(), cost),
    )
    conn.commit()
    conn.close()


def test_spend_since_local_midnight_counts_and_before_does_not(tmp_path):
    path = tmp_path / "sessions.db"
    midnight = datetime.combine(date.today(), time.min).astimezone()
    with patch.object(dbmod, "DB_PATH", path):
        d = dbmod.Database()
        try:
            _insert(path, midnight + timedelta(minutes=1), 0.25)   # today, local
            _insert(path, midnight - timedelta(minutes=1), 5.00)   # yesterday, local
            assert d.get_daily_spend() == 0.25
            assert d.get_today_stats()["calls"] == 1
        finally:
            d.close()
