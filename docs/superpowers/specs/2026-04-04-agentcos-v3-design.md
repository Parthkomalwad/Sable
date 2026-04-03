# AgenticOS v3 — Design Document

**Date:** 2026-04-04  
**Status:** Approved for implementation  
**Approach:** Strict phase order (A) — each phase passes acceptance criteria before the next begins

---

## What we're building

Two major additions on top of the existing v2 Python login shell:

1. **Task Engine** — autonomous background agents that survive SSH disconnects, run in dedicated tmux windows, maintain versioned memory snapshots, and execute inside a bubblewrap sandbox (with Python-layer fallback)
2. **Adaptive Skill Learning** — observes repeated command patterns across sessions, auto-generates markdown skill files via the configured LLM backend after 3 occurrences

---

## Constraints

- Linux-native; installs on any distro via `install.sh`
- No new dependencies beyond the approved list
- Keyword-based pattern matching only — no embeddings, no extra LLM calls at detection time
- User guidance to running agents is non-blocking, prepended to next LLM turn (no pause)
- Audit logging must be added in Phase 1 (currently missing from codebase); PatternWatcher depends on it
- `safety.py` blocklist runs on every task agent command — sandbox is a second layer, not a replacement
- No changes to: `watch.py`, `router.py`, `executor.py`, `safety.py`, any LLM backend file

---

## Architecture

```
orchestrator window (window 0)
├── pane 0: main shell REPL (loop.py)
├── pane 1: telemetry sidebar (watch.py) — existing
└── pane 2: tasks panel (tasks/panel.py) — NEW, full-width bottom strip

task window (window 1+, one per task)
└── pane 0: TaskAgent REPL (tasks/agent.py)
```

```
shell/
  tasks/          NEW — task engine
    __init__.py
    panel.py      pane 2 renderer, polls SQLite every 5s
    memory.py     per-task versioned snapshots
    sandbox.py    bwrap wrapper + Python-layer fallback
    agent.py      autonomous goal-directed REPL
    manager.py    lifecycle: spawn/pause/resume/kill/attach
    reconcile.py  startup: mark lost tasks on reconnect
    skills.py     skill loader: merges global + local skills

  skills/         NEW — adaptive learning
    __init__.py
    pattern_watcher.py   session-end observer, upserts skill_patterns
    crystalliser.py      LLM-driven skill file generator
    index.py             skills_index.json manager
```

---

## Phase 1 — Database schema + audit logging

### DB additions (db.py)

Four new tables appended after existing definitions — no existing tables altered:

- `tasks` — lifecycle state for each agent (status, tmux window, step count, last output)
- `task_events` — per-turn telemetry for agents (tokens, cost, model, compression ratio)
- `task_memory` — versioned memory snapshot metadata
- `skill_patterns` — detected usage patterns with occurrence counts and crystallisation flag

### Audit logging (new)

`audit.log` at `~/.local/share/agentic-shell/audit.log` — append-only, one line per executed command.

Format: `<ISO timestamp>\t<session_id>\t<command>`

Wired into `loop.py` at the point where a command is confirmed and sent to the executor. This is the source PatternWatcher reads in Phase 5.

### Config addition (schema.py)

One new field on `ShellConfig`:
```python
tasks_base_dir: str = "~/tasks"
```

---

## Phase 2 — tmux pane 2

`layout.py` gets a constant `TASKS_PANEL_HEIGHT_PERCENT = 12` and, after creating pane 1, creates pane 2 on window 0 only using a **vertical split** (horizontal bar across the bottom).

> Note: the PRD says `vertical=False` for pane 2 but that creates a side-by-side split. A bottom strip requires `vertical=True` (split horizontally). We use `vertical=True, percent=12`.

Result:
```
┌─────────────────────────────┬──────────────────┐
│   main shell  (pane 0)      │  sidebar (pane 1)│
├─────────────────────────────┴──────────────────┤
│           tasks panel (pane 2)                  │
└─────────────────────────────────────────────────┘
```

---

## Phase 3 — Task Engine files

### tasks/panel.py

Runs in pane 2. Polls `tasks` table every 5s, renders a single overwriting status line via Rich. Handles missing table silently (first launch). Sets `PROMPT_TOOLKIT_NO_CPR=1`.

Status symbols: `●` running (green), `✓` done (green), `⏸` paused (yellow), `✗` lost (red), `○` starting (dim white).

### tasks/memory.py — TaskMemory

Wraps the existing `compressor.py` interface with AGGRESSIVE mode. Key behaviours:
- Goal is always pinned at position 0 — never compressed
- After each completed plan step: replace raw exchange with 1-2 sentence summary
- Only current step's raw turns kept verbatim
- Snapshots saved to `~/tasks/<name>/.agentic/memory/vN.json` and indexed in `task_memory` table
- Skills loaded as context are content-hashed — if same hash appeared in previous snapshot, reference by hash instead of re-embedding (token savings)

### tasks/sandbox.py — Sandbox

Detects bwrap at runtime. If available: wraps commands with full bwrap invocation (bind task folder R/W, everything else R/O, unshare-pid). If not: logs one warning, falls back to `intercept_write()` which blocks any write targeting a path outside the task folder.

### tasks/agent.py — TaskAgent

Autonomous REPL. Per-turn sequence:
1. Build context: system prompt + pinned goal + compressed history + matched skills list
2. `backend.complete()` — same LLMBackend as loop.py
3. `safety.py` blocklist check
4. `sandbox.wrap_command()` + PtyProcessUnicode
5. Update `tasks` row
6. Write `task_events` row
7. Compress if token threshold exceeded
8. Check for `"done": true` in LLM JSON response
9. Drain non-blocking guidance queue — prepend any pending message to next context

**Guidance input:** a daemon thread reads stdin line-by-line into a `queue.Queue`. Main loop drains it non-blocking before each LLM call. No pausing, no special prefixes.

**Done signal:** LLM returns `{"done": true}`. Agent sets `status = completed`, writes `ended_at`, keeps tmux window open for review.

### tasks/manager.py — TaskManager

Lifecycle operations called from `/task` builtins:
- `spawn`: mkdir, write tasks row (starting), new tmux window, launch agent
- `pause/resume`: SIGTSTP/SIGCONT on ptyprocess, update status
- `kill`: close tmux window, set completed + ended_at
- `attach`: libtmux `select_window`
- `inspect`: open plain bash in task folder (agent not running)
- `checkpoint/revert`: delegate to TaskMemory
- `list_tasks`, `stats`, `history`: SQLite reads

### tasks/reconcile.py

Called once from `main.py` after config loads. Reads all tasks with status in `(running, starting, paused)`, checks each `tmux_window_id` against live session, marks missing ones `lost`. Returns list of lost names for main.py to print.

### tasks/skills.py — TaskSkillLoader

Keyword-matches goal against skill filenames and first-line descriptions. Local skills (in `~/tasks/<name>/.agentic/skills/`) override global on name collision. Returns list with name, content, hash, source. In Phase 5, `load_relevant()` is updated to consult `SkillIndex.get_ranked()` instead of raw keyword matching.

---

## Phase 4 — Builtin wiring

### loop.py additions

`/task` and `/skill` checked before router, following the existing `/clip` pattern.

`/task` routing table: attach, pause, resume, done/kill, inspect, stats, history, checkpoint, revert, list.

`/skill` routing table: new (markdown or script, global or local), list, edit.

Audit log write wired here — one line appended to `audit.log` for every command that reaches the executor.

### main.py addition

```python
from shell.tasks.reconcile import reconcile
lost_tasks = reconcile(db_path, tmux_session)
for name in lost_tasks:
    print(f"[warning] task '{name}' was lost while disconnected")
```

---

## Phase 5 — Adaptive skill learning

### skills/pattern_watcher.py — PatternWatcher

Called at `/exit`. Reads `audit.log` and `token_events` table. Groups commands by:
- Same repository path (inferred from cwd in audit log)
- Same intent keywords (top N non-stopword tokens from the goal/command sequence)
- Same command sequence shape (command names without arguments)

Computes a stable `pattern_hash` = SHA256 of sorted frozenset of (repo_path, intent_keywords). Upserts into `skill_patterns`. Returns patterns that crossed threshold (occurrence_count >= 3, crystallised = 0).

### skills/crystalliser.py — SkillCrystalliser

Pulls raw command history for a pattern cluster from `audit.log`. Sends to LLM with a skill-writing system prompt. Writes output to `skills/instructions/<slug>.md`. Updates `skills_index.json`. Marks `skill_patterns` row crystallised.

`update()` re-crystallises an existing skill when PatternWatcher detects divergence from recent usage.

### skills/index.py — SkillIndex

Manages `skills_index.json`. Tracks: name, file, keywords, auto_generated flag, confidence (0.0–1.0), use_count, last_used, needs_update, created_at.

Confidence nudges: +0.05 on success (max 1.0), -0.1 on failure (min 0.0). Initial: 0.5 auto-generated, 1.0 manual.

`get_ranked()`: keyword-match then sort by confidence desc, use_count desc.

---

## Key decisions recorded

| Decision | Rationale |
|---|---|
| Keyword-based pattern matching, not embeddings | No new deps; shell commands are structured enough that keywords cluster correctly |
| Guidance input is non-blocking, prepended to next turn | Preserves autonomous nature; explicit `/task pause` exists for stopping |
| Audit logging added in Phase 1 | PatternWatcher needs populated logs; wiring it early means real data by Phase 5 |
| pane 2 uses `vertical=True` (not `vertical=False` as PRD states) | PRD has an error; `vertical=False` = side-by-side, `vertical=True` = horizontal bar |
| Revert is context-only | Filesystem undo is git's job |
| bwrap fallback uses path interception, not deny-all | Deny-all would break too many legitimate agent operations |

---

## Acceptance criteria (from PRD)

- **Phase 1:** Four new tables exist in `sessions.db` after fresh launch; `audit.log` is written on each executed command
- **Phase 2:** Three-pane layout visible on new login (shell, sidebar, tasks bar)
- **Phase 3:** `python3 -m shell.tasks.agent --task test --goal "create hello.txt"` creates file in `~/tasks/test/`, marks completed in SQLite
- **Phase 4:** `/task list` prints rows; `/task <name> pause` pauses; `/skill list` prints available skills
- **Phase 5:** Running same deploy sequence 3 times across sessions produces a skill file in `skills/instructions/` and a matching entry in `skills_index.json`
- **Phase 6:** SSH disconnect + reconnect shows task as `lost`; `/task <name> revert v1` loads earlier context without error
