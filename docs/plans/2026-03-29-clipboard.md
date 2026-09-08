# Clipboard / Snippets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent snippet system with a scrollable sidebar panel, `/clip` builtin for CRUD, and click-to-run from the sidebar via tmux send-keys.

**Architecture:** New `shell/clipboard/manager.py` handles the interactive TUI picker. `shell/telemetry/db.py` gains a `snippets` table and 4 methods. `shell/telemetry/watch.py` gains a `_panel_clipboard()` and scroll state driven by a key-state file. `shell/loop.py` gets `/clip` builtin parsing.

**Tech Stack:** Python, SQLite (existing WAL db), prompt_toolkit (existing), Rich (existing), tmux send-keys.

**No tests required development only.**

---

### Task 1: Snippets table + DB methods

**Files:**
- Modify: `shell/telemetry/db.py`

- [ ] **Step 1: Add CREATE TABLE constant after `_CREATE_SESSION_MEMORY`**

```python
_CREATE_SNIPPETS = """
CREATE TABLE IF NOT EXISTS snippets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    command    TEXT NOT NULL,
    note       TEXT DEFAULT '',
    tags       TEXT DEFAULT '',
    use_count  INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
)
"""
```

- [ ] **Step 2: Execute it in `Database.__init__` after the existing two table creates**

```python
self._conn.execute(_CREATE_SNIPPETS)
self._conn.commit()
```

- [ ] **Step 3: Add `add_snippet` method to `Database` class**

```python
def add_snippet(self, command: str, note: str = "", tags: str = "") -> int:
    """Insert a snippet, return its new id."""
    from datetime import datetime, timezone
    cur = self._conn.execute(
        "INSERT INTO snippets (command, note, tags, use_count, created_at) VALUES (?, ?, ?, 0, ?)",
        (command, note, tags, datetime.now(timezone.utc).isoformat()),
    )
    self._conn.commit()
    return cur.lastrowid
```

- [ ] **Step 4: Add `list_snippets` method**

```python
def list_snippets(self, tag: str = "") -> list[dict]:
    """Return all snippets ordered by use_count DESC, created_at DESC.

    If tag is given, filter to snippets whose tags field contains that tag.
    """
    if tag:
        rows = self._conn.execute(
            """SELECT id, command, note, tags, use_count, created_at
               FROM snippets
               WHERE ',' || tags || ',' LIKE ?
               ORDER BY use_count DESC, created_at DESC""",
            (f"%,{tag},%",),
        ).fetchall()
    else:
        rows = self._conn.execute(
            """SELECT id, command, note, tags, use_count, created_at
               FROM snippets
               ORDER BY use_count DESC, created_at DESC""",
        ).fetchall()
    return [
        {"id": r[0], "command": r[1], "note": r[2],
         "tags": r[3], "use_count": r[4], "created_at": r[5]}
        for r in rows
    ]
```

- [ ] **Step 5: Add `delete_snippet` method**

```python
def delete_snippet(self, snippet_id: int) -> bool:
    """Delete snippet by id. Return True if a row was deleted."""
    cur = self._conn.execute("DELETE FROM snippets WHERE id = ?", (snippet_id,))
    self._conn.commit()
    return cur.rowcount > 0
```

- [ ] **Step 6: Add `increment_use` method**

```python
def increment_use(self, snippet_id: int) -> None:
    """Increment use_count for a snippet."""
    self._conn.execute(
        "UPDATE snippets SET use_count = use_count + 1 WHERE id = ?", (snippet_id,)
    )
    self._conn.commit()
```

- [ ] **Step 7: Commit**

```bash
git add shell/telemetry/db.py
git commit -m "feat: add snippets table and CRUD methods to Database"
```

---

### Task 2: Clipboard module skeleton

**Files:**
- Create: `shell/clipboard/__init__.py`
- Create: `shell/clipboard/manager.py`

- [ ] **Step 1: Create empty `__init__.py`**

```python
```
(empty file)

- [ ] **Step 2: Create `shell/clipboard/manager.py` with imports and helpers**

```python
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
```

- [ ] **Step 3: Commit**

```bash
git add shell/clipboard/__init__.py shell/clipboard/manager.py
git commit -m "feat: scaffold clipboard module"
```

---

### Task 3: `/clip` CLI commands (add/del/list/run)

**Files:**
- Modify: `shell/clipboard/manager.py`
- Modify: `shell/loop.py`

- [ ] **Step 1: Add `run_clip_command` to `shell/clipboard/manager.py`**

This function handles `/clip add`, `/clip del`, `/clip list`, `/clip run` from the shell builtin.

```python
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
```

- [ ] **Step 2: Add `_cmd_list`**

```python
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
```

- [ ] **Step 3: Add `_cmd_add`**

```python
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
```

- [ ] **Step 4: Add `_cmd_del`**

```python
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
```

- [ ] **Step 5: Add `_cmd_run`**

```python
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
```

- [ ] **Step 6: Wire `/clip` into `_handle_builtin` in `shell/loop.py`**

Find the block that handles `/history` (around line 467) and add after it:

```python
    if cmd == "/clip" or cmd.startswith("/clip "):
        from shell.clipboard.manager import run_clip_command, open_picker
        remainder = cmd[len("/clip"):].strip()
        if not remainder:
            open_picker(db)
        else:
            run_clip_command(remainder, db)
        return True
```

- [ ] **Step 7: Add `/clip` to `_HELP_TEXT` in `shell/loop.py`**

Find the `_HELP_TEXT` string and add this line after the `/history` line:

```python
"  /clip           Snippet clipboard (add/run/del)\n"
```

- [ ] **Step 8: Commit**

```bash
git add shell/clipboard/manager.py shell/loop.py
git commit -m "feat: /clip CLI add, del, list, run subcommands"
```

---

### Task 4: Interactive TUI picker (`open_picker`)

**Files:**
- Modify: `shell/clipboard/manager.py`

- [ ] **Step 1: Add `open_picker` function**

```python
def open_picker(db: "Database") -> None:
    """Full-screen interactive snippet picker using prompt_toolkit."""
    from prompt_toolkit import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.formatted_text import ANSI

    snippets = db.list_snippets()
    selected = [0]       # mutable so closures can write
    filter_text = [""]
    mode = ["nav"]       # "nav" or "filter" or "add"
    message = [""]

    def _filtered():
        f = filter_text[0].lower()
        if not f:
            return snippets
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
            visible = _filtered()
            if selected[0] > 0:
                selected[0] -= 1

    @kb.add("down")
    def _down(event):
        if mode[0] == "nav":
            visible = _filtered()
            if selected[0] < len(visible) - 1:
                selected[0] += 1

    @kb.add("/")
    def _filter(event):
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

    # All printable chars in filter mode append to filter_text
    for ch in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.":
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
        open_picker(db)  # re-open after add
```

- [ ] **Step 2: Add `_interactive_add` helper (called when `a` pressed in TUI)**

```python
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
```

- [ ] **Step 3: Commit**

```bash
git add shell/clipboard/manager.py
git commit -m "feat: /clip interactive TUI picker with filter, add, delete, run"
```

---

### Task 5: Sidebar clipboard panel

**Files:**
- Modify: `shell/telemetry/watch.py`

- [ ] **Step 1: Add scroll state and key-file path at module level (after `SIDEBAR_WIDTH = 44`)**

```python
_clip_selected: int = 0   # index of highlighted snippet in sidebar
_CLIP_KEY_FILE = str(Path.home() / ".local" / "share" / "agentic-shell" / "clip_key")
```

Also add `from pathlib import Path` at the top of `watch.py` imports if not already present.

- [ ] **Step 2: Add `_panel_clipboard` function**

Add this function after `_panel_tokens` and before `_panel_shortcuts`:

```python
def _panel_clipboard(db) -> "Panel":
    global _clip_selected
    try:
        snippets = db.list_snippets()
    except Exception:
        snippets = []

    t = Text()
    if not snippets:
        t.append("no snippets yet\n", style="color(238)")
        t.append("/clip add \"cmd\"", style="color(141)")
        t.append(" to save one\n", style="color(238)")
    else:
        visible_count = 3
        total = len(snippets)
        # Clamp selected
        _clip_selected = max(0, min(_clip_selected, total - 1))
        start = max(0, min(_clip_selected - 1, total - visible_count))
        visible = snippets[start:start + visible_count]

        for i, s in enumerate(visible):
            actual_idx = start + i
            is_selected = actual_idx == _clip_selected
            marker = "▶ " if is_selected else "  "
            marker_style = "color(141) bold" if is_selected else "color(238)"
            note_str = (s["note"] or s["command"])[:24]
            tag_str = f"[{s['tags'].split(',')[0]}]" if s["tags"] else ""
            t.append(marker, style=marker_style)
            t.append(f"{note_str:<24}", style="color(253)" if is_selected else "color(238)")
            t.append(f" {tag_str}\n", style="color(55)")
            cmd_preview = s["command"][:32]
            t.append(f"   {cmd_preview}\n", style="color(238)")

        if total > visible_count:
            t.append(f"\n  ↑↓ scroll  {_clip_selected+1}/{total}", style="color(55)")

    return Panel(t, title="[color(141) bold]◈ clipboard[/color(141) bold]", border_style="color(55)", padding=(0, 1))
```

- [ ] **Step 3: Add `_read_clip_key` helper**

```python
def _read_clip_key() -> str | None:
    """Read and delete the clip_key file. Returns 'UP', 'DOWN', 'ENTER', or None."""
    try:
        p = Path(_CLIP_KEY_FILE)
        if p.exists():
            val = p.read_text().strip()
            p.unlink()
            return val if val in ("UP", "DOWN", "ENTER") else None
    except Exception:
        pass
    return None
```

- [ ] **Step 4: Add `_handle_clip_key` helper**

```python
def _handle_clip_key(key: str, db) -> None:
    """Mutate _clip_selected or run snippet based on key."""
    global _clip_selected
    try:
        snippets = db.list_snippets()
    except Exception:
        return
    if not snippets:
        return

    if key == "UP":
        _clip_selected = max(0, _clip_selected - 1)
    elif key == "DOWN":
        _clip_selected = min(len(snippets) - 1, _clip_selected + 1)
    elif key == "ENTER":
        s = snippets[_clip_selected]
        try:
            result = subprocess.run(
                ["tmux", "display-message", "-p", "#S"],
                capture_output=True, text=True, timeout=1
            )
            session = result.stdout.strip()
            if session:
                db.increment_use(s["id"])
                subprocess.run(
                    ["tmux", "send-keys", "-t", f"{session}:0.0", s["command"], "Enter"],
                    capture_output=True
                )
        except Exception:
            pass
```

- [ ] **Step 5: Insert `_panel_clipboard(db)` into `_render_all` between tokens and shortcuts**

Find this in `_render_all`:
```python
    bc.print(_panel_tokens(db))
    bc.print(_panel_shortcuts())
```

Replace with:
```python
    bc.print(_panel_tokens(db))
    bc.print(_panel_clipboard(db))
    bc.print(_panel_shortcuts())
```

- [ ] **Step 6: Update `run()` to handle key file and reduce sleep when snippets exist**

Replace the existing `run()` body:

```python
def run():
    from shell.telemetry.db import Database
    db = Database()
    model = "unknown"
    try:
        while True:
            m = db.get_last_model()
            if m != "unknown":
                model = m
            elif model == "unknown":
                model = _get_config_model()

            # Handle sidebar key presses (written by tmux key bindings)
            key = _read_clip_key()
            if key:
                _handle_clip_key(key, db)

            frame = _render_all(db, model)
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.write(frame)
            sys.stdout.flush()

            # Faster refresh when key was pressed for responsive feel
            time.sleep(1 if key else 5)
    except KeyboardInterrupt:
        pass
    finally:
        db.close()
```

- [ ] **Step 7: Commit**

```bash
git add shell/telemetry/watch.py
git commit -m "feat: clipboard panel in sidebar with scroll state and key-file handler"
```

---

### Task 6: tmux key bindings for sidebar navigation

**Files:**
- Modify: `install.sh`
- Modify: `shell/loop.py` (bind keys in `/new` session too)

The sidebar pane needs Up/Down/Enter bound to write to `clip_key` file. We scope them to only fire when the sidebar pane is active using a tmux `if-shell` condition.

- [ ] **Step 1: Add key binding setup function in `shell/telemetry/watch.py`**

```python
def _setup_tmux_clip_keys() -> None:
    """Bind Up/Down/Enter in tmux to write clip_key file when sidebar pane is focused.

    Uses tmux bind-key with if-shell to check active pane index.
    Bindings are session-scoped and only write the file when pane 1 is active.
    """
    key_file = _CLIP_KEY_FILE
    # Only bind if we're inside tmux
    if not os.environ.get("TMUX"):
        return
    try:
        bindings = [
            ("Up",    "UP"),
            ("Down",  "DOWN"),
            ("Enter", "ENTER"),
        ]
        for tmux_key, val in bindings:
            condition = 'test "#{pane_index}" = "1"'
            action = f'run-shell "echo {val} > {key_file}"'
            fallback = f'send-keys {tmux_key}'
            subprocess.run(
                ["tmux", "bind-key", "-n", tmux_key,
                 "if-shell", condition, action, fallback],
                capture_output=True, timeout=2
            )
    except Exception:
        pass
```

- [ ] **Step 2: Call `_setup_tmux_clip_keys()` at the start of `run()` in `watch.py`**

Add as the first line inside the `try:` block in `run()`:

```python
    try:
        _setup_tmux_clip_keys()
        while True:
```

- [ ] **Step 3: Commit**

```bash
git add shell/telemetry/watch.py
git commit -m "feat: bind Up/Down/Enter in sidebar tmux pane for clipboard navigation"
```

---

### Task 7: Update shortcuts panel to show `/clip`

**Files:**
- Modify: `shell/telemetry/watch.py`

- [ ] **Step 1: Add `/clip` to the commands list in `_panel_shortcuts`**

Find the `commands` list in `_panel_shortcuts`:
```python
    commands = [
        ("/help",    "all commands"),
        ("/history", "cmd history + costs"),
```

Add after `/history`:
```python
        ("/clip",    "snippet clipboard"),
```

- [ ] **Step 2: Commit**

```bash
git add shell/telemetry/watch.py
git commit -m "feat: add /clip to shortcuts panel"
```

---

### Task 8: Manual smoke test + push

- [ ] **Step 1: Pull on the server and restart**

```bash
git pull && tmux kill-server && agentic-shell
```

- [ ] **Step 2: Add a snippet**

```
/clip add "docker ps -a" --note "list containers" --tags docker
```
Expected: `✓ snippet #1 saved`

- [ ] **Step 3: List snippets**

```
/clip list
```
Expected: shows snippet #1 with note and command

- [ ] **Step 4: Verify sidebar panel**

Sidebar should show `◈ clipboard` panel between tokens and shortcuts with snippet #1 visible.

- [ ] **Step 5: Navigate sidebar with arrow keys**

Click into the sidebar pane, press Down/Up `▶` marker should move.

- [ ] **Step 6: Run from sidebar**

With snippet highlighted, press Enter command should appear and run in the main pane.

- [ ] **Step 7: Open TUI picker**

```
/clip
```
Expected: full-screen picker opens, shows snippets, filter works with `/`, `d` deletes, `a` adds, Enter runs.

- [ ] **Step 8: Delete a snippet**

```
/clip del 1
```
Expected: `✓ snippet #1 deleted`

- [ ] **Step 9: Push**

```bash
git push
```
