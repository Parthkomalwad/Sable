"""The per-job circuit breaker (Phase 3, I2).

`app/budget.py` limits a session; this limits one job (a goal, a sub-agent),
because a Phase 5 daemon job has a budget and no session. It is checked
before each turn and never interrupts one: killing a command mid-run could
leave the filesystem in a state nothing recorded.

A trip is a row in `breaker_trips`, the same table-first precedent as
`policy_queue`, so Phase 5's `/inbox` can read it. While any trip is open,
every other sub-agent pauses before its next turn (`wait_while_held`): a
runaway is a reason to stop autonomous work, not just the one job, until the
user says `/breaker reset`, after which they carry on. Only a job over its
own limits stops for good. The interactive orchestrator never waits on
another job's trip: a goal the user typed stops on its own limits only.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field

_CREATE = """
CREATE TABLE IF NOT EXISTS breaker_trips (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    job        TEXT NOT NULL,
    reason     TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'tripped'   -- tripped | reset
)
"""

#: Keys `per_job_budget` accepts. None or absent means unlimited.
BUDGET_KEYS = ("tokens", "usd", "turns", "wall_s")

#: Keys one `tool_budgets` entry accepts (J12). Absent means unlimited.
TOOL_BUDGET_KEYS = ("max_calls_per_goal", "max_bytes", "max_cost")


@dataclass(frozen=True)
class Limits:
    tokens: int | None = None
    usd: float | None = None
    turns: int | None = None
    wall_s: float | None = None
    consecutive_failures: int | None = None
    #: J12: `{"web": {"max_calls_per_goal": 2}}`. A key budgets that tool and
    #: every tool under it (`web` covers `web.search` and `web.fetch`).
    tools: dict = field(default_factory=dict, hash=False)

    @staticmethod
    def from_config(config) -> "Limits":
        per_job = getattr(config, "per_job_budget", None) or {}
        return Limits(
            **{k: per_job.get(k) for k in BUDGET_KEYS},
            consecutive_failures=getattr(config, "breaker_consecutive_failures", None),
            tools=dict(getattr(config, "tool_budgets", None) or {}),
        )


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE)
    conn.commit()


def tripped(conn: sqlite3.Connection) -> list[dict]:
    ensure_table(conn)
    rows = conn.execute(
        "SELECT id, created_at, job, reason FROM breaker_trips WHERE status = 'tripped' ORDER BY id"
    ).fetchall()
    return [dict(zip(("id", "created_at", "job", "reason"), r)) for r in rows]


def reset(conn: sqlite3.Connection) -> int:
    """Close every open trip. Returns how many there were."""
    ensure_table(conn)
    cur = conn.execute("UPDATE breaker_trips SET status = 'reset' WHERE status = 'tripped'")
    conn.commit()
    return cur.rowcount


class Breaker:
    """Counts one job's spend and says, before each turn, whether it may run."""

    def __init__(self, job: str, limits: Limits, db_path: str,
                 held_by_trips: bool = True) -> None:
        self._job = job
        self._held_by_trips = held_by_trips
        self._limits = limits
        self._db_path = db_path
        self._start = time.monotonic()
        self.tokens = 0
        self.usd = 0.0
        self.turns = 0
        self.failures = 0
        # J12 per-tool spend, keyed by budget key, and the tool trip if any.
        self._tool_used: dict[str, dict[str, float]] = {}
        self._tool_trip: str | None = None
        self._recorded: str | None = None

    def _tool_keys(self, name: str) -> list[str]:
        return [k for k in self._limits.tools if name == k or name.startswith(k + ".")]

    def _trip_tool(self, reason: str) -> str:
        """Stop this job: recorded now, and `check()` refuses the next turn."""
        self._tool_trip = reason
        self.check()
        return f"[breaker: {reason}; the job stops before its next turn]"

    def before_tool(self, name: str) -> str | None:
        """None if tool `name` may run now, else the refusal the model reads.

        A call that would go past `max_calls_per_goal` never runs; bytes and
        cost are only known afterwards, so a budget already spent refuses too.
        """
        for key in self._tool_keys(name):
            lim, used = self._limits.tools[key], self._tool_used.setdefault(
                key, {"max_calls_per_goal": 0, "max_bytes": 0, "max_cost": 0.0})
            for k in TOOL_BUDGET_KEYS:
                limit = lim.get(k)
                if limit is not None and used[k] >= limit:
                    return self._trip_tool(f"tool {name}: {key}.{k} limit reached ({used[k]:g} of {limit:g})")
        for key in self._tool_keys(name):
            self._tool_used[key]["max_calls_per_goal"] += 1
        return None

    def after_tool(self, name: str, nbytes: int, cost: float = 0.0) -> tuple[int | None, str | None]:
        """Count a finished call. Returns (bytes to keep, trip notice).

        The call that crosses `max_bytes` trips, and its output is cut to
        what the budget had left, so the model never reads past the limit.
        Cost is only reported, so the call that crosses `max_cost` is kept.
        """
        keep, notice = None, None
        for key in self._tool_keys(name):
            lim, used = self._limits.tools[key], self._tool_used[key]
            before = used["max_bytes"]
            used["max_bytes"] += nbytes
            used["max_cost"] += cost or 0.0
            if lim.get("max_bytes") is not None and used["max_bytes"] > lim["max_bytes"]:
                left = max(0, int(lim["max_bytes"] - before))
                keep = left if keep is None else min(keep, left)
                notice = self._trip_tool(f"tool {name}: {key}.max_bytes limit reached "
                                         f"({used['max_bytes']:g} of {lim['max_bytes']:g})")
            elif lim.get("max_cost") is not None and used["max_cost"] >= lim["max_cost"]:
                notice = self._trip_tool(f"tool {name}: {key}.max_cost limit reached "
                                         f"({used['max_cost']:g} of {lim['max_cost']:g})")
        return keep, notice

    def record(self, tokens: int = 0, usd: float = 0.0, failed: bool = False) -> None:
        """One finished turn. A success ends a run of failures."""
        self.turns += 1
        self.tokens += tokens or 0
        self.usd += usd or 0.0
        self.failures = self.failures + 1 if failed else 0

    def wait_while_held(self, on_pause=None, sleep=time.sleep, poll_s: float = 5.0) -> bool:
        """Block, without spending, while another job's trip is open.

        `on_pause(reason)` is called once when a pause begins. Paused time is
        not counted against `wall_s`. An unreadable DB does not hold the job.
        Returns True if it paused.
        """
        paused_at = None
        while True:
            try:
                conn = sqlite3.connect(self._db_path)
                try:
                    open_trips = tripped(conn)
                finally:
                    conn.close()
            except sqlite3.Error:
                open_trips = []
            if not open_trips:
                break
            if paused_at is None:
                paused_at = time.monotonic()
                if on_pause:
                    t = open_trips[0]
                    on_pause(f"{t['job']}: {t['reason']}")
            sleep(poll_s)
        if paused_at is not None:
            self._start += time.monotonic() - paused_at
        return paused_at is not None

    def _over(self) -> str | None:
        if self._tool_trip:
            return self._tool_trip
        lim = self._limits
        checks = (
            ("turns", self.turns, lim.turns),
            ("tokens", self.tokens, lim.tokens),
            ("usd", self.usd, lim.usd),
            ("wall_s", time.monotonic() - self._start, lim.wall_s),
            ("consecutive_failures", self.failures, lim.consecutive_failures),
        )
        for name, used, limit in checks:
            if limit is not None and used >= limit:
                return f"{name} limit reached ({used:g} of {limit:g})"
        return None

    def check(self) -> str | None:
        """None if the next turn may run, else the reason it may not.

        A new trip is recorded and announced once. Never raises: an
        unreadable DB must not stop the shell, the same rule as budget.py,
        but an over-limit job still stops because `_over` needs no DB.
        """
        reason = self._over()
        try:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                open_trips = tripped(conn)
                if reason is None:
                    if open_trips and self._held_by_trips:
                        t = open_trips[0]
                        return f"breaker tripped by {t['job']}: {t['reason']}. /breaker reset to resume"
                    return None
                if reason == self._recorded:
                    return f"{reason}. /breaker reset to resume"
                self._recorded = reason
                conn.execute(
                    "INSERT INTO breaker_trips (created_at, job, reason) VALUES (?, ?, ?)",
                    (time.time(), self._job, reason),
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error:
            return reason
        from sable.core.events.bus import EventBus

        EventBus(db_path=self._db_path).publish(self._job, "breaker", {"reason": reason})
        return f"{reason}. /breaker reset to resume"


def block(job: str, reason: str) -> str:
    """The user-facing breaker block. Plain text: it is printed by both loops."""
    return f"\n  [breaker] {job} stopped before its next turn\n  {reason}\n"
