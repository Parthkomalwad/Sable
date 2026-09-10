"""Shared terminal output.

Extracted from `app/repl.py` in Phase 0.5 step 3 so that the builtin
handlers in `app/builtins/` can print without importing the REPL back.

`out()` writes to stdout directly rather than through Rich. That is
deliberate and pre-existing: it is used for plain single lines and for text
that is already ANSI-coloured, where Rich's markup parsing would try to
interpret square brackets in user data (a path, a command, an error) as
style tags. Panels, tables and anything that needs styling go through Rich
in the module that renders them.

Phase 1 replaces this with the single themed Console described in
structure.md §2 (`ui/console.py` proper). Until then this keeps the
behaviour identical to what shipped, moved rather than rewritten.
"""
from __future__ import annotations

import sys


def out(text: str) -> None:
    """Write one line to stdout and flush. Never blocks."""
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
