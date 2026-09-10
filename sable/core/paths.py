"""Filesystem locations for Sable's new-style state.

`~/.sable/` is the home the project is migrating to (structure.md 3.1). Only
state introduced from Phase 0 onward lives here; the existing runtime paths
(`~/.config/agentic-shell`, `~/.local/share/agentic-shell`,
`/var/log/agentic-shell`) stay where they are until the Phase 0.5 migration
moves everything at once, so current installs keep their data.
"""
from __future__ import annotations

from pathlib import Path

SABLE_HOME = Path.home() / ".sable"
STATE_DIR = SABLE_HOME / "state"

# Router corrections: one "input<TAB>label" row per correction, appended when
# the user overrides a routing decision (Ctrl+B or an answer to [b/a]).
ROUTER_CORRECTIONS = STATE_DIR / "router_corrections.tsv"

# Presence of this file disables the agentic layer; `sable on` removes it.
DISABLED_FLAG = SABLE_HOME / "disabled"


def ensure_state_dir() -> Path:
    """Create ~/.sable/state/ if needed and return it."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR


def is_disabled() -> bool:
    """True when the agentic layer has been switched off with `sable off`."""
    return DISABLED_FLAG.exists()
