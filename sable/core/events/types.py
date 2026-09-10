"""TokenEvent dataclass for telemetry records."""
from __future__ import annotations
from dataclasses import dataclass


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
