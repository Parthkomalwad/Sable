"""The `/new` builtin: restart the shell with a fresh session.

Extracted from `app/repl.py` in Phase 0.5 step 3. Pure move, no logic change.
"""
from __future__ import annotations

import time

from sable.ui.console import out as _out


def _start_new_session() -> None:
    import subprocess, shutil, time
    if not shutil.which("tmux"):
        _out("tmux not found restarting shell process.")
        os.execv(sys.executable, [sys.executable, "-m", "sable.app.main"])
        return

    _out("Starting new session...")
    try:
        result = subprocess.run(["tmux", "display-message", "-p", "#S"], capture_output=True, text=True)
        current_session = result.stdout.strip()
    except Exception:
        current_session = ""

    new_name = f"sable-{int(time.time()) % 10000}"
    install_dir = os.environ.get("PYTHONPATH", "")
    venv_python = os.environ.get("AGENTIC_PYTHON", sys.executable)

    try:
        # Create session sized to current terminal
        import shutil as _shutil
        _ts = _shutil.get_terminal_size((220, 50))
        subprocess.run(["tmux", "new-session", "-d", "-s", new_name,
                        "-x", str(_ts.columns), "-y", str(_ts.lines)], check=True)

        # Capture the main pane ID, then split using IDs (avoids numbering ambiguity)
        main_pane = subprocess.run(
            ["tmux", "display-message", "-t", f"{new_name}:0.0", "-p", "#{pane_id}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        # Split right: telemetry sidebar (48 cols)
        tele_pane = subprocess.run(
            ["tmux", "split-window", "-h", "-t", main_pane, "-l", "48", "-P", "-F", "#{pane_id}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        # Split main pane bottom: tasks panel (~30%)
        task_lines = max(8, _ts.lines * 30 // 100)
        task_pane = subprocess.run(
            ["tmux", "split-window", "-v", "-t", main_pane, "-l", str(task_lines), "-P", "-F", "#{pane_id}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        # Layout: main_pane=top-left shell, tele_pane=right, task_pane=bottom-left
        subprocess.run(["tmux", "send-keys", "-t", tele_pane,
            f"trap '' INT; clear; while true; do PYTHONPATH={install_dir} PROMPT_TOOLKIT_NO_CPR=1 {venv_python} -m sable.ui.sidebar.watch; sleep 2; done",
            "Enter"], check=True)

        subprocess.run(["tmux", "send-keys", "-t", task_pane,
            f"trap '' INT; clear; while true; do PYTHONPATH={install_dir} PROMPT_TOOLKIT_NO_CPR=1 {venv_python} -m sable.ui.sidebar.agents_panel; sleep 2; done",
            "Enter"], check=True)

        # Shell in main pane AGENTIC_NEW_SESSION=1 skips session resume
        subprocess.run(["tmux", "send-keys", "-t", main_pane,
            f"trap '' INT; EXIT_FLAG=$HOME/.local/share/agentic-shell/exit_requested; while true; do rm -f \"$EXIT_FLAG\"; clear; PYTHONPATH={install_dir} PROMPT_TOOLKIT_NO_CPR=1 NO_TMUX=1 AGENTIC_NEW_SESSION=1 {venv_python} -m sable.app.main; AGENTIC_NEW_SESSION=''; if [ -f \"$EXIT_FLAG\" ]; then rm -f \"$EXIT_FLAG\"; echo 'dropping to bash run agentic-shell to return'; exec /bin/bash; fi; echo '[shell exited restarting in 2s]'; sleep 2; done",
            "Enter"], check=True)

        subprocess.run(["tmux", "select-pane", "-t", main_pane], check=True)
        subprocess.run(["tmux", "switch-client", "-t", new_name], check=True)
        # Do NOT kill the old session the SSH client is attached to it.
        # Killing it would drop the connection. User can kill old sessions manually.
    except Exception as exc:
        _out(f"Failed to create new session: {exc}")
