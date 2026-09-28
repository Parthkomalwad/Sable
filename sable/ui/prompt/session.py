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
    except (OSError, subprocess.SubprocessError):
        # No git, or not a repo. The prompt simply loses its branch segment.
        pass

    hhmm = _time.strftime("%H:%M")

    from sable.ui.theme import sgr

    # Colours come from the active G6 theme (ui/theme.py).
    reset = '\033[0m'
    path_seg = f"{sgr('path')} {display_cwd} {reset}{sgr('path_arrow')}\ue0b0{reset}"
    git_seg = ""
    if branch:
        git_seg = f"{sgr('git')}  {branch} {reset}{sgr('git_arrow')}\ue0b0{reset}"
    time_seg = f"{sgr('time')} {hhmm} {reset} "
    # The cursor takes the theme's error colour after a non-zero exit
    cursor = f"{sgr('err' if last_exit != 0 else 'ok')}\u276f{reset}"

    return path_seg + git_seg + time_seg + cursor + ' '


#: Ctrl+B's request, taken by the REPL before it routes the next line. It
#: lives here because the key binding runs here; it used to be a `global`
#: that named this module's variable while the REPL read its own, so Ctrl+B
#: printed "[bash mode]" and changed nothing.
_bypass_requested = False


def take_bypass() -> bool:
    """True once after Ctrl+B was pressed, then False until pressed again."""
    global _bypass_requested
    requested, _bypass_requested = _bypass_requested, False
    return requested


def _make_key_bindings(db=None) -> KeyBindings:
    kb = KeyBindings()

    @kb.add("c-b")
    def _ctrl_b(event) -> None:
        # Only the request is recorded here. Recording the router correction
        # (I3) is the REPL's job: this module is `ui` and may not import
        # `app`, which is where `_record_router_correction` lives; calling it
        # from here raised NameError whenever text was already typed.
        global _bypass_requested
        _bypass_requested = True
        _out("[bash mode] next command runs directly")

    @kb.add("c-t")
    def _ctrl_t(event) -> None:
        try:
            from sable.ui.tmux.layout import toggle_sidebar
            toggle_sidebar()
        except (ImportError, OSError):
            pass

    @kb.add("c-\\")
    def _ctrl_backslash(event) -> None:
        """Drop to a plain bash subshell for as long as the user wants."""
        event.app.current_buffer.set_document(
            __import__("prompt_toolkit.document", fromlist=["Document"]).Document("/bash")
        )
        event.app.current_buffer.validate_and_handle()

    @kb.add("c-g")
    def _ctrl_g(event) -> None:
        """Steer a running agent (A7).

        Prefills the guidance command rather than sending anything: nothing
        tracks which agent is "focused", and guessing wrong sends advice to
        the wrong worker. The user completes the name and the text, and the
        agent reads it at the top of its next turn.
        """
        from prompt_toolkit.document import Document

        event.app.current_buffer.set_document(Document("/task guide "))

    @kb.add("c-p")
    def _ctrl_p(event) -> None:
        """G5 palette. Submitted as a line so it opens outside this prompt."""
        from prompt_toolkit.document import Document

        event.app.current_buffer.set_document(Document("/palette"))
        event.app.current_buffer.validate_and_handle()

    @kb.add("c-x")
    def _ctrl_x(event) -> None:
        event.app.current_buffer.set_document(
            __import__("prompt_toolkit.document", fromlist=["Document"]).Document("/config")
        )
        event.app.current_buffer.validate_and_handle()

    return kb
