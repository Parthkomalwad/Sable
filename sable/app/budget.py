"""Session budget enforcement and its hard-stop latch.

Extracted from `app/repl.py` in Phase 0.5 step 3 alongside the builtin
split. The flag and the check that sets it were a module-level `global` in
the REPL, read and written from two places. Splitting the dispatcher out
would have left one module writing another's global through an import,
which is worse than what it replaced, so both move here together and the
state is reached through functions rather than a shared name.

Behaviour is unchanged: the latch is process-wide and per-session, exactly
as before, and `/budget reset` clears it.
"""
from __future__ import annotations

from sable.core.config.schema import ShellConfig
from sable.ui.console import out as _out

# Once the daily or session budget is exhausted, LLM calls stay disabled
# until the user clears it with `/budget reset`. Deliberately process-wide:
# a new REPL turn must not silently re-enable spending.
_hard_stop: bool = False


def is_stopped() -> bool:
    return _hard_stop


def reset() -> None:
    """Clear the latch. Backs `/budget reset`."""
    global _hard_stop
    _hard_stop = False


def check_and_enforce(db, config: ShellConfig, session_id: str) -> bool:
    """True if an LLM call may proceed, False if the budget forbids it.

    Warns at 80% and latches the hard stop at the limit. A telemetry failure
    must not block the shell, so a broken DB is treated as "allowed".
    """
    global _hard_stop

    if _hard_stop:
        _out("Budget exhausted. Use '/budget reset' to continue.")
        return False
    if db is None:
        return True
    try:
        status = db.check_budget(config, session_id)
    except Exception:
        # Budget accounting is best effort; an unreadable DB should not stop
        # the user working. Left broad deliberately, matching the behaviour
        # this replaced, and covered by the Phase 1 exception cleanup.
        return True

    if status == "HARD_STOP":
        _hard_stop = True
        _out("Budget limit reached. LLM calls disabled. Use '/budget reset' to clear.")
        return False
    if status == "WARNING":
        _out("Warning: Budget at 80%+ approaching limit.")
    return True
