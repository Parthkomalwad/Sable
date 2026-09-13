"""Degraded modes, named and reported (Phase 1, I7).

structure.md §4.7: nothing swallows errors. The companion rule is that when
Sable *can* still work but with less than its full capability, it says so once,
plainly, and says what still works. The failure this prevents is the quiet one:
bwrap missing so a sub-agent runs under a weaker sandbox, the LLM unreachable
so every goal silently becomes a bash line, and no line of output anywhere
admitting it.

Each check answers three questions: is it degraded, what is reduced, and what
does the user do about it. A check never raises and never blocks: they run at
startup, where a hang costs the user their login shell.

Detection only. Rendering belongs to `ui/`, and `app/` decides when to look.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

#: Below this width the sidebar is hidden and blocks reflow (layout.py uses
#: 100 for its own split decision; the playground hook collapses at 120).
MIN_TERMINAL_WIDTH = 80


@dataclass(frozen=True)
class Degradation:
    """One capability that is missing, and what that costs.

    `hint` is the action the user can take. It is not optional: a banner that
    reports a problem without saying what to do about it is noise.
    """

    name: str
    summary: str
    reduced: str
    hint: str

    def line(self) -> str:
        """One-line form, for a banner."""
        return f"{self.summary} — {self.reduced}"


def check_tmux() -> Degradation | None:
    """tmux absent: no sidebar, no sub-agent windows."""
    if shutil.which("tmux"):
        return None
    return Degradation(
        name="tmux",
        summary="tmux not found",
        reduced="no sidebar, no tasks bar, and sub-agents cannot be spawned",
        hint="install tmux, or keep using Sable as a plain prompt",
    )


def check_sandbox() -> Degradation | None:
    """bwrap missing or userns unavailable: weaker sandbox for sub-agents.

    Reuses `Sandbox`'s own probe rather than repeating it, so the banner can
    never disagree with what the sandbox actually did.
    """
    try:
        from sable.agents.sandbox import Sandbox
    except ImportError:
        return None

    probe = Sandbox.__new__(Sandbox)
    try:
        if probe._probe_bwrap():
            return None
    except (OSError, AttributeError):
        return None
    return Degradation(
        name="sandbox",
        summary="sandbox: bash-wrapper fallback (bwrap unavailable)",
        reduced="sub-agents are confined by a bash wrapper, not a kernel namespace",
        hint="install bubblewrap and enable user namespaces for full isolation",
    )


def check_database(db_path: str | Path | None = None) -> Degradation | None:
    """SQLite unwritable or locked: no telemetry, no bus, no replay."""
    if db_path is None:
        from sable.core.db import DB_PATH

        db_path = DB_PATH
    path = Path(db_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path), timeout=1.0)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS _sable_health (id INTEGER PRIMARY KEY)"
            )
            connection.commit()
        finally:
            connection.close()
        return None
    except (sqlite3.Error, OSError):
        return Degradation(
            name="database",
            summary="database locked or unwritable",
            reduced="no cost tracking, no agent events, no /why or replay",
            hint=f"check permissions on {path}",
        )


def check_terminal_width(width: int | None = None) -> Degradation | None:
    """Too narrow for the sidebar and for block layout."""
    if width is None:
        try:
            width = os.get_terminal_size().columns
        except OSError:
            # Not a terminal at all (a pipe, a test). Nothing to warn about.
            return None
    if width >= MIN_TERMINAL_WIDTH:
        return None
    return Degradation(
        name="terminal",
        summary=f"terminal is {width} columns",
        reduced="the sidebar is hidden and output wraps awkwardly",
        hint=f"widen to at least {MIN_TERMINAL_WIDTH} columns",
    )


def check_keyring() -> Degradation | None:
    """No keyring: API keys fall back to config.json on disk."""
    try:
        import secretstorage  # noqa: F401
    except ImportError:
        return Degradation(
            name="keyring",
            summary="no system keyring",
            reduced="API keys are read from config.json instead of the keyring",
            hint="install secretstorage, and keep config.json at mode 600",
        )
    return None


#: Every startup check, in the order a banner should list them. The LLM is not
#: here: reaching it costs a network round trip, so it is discovered on first
#: use and latched by `llm_unreachable()` instead.
STARTUP_CHECKS = (
    check_tmux,
    check_sandbox,
    check_database,
    check_terminal_width,
    check_keyring,
)


def startup_degradations() -> list[Degradation]:
    """Run every startup check. Never raises; a broken check is not a banner."""
    found: list[Degradation] = []
    for check in STARTUP_CHECKS:
        try:
            result = check()
        except (OSError, sqlite3.Error, ImportError, AttributeError):
            continue
        if result is not None:
            found.append(result)
    return found


def llm_unreachable(reason: str = "") -> Degradation:
    """The degradation for a backend that could not be reached.

    Built on demand rather than probed at startup: a network round trip on
    every login is a cost the user pays for information that goes stale
    immediately.
    """
    detail = f": {reason}" if reason else ""
    return Degradation(
        name="llm",
        summary=f"LLM unreachable{detail}",
        reduced="bash-only mode, natural language is not routed to a model",
        hint="check the backend is running, then retry; bash still works",
    )
