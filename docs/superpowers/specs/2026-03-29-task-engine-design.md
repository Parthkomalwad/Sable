# Task Engine — Design Spec

**Date:** 2026-03-29
**Status:** Approved
**Scope:** Autonomous sandboxed task agents with versioned memory, shared skills, per-task telemetry, and tmux-native process management

---

## Overview

The Task Engine adds autonomous, sandboxed, long-running task agents to AgenticOS. The main shell becomes an orchestrator — it spawns named task agents, each running in its own tmux window with an isolated filesystem sandbox, separate LLM context, versioned memory, and access to a shared skills library. Tasks survive SSH disconnects and can be monitored, entered, interrupted, and resumed at any time.

---

## Architecture

```
Orchestrator (main shell, tmux window 0)
  └─ router detects long-running agentic goal
  └─ TaskManager.spawn(name, goal)
       ├─ creates ~/tasks/<name>/
       ├─ writes task record to SQLite (tasks table)
       ├─ wraps execution in bubblewrap sandbox
       ├─ opens new tmux window for the task agent
       └─ task agent runs its own REPL + LLM loop

Task Agent (each tmux window, window 1+)
  ├─ own LLM context (goal + compressed history)
  ├─ own versioned memory snapshots
  ├─ cwd locked to ~/tasks/<name>/
  ├─ write-blocked outside that folder (bubblewrap)
  └─ updates SQLite tasks row every turn

Tasks Panel (tmux pane 2, full-width bottom strip)
  └─ polls SQLite every 5s, renders all task rows

Skills Library
  ├─ ~/.local/share/agentic-shell/skills/instructions/  (global markdown)
  ├─ ~/.local/share/agentic-shell/skills/scripts/       (global executables)
  └─ ~/tasks/<name>/.agentic/skills/                    (per-task overrides)
```

### tmux Layout

```
┌─────────────────────────────┬──────────────────┐
│                             │                  │
│      Main Shell (pane 0)    │  Sidebar (pane 1) │
│         ~80% width          │   44 cols fixed  │
│                             │                  │
├─────────────────────────────┴──────────────────┤
│           Tasks Panel (pane 2) — full width     │
│  [react-app] ● running  step 3/6 · 2,341 tok   │
└─────────────────────────────────────────────────┘
```

Tasks in separate tmux windows (window 1, 2, 3…). `Ctrl+B n/p` to navigate. `Ctrl+B 0` always returns to orchestrator.

### New Files

```
shell/
  tasks/
    manager.py      spawn, list, attach, interrupt, kill, reconcile
    agent.py        task REPL loop (goal-directed, trimmed from loop.py)
    sandbox.py      bubblewrap wrapper + write-intercept fallback
    memory.py       per-task memory + versioned snapshot management
    skills.py       skill loader — global + local merge, keyword matching
    reconcile.py    startup reconciliation against live tmux windows
```

### New SQLite Tables

**`tasks`**
```sql
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,          -- starting, running, paused, completed, lost
    tmux_window_id TEXT,
    folder_path TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    last_output TEXT,
    step_count INTEGER DEFAULT 0,
    current_step TEXT,
    snapshot_count INTEGER DEFAULT 0,
    last_checkpoint_label TEXT
)
```

**`task_events`**
```sql
CREATE TABLE task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    step TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    compression_ratio REAL,
    snapshot_version TEXT,
    model TEXT
)
```

**`task_memory`**
```sql
CREATE TABLE task_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name TEXT NOT NULL,
    version TEXT NOT NULL,         -- v1, v2, v3 or user label
    label TEXT NOT NULL,           -- "auto" or user-provided tag
    compressed TEXT NOT NULL,
    raw_last_turns TEXT NOT NULL,
    turn_count INTEGER NOT NULL,
    token_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
)
```

---

## Task Lifecycle

### Spawning

1. Orchestrator router detects long-running goal (not a one-shot command)
2. LLM infers task name as a slug from the goal (e.g. `react-app`, `ml-pipeline`)
3. `TaskManager.spawn(name, goal)`:
   - Creates `~/tasks/<name>/` and `~/tasks/<name>/.agentic/memory/`
   - Writes task record to SQLite with status `starting`
   - Launches bubblewrap sandbox wrapping a new tmux window
   - Task agent starts, loads goal as first instruction, begins executing
4. Bottom tasks panel updates within 5 seconds showing new task row

### Running

Task agent (`agent.py`) runs its own goal-directed REPL:
- LLM receives: system prompt + goal (pinned) + compressed history + available skills list
- Executes commands via ptyprocess inside bubblewrap sandbox
- Updates SQLite `tasks` row (`last_output`, `step_count`, `status`) every turn
- Writes `task_events` row after every LLM call
- Compresses memory when token threshold exceeded → saves versioned snapshot

### Interrupting & Resuming

- `/task <name> pause` → sends `SIGTSTP` to task ptyprocess, status → `paused`
- `/task <name> resume` → sends `SIGCONT`, status → `running`
- Switching to task window and `Ctrl+C` also interrupts current command

### Entering a Task

- `/task <name>` → switches tmux focus to task's window. Interact with agent directly.
- `/task <name> inspect` → opens raw bash shell in task folder (you, no agent, full control)
- `Ctrl+B 0` → return to orchestrator window

### Completing

- Task agent signals completion (LLM returns done signal or user runs `/task <name> done`)
- Status → `completed`, `ended_at` set, tmux window kept open for review
- Bottom panel shows ✓ green row

### Reconciliation on Reconnect

On orchestrator startup, `reconcile.py`:
1. Reads all `running`/`starting`/`paused` tasks from SQLite
2. Checks each tmux window ID via libtmux
3. Window alive → status unchanged, handle re-attached
4. Window gone → status → `lost`, user notified at next prompt: `[react-app] lost while disconnected`

---

## Sandboxing

### Primary: bubblewrap

```bash
bwrap \
  --bind ~/tasks/<name>  ~/tasks/<name> \   # full read/write
  --ro-bind /usr /usr \                     # read-only system
  --ro-bind /etc /etc \
  --ro-bind /home /home \                   # read-only rest of home
  --ro-bind /tmp /tmp \
  --dev /dev \
  --proc /proc \
  --unshare-pid \
  -- /bin/bash -c "<command>"
```

- Task folder: only writable location
- Rest of filesystem: read-only
- Network: unrestricted (normal user access)
- bubblewrap is required as a system dependency (added to install.sh)

### Fallback: write-intercept

If `bwrap` not found at runtime, `sandbox.py` falls back to Python-layer interception:
- Parses every command before execution
- Blocks any write-targeting path outside the task folder
- Orchestrator warns on startup: `bubblewrap not found — using software sandbox (weaker)`

### Safety Layer

Bubblewrap is a second layer — `safety.py` destructive blocklist still runs on every task command. Dangerous commands require `YES` confirmation in the task window.

---

## Versioned Memory & Snapshots

### Storage

```
~/tasks/<name>/.agentic/memory/
  v1.json          (auto snapshot)
  v2.json          (auto snapshot)
  v3.json          (manual: "before database setup")
  current.json     (active context)
```

Each snapshot:
```json
{
  "version": "v3",
  "label": "before database setup",
  "type": "manual",
  "compressed": "...",
  "raw_last_turns": [...],
  "turn_count": 14,
  "token_count": 1840,
  "created_at": "2026-03-29T10:22:00"
}
```

### Automatic Snapshots

- Triggered by same token threshold as main shell compressor
- Uses `AGGRESSIVE` compression mode (vs `MODERATE` for main shell)
- Goal statement always pinned verbatim at position 0, never compressed
- After each completed plan step, raw turns replaced with 1-2 sentence step summary
- Snapshot saved to both SQLite `task_memory` table and `.agentic/memory/vN.json`

### Manual Checkpoints

```
/task react-app checkpoint "before database setup"
```

Saves current memory state immediately with user-provided label.

### Reverting

```
/task react-app revert v2
/task react-app revert "before database setup"
```

- Loads snapshot as active context for next LLM call
- Does NOT undo filesystem changes — context only
- Previous active context auto-saved as snapshot before revert

### Orchestrator View

- Bottom panel: `snapshot_count` and `last_checkpoint_label` per task
- `/task <name> history` — full snapshot list with labels, turn counts, token counts

---

## Compression Strategy

Three layers applied to task agents:

**Layer 1 — AGGRESSIVE compression mode**
Tasks are goal-directed and linear. Older steps compress very well. `token-reducer` called with `AGGRESSIVE` setting (vs `MODERATE` for main shell).

**Layer 2 — Step summaries**
After each completed plan step, agent summarizes what it did in 1-2 sentences. Raw turn exchange replaced with summary. Only current step's raw turns kept verbatim. Dramatically reduces context growth for long tasks.

**Layer 3 — Skill deduplication**
Skills loaded as context are content-hashed. If the same skill appeared in the previous snapshot, it is referenced by hash rather than re-embedded. Saves tokens on every call after the first.

---

## Per-Task Telemetry

### Bottom Tasks Panel (pane 2)

```
[react-app]   ● running   step 3/6  · 2,341 tok · $0.0012  · 4m32s
[ml-pipeline] ✓ done      6 steps   · 8,102 tok · $0.0041  · 12m10s
[data-scrape] ⏸ paused    step 2/4  · 1,203 tok · $0.0006  · 1m15s
```

Polls SQLite every 5 seconds. Full-width pane, fixed height (configurable, default 3 rows + header).

### Per-Task Stats

`/task <name> stats` shows:
- Token cost per step (table)
- Compression ratio per snapshot
- Total vs compressed context size over time
- Total cost, total tokens, total calls
- Runtime, step count, snapshot count

### Aggregated View

`/stats` on main shell shows combined: all tasks + main shell for the day.

---

## Skills Library

### Directory Structure

```
~/.local/share/agentic-shell/skills/
  instructions/          (global markdown — loaded as LLM context)
    deploy-nginx.md
    setup-react.md
    docker-basics.md
  scripts/               (global executables — called by agent)
    deploy_nginx.py
    health_check.sh

~/tasks/<name>/.agentic/skills/
  instructions/          (task-local, overrides global on name collision)
  scripts/               (task-local executables)
```

### How Agents Use Skills

**Markdown skills** — merged (local overrides global), keyword-matched against current goal, injected into system prompt. Not all skills loaded every call — only relevant ones.

**Executable scripts** — called by name:
```
skill:deploy_nginx
skill:health_check --port 3000
```
`skills.py` resolves path (local first, then global), runs inside sandbox, returns output to agent as context.

**Skill discovery** — on task start, available skills listed in system prompt:
```
Available skills: deploy-nginx, setup-react, docker-basics, seed_db (local)
Call executable skills with: skill:<name> [args]
```

### Managing Skills

```
/skill new deploy-nginx              # global markdown skill
/skill new deploy_nginx --script     # global executable script
/skill new seed_db --local           # task-local (must be inside a task)
/skill list                          # all global skills
/skill list --local                  # task-local skills for current task
/skill edit deploy-nginx             # opens in $EDITOR
```

Skills are plain files — naturally versioned by git if the folder is a git repo.

---

## User-Facing Commands

| Command | Description |
|---|---|
| `/task <name> pause` | Pause a running task |
| `/task <name> resume` | Resume a paused task |
| `/task <name>` | Switch to task's tmux window |
| `/task <name> inspect` | Open raw bash shell in task folder |
| `/task <name> done` | Mark task complete manually |
| `/task <name> kill` | Kill task and clean up |
| `/task <name> stats` | Full token/cost breakdown |
| `/task <name> history` | List all memory snapshots |
| `/task <name> checkpoint "<label>"` | Save named memory snapshot |
| `/task <name> revert <version>` | Revert context to snapshot |
| `/task list` | List all tasks and statuses |
| `/skill new <name>` | Create a new global markdown skill |
| `/skill new <name> --script` | Create a new global executable skill |
| `/skill new <name> --local` | Create task-local skill |
| `/skill list` | List all available skills |
| `/skill edit <name>` | Edit a skill |

---

## File and Directory Layout (additions)

```
~/tasks/                             (configurable base, default ~/tasks/)
  <name>/
    .agentic/
      memory/
        v1.json … vN.json
        current.json
      skills/
        instructions/
        scripts/

~/.local/share/agentic-shell/
  skills/
    instructions/
    scripts/

~/.config/agentic-shell/config.json
  tasks_base_dir: "~/tasks"          (new config field)
```

---

## Key Design Decisions

| Decision | Reason |
|---|---|
| tmux windows per task | Survives SSH disconnect, natural navigation with Ctrl+B, orchestrator always window 0 |
| SQLite reconciliation on startup | No daemon needed — libtmux + SQLite is sufficient for robust state recovery |
| bubblewrap as primary sandbox | Kernel-enforced, not code-enforced. Available on Ubuntu 22.04+, used by Flatpak |
| AGGRESSIVE compression for tasks | Tasks are linear goal-directed sequences — old steps compress far better than open-ended shell sessions |
| Step summaries replace raw turns | Single biggest token savings for multi-step tasks — raw turns grow O(N), summaries grow O(1) per step |
| Skill keyword matching | Avoids bloating every LLM call with all skills — only injects what's relevant to current goal |
| Local skills override global | Project-specific deploy scripts should take precedence without needing to modify global library |
| Goal pinned verbatim in compression | Agent must never lose sight of what it's trying to accomplish |
| Revert is context-only | Filesystem changes are not tracked/reversible here — that's git's job. Memory revert is about correcting LLM direction, not undoing commands |
