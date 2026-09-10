"""The prompt surface: completion, the powerline prompt, key bindings.

Extracted from `app/repl.py` in Phase 0.5 step 3 (docs/structure.md §5,
`ui/prompt/*`). Pure move, no logic change.

These three are what the user actually touches at the prompt, and they are
prompt_toolkit-specific in a way the REPL loop is not. Keeping them here
lets `app/repl.py` stay a thin read-route-dispatch loop.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings

from sable.ui.console import out as _out


class _ShellCompleter(Completer):
    """Tab completer: first word = command from PATH, rest = filesystem paths."""

    def __init__(self) -> None:
        self._path_completer = PathCompleter(expanduser=True)
        self._bins: list[str] = []
        self._bins_loaded = False

    def _load_bins(self) -> None:
        """Lazily collect all executable names from PATH directories."""
        if self._bins_loaded:
            return
        seen: set[str] = set()
        for directory in os.environ.get("PATH", "").split(":"):
            try:
                for entry in os.scandir(directory):
                    if entry.is_file(follow_symlinks=True) and os.access(entry.path, os.X_OK):
                        seen.add(entry.name)
            except (PermissionError, FileNotFoundError):
                pass
        self._bins = sorted(seen)
        self._bins_loaded = True

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        words = text.split()

        # If no words typed yet, or only one word being typed → complete command name
        if not words or (len(words) == 1 and not text.endswith(" ")):
            self._load_bins()
            prefix = words[0] if words else ""
            for name in self._bins:
                if name.startswith(prefix):
                    yield Completion(name, start_position=-len(prefix))
            return

        # Otherwise complete the current argument as a filesystem path
        yield from self._path_completer.get_completions(document, complete_event)


def _render_prompt(cwd: str, last_exit: int) -> str:
    """Return a Powerline-style prompt string for prompt_toolkit HTML().

    Segments: [path block] [git branch block] [time block] ❯
    Uses ANSI 256-color codes via HTML() spans.
    Falls back gracefully if git is unavailable.
    """
    import subprocess
    import time as _time

    home = str(Path.home())
    display_cwd = cwd.replace(home, "~") if cwd.startswith(home) else cwd

    # Git branch (silent fail)
    branch = ""
    try:
        res = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=1
        )
        branch = res.stdout.strip()
    except Exception:
        pass

    hhmm = _time.strftime("%H:%M")

    # Path segment soft blue bg (#005f87 = 24)
    path_seg = (
        '\033[48;5;24m\033[97m'   # blue bg, bright white fg
        f' {display_cwd} '
        '\033[0m'
        '\033[38;5;24m\033[48;5;55m\ue0b0\033[0m'  # powerline arrow (Unicode or space fallback)
    )

    # Git segment soft purple bg (55)
    git_seg = ""
    if branch:
        git_seg = (
            '\033[48;5;55m\033[97m'
            f'  {branch} '
            '\033[0m'
            '\033[38;5;55m\033[48;5;236m\ue0b0\033[0m'
        )

    # Time segment dark grey bg (236)
    time_seg = (
        '\033[48;5;236m\033[2;37m'
        f' {hhmm} '
        '\033[0m '
    )

    # Cursor white normally, red if last exit non-zero
    if last_exit != 0:
        cursor = '\033[38;5;203m❯\033[0m'
    else:
        cursor = '\033[0;37m❯\033[0m'

    return path_seg + git_seg + time_seg + cursor + ' '


def _make_key_bindings(db=None) -> KeyBindings:
    kb = KeyBindings()

    @kb.add("c-b")
    def _ctrl_b(event) -> None:
        global _bypass_next
        _bypass_next = True
        # Whatever is already typed was misrouted by definition: the user
        # reached for the bypass. Record it as a bash correction (I3).
        pending = event.app.current_buffer.text.strip()
        if pending:
            _record_router_correction(pending, "bash")
        _out("[bash mode] next command runs directly")

    @kb.add("c-t")
    def _ctrl_t(event) -> None:
        try:
            from sable.ui.tmux.layout import toggle_sidebar
            toggle_sidebar()
        except Exception:
            pass

    @kb.add("c-\\")
    def _ctrl_backslash(event) -> None:
        """Drop to a plain bash subshell for as long as the user wants."""
        event.app.current_buffer.set_document(
            __import__("prompt_toolkit.document", fromlist=["Document"]).Document("/bash")
        )
        event.app.current_buffer.validate_and_handle()

    @kb.add("c-x")
    def _ctrl_x(event) -> None:
        event.app.current_buffer.set_document(
            __import__("prompt_toolkit.document", fromlist=["Document"]).Document("/config")
        )
        event.app.current_buffer.validate_and_handle()

    return kb
