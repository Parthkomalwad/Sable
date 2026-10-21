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

        if text.lstrip().startswith("/"):
            yield from _slash_completions(text)
            return

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


def _slash_completions(text: str):
    """Builtin names with their summaries, then the chosen command's subcommands.

    Both come from the command catalog (ui/commands.py), the list /help reads.
    """
    from sable.ui.commands import COMMANDS, find, subcommands

    words = text.split()
    if len(words) == 1 and not text.endswith(" "):
        prefix = words[0]
        for c in COMMANDS:
            if c.name.startswith(prefix):
                yield Completion(c.name, start_position=-len(prefix),
                                 display=c.name, display_meta=c.summary)
        return
    cmd = find(words[0])
    if cmd is None:
        return
    typed = "" if text.endswith(" ") else words[-1]
    if len(words) - (0 if text.endswith(" ") else 1) != 1:
        return   # only the word straight after the command is a subcommand
    for word, what in subcommands(cmd):
        if word.startswith(typed):
            yield Completion(word, start_position=-len(typed), display=word, display_meta=what)


#: Completion menu colours, in the prompt's palette.
COMPLETION_STYLE = {
    "completion-menu": "bg:#1c2129 #c9d1d9",
    "completion-menu.completion": "bg:#1c2129 #b4a9ff",
    "completion-menu.completion.current": "bg:#3b3470 #ffffff bold",
    "completion-menu.meta.completion": "bg:#1c2129 #7d8794",
    "completion-menu.meta.completion.current": "bg:#3b3470 #d0cce8",
    "scrollbar.background": "bg:#1c2129",
    "scrollbar.button": "bg:#4d4690",
}


def slash_line_filter():
    """True while the line being typed starts with `/`: the menu opens as you type."""
    from prompt_toolkit.application.current import get_app
    from prompt_toolkit.filters import Condition
    return Condition(lambda: get_app().current_buffer.text.lstrip().startswith("/"))


def _render_prompt(cwd: str, last_exit: int) -> str:
    """The prompt: `~/api main ✓ ❯`, as in the README.

    Path, then branch when there is one, then the last exit: a check, or a
    cross with the code. The cursor is the accent colour. The time used to
    sit here too; the tmux status bar carries it now.
    """
    import subprocess

    home = str(Path.home())
    display_cwd = cwd.replace(home, "~", 1) if cwd.startswith(home) else cwd

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

    from sable.ui.theme import sgr

    # Colours come from the active G6 theme (ui/theme.py).
    reset = "\033[0m"
    parts = [f"{sgr('path')}{display_cwd}{reset}"]
    if branch:
        parts.append(f"{sgr('git')}{branch}{reset}")
    if last_exit == 0:
        parts.append(f"{sgr('ok')}✓{reset}")
    else:
        parts.append(f"{sgr('err')}✗ {last_exit}{reset}")
    return " ".join(parts) + f" {sgr('accent')}❯{reset} "


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
