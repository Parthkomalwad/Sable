"""Numbered config migrations (Phase 7, I6).

A config without `schema_version` is version 1. Each migrator is a pure
function from version N to N+1 that returns a new dict and the notes to show;
none of them drops a key it does not know, so a config written by a newer
Sable survives an older one's doctor.
"""
from __future__ import annotations

import copy

CURRENT = 2

#: Keys added to ShellConfig after v0.3, with the defaults from_dict assumes.
_V2_DEFAULTS = {
    "tasks_base_dir": "~/tasks",
    "theme": "default",
    "models": {},
    "per_job_budget": {},
    "breaker_consecutive_failures": None,
    "tools": {},
    "tool_budgets": {},
    "notify": {},
    "mcp": {},
}


def version_of(data: dict) -> int:
    """The config's schema version; a config without the key is version 1."""
    return int(data.get("schema_version", 1))


def _v1_to_v2(data: dict) -> tuple[dict, list[str]]:
    added = [k for k in _V2_DEFAULTS if k not in data]
    out = {**copy.deepcopy(_V2_DEFAULTS), **data, "schema_version": 2}
    notes = ["1 -> 2: added schema_version"]
    if added:
        notes.append("1 -> 2: defaults for " + ", ".join(added))
    return out, notes


#: MIGRATORS[n] takes version n to n + 1.
MIGRATORS = {1: _v1_to_v2}


def migrate(data: dict) -> tuple[dict, list[str]]:
    """Bring `data` to CURRENT. Returns (new dict, notes); the input is untouched."""
    out = copy.deepcopy(data)
    notes: list[str] = []
    while version_of(out) < CURRENT:
        out, step = MIGRATORS[version_of(out)](out)
        notes.extend(step)
    return out, notes
