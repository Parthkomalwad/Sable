"""The agent bus: an append-only event stream in SQLite.

Phase 1 (A1). Agents publish what they are doing; the orchestrator, the
sidebar and later the daemon read it. Before this, a spawned worker
communicated by writing `status.md` / `result.md` and the orchestrator polled
those files, which cost up to a 3 second lag and used a read-then-unlink
handshake that loses an event if the reader crashes between the two.

**Why SQLite and not a socket.** The database is already open in WAL mode and
already read by a separate sidebar process. An append-only table with an
AUTOINCREMENT id gives push-like tailing (`WHERE id > ?`) that works across
tmux windows and survives a reader restart, because the cursor is just an
integer the reader owns. A socket would need a broker, a reconnect story and
a replay buffer to match that. Revisit only if latency becomes a real
problem, which it will not at one event per agent turn.

**Ordering is by `id`, never by `ts`.** The timestamp is for humans and has
tie-prone resolution; the primary key is what defines the sequence.

**Publishing must never break the agent.** An agent reporting its own
progress cannot be taken down by the reporting failing, so `publish` swallows
database errors and returns None. Reads propagate errors normally: a caller
tailing the bus wants to know the tail is broken.
"""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sable.core.events.types import AgentEvent, EventKind

#: How often `tail` and `wait_for` re-query while blocking. 200 ms is well
#: under human perception for a status change and costs one indexed query
#: against a local file per tick.
POLL_INTERVAL = 0.2


class EventBus:
    """Publish and read agent events.

    Holds its own connection rather than sharing `Database`'s. A worker runs
    in a different process from the REPL, and even in-process a long tail
    should not sit on the connection the shell uses for telemetry.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from sable.core.db import DB_PATH

            db_path = DB_PATH
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Create the table if this process reached the bus before Database.

        A worker is a fresh process and may publish before anything has
        constructed `Database`, so the bus cannot assume the schema exists.
        The statements are the same `IF NOT EXISTS` forms `core/db.py` runs.
        """
        from sable.core.db import _CREATE_AGENT_EVENTS, _CREATE_AGENT_EVENTS_INDEX

        self._conn.execute(_CREATE_AGENT_EVENTS)
        self._conn.execute(_CREATE_AGENT_EVENTS_INDEX)
        self._conn.commit()

    # ── writing ─────────────────────────────────────────────────────────

    def publish(
        self, agent: str, kind: str, payload: dict[str, Any] | None = None
    ) -> int | None:
        """Append one event. Returns its id, or None if the write failed.

        Never raises. An agent publishing its own progress must not die
        because the bus is unavailable; the work it is reporting on is more
        important than the report.
        """
        event = AgentEvent(agent=agent, kind=kind, payload=payload or {})
        try:
            cursor = self._conn.execute(
                "INSERT INTO agent_events (ts, agent, kind, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (event.ts, event.agent, event.kind, event.payload_json()),
            )
            self._conn.commit()
            return cursor.lastrowid
        except sqlite3.Error:
            return None

    # ── reading ─────────────────────────────────────────────────────────

    def since(
        self, cursor: int = 0, agent: str | None = None, limit: int = 1000
    ) -> list[AgentEvent]:
        """Every event after `cursor`, oldest first.

        One non-blocking query. `tail` is this in a loop; a caller that has
        its own loop (a Rich render cycle, say) should use this directly
        rather than spawning a second one.
        """
        sql = "SELECT id, ts, agent, kind, payload_json FROM agent_events WHERE id > ?"
        params: list[Any] = [cursor]
        if agent is not None:
            sql += " AND agent = ?"
            params.append(agent)
        sql += " ORDER BY id LIMIT ?"
        params.append(limit)
        return [AgentEvent.from_row(row) for row in self._conn.execute(sql, params)]

    def latest_id(self) -> int:
        """The current head, for starting a tail at "now" rather than replaying."""
        row = self._conn.execute("SELECT COALESCE(MAX(id), 0) FROM agent_events").fetchone()
        return int(row[0])

    def tail(
        self,
        cursor: int = 0,
        agent: str | None = None,
        timeout: float | None = None,
        stop_on_terminal: bool = False,
    ) -> Iterator[AgentEvent]:
        """Yield events as they arrive, blocking between polls.

        `timeout` is the limit on waiting with nothing new, refreshed each
        time an event arrives, so a steady stream is never cut off mid-flow.
        With `stop_on_terminal`, the iterator ends after a completed / failed
        / lost event, which is how a caller follows one agent to its end
        without needing to know how long that takes.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            events = self.since(cursor, agent=agent)
            for event in events:
                cursor = event.id or cursor
                yield event
                if stop_on_terminal and event.is_terminal:
                    return
            if events:
                deadline = None if timeout is None else time.monotonic() + timeout
                continue
            if deadline is not None and time.monotonic() >= deadline:
                return
            time.sleep(POLL_INTERVAL)

    def wait_for(
        self,
        agent: str,
        kinds: str | set[str] = EventKind.TERMINAL,
        cursor: int = 0,
        timeout: float = 300.0,
    ) -> AgentEvent | None:
        """Block until `agent` publishes one of `kinds`. None on timeout.

        Defaults to the terminal kinds, so `wait_for("worker-1")` means "wait
        until that worker is done, however it ends".

        `cursor` should be the bus head captured *before* the agent was
        spawned. Without it a fast agent can finish before the wait starts
        and the event would be missed; with it the wait sees the whole
        history and returns immediately.
        """
        wanted = {kinds} if isinstance(kinds, str) else set(kinds)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.since(cursor, agent=agent):
                cursor = event.id or cursor
                if event.kind in wanted:
                    return event
            time.sleep(POLL_INTERVAL)
        return None

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "EventBus":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
