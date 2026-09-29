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


_CLI_USAGE = """sable - your server's AI operator

  sable              start the shell, or re-attach a running session
  sable on           enable the agentic layer for new logins
  sable off          disable it; logins go straight to bash
  sable status       report which mode is active
  sable daemon ...   run|install|status|stop the background daemon (sabled)
  sable doctor       check the install; --fix migrates config and repairs
  sable eval         run the eval task suite (mock backend unless --backend)
  sable --wrap      run inside the current bash, no chsh or /etc/shells
  sable --mcp-serve  serve Sable's tools to an MCP client over stdio
  sable export [file]          archive skills, palace, policy and hooks
  sable import <file> [--yes]  restore such an archive (backs up overwrites)
  sable sync <remote> [--yes]  the same set through a git remote
  sable share --approve-only [--ttl 1h] [--name N] [--topic T]
                     a teammate approves inbox items from ntfy
  sable share --read-only [--ttl 1h] [--topic T]   a /dash snapshot every 5 min
  sable share --list | --stop [ID]
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

    if command == "daemon":
        from sable.daemon import service

        sys.exit(service.main(argv[1:]))

    if command == "doctor":
        from sable.app import doctor

        sys.exit(doctor.main(argv[1:]))

    if command == "eval":
        from sable.evals import runner

        sys.exit(runner.main(argv[1:]))

    if command == "--mcp-serve":
        # No REPL, no tmux, no banner: stdout belongs to the protocol.
        from sable.policy.privilege import root_refusal

        refusal = root_refusal()
        if refusal:
            sys.stderr.write(refusal + "\n")
            sys.exit(1)
        from sable.mcp import serve

        sys.exit(serve.main())

    if command in ("export", "import", "sync"):
        sys.exit(_portable(command, argv[1:]))

    if command == "share":
        sys.exit(_share(argv[1:]))

    if command in ("on", "off", "status"):
        from sable.app import mode

        action = {"on": mode.enable, "off": mode.disable, "status": mode.status}[command]
        sys.stdout.write(action() + "\n")
        sys.stdout.flush()
        return True

    return False


def _portable(command: str, args: list[str]) -> int:
    """`sable export|import|sync` (I8); the logic is in core/portable.py."""
    from pathlib import Path as _Path

    from sable.core import portable
    from sable.policy.rules import match_secret
    from sable.ui.console import out

    yes = "--yes" in args
    rest = [a for a in args if a != "--yes"]

    def confirm(prompt: str) -> bool:
        try:
            return input(prompt).strip().lower() in ("y", "yes")
        except EOFError:
            return False

    def reindex() -> None:
        try:
            from sable.memory import palace
        except ImportError:  # the palace ships in a parallel task
            return
        if hasattr(palace, "reindex"):
            palace.reindex()

    def mark_imported(names: set[str], origin: str) -> None:
        from sable.skills.index import SkillIndex

        SkillIndex().mark_imported(names, origin)

    if command == "export":
        dest = _Path(rest[0]) if rest else None
        return portable.export(dest, out, match_secret)
    if len(rest) != 1:
        out(f"usage: sable {command} <{'file' if command == 'import' else 'git-remote'}> [--yes]")
        return 2
    if command == "import":
        return portable.import_archive(_Path(rest[0]), out, confirm, yes, reindex,
                                       mark_imported)
    return portable.sync(rest[0], out, confirm, yes, match_secret, reindex, mark_imported)


def _share(args: list[str]) -> int:
    """`sable share` (K12); the daemon handler in daemon/share.py does the pushing."""
    import argparse
    import time

    from sable.core.db import DB_PATH
    from sable.daemon import notify, share
    from sable.ui.console import out

    p = argparse.ArgumentParser(prog="sable share")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--approve-only", action="store_true")
    g.add_argument("--read-only", action="store_true")
    g.add_argument("--list", action="store_true")
    g.add_argument("--stop", nargs="?", const="", metavar="ID")
    p.add_argument("--ttl", default="1h")
    p.add_argument("--name")
    p.add_argument("--topic")
    try:
        a = p.parse_args(args)
        ttl = share.parse_ttl(a.ttl)
    except ValueError as exc:
        out(f"sable share: {exc}")
        return 2
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        if a.list:
            rows = share.list_active(conn)
            for r in rows:
                left = int((r["expires_at"] - time.time()) // 60)
                out(f"{r['id']}  {r['mode']}  {r['name'] or '-'}  topic {r['topic']}  {left} min left")
            if not rows:
                out("no active shares")
            return 0
        if a.stop is not None:
            n = share.stop(conn, a.stop or None)
            out(f"stopped {n} share(s)")
            return 0 if n else 1
        if a.approve_only and not a.name:
            out("sable share --approve-only needs --name, it goes in the audit")
            return 2
        try:
            sh = share.create(conn, "approve" if a.approve_only else "read", ttl, a.name, a.topic)
        except ValueError as exc:
            out(f"sable share: {exc}")
            return 2
    finally:
        conn.close()
    server = notify.settings()["server"]
    out(f"share {sh['id']} ({sh['mode']}) for {a.ttl}")
    out(f"  topic: {server}/{sh['topic']}")
    out("  the teammate subscribes to that topic in the ntfy app (or opens the URL);")
    if sh["reply_topic"]:
        out(f"  replies arrive on {sh['reply_topic']}; each Approve / Reject works once, for 1 h,")
        out("  and only while this share is active. deny-tier commands are never offered.")
    out(f"  the daemon does the pushing (sable daemon status); stop with: sable share --stop {sh['id']}")
    return 0


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

    # I4: never run the agent layer as root, and never lock root out either.
    from sable.policy.privilege import root_refusal

    refusal = root_refusal()
    if refusal:
        sys.stderr.write(refusal + "\n")
        sys.stderr.flush()
        os.execvp("/bin/bash", ["/bin/bash", "-l"])

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

    # H4: traces to otel.endpoint; with no endpoint this starts nothing.
    from sable.core import otel
    from sable.policy.engine import redact_text
    otel.configure(config.otel, redact=redact_text)

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
