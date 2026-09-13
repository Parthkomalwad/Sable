"""Prompt replay: what the model actually saw, per turn.

Phase 1 (I9). Every agent turn records the exact message list that was sent,
the system prompt that framed it, and the raw text that came back. `/task
<name> replay` and `/why` render it.

**Why this is not the audit log.** `core/audit.py` records that a command ran.
This records why the agent thought it should. When an agent does something
inexplicable, the answer is almost always in the context it was given, which is
exactly the thing nothing kept until now.

**Everything is redacted on the way in.** `policy/engine.py:strip_secrets` runs
over the system prompt, every message body and the response before any of it
reaches the table. Redacting on read instead would leave the secret sitting in
the database, and this table is the one a user is most likely to `cat`, export,
or paste into a bug report.

**Writing never raises**, for the same reason publishing an event does not: an
agent recording its own reasoning must not die because the recording failed.
Reads propagate errors normally.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class AgentTurn:
    """One recorded model turn. Every text field is already redacted."""

    agent: str
    role: str
    turn: int
    system_prompt: str
    messages: list[dict] = field(default_factory=list)
    response: str = ""
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    ts: str = ""
    id: int | None = None

    @classmethod
    def from_row(cls, row: tuple) -> "AgentTurn":
        """Build a turn from an `agent_turns` row.

        A messages blob that will not parse is surfaced as a single synthetic
        message rather than discarded, so a corrupt write stays visible to
        whoever is reading the replay.
        """
        (
            turn_id, ts, agent, role, turn, model, system_prompt,
            messages_json, response, prompt_tokens, completion_tokens, cost_usd,
        ) = row
        try:
            messages = json.loads(messages_json)
        except (json.JSONDecodeError, TypeError):
            messages = [{"role": "?", "content": messages_json}]
        if not isinstance(messages, list):
            messages = [{"role": "?", "content": str(messages)}]
        return cls(
            agent=agent, role=role, turn=turn, system_prompt=system_prompt,
            messages=messages, response=response, model=model,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            cost_usd=cost_usd, ts=ts, id=turn_id,
        )


#: Signature of a redactor: text in, safe text out.
Redactor = Callable[[str], str]


def _no_redaction(text: str) -> str:
    """The explicit opt-out. Never the default."""
    return text


def _coerce(text: Any) -> str:
    return text if isinstance(text, str) else str(text)


class ReplayLog:
    """Read and write `agent_turns`.

    Holds its own connection, like `EventBus` and for the same reason: a worker
    runs in a different process from the REPL, and a long read should not sit on
    the connection the shell uses for telemetry.
    """

    def __init__(
        self, db_path: str | Path | None = None, redact: Redactor | None = None
    ) -> None:
        """`redact` is applied to every stored string.

        Passed in rather than imported, because `core` sits below `policy` in
        the layering rule and may not reach up to it. The agents that record
        turns live in `agents`, which may import `policy`, so they supply
        `strip_secrets`. Omitting it stores text verbatim, which is why every
        caller in Sable passes one.
        """
        self._redact: Redactor = redact or _no_redaction
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
        """Create the table if this process got here before Database did.

        A worker is a fresh process and may record its first turn before
        anything has constructed `Database`.
        """
        from sable.core.db import _CREATE_AGENT_TURNS, _CREATE_AGENT_TURNS_INDEX

        self._conn.execute(_CREATE_AGENT_TURNS)
        self._conn.execute(_CREATE_AGENT_TURNS_INDEX)
        self._conn.commit()

    # ── writing ─────────────────────────────────────────────────────────

    def record(
        self,
        agent: str,
        role: str,
        turn: int,
        system_prompt: str,
        messages: list[dict],
        response: str,
        model: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> int | None:
        """Store one turn, redacted. Returns its id, or None if the write failed.

        Never raises. The agent's work matters more than the record of it.
        """
        try:
            redacted_messages = [
                {
                    "role": str(message.get("role", "?")),
                    "content": self._redact(_coerce(message.get("content", ""))),
                }
                for message in messages
            ]
            cursor = self._conn.execute(
                "INSERT INTO agent_turns "
                "(ts, agent, role, turn, model, system_prompt, messages_json, "
                " response, prompt_tokens, completion_tokens, cost_usd) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    agent,
                    role,
                    turn,
                    model,
                    self._redact(_coerce(system_prompt)),
                    json.dumps(redacted_messages, default=str),
                    self._redact(_coerce(response)),
                    prompt_tokens,
                    completion_tokens,
                    cost_usd,
                ),
            )
            self._conn.commit()
            return cursor.lastrowid
        except (sqlite3.Error, AttributeError, TypeError, ValueError):
            # AttributeError/TypeError cover a malformed messages list: a
            # caller passing something that is not a list of dicts should lose
            # the record, not the run.
            return None

    # ── reading ─────────────────────────────────────────────────────────

    def turns_for(self, agent: str, limit: int = 100) -> list[AgentTurn]:
        """Every recorded turn for one agent, oldest first. Backs `/task replay`."""
        rows = self._conn.execute(
            "SELECT id, ts, agent, role, turn, model, system_prompt, messages_json, "
            "response, prompt_tokens, completion_tokens, cost_usd "
            "FROM agent_turns WHERE agent = ? ORDER BY id LIMIT ?",
            (agent, limit),
        )
        return [AgentTurn.from_row(row) for row in rows]

    def latest(self, agent: str | None = None) -> AgentTurn | None:
        """The most recent turn, for `/why`. None when nothing is recorded."""
        sql = (
            "SELECT id, ts, agent, role, turn, model, system_prompt, messages_json, "
            "response, prompt_tokens, completion_tokens, cost_usd FROM agent_turns"
        )
        params: list[Any] = []
        if agent is not None:
            sql += " WHERE agent = ?"
            params.append(agent)
        sql += " ORDER BY id DESC LIMIT 1"
        row = self._conn.execute(sql, params).fetchone()
        return AgentTurn.from_row(row) if row else None

    def agents(self) -> list[str]:
        """Every agent with a recorded turn, most recently active first."""
        rows = self._conn.execute(
            "SELECT agent FROM agent_turns GROUP BY agent ORDER BY MAX(id) DESC"
        )
        return [row[0] for row in rows]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ReplayLog":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def record_turn(
    db_path: str | Path | None, redact: Redactor | None = None, **kwargs
) -> int | None:
    """Record one turn and close the connection. Never raises.

    The convenience form for an agent that records a handful of turns and has
    no reason to hold a connection open between them. `redact` is forwarded to
    `ReplayLog`; callers in `agents` pass `policy.engine.strip_secrets`.
    """
    try:
        with ReplayLog(db_path=db_path, redact=redact) as log:
            return log.record(**kwargs)
    except (sqlite3.Error, OSError):
        return None
