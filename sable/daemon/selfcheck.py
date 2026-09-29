"""Weekly self-check (Phase 9 Task 7, K10). No model calls, ever.

Reads only what Sable already keeps: `goal_done` events (the orchestrator
publishes one per completed goal, with its steps, tokens, spend and matched
skills), the skill doctor's findings, the last `selfcheck` event (to tell
newly failing skills from old ones) and the latest `eval_runs` row. Runs on
Sunday at `maintenance_time`, once per week, and on demand through
`sable selfcheck` / `/selfcheck`.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sable.core.events.types import EventKind
from sable.daemon import maintenance, notify, service


@dataclass
class Report:
    since: str
    goals: int = 0
    with_skill: tuple[int, float, float, float] = (0, 0.0, 0.0, 0.0)     # n, steps, tokens, usd per goal
    without_skill: tuple[int, float, float, float] = (0, 0.0, 0.0, 0.0)
    failing: list[str] = field(default_factory=list)
    newly_failing: list[str] = field(default_factory=list)
    eval: tuple[str, int, int, str] | None = None                         # backend, passed, total, date

    def __str__(self) -> str:
        lines = [f"sable self-check since {self.since[:10]}: {self.goals} goals completed"]
        if self.goals:
            lines.append(f"skills matched {self.with_skill[0]} of {self.goals} goals")
            for label, (n, steps, tokens, usd) in (("with a skill", self.with_skill),
                                                   ("without", self.without_skill)):
                if n:
                    lines.append(f"{label}: {steps:.1f} steps, {tokens:.0f} tokens, ${usd:.4f} per goal")
        if self.failing:
            lines.append("failing skills: " + ", ".join(self.failing))
        if self.newly_failing:
            lines.append("newly failing: " + ", ".join(self.newly_failing) + " (see /skill doctor)")
        if self.eval:
            backend, passed, total, date = self.eval
            lines.append(f"latest eval: {passed}/{total} passed ({backend}, {date[:10]})")
        else:
            lines.append("latest eval: none run")
        return "\n".join(lines)


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[tuple]:
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:     # table not created yet: nothing to report
        return []


def _failing_now() -> list[str]:
    from sable.skills.doctor import diagnose
    from sable.skills.index import SkillIndex
    return sorted({f.skill for f in diagnose(SkillIndex()) if f.kind == "failing"})


def _mean(goals: list[dict]) -> tuple[int, float, float, float]:
    n = len(goals)
    if not n:
        return (0, 0.0, 0.0, 0.0)
    return (n, *(sum(float(g.get(k, 0)) for g in goals) / n for k in ("steps", "tokens", "usd")))


def summarize(conn: sqlite3.Connection, now: datetime, days: int = 7,
              failing: list[str] | None = None) -> Report:
    since = (now - timedelta(days=days)).astimezone(timezone.utc).isoformat()
    goals = [json.loads(p) for (p,) in _rows(
        conn, "SELECT payload_json FROM agent_events WHERE kind = ? AND ts >= ?",
        (EventKind.GOAL_DONE, since))]
    with_skill = [g for g in goals if g.get("skills")]
    without = [g for g in goals if not g.get("skills")]

    failing = sorted(_failing_now() if failing is None else failing)
    last = _rows(conn, "SELECT payload_json FROM agent_events WHERE kind = ? ORDER BY id DESC LIMIT 1",
                 (EventKind.SELFCHECK,))
    before = set(json.loads(last[0][0]).get("failing", [])) if last else set()

    ev = _rows(conn, "SELECT backend, SUM(passed), COUNT(*), created_at FROM eval_runs "
                     "WHERE run_id = (SELECT run_id FROM eval_runs ORDER BY id DESC LIMIT 1) "
                     "GROUP BY run_id")
    return Report(since=since, goals=len(goals), with_skill=_mean(with_skill),
                  without_skill=_mean(without), failing=failing,
                  newly_failing=[s for s in failing if s not in before],
                  eval=(ev[0][0], int(ev[0][1]), int(ev[0][2]), ev[0][3]) if ev else None)


def run(conn: sqlite3.Connection, now: datetime | None = None,
        failing: list[str] | None = None) -> Report:
    """Summarize, publish `selfcheck` on the bus, and push once if ntfy is set up."""
    from sable.core.events.bus import EventBus
    report = summarize(conn, now or datetime.now(timezone.utc), failing=failing)
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    with EventBus(db_path or None) as bus:
        bus.publish("selfcheck", EventKind.SELFCHECK, {"text": str(report), "failing": report.failing})
    notify.send("sable weekly self-check", str(report))   # no-op without a topic
    return report


def due(conn: sqlite3.Connection, now: datetime, at: str) -> bool:
    """Sunday at `at`, once per week even if the daemon restarts that minute."""
    if now.weekday() != 6 or now.strftime("%H:%M") != at:
        return False
    conn.execute("CREATE TABLE IF NOT EXISTS maintenance (task TEXT PRIMARY KEY, last_run_date TEXT)")
    today = now.date().isoformat()
    row = conn.execute("SELECT last_run_date FROM maintenance WHERE task = 'selfcheck'").fetchone()
    if row and row[0] == today:
        return False
    conn.execute("INSERT OR REPLACE INTO maintenance (task, last_run_date) VALUES ('selfcheck', ?)", (today,))
    conn.commit()
    run(conn, now)
    return True


@service.register
def selfcheck_tick(conn: sqlite3.Connection) -> None:
    due(conn, datetime.now(), maintenance._at())


def main(argv: list[str]) -> int:
    """`sable selfcheck`: run it now and print the report."""
    from rich.console import Console
    from sable.core.db import DB_PATH
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        Console(highlight=False).print(str(run(conn)), markup=False)
    finally:
        conn.close()
    return 0
