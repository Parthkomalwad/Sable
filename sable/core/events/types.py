"""Event dataclasses: telemetry records and the agent bus.

`TokenEvent` is per-LLM-call telemetry, written to `token_events`.
`AgentEvent` is one message on the agent bus, written to `agent_events`.

They are deliberately separate. Telemetry answers "what did this cost"; the
bus answers "what is happening right now". Phase 1's runtime publishes to the
bus and the sidebar tails it, which is what removes the file-poll lag between
a worker changing state and the UI noticing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class TokenEvent:
    """Represents one token-consuming event written to sessions.db."""
    timestamp: str          # ISO 8601
    session_id: str         # UUID generated at shell startup
    action_type: str        # "nl_route" | "bash" | "compress" | "resume"
    nl_input: str | None
    command: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    model: str | None
    exit_code: int | None


class EventKind:
    """The `kind` values the bus carries.

    A class of constants rather than an enum: these are stored as plain text
    in SQLite and read by other processes (the sidebar, the daemon later), so
    the string is the contract. An unknown kind is not an error, because an
    older sidebar must keep working against a newer runtime that publishes
    something it has not heard of.

    Lifecycle kinds mirror the `tasks.status` column so the two cannot
    disagree about what a worker is doing.
    """

    # Agent lifecycle.
    SPAWNED = "spawned"          # a sub-agent process was launched
    STARTED = "started"          # it reached its first turn
    STATUS = "status"            # a heartbeat: current step, command, output
    COMPLETED = "completed"      # finished, payload carries the result
    FAILED = "failed"            # gave up, payload carries the reason
    LOST = "lost"                # reconcile found its window gone

    # Work within a turn.
    TURN = "turn"                # one model turn, for `/task replay`
    COMMAND = "command"          # a command was executed
    GUIDANCE = "guidance"        # a human steered the agent (Ctrl+G)
    SKILL_USED = "skill_used"    # a skill was injected, payload carries confidence

    #: Kinds that mean the agent will publish nothing further.
    TERMINAL = frozenset({COMPLETED, FAILED, LOST})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentEvent:
    """One append-only row on the agent bus.

    `id` is assigned by SQLite and is the ordering and cursor key: tailing is
    `WHERE id > ?`. It is None until the event has been published.
    """

    agent: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=_utc_now)
    id: int | None = None

    @property
    def is_terminal(self) -> bool:
        """True when no further events are expected from this agent."""
        return self.kind in EventKind.TERMINAL

    def payload_json(self) -> str:
        """Serialise the payload for storage.

        Falls back to `str` for anything not JSON-serialisable rather than
        raising: a bus write must never take down the agent that is trying to
        report its own progress.
        """
        return json.dumps(self.payload, default=str)

    @classmethod
    def from_row(cls, row: tuple) -> "AgentEvent":
        """Build an event from an `agent_events` row.

        A row whose payload is not valid JSON is surfaced as
        `{"_raw": "<text>"}` rather than discarded, so a corrupt write stays
        visible to whoever is reading the stream.
        """
        event_id, ts, agent, kind, payload_json = row
        try:
            payload = json.loads(payload_json)
        except (json.JSONDecodeError, TypeError):
            payload = {"_raw": payload_json}
        if not isinstance(payload, dict):
            payload = {"_value": payload}
        return cls(agent=agent, kind=kind, payload=payload, ts=ts, id=event_id)
