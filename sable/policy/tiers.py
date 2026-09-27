"""What policy decided about a command, as data.

Phase 3 Task 1. `is_destructive` answered yes or no, which cannot say
"refuse outright" and cannot say which rule fired. A tier can do the first
and a `Decision` carries the second, so a confirm block and `/policy explain`
read the same object instead of reconstructing it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sable.policy.rules import Rule


class Tier(str, Enum):
    """A `str` enum so it survives SQLite and JSON without a converter."""

    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass(frozen=True)
class Decision:
    tier: Tier
    rule: Rule | None
    why: str
    source: str   # which policy file the rule came from
