"""Mode switch between the agentic shell and plain bash (I13).

Three ways out and back:

- `/bash` (alias `/plain`, key Ctrl+\\) drops to an interactive bash subshell in
  the same pane for as long as you want it. `exit` returns to the Sable prompt
  with session context intact, because the REPL process never went away.
- `sable off` writes a flag file that the .bashrc launcher and the restart loop
  both honour, so logins go straight to bash until `sable on`.
- `sable --wrap` runs Sable inside an existing bash without chsh or
  /etc/shells, the low-commitment install path.

Every switch prints one line saying which mode you are now in and how to get
back, so the shell is never silently in a state the user did not choose.
"""
from __future__ import annotations

import os
import shutil
import sys

from shell import paths

PURPLE = "\033[38;5;141m"
GREEN = "\033[38;5;114m"
DIM = "\033[2;37m"
RESET = "\033[0m"


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def banner(message: str, hint: str) -> None:
    """Print the one-line mode banner: where you are, how to leave."""
    _out(f"{PURPLE}  {message}{RESET}  {DIM}{hint}{RESET}")


# --- persistent on/off toggle -------------------------------------------------


def enable() -> str:
    """Remove the disabled flag. Returns a message for the caller to print."""
    try:
        paths.DISABLED_FLAG.unlink()
        return "sable is on. New logins land in the agentic shell."
    except FileNotFoundError:
        return "sable is already on."
    except OSError as exc:
        return f"could not enable sable: {exc}"


def disable() -> str:
    """Write the disabled flag. Returns a message for the caller to print."""
    try:
        paths.SABLE_HOME.mkdir(parents=True, exist_ok=True)
        paths.DISABLED_FLAG.write_text(
            "Sable is disabled. Delete this file or run: sable on\n",
            encoding="utf-8",
        )
        return "sable is off. Logins go straight to bash. Run: sable on"
    except OSError as exc:
        return f"could not disable sable: {exc}"


def status() -> str:
    """Return a one-line description of the current mode."""
    if paths.is_disabled():
        return f"sable is off ({paths.DISABLED_FLAG}). Run: sable on"
    return "sable is on. Run: sable off to log straight into bash."


# --- temporary escape to a plain subshell -------------------------------------


def run_plain_subshell(cwd: str | None = None) -> int:
    """Run an interactive bash in this pane until the user types `exit`.

    Returns the subshell's exit status. The Sable REPL is only suspended, not
    replaced, so returning restores the prompt with session context intact.

    The sidebar is hidden for the duration so the plain shell gets the full
    pane, and restored afterwards even if bash exits badly.
    """
    bash = shutil.which("bash") or "/bin/bash"
    cwd = cwd or os.getcwd()

    hidden = _hide_sidebar()
    banner("[plain] bash subshell", "type 'exit' to come back to sable")
    try:
        return _spawn_interactive(bash, cwd)
    finally:
        if hidden:
            _show_sidebar()
        banner("back in sable", "type a goal, or /bash to drop out again")


def _spawn_interactive(bash: str, cwd: str) -> int:
    """Run bash attached to this terminal and wait for it.

    ptyprocess owns command execution elsewhere in Sable, but an interactive
    subshell must inherit this process's real terminal: a pty wrapper would put
    a second pty between the user and bash, which breaks job control, window
    resize and Ctrl+C. os.spawnv hands over the actual tty and gives it back.
    """
    env_note = "[plain] "
    previous_ps1 = os.environ.get("PS1")
    os.environ["SABLE_PLAIN"] = "1"
    # Tag the prompt so it is obvious which shell you are typing into.
    os.environ["PS1"] = f"{env_note}\\w $ "
    try:
        return os.spawnve(
            os.P_WAIT,
            bash,
            [bash, "--norc", "-i"],
            {**os.environ, "PS1": f"{env_note}\\w $ "},
        )
    except OSError as exc:
        _out(f"could not start bash: {exc}")
        return 1
    finally:
        os.environ.pop("SABLE_PLAIN", None)
        if previous_ps1 is None:
            os.environ.pop("PS1", None)
        else:
            os.environ["PS1"] = previous_ps1


def _hide_sidebar() -> bool:
    """Hide the telemetry sidebar. Returns True if it was hidden."""
    if not os.environ.get("TMUX"):
        return False
    try:
        from shell.tui.layout import toggle_sidebar

        toggle_sidebar()
        return True
    except (ImportError, OSError):
        return False


def _show_sidebar() -> None:
    if not os.environ.get("TMUX"):
        return
    try:
        from shell.tui.layout import toggle_sidebar

        toggle_sidebar()
    except (ImportError, OSError):
        pass


# --- session re-attach --------------------------------------------------------


def session_name(username: str | None = None) -> str:
    user = username or os.environ.get("USER") or os.environ.get("USERNAME") or "user"
    return f"sable-{user}"


def existing_session() -> str | None:
    """Return the name of a live sable tmux session, or None."""
    if not shutil.which("tmux"):
        return None
    import subprocess

    name = session_name()
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", name],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    return name if result.returncode == 0 else None


def attach_existing() -> bool:
    """Attach to a running sable session if there is one.

    Replaces this process on success, so it only returns when there was
    nothing to attach to.
    """
    name = existing_session()
    if name is None or os.environ.get("TMUX"):
        return False
    banner(f"re-attaching to {name}", "detach with Ctrl+A d")
    os.execvp("tmux", ["tmux", "attach-session", "-t", name])
    return True
