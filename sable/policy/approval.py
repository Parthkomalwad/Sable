"""The approval card: what a waiting-for-you moment looks like.

Every place Sable stops and asks before running something draws the same
box, so "the shell is waiting on me" reads the same everywhere:

    ╭─ ◆ WAITING FOR YOU ─────────────────────────────── writes ─╮
    │ Creating a Python script that prints 'Hello, World!'        │
    │                                                             │
    │ $ echo 'print("Hello, World!")' > hello.py                  │
    ╰─────────────────────────────────────────────────────────────╯
      ↵ run   e edit   q cancel  ›

The border takes the risk colour the caller passes (green read-only, amber
writes, red destructive). Pure rendering: callers read the answer.
"""
from __future__ import annotations

import shutil
import textwrap

RESET = "\033[0m"
BOLD = "\033[1m"
TEXT = "\033[38;5;252m"
CMD = "\033[1;38;5;255m"
DIM = "\033[38;5;245m"
FAINT = "\033[38;5;240m"
KEY = "\033[1;38;5;141m"
OK = "\033[38;5;114m"
NO = "\033[38;5;203m"

MAX_WIDTH = 96


def _width() -> int:
    return max(40, min(MAX_WIDTH, shutil.get_terminal_size((100, 24)).columns - 4))


def card(title: str, colour: str, lines: list[tuple[str, str]], tag: str = "",
         width: int | None = None) -> str:
    """A rounded box: `title` in the top edge, `tag` at its right, then lines.

    `lines` are (style, text) pairs; "" text is a blank row. Long text wraps
    inside the box rather than breaking its right edge.
    """
    width = width or _width()
    inner = width - 4
    head = f"─ {title} "
    tail = f" {tag} ─" if tag else ""
    fill = max(1, width - 2 - len(head) - len(tail))
    out = [f"  {colour}╭{BOLD}{head}{RESET}{colour}{'─' * fill}{tail}╮{RESET}"]
    for style, text in lines:
        chunks = textwrap.wrap(text, inner, break_long_words=True, break_on_hyphens=False,
                               subsequent_indent="  " if text.startswith("$ ") else "") or [""]
        for chunk in chunks:
            out.append(f"  {colour}│{RESET} {style}{chunk:<{inner}}{RESET} {colour}│{RESET}")
    out.append(f"  {colour}╰{'─' * (width - 2)}╯{RESET}")
    return "\n".join(out)


def keys(*pairs: tuple[str, str]) -> str:
    """The choice line under a card: `↵ run   e edit   q cancel  ›`."""
    parts = [f"{KEY}{k}{RESET} {DIM}{label}{RESET}" for k, label in pairs]
    return "    " + "   ".join(parts) + f"  {FAINT}›{RESET} "


def command_card(explanation: str, command: str, colour: str, level: str) -> str:
    """The orchestrator's proposal: why, then the command."""
    return card("◆ WAITING FOR YOU", colour,
                [(TEXT, explanation), ("", ""), (CMD, f"$ {command}")], tag=level)


def yes_card(label: str, command: str, why: str, colour: str, level: str) -> str:
    """A policy confirm: the rule that fired, what it guards against, the command."""
    return card(f"⚠ {label}", colour,
                [(CMD, f"$ {command}"), ("", ""), (DIM, why)], tag=level)


def outcome(approved: bool, what: str = "") -> str:
    """One line after the answer, so the scrollback records the decision."""
    if approved:
        return f"    {OK}✓ approved{RESET}{DIM}{'  ' + what if what else ''}{RESET}"
    return f"    {NO}✗ cancelled{RESET}{DIM}{'  ' + what if what else ''}{RESET}"
