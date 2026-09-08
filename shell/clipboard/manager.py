"""Clipboard/snippet manager interactive TUI picker and CLI parsing."""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shell.telemetry.db import Database

PURPLE = '\033[38;5;141m'
GREEN  = '\033[38;5;114m'
RED    = '\033[38;5;203m'
DIM    = '\033[2;37m'
RESET  = '\033[0m'


def _get_tmux_session() -> str:
    """Return the current tmux session name, or empty string if not in tmux."""
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#S"],
            capture_output=True, text=True, timeout=1
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _send_to_main_pane(command: str, session: str) -> None:
    """Send command string to pane 0.0 of the given tmux session."""
    subprocess.run(
        ["tmux", "send-keys", "-t", f"{session}:0.0", command, "Enter"],
        capture_output=True
    )


def run_clip_command(args_str: str, db: "Database") -> None:
    """Parse and execute a /clip subcommand.

    args_str is everything after '/clip ' e.g. 'add "docker ps" --note x --tags a,b'
    """
    try:
        parts = shlex.split(args_str)
    except ValueError:
        sys.stdout.write(f"{RED}clip: invalid quoting{RESET}\n")
        sys.stdout.flush()
        return

    if not parts:
        return  # caller opens TUI for empty args

    sub = parts[0]

    if sub == "list":
        _cmd_list(db)
    elif sub == "add":
        _cmd_add(parts[1:], db)
    elif sub == "del":
        _cmd_del(parts[1:], db)
    elif sub == "run":
        _cmd_run(parts[1:], db)
    else:
        sys.stdout.write(f"{RED}clip: unknown subcommand '{sub}'. Try /clip, /clip list, /clip add, /clip del, /clip run{RESET}\n")
        sys.stdout.flush()


def _cmd_list(db: "Database") -> None:
    snippets = db.list_snippets()
    if not snippets:
        sys.stdout.write(f"{DIM}  No snippets yet. Use /clip add \"<command>\" to save one.{RESET}\n")
        sys.stdout.flush()
        return
    sys.stdout.write(f"\n{PURPLE}  Saved Snippets{RESET}\n")
    sys.stdout.write(f"  {DIM}{'─' * 55}{RESET}\n")
    for s in snippets:
        tag_str = f"[{s['tags']}]" if s['tags'] else ""
        note_str = s['note'] or s['command'][:40]
        sys.stdout.write(
            f"  {DIM}{s['id']:>3}{RESET}  {note_str:<35}  {DIM}{tag_str}{RESET}\n"
            f"       {DIM}{s['command'][:55]}{RESET}\n"
        )
    sys.stdout.write("\n")
    sys.stdout.flush()


def _cmd_add(args: list[str], db: "Database") -> None:
    """Parse: ["<command>"] or ["<command>", "--note", "x", "--tags", "a,b"]"""
    if not args:
        sys.stdout.write(f"{RED}clip add: provide a command in quotes, e.g. /clip add \"docker ps -a\"{RESET}\n")
        sys.stdout.flush()
        return

    command = args[0]
    note = ""
    tags = ""

    i = 1
    while i < len(args):
        if args[i] == "--note" and i + 1 < len(args):
            note = args[i + 1]; i += 2
        elif args[i] == "--tags" and i + 1 < len(args):
            tags = args[i + 1]; i += 2
        else:
            i += 1

    # If note/tags not provided via flags, prompt interactively
    if not note:
        sys.stdout.write(f"  {DIM}Note (Enter to skip): {RESET}")
        sys.stdout.flush()
        try:
            note = input("").strip()
        except (EOFError, KeyboardInterrupt):
            note = ""

    if not tags:
        sys.stdout.write(f"  {DIM}Tags comma-separated (Enter to skip): {RESET}")
        sys.stdout.flush()
        try:
            tags = input("").strip()
        except (EOFError, KeyboardInterrupt):
            tags = ""

    snippet_id = db.add_snippet(command, note, tags)
    sys.stdout.write(f"  {GREEN}✓ snippet #{snippet_id} saved{RESET}\n\n")
    sys.stdout.flush()


def _cmd_del(args: list[str], db: "Database") -> None:
    if not args or not args[0].isdigit():
        sys.stdout.write(f"{RED}clip del: provide a snippet id, e.g. /clip del 3{RESET}\n")
        sys.stdout.flush()
        return
    snippet_id = int(args[0])
    deleted = db.delete_snippet(snippet_id)
    if deleted:
        sys.stdout.write(f"  {GREEN}✓ snippet #{snippet_id} deleted{RESET}\n\n")
    else:
        sys.stdout.write(f"  {RED}snippet #{snippet_id} not found{RESET}\n\n")
    sys.stdout.flush()


def _cmd_run(args: list[str], db: "Database") -> None:
    if not args or not args[0].isdigit():
        sys.stdout.write(f"{RED}clip run: provide a snippet id, e.g. /clip run 3{RESET}\n")
        sys.stdout.flush()
        return
    snippet_id = int(args[0])
    snippets = db.list_snippets()
    match = next((s for s in snippets if s["id"] == snippet_id), None)
    if not match:
        sys.stdout.write(f"  {RED}snippet #{snippet_id} not found{RESET}\n\n")
        sys.stdout.flush()
        return
    session = _get_tmux_session()
    if not session:
        sys.stdout.write(f"  {RED}not in a tmux session cannot send to pane{RESET}\n\n")
        sys.stdout.flush()
        return
    db.increment_use(snippet_id)
    _send_to_main_pane(match["command"], session)
    sys.stdout.write(f"  {GREEN}✓ sent: {match['command']}{RESET}\n\n")
    sys.stdout.flush()


def _interactive_add(db: "Database") -> None:
    """Prompt for command, note, tags sequentially then save."""
    sys.stdout.write(f"\n{PURPLE}  Add Snippet{RESET}\n")
    sys.stdout.write(f"  {DIM}{'─' * 40}{RESET}\n")

    sys.stdout.write(f"  {DIM}Command: {RESET}")
    sys.stdout.flush()
    try:
        command = input("").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if not command:
        return

    sys.stdout.write(f"  {DIM}Note (Enter to skip): {RESET}")
    sys.stdout.flush()
    try:
        note = input("").strip()
    except (EOFError, KeyboardInterrupt):
        note = ""

    sys.stdout.write(f"  {DIM}Tags comma-separated (Enter to skip): {RESET}")
    sys.stdout.flush()
    try:
        tags = input("").strip()
    except (EOFError, KeyboardInterrupt):
        tags = ""

    snippet_id = db.add_snippet(command, note, tags)
    sys.stdout.write(f"  {GREEN}✓ snippet #{snippet_id} saved{RESET}\n\n")
    sys.stdout.flush()


def open_picker(db: "Database") -> None:
    """Full-screen interactive snippet picker using prompt_toolkit."""
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.formatted_text import ANSI

    snippets = db.list_snippets()
    selected = [0]
    filter_text = [""]
    mode = ["nav"]   # "nav" or "filter"
    message = [""]

    def _filtered():
        f = filter_text[0].lower()
        if not f:
            return list(snippets)
        return [s for s in snippets if f in s["note"].lower() or f in s["tags"].lower() or f in s["command"].lower()]

    def _render():
        visible = _filtered()
        lines = []
        lines.append(f"{PURPLE}  ◈ Clipboard{RESET}  {DIM}({len(visible)} snippets){RESET}\n")
        lines.append(f"  {DIM}Filter: [{filter_text[0]:<30}]{RESET}\n")
        lines.append(f"  {DIM}{'─' * 55}{RESET}\n")
        if not visible:
            lines.append(f"  {DIM}  no snippets match{RESET}\n")
        for i, s in enumerate(visible):
            marker = f"{PURPLE}▶{RESET}" if i == selected[0] else " "
            tag_str = f"{DIM}[{s['tags']}]{RESET}" if s['tags'] else ""
            note_str = (s['note'] or s['command'])[:38]
            lines.append(f"  {marker} {DIM}{s['id']:>3}{RESET}  {note_str:<38}  {tag_str}\n")
            lines.append(f"       {DIM}{s['command'][:55]}{RESET}\n")
        lines.append(f"\n  {DIM}Enter=run  a=add  d=del  /=filter  q=quit{RESET}\n")
        if message[0]:
            lines.append(f"\n  {GREEN}{message[0]}{RESET}\n")
        return ANSI("".join(lines))

    kb = KeyBindings()

    @kb.add("q")
    @kb.add("escape")
    def _quit(event):
        event.app.exit()

    @kb.add("up")
    def _up(event):
        if mode[0] == "nav":
            if selected[0] > 0:
                selected[0] -= 1

    @kb.add("down")
    def _down(event):
        if mode[0] == "nav":
            visible = _filtered()
            if selected[0] < len(visible) - 1:
                selected[0] += 1

    @kb.add("/")
    def _filter_mode(event):
        mode[0] = "filter"
        filter_text[0] = ""
        selected[0] = 0

    @kb.add("backspace")
    def _backspace(event):
        if mode[0] == "filter" and filter_text[0]:
            filter_text[0] = filter_text[0][:-1]
            selected[0] = 0

    @kb.add("enter")
    def _enter(event):
        if mode[0] == "filter":
            mode[0] = "nav"
            return
        visible = _filtered()
        if not visible:
            return
        s = visible[selected[0]]
        session = _get_tmux_session()
        if not session:
            message[0] = "not in tmux cannot send to pane"
            return
        db.increment_use(s["id"])
        _send_to_main_pane(s["command"], session)
        event.app.exit()

    @kb.add("d")
    def _delete(event):
        if mode[0] != "nav":
            return
        visible = _filtered()
        if not visible:
            return
        s = visible[selected[0]]
        db.delete_snippet(s["id"])
        snippets[:] = db.list_snippets()
        if selected[0] >= len(_filtered()):
            selected[0] = max(0, len(_filtered()) - 1)
        message[0] = f"deleted #{s['id']}"

    # Printable chars in filter mode append to filter_text
    for ch in "bcefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.":
        @kb.add(ch)
        def _char(event, c=ch):
            if mode[0] == "filter":
                filter_text[0] += c
                selected[0] = 0

    @kb.add("a")
    def _add(event):
        if mode[0] == "filter":
            filter_text[0] += "a"
            return
        event.app.exit(result="add")

    layout = Layout(
        HSplit([
            Window(content=FormattedTextControl(text=_render, focusable=False)),
        ])
    )

    app = Application(layout=layout, key_bindings=kb, full_screen=True)
    result = app.run()

    if result == "add":
        _interactive_add(db)
        snippets[:] = db.list_snippets()
        open_picker(db)
