"""What the sidebar and `/dash` show, read from SQLite and nothing else.

Phase 4, Task 0. Both UIs render the same answers, so they come from one
place: agents and their status, the inbox (approvals a sub-agent is waiting
on, and open breaker trips), cost per agent, and an agent's recent log lines.
`/inbox` (Phase 5) adds one non-SQLite read: pending crystallised skills,
which live in skills_index.json.

Read-only by design. Each call opens a short-lived WAL connection, reads, and
closes, so a UI process never holds a lock the shell or an agent needs. A
missing table (a fresh install, a feature not used yet) is an empty answer,
never an exception: a dashboard that crashes because nobody has spawned an
agent yet is a dashboard nobody opens twice. The only writes a UI may make go
through the existing functions (`policy.queue.decide_request`,
`policy.breaker.reset`), not through this module.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

#: Status badges, in the words the roadmap uses. `tasks.status` values map
#: onto them; a pending approval overrides whatever the task says.
BADGES = {
    "starting": "thinking",
    "running": "running",
    "paused": "blocked",
    "completed": "done",
    "failed": "failed",
    "lost": "failed",
}
AWAITING = "awaiting approval"


@dataclass(frozen=True)
class Agent:
    name: str
    goal: str
    status: str        # the raw tasks.status
    badge: str         # what a human sees
    steps: int
    last_output: str
    cost_usd: float
    tokens: int


@dataclass(frozen=True)
class InboxItem:
    kind: str          # "approval" | "breaker" | "skill"
    id: int
    agent: str
    text: str          # the command waiting, the trip reason, or the skill name
    why: str
    created_at: float = 0.0


def _db(db_path: str | Path | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    from sable.core.db import DB_PATH

    return DB_PATH


def _rows(db_path, sql: str, params: tuple = ()) -> list[tuple]:
    path = _db(db_path)
    if not path.exists():
        return []
    try:
        # Read-only, so no `PRAGMA journal_mode=WAL` here: setting it is a write
        # and fails on a read-only connection. WAL is a property of the file,
        # set by the writers (core/db.py), and readers get it from there.
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        # A missing table or a locked file is an empty answer for a UI.
        return []


def _pending_agents(db_path) -> set[str]:
    return {r[0] for r in _rows(db_path, "SELECT agent FROM policy_queue WHERE status = 'pending'")}


def cost_by_agent(db_path=None) -> dict[str, tuple[float, int]]:
    """Agent name -> (cost in USD, tokens), from each worker's task events."""
    rows = _rows(db_path, "SELECT task_name, COALESCE(SUM(cost_usd), 0), "
                          "COALESCE(SUM(prompt_tokens + completion_tokens), 0) "
                          "FROM task_events GROUP BY task_name")
    return {name: (float(cost), int(tokens)) for name, cost, tokens in rows}


def agents(db_path=None, limit: int = 50) -> list[Agent]:
    """Sub-agents, newest first, with a badge a human can read at a glance."""
    waiting = _pending_agents(db_path)
    costs = cost_by_agent(db_path)
    rows = _rows(db_path, "SELECT name, goal, status, step_count, COALESCE(last_output, '') "
                          "FROM tasks ORDER BY id DESC LIMIT ?", (limit,))
    out = []
    for name, goal, status, steps, last in rows:
        badge = AWAITING if name in waiting else BADGES.get(status, status)
        cost, tokens = costs.get(name, (0.0, 0))
        out.append(Agent(name=name, goal=goal, status=status, badge=badge, steps=int(steps or 0),
                         last_output=last[-500:], cost_usd=cost, tokens=tokens))
    return out


def inbox(db_path=None) -> list[InboxItem]:
    """What needs a human: pending approvals, then open breaker trips."""
    items = [InboxItem("approval", qid, agent, command, f"{rule}: {why}" if rule else (why or ""))
             for qid, agent, command, rule, why in _rows(
                 db_path, "SELECT id, agent, command, rule, why FROM policy_queue "
                          "WHERE status = 'pending' ORDER BY id")]
    items += [InboxItem("breaker", tid, job, reason, "circuit breaker")
              for tid, job, reason in _rows(
                  db_path, "SELECT id, job, reason FROM breaker_trips WHERE status = 'tripped' ORDER BY id")]
    return items


def _pending_skills() -> list[InboxItem]:
    """Crystallised drafts from skills_index.json, read without SkillIndex's writes."""
    path = Path.home() / "skills" / "skills_index.json"
    try:
        entries = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    items = []
    for e in entries:
        if not isinstance(e, dict) or e.get("status") != "pending":
            continue
        try:
            at = datetime.fromisoformat(e.get("created_at") or "").timestamp()
        except (TypeError, ValueError):
            at = 0.0
        items.append(InboxItem("skill", 0, "crystalliser", e.get("name", ""), "skill proposal", at))
    return items


def inbox_all(db_path=None) -> list[InboxItem]:
    """Everything waiting on a human, oldest first; `/inbox` numbers them from 1.

    Oldest first so a new arrival never renumbers what is already listed.
    A daemon's queued step (agent `daemon:<job>`) reads as "scheduled job <job>".
    """
    items = [InboxItem("approval", qid, f"scheduled job {agent[7:]}" if agent.startswith("daemon:") else agent,
                       command, f"{rule}: {why}" if rule else (why or ""), at)
             for qid, at, agent, command, rule, why in _rows(
                 db_path, "SELECT id, created_at, agent, command, rule, why FROM policy_queue "
                          "WHERE status = 'pending'")]
    items += [InboxItem("breaker", tid, job, reason, "circuit breaker", at)
              for tid, at, job, reason in _rows(
                  db_path, "SELECT id, created_at, job, reason FROM breaker_trips WHERE status = 'tripped'")]
    return sorted(items + _pending_skills(), key=lambda i: i.created_at)


def tail(agent: str, n: int = 20, db_path=None) -> list[str]:
    """The agent's last `n` status lines from the event bus, oldest first."""
    rows = _rows(db_path, "SELECT kind, payload_json FROM agent_events WHERE agent = ? "
                          "ORDER BY id DESC LIMIT ?", (agent, n))
    lines = []
    for kind, payload in reversed(rows):
        try:
            data = json.loads(payload or "{}")
        except json.JSONDecodeError:
            data = {}
        text = data.get("command") or data.get("reason") or data.get("explanation") or data.get("name") or ""
        lines.append(f"{kind}: {text}".rstrip(": ").strip())
    return lines


def lanes(db_path=None) -> list[tuple[str, str]]:
    """The newest plan graph's lanes (A3) as (id, status), in the order declared."""
    rows = _rows(db_path, "SELECT payload_json FROM agent_events WHERE kind = 'graph.lane' "
                          "ORDER BY id DESC LIMIT 200")
    events = []
    for (payload,) in reversed(rows):
        try:
            events.append(json.loads(payload or "{}"))
        except json.JSONDecodeError:
            continue
    graph = events[-1].get("graph") if events else None
    latest: dict[str, str] = {}
    for data in events:
        if data.get("graph") == graph:
            latest[data.get("id", "?")] = data.get("status", "?")
    return list(latest.items())
