# Sable v3 Product Requirements Document

> Task Engine + Adaptive Skill Learning: autonomous background agents and a system that gets smarter the more you use it.

This document specifies every change and addition required to upgrade Sable from v2 to v3. It is written for direct implementation by Claude Code. Read the entire document before writing any code. Implement strictly in the phase order defined below.

---

## Baseline: what v2 already has

Sable v2 is a Python login shell that replaces `/bin/bash` on Linux servers. The following structure is the starting point. Do not modify any file listed here unless this document explicitly says to.

```
shell/
  main.py                  # SSH bypass, config load, session resume
  loop.py                  # prompt_toolkit REPL, builtin routing
  router.py                # BASH / AGENTIC / AMBIGUOUS classifier
  executor.py              # PtyProcessUnicode, cd interception
  safety.py                # 13-pattern blocklist, entropy redaction
  planner.py               # multi-step plan execution
  llm/
    base.py                # LLMBackend abstract, LLMResponse dataclass
    ollama.py
    openai.py
    anthropic.py
  telemetry/
    db.py                  # SQLite WAL token_events, session_memory, snippets
    events.py              # TokenEvent dataclass
    watch.py               # sidebar: 7 Rich panels, 5s poll, pane 1
  memory/
    compressor.py          # token-reducer MODERATE, keeps last 2 turns
    store.py               # load/save compressed context to SQLite
  config/
    wizard.py
    schema.py              # ShellConfig dataclass
    keyring.py
  tui/
    layout.py              # libtmux: pane 0 shell 80% + pane 1 sidebar 44col
    panel.py
  clipboard/
    manager.py

~/.local/share/agentic-shell/
  sessions.db              # SQLite WAL
  audit.log                # append-only command log
  skills/
    instructions/          # global markdown skills
    scripts/               # global executable skills
```

---

## Goals

- Add autonomous long-running task agents that survive SSH disconnects
- Add a full-width bottom tasks panel (tmux pane 2) on the orchestrator window
- Add adaptive skill learning: system observes usage, auto-creates skills, improves them over time
- Preserve all existing v2 behaviour exactly no regressions

## Non-goals

- No new UI framework or web interface
- No changes to `watch.py`, `router.py`, `executor.py`, `safety.py`, or any LLM backend file
- No database migrations that alter or drop existing tables

---

## Phase 1 Database schema

**Modify: `shell/telemetry/db.py`**

Add four new `CREATE TABLE IF NOT EXISTS` blocks after all existing table definitions. Do not alter any existing table.

```sql
CREATE TABLE IF NOT EXISTS tasks (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT NOT NULL UNIQUE,
    goal                 TEXT NOT NULL,
    status               TEXT NOT NULL,
    tmux_window_id       TEXT,
    folder_path          TEXT NOT NULL,
    started_at           TEXT NOT NULL,
    ended_at             TEXT,
    last_output          TEXT,
    step_count           INTEGER DEFAULT 0,
    current_step         TEXT,
    snapshot_count       INTEGER DEFAULT 0,
    last_checkpoint_label TEXT
);

CREATE TABLE IF NOT EXISTS task_events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name            TEXT NOT NULL,
    timestamp            TEXT NOT NULL,
    step                 TEXT,
    prompt_tokens        INTEGER DEFAULT 0,
    completion_tokens    INTEGER DEFAULT 0,
    cost_usd             REAL DEFAULT 0.0,
    compression_ratio    REAL,
    snapshot_version     TEXT,
    model                TEXT
);

CREATE TABLE IF NOT EXISTS task_memory (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name            TEXT NOT NULL,
    version              TEXT NOT NULL,
    label                TEXT NOT NULL,
    compressed           TEXT NOT NULL,
    raw_last_turns       TEXT NOT NULL,
    turn_count           INTEGER NOT NULL,
    token_count          INTEGER NOT NULL,
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_patterns (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_hash         TEXT NOT NULL UNIQUE,
    goal_cluster         TEXT NOT NULL,
    occurrence_count     INTEGER DEFAULT 1,
    first_seen           TEXT NOT NULL,
    last_seen            TEXT NOT NULL,
    crystallised         INTEGER DEFAULT 0,
    skill_name           TEXT
);
```

**Modify: `shell/config/schema.py`**

Add one new field to the `ShellConfig` dataclass:

```python
tasks_base_dir: str = "~/tasks"
```

---

## Phase 2 tmux layout: pane 2

**Modify: `shell/tui/layout.py`**

Add a constant at the top of the file:

```python
TASKS_PANEL_HEIGHT_PERCENT = 12
```

After the existing code that creates pane 1 (the sidebar), add pane 2 on window 0 only. Pane 2 is a full-width horizontal strip at the bottom.

```python
tasks_pane = window.split_window(vertical=False, percent=TASKS_PANEL_HEIGHT_PERCENT)
tasks_pane.send_keys("python3 -m shell.tasks.panel", enter=True)
```

Task windows (window 1 and above) do not get pane 2. Only the orchestrator window (window 0) has it.

The resulting layout on window 0:

```
┌─────────────────────────────┬──────────────────┐
│                             │                  │
│   main shell  (pane 0)      │  sidebar (pane 1)│
│   ~80% width                │  44 cols fixed   │
│                             │                  │
├─────────────────────────────┴──────────────────┤
│           tasks panel (pane 2) full width     │
│  [react-app] ● running  step 3/6 · 2,341 tok   │
└─────────────────────────────────────────────────┘
```

---

## Phase 3 Task Engine files

Create directory `shell/tasks/` with an empty `__init__.py`. Then create each file below.

### `shell/tasks/panel.py`

Runs inside pane 2. Polls the `tasks` SQLite table every 5 seconds and renders a single status line using Rich. Overwrites in place using `\r` never scrolls.

**Output format:**

```
 TASKS   [react-app] ● running  step 3/6 · 2,341 tok · $0.0012 · 4m32s   [ml-pipeline] ✓ done  6 steps
```

**Status symbols:**

| Symbol | Meaning | Color |
|---|---|---|
| `●` | running | green |
| `✓` | done | green |
| `⏸` | paused | yellow |
| `✗` | lost | red |
| `○` | starting | dim white |

**Rules:**
- Uses the same SQLite path as the rest of the shell
- If no tasks exist: show `TASKS  no active tasks`
- Handle SQLite read errors silently table may not exist on first launch
- Do not query cursor position set `PROMPT_TOOLKIT_NO_CPR=1` in environment
- Poll interval: 5 seconds, matching the sidebar

---

### `shell/tasks/memory.py`

Per-task versioned memory. Extends the existing compressor interface from `memory/compressor.py`.

```python
class TaskMemory:
    def __init__(self, task_name: str, task_folder: str, db_path: str): ...

    def compress(self, turns: list, goal: str) -> dict:
        """
        Compress using AGGRESSIVE mode (vs MODERATE in the main shell).
        Goal is always pinned at position 0 never compressed, never moved.
        After each completed plan step: replace raw turn exchange with
        a 1-2 sentence summary. Only the current step's raw turns kept verbatim.
        Returns: {"compressed": str, "raw_last_turns": list,
                  "turn_count": int, "token_count": int}
        """

    def save_snapshot(self, label: str = "auto", version: str = None) -> str:
        """
        Save snapshot to ~/tasks/<name>/.agentic/memory/vN.json
        and write a row to task_memory table in SQLite.
        Returns version string e.g. "v3".
        """

    def load_snapshot(self, version_or_label: str) -> dict:
        """
        Load by version ("v2") or label ("before database setup").
        Auto-saves current state as a snapshot before loading.
        Does NOT undo filesystem changes context only.
        """

    def list_snapshots(self) -> list:
        """
        Return all snapshots with version, label, turn_count,
        token_count, created_at.
        """
```

**Snapshot file format** at `~/tasks/<name>/.agentic/memory/vN.json`:

```json
{
  "version": "v3",
  "label": "before database setup",
  "type": "manual",
  "compressed": "...",
  "raw_last_turns": [],
  "turn_count": 14,
  "token_count": 1840,
  "created_at": "2026-03-29T10:22:00"
}
```

**Skill deduplication:** skills loaded as context are content-hashed. If the same hash appeared in the previous snapshot, reference by hash instead of re-embedding. Saves tokens on every call after the first.

---

### `shell/tasks/sandbox.py`

Bubblewrap sandbox wrapper with Python-layer fallback.

```python
class Sandbox:
    def __init__(self, task_name: str, task_folder: str): ...

    def is_bwrap_available(self) -> bool: ...

    def wrap_command(self, command: str) -> str:
        """
        If bwrap available: return bwrap-wrapped command string.
        If not: return command unchanged, intercept_write() will
        block unsafe writes. Log warning on first call if bwrap missing.
        """

    def intercept_write(self, command: str) -> bool:
        """
        Fallback only. Returns True if command is safe (writes only
        target the task folder). Returns False if write targets any
        path outside the task folder.
        """
```

**Bubblewrap configuration when available:**

```bash
bwrap \
  --bind ~/tasks/<name>  ~/tasks/<name> \
  --ro-bind /usr /usr \
  --ro-bind /etc /etc \
  --ro-bind /home /home \
  --ro-bind /tmp /tmp \
  --dev /dev \
  --proc /proc \
  --unshare-pid \
  -- /bin/bash -c "<command>"
```

Network is unrestricted. `safety.py` blocklist still runs on every task command sandbox is a second enforcement layer, not a replacement.

If bwrap is not found at runtime, print once to the orchestrator pane:

```
[warning] bubblewrap not found using software sandbox (weaker isolation)
```

---

### `shell/tasks/agent.py`

Goal-directed task REPL loop. Modelled on `loop.py` but fully autonomous.

```python
class TaskAgent:
    def __init__(self, task_name: str, goal: str, db_path: str,
                 config: ShellConfig): ...

    def run(self):
        """
        Main loop per turn:
        1. Build context: system prompt + pinned goal + compressed history
           + matched skills list
        2. Call backend.complete() same LLMBackend interface as loop.py
        3. Run safety.py blocklist check
        4. Execute via sandbox.wrap_command() + PtyProcessUnicode
        5. Update tasks row: last_output, step_count, status
        6. Write task_events row
        7. Compress memory if token threshold exceeded
        8. Check LLM response for done signal
        9. Accept non-blocking guidance input from user if available
        """
```

**LLM context structure on each call:**

```
[system prompt]
GOAL (pinned, never compressed): <goal text>
[compressed history of previous steps]
[current step raw turns verbatim]
Available skills: deploy-nginx, setup-react, seed_db (local)
```

**Done signal:** LLM returns `"done": true` in JSON response. Agent sets `status = completed`, writes `ended_at`, keeps the tmux window open for review.

**User guidance:** the agent accepts a line of input from the user at any point in its loop without blocking execution. If input is present, it is prepended to the next LLM context as a guidance message.

---

### `shell/tasks/manager.py`

Task lifecycle management. Called from the `/task` builtin in `loop.py`.

```python
class TaskManager:

    def spawn(self, name: str, goal: str) -> None:
        """
        1. Create ~/tasks/<name>/ and ~/tasks/<name>/.agentic/memory/
        2. Write tasks row with status "starting"
        3. Create new tmux window named after the task
        4. Start agent.py in that window
        5. Agent writes first turn → status updates to "running"
        """

    def pause(self, name: str) -> None:
        # Send SIGTSTP to task ptyprocess. Set status "paused".

    def resume(self, name: str) -> None:
        # Send SIGCONT. Set status "running".

    def kill(self, name: str) -> None:
        # Kill tmux window. Set status "completed". Write ended_at.

    def attach(self, name: str) -> None:
        # Switch tmux focus to task window via libtmux select_window.

    def inspect(self, name: str) -> None:
        # Open plain bash shell in ~/tasks/<name>/. Agent not running.

    def checkpoint(self, name: str, label: str) -> None:
        # Trigger TaskMemory.save_snapshot(label).

    def revert(self, name: str, version_or_label: str) -> None:
        # Call TaskMemory.load_snapshot(). Context only no filesystem undo.

    def list_tasks(self) -> list:
        # Return all tasks from SQLite with current status.

    def stats(self, name: str) -> dict:
        # Token cost per step, compression ratios, total cost/tokens/calls,
        # runtime, step count, snapshot count from task_events table.

    def history(self, name: str) -> list:
        # All memory snapshots from task_memory table.
```

---

### `shell/tasks/reconcile.py`

Startup reconciliation. Called once from `main.py` immediately after config loads.

```python
def reconcile(db_path: str, tmux_session) -> list:
    """
    1. Read all tasks with status in ("running", "starting", "paused")
    2. For each: check if tmux_window_id exists in live tmux session
    3. Window alive: leave status unchanged
    4. Window gone: set status "lost"
    5. Return list of task names marked lost
    Caller (main.py) prints one warning line per lost task at next prompt.
    """
```

---

### `shell/tasks/skills.py`

Skill loader for task agents. Merges global and per-task local skills.

```python
class TaskSkillLoader:
    def __init__(self, task_folder: str, global_skills_dir: str): ...

    def load_relevant(self, goal: str) -> list:
        """
        Keyword-match goal against skill filenames and first-line descriptions.
        Local skills override global on name collision.
        Returns: [{"name": str, "content": str, "hash": str,
                   "source": "local" | "global"}]
        """

    def list_available(self) -> str:
        """
        Returns comma-separated skill names for system prompt.
        Example: "deploy-nginx, setup-react, seed_db (local)"
        """

    def run_script(self, skill_name: str, args: list, sandbox) -> str:
        """
        Resolve script path (local first, then global).
        Run inside sandbox. Return output as string for agent context.
        """
```

---

## Phase 4 Builtin command wiring

**Modify: `shell/loop.py`**

Add `/task` and `/skill` to the existing builtin check block. These must be checked before the router is called exactly like `/clip`, `/config`, and `/help`.

**`/task` command routing:**

| Command | Action |
|---|---|
| `/task <name>` | `manager.attach(name)` |
| `/task <name> pause` | `manager.pause(name)` |
| `/task <name> resume` | `manager.resume(name)` |
| `/task <name> done` | `manager.kill(name)` |
| `/task <name> kill` | `manager.kill(name)` |
| `/task <name> inspect` | `manager.inspect(name)` |
| `/task <name> stats` | print `manager.stats(name)` |
| `/task <name> history` | print `manager.history(name)` |
| `/task <name> checkpoint "<label>"` | `manager.checkpoint(name, label)` |
| `/task <name> revert <version>` | `manager.revert(name, version)` |
| `/task list` | print `manager.list_tasks()` |

**`/skill` command routing:**

| Command | Action |
|---|---|
| `/skill new <name>` | Create global markdown skill file |
| `/skill new <name> --script` | Create global executable skill file |
| `/skill new <name> --local` | Create task-local skill (must be inside a task window) |
| `/skill list` | List all global skills |
| `/skill list --local` | List task-local skills for current task |
| `/skill edit <name>` | Open skill file in `$EDITOR` |

**Modify: `shell/main.py`**

Add one call after config loads and before the REPL loop starts:

```python
from shell.tasks.reconcile import reconcile

lost_tasks = reconcile(db_path, tmux_session)
for name in lost_tasks:
    print(f"[warning] task '{name}' was lost while disconnected")
```

---

## Phase 5 Adaptive skill learning

Create directory `shell/skills/` with an empty `__init__.py`. Then create the following files.

### `shell/skills/pattern_watcher.py`

Background observer. Called at the end of each session (hooked into the `/exit` flow in `loop.py`) and optionally on a timer.

```python
class PatternWatcher:
    def __init__(self, db_path: str, audit_log_path: str): ...

    def scan(self) -> list:
        """
        1. Read recent entries from audit.log and token_events table
        2. Group commands by semantic similarity:
           - Same repository path
           - Same intent keywords in the goal
           - Same command sequence shape
        3. For each group: compute a stable pattern_hash
        4. Upsert into skill_patterns table:
           - If hash exists: increment occurrence_count, update last_seen
           - If hash is new: insert with occurrence_count = 1
        5. Return list of patterns that crossed the crystallisation threshold
           (occurrence_count >= 3 and crystallised = 0)
        """

    def get_pending_crystallisation(self) -> list:
        """
        Return all rows from skill_patterns where
        occurrence_count >= 3 and crystallised = 0.
        """
```

### `shell/skills/crystalliser.py`

Converts a detected pattern into a skill file.

```python
class SkillCrystalliser:
    def __init__(self, db_path: str, audit_log_path: str,
                 skills_dir: str, llm_backend): ...

    def crystallise(self, pattern: dict) -> str:
        """
        1. Pull the raw command history for this pattern cluster
           from audit.log (the matching occurrences)
        2. Send to LLM with this system prompt:
           "You are writing a skill file for an agentic shell.
            Here are N times the user performed this task.
            Write a concise markdown skill document capturing:
            the exact steps, flags, paths, and any gotchas observed.
            Be specific. Use the actual values from the history.
            Output only the markdown content, no preamble."
        3. Write output to skills/instructions/<slug>.md
        4. Add entry to skills_index.json
        5. Mark skill_patterns row as crystallised = 1, skill_name = <slug>
        6. Return the skill name
        """

    def update(self, skill_name: str, new_occurrences: list) -> None:
        """
        Called when an existing auto-generated skill has diverged from
        recent usage (detected by PatternWatcher).
        Re-crystallises with the full history including new occurrences.
        Overwrites the existing skill file.
        Updates skills_index.json entry.
        """
```

### `shell/skills/index.py`

Manages `skills_index.json` the metadata registry for all skills.

```python
class SkillIndex:
    def __init__(self, skills_dir: str): ...

    def add(self, name: str, auto_generated: bool = False) -> None:
        """
        Add or update a skill entry in skills_index.json.
        Initial confidence: 0.5 for auto-generated, 1.0 for manual.
        """

    def record_use(self, name: str, success: bool) -> None:
        """
        Increment use_count. Update last_used timestamp.
        If success=True: nudge confidence up by 0.05 (max 1.0).
        If success=False: nudge confidence down by 0.1 (min 0.0).
        """

    def get_ranked(self, goal: str) -> list:
        """
        Keyword-match goal against skill names and keywords.
        Return matched skills sorted by: confidence desc, use_count desc.
        """

    def flag_for_recrystallisation(self, name: str) -> None:
        """
        Called when audit.log shows the user deviated significantly
        from the skill's prescribed steps.
        Sets needs_update = true in the index entry.
        """
```

**`skills_index.json` entry format:**

```json
{
  "name": "deploy-myrepo",
  "file": "deploy-myrepo.md",
  "keywords": ["deploy", "git", "myrepo", "restart"],
  "auto_generated": true,
  "confidence": 0.75,
  "use_count": 12,
  "last_used": "2026-03-29T14:00:00",
  "needs_update": false,
  "created_at": "2026-03-10T09:00:00"
}
```

**Modify: `shell/tasks/skills.py`** (already created in Phase 3)

Update `load_relevant()` to consult `SkillIndex.get_ranked()` instead of doing raw keyword matching. This makes the ranking confidence-aware and use-count-aware.

**Modify: `shell/loop.py`**

Hook `PatternWatcher.scan()` into the `/exit` builtin, before the exit flag is written:

```python
from shell.skills.pattern_watcher import PatternWatcher

watcher = PatternWatcher(db_path, audit_log_path)
pending = watcher.scan()

if pending:
    from shell.skills.crystalliser import SkillCrystalliser
    crystalliser = SkillCrystalliser(db_path, audit_log_path, skills_dir, backend)
    for pattern in pending:
        name = crystalliser.crystallise(pattern)
        print(f"[skills] learned new skill: {name}")
```

---

## Phase 6 File and directory layout (complete picture)

After all phases are implemented, the full additions to the filesystem are:

```
shell/
  tasks/
    __init__.py
    panel.py
    memory.py
    sandbox.py
    agent.py
    manager.py
    reconcile.py
    skills.py
  skills/
    __init__.py
    pattern_watcher.py
    crystalliser.py
    index.py

~/tasks/
  <name>/
    .agentic/
      memory/
        v1.json ... vN.json
        current.json
      skills/
        instructions/
        scripts/

~/.local/share/agentic-shell/
  skills/
    instructions/          # global markdown skills (already exists)
    scripts/               # global executables (already exists)
    skills_index.json      # NEW: metadata registry
```

---

## Key constraints for implementation

| Constraint | Reason |
|---|---|
| SQLite WAL mode already set do not change it | Sidebar + task panel + agent all read/write simultaneously |
| All new processes must not block the main REPL | Same reason watch.py is a separate process |
| `safety.py` runs on every command including task agent commands | Sandbox is a second layer, not a replacement |
| Goal is always pinned verbatim in task memory | Agent must never lose sight of what it is trying to do |
| Revert is context-only, never filesystem | Filesystem undo is git's job |
| Auto-generated skills are flagged in the index | LLM treats them as strong suggestions until confidence is high |
| Pattern threshold is 3 occurrences before crystallisation | Avoids creating skills from one-off commands |
| Skill confidence is nudged, not set absolutely | Gradual trust-building a skill earns its weight over time |

---

## Acceptance criteria

Phase 1 is complete when: the four new tables exist in `sessions.db` after a fresh launch with no errors.

Phase 2 is complete when: a new login shows a three-row bottom strip below the main shell and sidebar.

Phase 3 is complete when: `python3 -m shell.tasks.agent --task test --goal "create a file called hello.txt"` runs, creates the file inside `~/tasks/test/`, and marks the task completed in SQLite.

Phase 4 is complete when: `/task list` prints task rows, `/task <name> pause` pauses a running task, and `/skill list` prints available skills all from the main shell.

Phase 5 is complete when: running the same deploy sequence 3 times across sessions causes a skill file to appear in `skills/instructions/` and `skills_index.json` to contain a matching entry.

Phase 6 is complete when: SSH disconnect during a running task followed by reconnect shows the task as `lost` in the panel, and `/task <name> revert v1` loads an earlier context without error.

---

*Status: approved for implementation. Start with Phase 1 and confirm each phase passes its acceptance criteria before proceeding to the next.*