"""Entry point for Sable.

SSH_ORIGINAL_COMMAND bypass MUST remain the first executable code.
Handles startup, config loading, session resume, and launches the REPL.
"""
import os
import sys

# --- SSH bypass: must be first executable lines, non-negotiable ---
_original_cmd = os.environ.get("SSH_ORIGINAL_COMMAND")
if _original_cmd:
    os.execvp("/bin/bash", ["/bin/bash", "-c", _original_cmd])
    sys.exit(0)
# -----------------------------------------------------------------


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
        from shell import __version__

        sys.stdout.write(f"sable {__version__}\n")
        return True

    if command in ("on", "off", "status"):
        from shell import mode

        action = {"on": mode.enable, "off": mode.disable, "status": mode.status}[command]
        sys.stdout.write(action() + "\n")
        sys.stdout.flush()
        return True

    return False


def main(argv: list[str] | None = None) -> None:
    """Shell entry point called by the installed binary."""
    import json
    import uuid
    from pathlib import Path

    from shell.config.schema import ShellConfig

    argv = list(sys.argv[1:] if argv is None else argv)

    if _handle_cli(argv):
        return

    wrap_mode = "--wrap" in argv

    # `sable off` is honoured here too, so an already-open terminal that runs
    # `sable` after disabling gets the same answer as a fresh login.
    from shell import mode, paths

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
        from shell.config.wizard import run_wizard
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
        from shell.config.wizard import run_wizard
        run_wizard()
        try:
            raw = json.loads(config_path.read_text())
            config = ShellConfig.from_dict(raw)
        except Exception:
            config = ShellConfig.defaults()

    # Reconcile: mark tasks whose tmux window is gone as lost
    try:
        from shell.tasks.reconcile import reconcile
        from shell.telemetry.db import DB_PATH
        lost_tasks = reconcile(str(DB_PATH))
        for task_name in lost_tasks:
            sys.stdout.write(f"[warning] task '{task_name}' was lost while disconnected\n")
        sys.stdout.flush()
    except Exception:
        pass

    # --- Session resume: load compressed context if available ---
    username = os.environ.get("USER", os.environ.get("USERNAME", "user"))
    ctx = ""
    if not os.environ.get("AGENTIC_NEW_SESSION"):
        try:
            from shell.memory.store import load_session_context
            ctx = load_session_context(username) or ""
            if ctx:
                preview = ctx[:300] + ("..." if len(ctx) > 300 else "")
                sys.stdout.write(f"session resumed ({len(ctx)} chars)\n{preview}\n")
                sys.stdout.flush()
        except Exception:
            pass  # Session resume is best-effort

    # --- Launch tmux session with sidebar (no-op if already in tmux or tmux unavailable) ---
    if False:  # tmux handled by wrapper script
        try:
            from shell.tui.layout import create_session
            create_session(username)
            # create_session calls os.execvp to attach if we reach here, tmux unavailable
        except Exception:
            pass

    from shell import loop  # lazy import to avoid circular imports

    # Welcome banner (screen already cleared by wrapper before Python starts)
    sys.stdout.write("\n\033[38;5;141m  ✦ Sable\033[0m\n")
    sys.stdout.write(f"\033[2;37m  {config.backend} · {config.model}  |  type naturally or use bash directly\033[0m\n")
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
