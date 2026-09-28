"""tmux session and pane layout.

On login, creates a new tmux session named 'sable-{username}' and splits
the window 80/20 horizontally:
- Left (80%): shell REPL
- Right (20%): telemetry watch process

Ctrl+T toggles sidebar visibility.
Auto-hides sidebar if terminal width < 100 columns.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

TASKS_PANEL_HEIGHT_PERCENT = 12


def _get_terminal_width() -> int:
    """Return current terminal width."""
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def create_session(username: str) -> None:
    """Create the sable tmux session with 80/20 split.

    If already inside a tmux session, this is a no-op.

    Args:
        username: OS username, used to name the session.
    """
    # If already in tmux, do nothing
    if os.environ.get("TMUX"):
        return

    if not shutil.which("tmux"):
        return

    try:
        import libtmux

        server = libtmux.Server()
        session_name = f"sable-{username}"

        # If session already exists, attach and return
        existing = server.find_where({"session_name": session_name})
        if existing:
            os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])
            return

        # Create new session (detached so we can configure it)
        session = server.new_session(
            session_name=session_name,
            detach=True,
            window_name="shell",
        )
        window = session.active_window
        main_pane = window.active_pane

        width = _get_terminal_width()

        if width >= 100:
            # Split right pane at 20% width
            sidebar_width = max(20, width // 5)
            sidebar_pane = window.split_window(
                vertical=False,  # horizontal split (side by side)
                percent=20,
                start_directory=str(os.path.expanduser("~")),
            )
            # Start telemetry watcher in sidebar
            python_bin = sys.executable
            sidebar_pane.send_keys(f"{python_bin} -m sable.ui.sidebar.watch", enter=True)

        # Create tasks panel horizontal strip at bottom (vertical=True splits horizontally)
        tasks_pane = window.split_window(
            vertical=True,
            percent=TASKS_PANEL_HEIGHT_PERCENT,
            start_directory=str(os.path.expanduser("~")),
        )
        python_bin = sys.executable
        tasks_pane.send_keys(
            f"PROMPT_TOOLKIT_NO_CPR=1 {python_bin} -m sable.ui.sidebar.agents_panel",
            enter=True,
        )

        # Start the REPL in main pane
        python_bin = sys.executable
        # No --no-tmux flag: nothing parses one, and it would be redundant if
        # it did. create_session() already returns early when $TMUX is set,
        # which is the condition the flag was standing in for.
        main_pane.send_keys(f"{python_bin} -m sable.app.main", enter=True)

        # Attach to the session
        os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])

    except (ImportError, OSError, subprocess.SubprocessError, AttributeError, KeyError):
        # libtmux unavailable, or tmux itself failed. Run without the sidebar
        # rather than refusing to start; the I7 banner reports the same thing.
        return


def toggle_sidebar() -> None:
    """Toggle visibility of the telemetry sidebar pane."""
    if not os.environ.get("TMUX"):
        return

    try:
        import libtmux

        server = libtmux.Server()
        session = server.find_where({"session_name": os.environ.get("TMUX_SESSION", "")})
        if not session:
            # Try to find current session from TMUX env
            tmux_env = os.environ.get("TMUX", "")
            # TMUX=socket,pid,session_id use list-panes approach
            import subprocess
            result = subprocess.run(
                ["tmux", "display-message", "-p", "#{session_name}"],
                capture_output=True, text=True,
            )
            session_name = result.stdout.strip()
            session = server.find_where({"session_name": session_name})

        if not session:
            return

        window = session.active_window
        panes = window.panes

        if len(panes) < 2:
            return

        # Toggle: hide if visible (more than 1 pane), show if hidden
        sidebar_pane = panes[-1]
        # libtmux doesn't have a direct hide API; resize to 0 width to "hide"
        # Instead, use resize-pane -x to collapse/restore
        import subprocess
        # Check current width
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", sidebar_pane.id, "#{pane_width}"],
            capture_output=True, text=True,
        )
        current_width = int(result.stdout.strip() or "0")

        if current_width > 2:
            # Collapse to 1 column (effectively hidden)
            subprocess.run(["tmux", "resize-pane", "-t", sidebar_pane.id, "-x", "1"])
        else:
            # Restore to 20% of terminal width
            term_width = _get_terminal_width()
            sidebar_width = max(20, term_width // 5)
            subprocess.run(["tmux", "resize-pane", "-t", sidebar_pane.id, "-x", str(sidebar_width)])

    except (ImportError, OSError, subprocess.SubprocessError, AttributeError,
            IndexError, ValueError):
        return


# ---------------------------------------------------------------------------
# G6 layout presets
# ---------------------------------------------------------------------------

#: preset -> (main pane zoomed, sidebar shown, tasks strip shown)
PRESETS: dict[str, tuple[bool, bool, bool]] = {
    "focus": (True, False, False),
    "fleet": (False, True, True),
    "minimal": (False, False, True),
}


def _pick_panes(panes: list[tuple[str, int, int]], main_id: str) -> tuple[str | None, str | None]:
    """(sidebar, tasks) pane ids from `(id, width, height)` rows.

    create_session makes the sidebar a full-height column and the tasks panel
    a short strip, so among the panes that are not ours the tallest is the
    sidebar and the other is the tasks strip.
    """
    others = sorted((p for p in panes if p[0] != main_id), key=lambda p: -p[2])
    sidebar = others[0][0] if len(others) >= 2 else None
    tasks = others[-1][0] if others else None
    return sidebar, tasks


def preset_commands(name: str, main_id: str, panes: list[tuple[str, int, int]],
                    zoomed: bool, term_width: int, term_height: int) -> list[list[str]]:
    """The tmux argv lists that turn the current layout into preset `name`."""
    zoom, show_sidebar, show_tasks = PRESETS[name]
    sidebar, tasks = _pick_panes(panes, main_id)
    cmds: list[list[str]] = []
    if zoom != zoomed:
        cmds.append(["tmux", "resize-pane", "-Z", "-t", main_id])
    if zoom:
        return cmds
    if sidebar:
        width = max(20, term_width // 5) if show_sidebar else 1
        cmds.append(["tmux", "resize-pane", "-t", sidebar, "-x", str(width)])
    if tasks:
        height = max(3, term_height * TASKS_PANEL_HEIGHT_PERCENT // 100) if show_tasks else 1
        cmds.append(["tmux", "resize-pane", "-t", tasks, "-y", str(height)])
    return cmds


def apply_preset(name: str) -> str:
    """Apply a layout preset to the current tmux window. Returns a message."""
    if name not in PRESETS:
        return f"usage: /layout {'|'.join(PRESETS)}"
    main_id = os.environ.get("TMUX_PANE", "")
    if not os.environ.get("TMUX") or not main_id:
        return "layout: not inside tmux, nothing to change"
    try:
        res = subprocess.run(
            ["tmux", "list-panes", "-t", main_id,
             "-F", "#{pane_id} #{pane_width} #{pane_height} #{window_zoomed_flag}"
             " #{window_width} #{window_height}"],
            capture_output=True, text=True, timeout=2,
        )
        rows = [line.split() for line in res.stdout.splitlines() if line.strip()]
        panes = [(r[0], int(r[1]), int(r[2])) for r in rows]
        zoomed = rows[0][3] == "1"
        width, height = int(rows[0][4]), int(rows[0][5])
        for cmd in preset_commands(name, main_id, panes, zoomed, width, height):
            subprocess.run(cmd, capture_output=True, timeout=2)
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return "layout: tmux did not answer"
    return f"layout: {name}"
