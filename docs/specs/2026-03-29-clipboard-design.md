# Clipboard / Snippets Feature Design Spec

> **For agentic workers:** No tests required. Development only.

**Goal:** Add a persistent snippet/clipboard system to AgenticOS a sidebar panel showing saved commands, a `/clip` builtin for managing them, and click-to-run from the sidebar via tmux.

**Architecture:** New `shell/clipboard/` module handles TUI and logic. SQLite DB extended with a `snippets` table. Sidebar gets a new scrollable `◈ clipboard` panel between tokens and shortcuts. Arrow keys in the sidebar pane scroll the panel; Enter sends the selected snippet to the main pane.

**Tech Stack:** Python, SQLite (existing DB), prompt_toolkit (existing dep), Rich (existing dep), tmux send-keys.

---

## Data Storage

New `snippets` table in the existing `~/.local/share/agentic-shell/telemetry.db`:

```sql
CREATE TABLE IF NOT EXISTS snippets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    command    TEXT NOT NULL,
    note       TEXT DEFAULT '',
    tags       TEXT DEFAULT '',
    use_count  INTEGER DEFAULT 0,
    created_at TEXT
);
```

`tags` is a comma-separated string e.g. `"docker,server"`.

### New methods on `Database` in `shell/telemetry/db.py`

```python
def add_snippet(self, command: str, note: str = "", tags: str = "") -> int:
    """Insert a snippet, return its id."""

def list_snippets(self, tag: str = "") -> list[dict]:
    """Return all snippets (optionally filtered by tag) as list of dicts:
    {id, command, note, tags, use_count, created_at}
    Ordered by use_count DESC, created_at DESC."""

def delete_snippet(self, snippet_id: int) -> bool:
    """Delete snippet by id. Return True if deleted."""

def increment_use(self, snippet_id: int) -> None:
    """Increment use_count for snippet."""
```

---

## Sidebar Panel `shell/telemetry/watch.py`

### Placement
Between `_panel_tokens` and `_panel_shortcuts` in `_render_all()`.

### Panel appearance
```
╭─ ◈ clipboard ──────────────────────────╮
│ 1  list containers        [docker]     │
│    docker ps -a                        │
│ 2  git log pretty         [git]        │
│    git log --oneline --graph           │
│ 3  restart nginx          [server]     │
│    sudo systemctl restart nginx        │
│                          ↑↓ scroll    │
╰────────────────────────────────────────╯
```

- Shows 3 snippets at a time (fits 44-col sidebar)
- Highlighted snippet (current scroll position) shown with `▶` marker
- Scroll hint `↑↓ scroll` shown at bottom if more snippets exist

### Scroll state
Module-level in `watch.py`:
```python
_clip_offset: int = 0       # index of top visible snippet
_clip_selected: int = 0     # index of highlighted snippet
```

### Arrow key + Enter bindings in sidebar pane
Set via `tmux bind-key -n` scoped to the sidebar pane using a shell loop in `watch.py` `run()` on startup:

```python
def _bind_sidebar_keys(pane_id: str) -> None:
    """Bind Up/Down/Enter in the sidebar tmux pane only."""
    # Uses tmux bind-key -n with a condition on pane_id
```

- **Up arrow** → decrement `_clip_selected`, adjust `_clip_offset`
- **Down arrow** → increment `_clip_selected`, adjust `_clip_offset`
- **Enter** → read selected snippet command → `tmux send-keys -t 0.0 "<command>" Enter` + `increment_use()`

Since `watch.py` is a separate process, key events are communicated via a small socket or a single-line state file at `~/.local/share/agentic-shell/clip_key`. The sidebar loop checks this file each refresh cycle (every 5s → reduced to 1s when clipboard panel is active).

### State file protocol
`~/.local/share/agentic-shell/clip_key` contains one of: `UP`, `DOWN`, `ENTER`. The sidebar process reads and deletes it on each tick. tmux key bindings write to this file via `echo UP > ~/.local/share/agentic-shell/clip_key`.

---

## `/clip` Builtin `shell/loop.py`

Added to `_handle_builtin()`. Parses the following forms:

| Command | Action |
|---|---|
| `/clip` | Open interactive TUI picker |
| `/clip list` | Print all snippets to stdout |
| `/clip add "<cmd>"` | Add snippet (prompts for note + tags interactively) |
| `/clip add "<cmd>" --note "x" --tags a,b` | Add snippet non-interactively |
| `/clip del <id>` | Delete snippet by id |
| `/clip run <id>` | Run snippet by id via tmux send-keys |

Parsing: simple `shlex.split()` on the remainder after `/clip`.

---

## Interactive TUI `shell/clipboard/manager.py`

Single function: `open_picker(db: Database) -> None`

Renders a full-screen (main pane width) picker using `prompt_toolkit` `Application`:

```
╭─ ◈ Clipboard ──────────────────────────────────────────────────╮
│ Filter: [                                        ]             │
│                                                               │
│ ▶ 1  list containers                      [docker]           │
│      docker ps -a                                             │
│   2  git log pretty                       [git]              │
│      git log --oneline --graph                                │
╰───────────────────────────────────────────────────────────────╯
  Enter=run  a=add  d=del  /=filter  q=quit
```

### Key bindings inside TUI
- `↑` / `↓` navigate snippets
- `/` focus filter input (filters by note text or tag, live)
- `a` open inline add form: prompts command → note → tags sequentially
- `d` delete highlighted snippet (confirm with `y`)
- `Enter` send highlighted command to main pane via `tmux send-keys -t <session>:0.0`, increment use_count, exit TUI
- `q` / `Escape` quit TUI without running anything

### Add form (inside TUI, triggered by `a`)
Three sequential `prompt_toolkit` prompts inline:
1. `Command: ` the shell command
2. `Note: ` short description (optional, Enter to skip)
3. `Tags: ` comma-separated tags (optional, Enter to skip)

On completion, snippet saved to DB, list refreshes.

### tmux pane targeting
To send a command to the main pane:
```python
subprocess.run([
    "tmux", "send-keys", "-t", f"{session_name}:0.0",
    command, "Enter"
])
```
`session_name` obtained via `tmux display-message -p "#S"` at TUI open time.

---

## File Summary

| File | Change |
|---|---|
| `shell/telemetry/db.py` | Add `snippets` table + 4 methods |
| `shell/telemetry/watch.py` | Add `_panel_clipboard()`, scroll state, key file reader, `_bind_sidebar_keys()` |
| `shell/loop.py` | Add `/clip` parsing + dispatch in `_handle_builtin()` |
| `shell/clipboard/__init__.py` | New empty file |
| `shell/clipboard/manager.py` | New file `open_picker()` TUI + `run_clip_command()` parser |
