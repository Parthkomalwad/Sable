"""Entry point for Sable.

The non-interactive bypass MUST remain the first executable code.
Handles startup, config loading, session resume, and launches the REPL.
"""
import os
import sqlite3
import sys

# --- Non-interactive bypass: must be first executable lines, non-negotiable ---
# Two ways a command arrives instead of an interactive session, and both must
# reach bash untouched or scp, rsync and git push over SSH hang:
#
# 1. `sable -c "<command>"`. This is the normal path. sshd runs the user's
#    login shell with -c for `ssh host cmd`, scp and rsync, and SSH_ORIGINAL
#    _COMMAND is NOT set. Anything that execs a login shell non-interactively
#    (su -c, a subshell) looks the same.
# 2. SSH_ORIGINAL_COMMAND set. Only happens behind ForceCommand or an
#    authorized_keys command=, where the real command is moved into the env.
#
# Checking only the env var, as earlier versions did, misses the common case.
_bypass_cmd = (
    sys.argv[2] if len(sys.argv) >= 3 and sys.argv[1] == "-c"
    else os.environ.get("SSH_ORIGINAL_COMMAND")
)
if _bypass_cmd:
    os.execvp("/bin/bash", ["/bin/bash", "-c", _bypass_cmd])
    sys.exit(0)
# -----------------------------------------------------------------------------


_CLI_USAGE = """sable - an agentic shell layer

  sable              start the shell, or re-attach a running session
  sable on           enable the agentic layer for new logins
  sable off          disable it; logins go straight to bash
  sable status       report which mode is active
  sable --wrap       run inside the current bash, no chsh or /etc/shells
  sable --version    print the version
"""


def _handle_cli(argv: list[str]) -> bool:
    """Handle the subcommands that never start a REPL.

    Returns True if the process should exit now.
    """
    if not argv:
        return False

    command = argv[0]

    if command in ("-h", "--help", "help"):
        sys.stdout.write(_CLI_USAGE)
        return True

    if command in ("-V", "--version", "version"):
        from sable import __version__

        sys.stdout.write(f"sable {__version__}\n")
        return True

    if command in ("on", "off", "status"):
        from sable.app import mode

        action = {"on": mode.enable, "off": mode.disable, "status": mode.status}[command]
        sys.stdout.write(action() + "\n")
        sys.stdout.flush()
        return True

    return False


def _report_degradations() -> None:
    """Print one line per degraded capability, with what to do about it (I7).

    Never raises and never blocks: this sits on the path to the user's login
    shell, so a probe that misbehaves must cost them nothing.
    """
    try:
        from sable.core.health import startup_degradations
    except ImportError:
        return

    degradations = startup_degradations()
    if not degradations:
        return

    AMBER = "\033[38;5;179m"
    DIM = "\033[2;37m"
    RESET = "\033[0m"
    for degradation in degradations:
        sys.stdout.write(f"{AMBER}  ! {degradation.line()}{RESET}\n")
        sys.stdout.write(f"{DIM}    {degradation.hint}{RESET}\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> None:
    """Shell entry point called by the installed binary."""
    import json
    import uuid
    from pathlib import Path

    from sable.core.config.schema import ShellConfig

    argv = list(sys.argv[1:] if argv is None else argv)

    if _handle_cli(argv):
        return

    wrap_mode = "--wrap" in argv

    # `sable off` is honoured here too, so an already-open terminal that runs
    # `sable` after disabling gets the same answer as a fresh login.
    from sable.app import mode
    from sable.core import paths

    if paths.is_disabled():
        sys.stdout.write("sable is off, run: sable on\n")
        sys.stdout.flush()
        return

    # With no arguments, re-attach a running session rather than starting a
    # second one. Replaces this process when it succeeds.
    if not argv and not wrap_mode:
        mode.attach_existing()

    session_id = str(uuid.uuid4())

    config_path = Path.home() / ".config" / "agentic-shell" / "config.json"

    # --- Run first-run wizard if config missing or setup not complete ---
    if not config_path.exists():
        from sable.core.config.wizard import run_wizard
        run_wizard()
        # After wizard, re-check wizard saves the file
        if not config_path.exists():
            sys.exit(0)

    # --- Load config ---
    try:
        raw = json.loads(config_path.read_text())
        config = ShellConfig.from_dict(raw)
        # Carry api_key through as a plain attribute (not in dataclass avoids validation issues)
        if "api_key" in raw and raw["api_key"]:
            config.api_key = raw["api_key"]  # type: ignore[attr-defined]
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        sys.stdout.write(f"Warning: Failed to parse config ({exc}). Using defaults.\n")
        sys.stdout.flush()
        config = ShellConfig.defaults()

    # Re-run wizard if setup was not completed
    if not config.setup_complete:
        from sable.core.config.wizard import run_wizard
        run_wizard()
        try:
            raw = json.loads(config_path.read_text())
            config = ShellConfig.from_dict(raw)
        except (OSError, json.JSONDecodeError, ValueError, KeyError):
            config = ShellConfig.defaults()

    # Reconcile: mark tasks whose tmux window is gone as lost
    try:
        from sable.agents.reconcile import reconcile
        from sable.core.db import DB_PATH
        lost_tasks = reconcile(str(DB_PATH))
        for task_name in lost_tasks:
            sys.stdout.write(f"[warning] task '{task_name}' was lost while disconnected\n")
        sys.stdout.flush()
    except (ImportError, sqlite3.Error, OSError):
        # No tmux, no database, or no tasks table yet. Reconcile is a tidy-up
        # on the way in, never a reason to refuse the login shell.
        pass

    # --- Session resume: load compressed context if available ---
    username = os.environ.get("USER", os.environ.get("USERNAME", "user"))
    ctx = ""
    if not os.environ.get("AGENTIC_NEW_SESSION"):
        try:
            from sable.memory.session import load_session_context
            ctx = load_session_context(username) or ""
            if ctx:
                preview = ctx[:300] + ("..." if len(ctx) > 300 else "")
                sys.stdout.write(f"session resumed ({len(ctx)} chars)\n{preview}\n")
                sys.stdout.flush()
        except (ImportError, sqlite3.Error, OSError):
            pass  # Session resume is best-effort

    # --- Launch tmux session with sidebar (no-op if already in tmux or tmux unavailable) ---
    if False:  # tmux handled by wrapper script
        try:
            from sable.ui.tmux.layout import create_session
            create_session(username)
            # create_session calls os.execvp to attach if we reach here, tmux unavailable
        except (ImportError, OSError):
            pass

    from sable.app import repl as loop# lazy import to avoid circular imports

    # Welcome banner (screen already cleared by wrapper before Python starts)
    sys.stdout.write("\n\033[38;5;141m  ✦ Sable\033[0m\n")
    sys.stdout.write(f"\033[2;37m  {config.backend} · {config.model}  |  type naturally or use bash directly\033[0m\n")

    # I7: anything running in a reduced capability says so here, once, with
    # what still works. A silent fallback is the failure this prevents.
    _report_degradations()
    if wrap_mode:
        sys.stdout.write(
            "\033[2;37m  wrap mode: running inside your bash, /exit returns to it\033[0m\n"
        )
    sys.stdout.write("\n")
    sys.stdout.flush()

    try:
        loop.start(config, session_id, session_context=ctx)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
