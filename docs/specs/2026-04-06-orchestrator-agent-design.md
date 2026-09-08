# Orchestrator Agent Design Document

**Date:** 2026-04-06
**Status:** Approved for implementation

---

## Problem

The main REPL agent is too limited: it makes a single LLM call, emits one shell command, and shows a confirm prompt. It cannot reason across multiple steps, decide when to delegate, or coordinate parallel work. Sub-agents each create their own isolated folder with no shared read access between them.

---

## What We're Building

Two changes on top of the existing v3 codebase:

1. **OrchestratorAgent** a multi-turn reasoning loop that replaces the single LLM call in `loop.py`. It acts directly for simple tasks and spawns `TaskAgent`s for long-running or parallelizable work.
2. **Shared task folder with per-agent write isolation** one folder per user request; sub-agents share read access but write only to their own subfolder.

---

## Architecture

```
User types NL input
    │
    ▼
loop.py REPL
  → router classifies as NL
  → OrchestratorAgent(goal, cwd, config).run()
        │
        ├── Turn N: action=run   → execute command directly, feed output back
        ├── Turn N: action=spawn → TaskManager.spawn() with shared task dir
        ├── Turn N: action=run   → monitor sub-agents via status.md
        └── Turn N: action=done  → print summary, return to REPL
```

**New file:** `shell/tasks/orchestrator.py`

**Changed files:**
- `loop.py` NL branch replaced with `OrchestratorAgent.run()`
- `shell/tasks/sandbox.py` add `shared_read_dir` parameter
- `shell/tasks/manager.py` add `task_base_dir` parameter to `spawn()`

**Unchanged:** `TaskAgent`, all LLM backends, `safety.py`, `router.py`, `executor.py`, `watch.py`

---

## Section 1: OrchestratorAgent

### Location
`shell/tasks/orchestrator.py`

### Initialization
```python
OrchestratorAgent(
    goal: str,          # original user input
    cwd: str,           # current working directory at time of request
    config: ShellConfig,
    db_path: str,
    task_manager: TaskManager,
)
```

On init:
- Generates a task slug: `<sanitized-goal-prefix>-<YYYYMMDD>` (e.g. `build-react-app-20260406`)
- Does **not** create the task folder yet creation is lazy
- Task folder `tasks/<slug>/` is created only when the first `spawn` action is triggered
- Simple tasks that never spawn leave no folder on disk

### Reasoning Loop

Max 20 turns. Each turn:

1. Build messages: pinned goal + command history + sub-agent status summaries
2. Call LLM (`backend.complete()`)
3. Parse action from response
4. Execute action
5. Check for `done=true`

### Action Schema

The orchestrator's system prompt instructs the LLM to respond with one of three actions:

```json
{ "action": "run", "command": "ls -la", "explanation": "check what exists" }
```
```json
{ "action": "spawn", "name": "frontend", "goal": "create React app with Vite in ./frontend/", "explanation": "long-running scaffold" }
```
```json
{ "action": "done", "explanation": "React app running on port 3000, Dockerfile written" }
```

### Action Handling

**`run`:**
- Show `↵ run  e edit  q cancel` prompt (same UX as today, moved from `loop.py`)
- If confirmed: execute via `ptyprocess` in `tasks/<slug>/` as CWD
- Feed output back into conversation as next user turn
- Write to audit log

**`spawn`:**
- Print `[spawning agent: <name>]`
- Write handoff file to `tasks/<slug>/<name>/.agentic/handoff.txt` containing: original goal + orchestrator history summary + sub-agent's specific goal
- Call `task_manager.spawn(name=<name>, goal=<goal>, task_base_dir=tasks/<slug>/)`
- Continue loop (non-blocking orchestrator does not wait)

**`done`:**
- Print explanation to user
- Write `tasks/<slug>/.agentic/result.md`
- Return to REPL

### Sub-agent Monitoring

Each turn, before calling LLM, orchestrator reads:
- `tasks/<slug>/<agent-name>/.agentic/status.md` injected as `[agent <name> status]` message
- `tasks/<slug>/<agent-name>/.agentic/result.md` if exists, injected as `[agent <name> result]` and marked consumed

### Interruption

Ctrl+C during orchestrator loop → `KeyboardInterrupt` caught → print `[interrupted]` → return to REPL. Spawned sub-agents keep running in their tmux windows.

### System Prompt (key principles)

- You are an orchestrator. Act directly for simple/fast tasks. Spawn sub-agents only for long-running work (>30s) or tasks that can run in parallel.
- You have full R/W access inside `tasks/<slug>/`. Sub-agents handle their own subfolders.
- Every command must be non-interactive.
- Read sub-agent status each turn to know when they finish.
- When all work is done, emit `action=done`.

---

## Section 2: Task Folder Structure

```
~/.local/share/agentic-shell/tasks/
└── build-react-app-20260406/          ← shared task dir, created by orchestrator
    ├── .agentic/
    │   ├── status.md                  ← orchestrator live status
    │   └── result.md                  ← written on done
    ├── frontend/                      ← sub-agent 1 subfolder
    │   ├── workspace/                 ← sub-agent 1 write root
    │   └── .agentic/
    │       ├── handoff.txt            ← consumed on agent startup
    │       ├── status.md
    │       └── result.md
    └── docker/                        ← sub-agent 2 subfolder
        ├── workspace/
        └── .agentic/
            ├── handoff.txt
            ├── status.md
            └── result.md
```

---

## Section 3: Sandbox Changes

### New parameter: `shared_read_dir`

`Sandbox.__init__` gains an optional `shared_read_dir: str | None = None` parameter.

**Python-layer fallback (bash guard script):**
- Current behavior: blocks writes outside `task_dir`
- New behavior: additionally allows reads from `shared_read_dir` explicitly (no change needed reads are already allowed everywhere; the guard only blocks writes)
- No code change needed for reads in the Python fallback path

**bwrap path:**
- Add a read-only bind mount for `shared_read_dir`: `--ro-bind <shared_read_dir> <shared_read_dir>`
- This gives the sub-agent read access to the full task folder while its write root remains `task_dir/workspace/`

### TaskManager.spawn() change

Add `task_base_dir: str | None = None` parameter. When set:
- Sub-agent's `task_dir` = `task_base_dir/<name>/`
- Sub-agent's `workspace` = `task_base_dir/<name>/workspace/`
- `shared_read_dir` passed to Sandbox = `task_base_dir/` (the shared task root)

When not set: existing behavior unchanged (global `tasks_base/<name>/`).

---

## Section 4: loop.py Changes

### Before (NL path)
```python
response = await _call_llm(backend, user_input, cwd, config, session_context)
command = _display_command_preview(response)
if command:
    executor.run(command)
```

### After (NL path)
```python
from shell.tasks.orchestrator import OrchestratorAgent
agent = OrchestratorAgent(
    goal=user_input,
    cwd=cwd,
    config=config,
    db_path=str(DB_PATH),
    task_manager=task_manager,
)
try:
    agent.run()
except KeyboardInterrupt:
    _out("[interrupted]")
```

**Confirm/edit/cancel prompt** (`_display_command_preview`) moves into `OrchestratorAgent` called before every `run` action.

**Telemetry** (`_log_event`, `_write_audit_log`, budget check) called inside orchestrator per turn, same as today.

**Everything else in `loop.py`** (bash bypass, `/commands`, sidebar, key bindings) unchanged.

---

## Acceptance Criteria

1. `"list files here"` → orchestrator runs `ls`, shows result, done in 1 turn no task folder created
2. `"build a React app with Docker"` → orchestrator creates task folder, spawns at least one sub-agent, monitors it, reports done when sub-agent finishes
3. Sub-agent can read files from sibling agent's workspace but cannot write there (verified via sandbox)
4. Ctrl+C during orchestrator loop returns to REPL; spawned agents continue running
5. Existing `/task list`, `/attach`, `/kill` commands still work on spawned sub-agents
6. Pure bash path (Ctrl+B) completely unaffected

---

## What Does NOT Change

- `TaskAgent` internals
- All LLM backends
- `safety.py`, `router.py`, `executor.py`, `watch.py`
- `/task` command handlers in `loop.py`
- Telemetry schema
