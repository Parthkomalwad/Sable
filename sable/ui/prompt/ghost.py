"""K1 ghost text: history suggestions ranked by directory, then recency.

`FileHistory` stores lines without the directory they were typed in, so a
second file beside it keeps `cwd<TAB>command` per line. The old history file
is untouched and still drives Up/Down and Ctrl+R.

The index is loaded on a background thread. Until it is ready, and whenever a
lookup runs past its budget, the suggestion is None: a missing hint costs
nothing, a slow keystroke is felt.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion

BUDGET_S = 0.150
#: Entries kept in memory. Older lines stay on disk but are not searched.
MAX_ENTRIES = 5000


def default_path() -> Path:
    return Path.home() / ".local" / "share" / "agentic-shell" / "history_cwd"


class CwdHistorySuggest(AutoSuggest):
    """Prefix match over history: same directory first, then most recent."""

    def __init__(self, path: Path | None = None, cwd_fn=None, load: bool = True) -> None:
        self._path = path or default_path()
        self._cwd_fn = cwd_fn
        self._entries: list[tuple[str, str]] = []  # oldest first
        self._ready = threading.Event()
        if load:
            threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            lines = self._path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        entries = []
        for raw in lines[-MAX_ENTRIES:]:
            cwd, sep, cmd = raw.partition("\t")
            if sep and cmd:
                entries.append((cwd, cmd))
        self._entries = entries + self._entries
        self._ready.set()

    def record(self, cwd: str, command: str) -> None:
        """Add one typed line to the index and the file. Never raises."""
        command = command.strip()
        if not command or "\n" in command or "\t" in cwd:
            return
        self._entries.append((cwd, command))
        del self._entries[:-MAX_ENTRIES]
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(f"{cwd}\t{command}\n")
        except OSError:
            pass

    def suggest(self, prefix: str, cwd: str, budget: float = BUDGET_S) -> str | None:
        """The rest of the best matching command, or None."""
        if not prefix.strip() or not self._ready.is_set():
            return None
        deadline = time.monotonic() + budget
        fallback = None
        # Newest first: the first same-directory hit wins outright, the first
        # hit anywhere is kept in case none of them is here.
        for i, (entry_cwd, cmd) in enumerate(reversed(self._entries)):
            if i % 256 == 0 and time.monotonic() > deadline:
                return None
            if cmd.startswith(prefix) and cmd != prefix:
                if entry_cwd == cwd:
                    return cmd[len(prefix):]
                if fallback is None:
                    fallback = cmd[len(prefix):]
        return fallback

    def get_suggestion(self, buffer, document):
        import os

        try:
            cwd = self._cwd_fn() if self._cwd_fn else os.getcwd()
        except OSError:
            return None
        rest = self.suggest(document.text.splitlines()[-1] if document.text else "", cwd)
        return Suggestion(rest) if rest else None
