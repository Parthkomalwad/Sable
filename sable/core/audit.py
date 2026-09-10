"""The audit ledger: what ran, when, for whom.

Extracted from `app/repl.py` in Phase 0.5 step 3 (docs/structure.md §5).
`agents/orchestrator.py` imported `_write_audit_log` from the REPL, which
made `agents` depend on `app` and violated the layering rule. Writing a line
to a log file needs nothing from the REPL, so it belongs in `core/`.

Two formats are written, both pre-existing and both kept as they are:

  - `write_command()` -> AUDIT_LOG_PATH, tab separated, consumed by the skills
    PatternWatcher, which parses it to find repeated command clusters.
    Changing this format silently breaks skill crystallisation.
  - `write_action()` -> /var/log/agentic-shell/audit.log, key=value, the
    system-wide ledger.

Both swallow permission errors: an unwritable audit log must never take the
shell down.
"""
from __future__ import annotations

import datetime
import getpass


def write_command(session_id: str, cwd: str, command: str) -> None:
    """Append one command to the session audit log.

    Format: ISO8601 \t session_id \t cwd \t command

    Parsed by `skills/watcher.py`. Keep the field order and the tab
    separator, or pattern detection stops finding anything.
    """
    from sable.core.db import AUDIT_LOG_PATH

    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    line = f"{stamp}\t{session_id}\t{cwd}\t{command}\n"
    try:
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(line)
    except (PermissionError, OSError):
        pass  # an unwritable audit log is not worth failing a command over


def write_action(action: str, command: str, exit_code: int | None = None) -> None:
    """Append one action to the system-wide ledger.

    Format: ISO8601 user=… action=… cmd=… exit=…
    """
    log_path = "/var/log/agentic-shell/audit.log"
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        # getuser() raises when there is no passwd entry for the uid, which
        # happens in containers run with --user.
        user = "unknown"
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    line = f"{stamp} user={user} action={action} cmd={command!r} exit={exit_code}\n"
    try:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(line)
    except (PermissionError, OSError):
        pass
