# Sable - Architecture Overview

> A Python login shell that replaces `/bin/bash` on any Linux server. SSH in. Your server understands plain English.

This document describes the full system architecture across three layers: the core shell flow, the intelligence and telemetry layer, and the sidebar, tmux layout, and clipboard system. Each layer is covered by a dedicated diagram followed by a component breakdown.

---

## Diagram 1 - Core shell flow

![Core shell flow](assets/core.png)

This diagram covers everything from SSH connection to command execution. It shows how a user's input travels from the SSH client through the entry point, into the REPL loop, gets classified by the router, and lands in the executor. Storage on the right represents the SQLite database and config file that persist across sessions.

**Containers:** Entry · Shell Core · Execution · Prompt UI · Storage

**Key paths:**

- SSH non-interactive commands (`scp`, `rsync`, `git push`) hit the `SSH_ORIGINAL_COMMAND` bypass at the very first line of `main.py` and exec directly to bash. The agentic shell is never in their path.
- Interactive logins load config, resume session context from SQLite, and enter the `prompt_toolkit` REPL loop.
- The router classifies every line as `BASH`, `AGENTIC`, or `AMBIGUOUS`. Ambiguous inputs ask the user to choose.
- All commands execute via `PtyProcessUnicode` so interactive programs (vim, htop, ssh) get a real TTY automatically.
- `cd` is intercepted before the PTY and calls `os.chdir()` directly on the Python process. Subprocess `cd` has no effect on the parent.

---

## Diagram 2 - Intelligence and telemetry layer

![Intelligence and telemetry layer](assets/architecture.png)

This diagram covers the LLM backends, safety guard, multi-step planner, telemetry database, session memory, and audit trail. These components activate on every agentic route decision.

**Containers:** LLM Layer · Safety · Planner · Telemetry · Memory · Audit

**Key paths:**

- All three backends (Ollama, OpenAI, Anthropic) implement the same `LLMBackend` abstract interface. The router calls `backend.complete()` without knowing which backend is active.
- Every backend streams via SSE using `httpx` and `httpx-sse`. Tokens are extracted from the final SSE chunk.
- The JSON response contract is `{command, explanation, safe, plan}`. A four-step fallback chain handles malformed responses: strip fences, parse, re-ask, show raw.
- The safety guard runs on every command regardless of route (bash or agentic). Eleven regex patterns plus a Shannon entropy check for unknown secret formats.
- Multi-step plans from the `plan` array are executed by `planner.py` step by step, with `○ → ✓ / ✗` status rendered live in a Rich panel.
- Every LLM call writes a `TokenEvent` to SQLite. `tiktoken` counts tokens client-side for budget checks before the call; actual API response counts are used for logging.
- Session memory is compressed with `token-reducer` after every N turns exceeding a token threshold. The last 2 turns are always kept verbatim. On next login, the compressed context is loaded as prior messages.

---

## Diagram 3 - Sidebar, tmux layout, and clipboard

![Sidebar, tmux layout, and clipboard](assets/tmux-layout.png)

This diagram covers the tmux session structure, the sidebar watch process and its seven panels, the clipboard manager and TUI picker, and the file-based IPC used for sidebar key navigation.

**Containers:** tmux Layout · Sidebar · Clipboard · Key IPC · Files and Dirs

**Key paths:**

- On login, `layout.py` uses `libtmux` to create a tmux session named `sable-NNNN` with a single window split into two panes: pane 0 (shell, ~80% width) and pane 1 (sidebar, 44 columns fixed).
- The sidebar is a separate Python process running in pane 1. It polls SQLite every 5 seconds and re-renders seven Rich panels: session stats, system metrics, git status, top processes, 7-day token table, clipboard snippet list, and shortcuts reference.
- Sidebar width is fixed at 44 columns. Terminal size is not queried dynamically inside tmux (`PROMPT_TOOLKIT_NO_CPR=1` prevents cursor position queries that freeze the prompt).
- The clipboard picker is a full-screen `prompt_toolkit.Application` launched by `/clip`. It supports live filter, `↑↓` navigation, inline add, delete, and `Enter` to run the selected snippet in pane 0 via `tmux send-keys`.
- Sidebar key navigation (Up/Down/Enter in the clipboard panel) uses a file-based IPC mechanism: tmux key bindings in pane 1 write `UP`, `DOWN`, or `ENTER` to `~/.local/share/agentic-shell/clip_key`. Each render cycle, `watch.py` reads and deletes this file and updates scroll state accordingly.
- `/exit` touches an `exit_requested` flag file. The shell runs inside a `while true` restart loop in `install.sh`; the flag tells the loop to `exec bash` instead of restarting.
- `/new` sets `AGENTIC_NEW_SESSION=1` and creates a fresh tmux session. New sessions do not load previous session context from SQLite.

---

## Component reference

| Component | File | Role color | Description |
|---|---|---|---|
| Entry point | `shell/main.py` | purple | SSH bypass, config load, session resume, wizard trigger |
| REPL loop | `shell/loop.py` | purple | `prompt_toolkit` PromptSession, builtin handling, routing pipeline |
| Router | `shell/router.py` | magenta | Classifies input as BASH, AGENTIC or AMBIGUOUS; `explain()` backs `/route why` |
| Executor | `shell/executor.py` | amber | PtyProcessUnicode for all commands, `cd` interception, Rich-rendered `ls` and `cat` |
| Safety | `shell/safety.py` | red | 11-pattern blocklist, entropy redaction, destructive confirm flow |
| Planner | `shell/planner.py` | amber | Multi-step plan execution with per-step status |
| LLM base | `shell/llm/base.py` | green | Abstract `LLMBackend`, `LLMResponse` dataclass, system prompt builder, JSON fallback chain |
| Ollama backend | `shell/llm/ollama.py` | olive | NDJSON streaming, local inference |
| OpenAI backend | `shell/llm/openai.py` | gray | SSE streaming via httpx-sse |
| Anthropic backend | `shell/llm/anthropic.py` | gray | SSE streaming, requires `anthropic-version: 2023-06-01` header |
| Telemetry DB | `shell/telemetry/db.py` | indigo | SQLite WAL: `token_events`, `session_memory`, `snippets`, `tasks`, `task_events`, `task_memory`, `skill_patterns` |
| Token events | `shell/telemetry/events.py` | indigo | `TokenEvent` dataclass |
| Sidebar | `shell/telemetry/watch.py` | indigo | 7-panel Rich renderer, 5s poll (1s while a clip key is pending), clip_key IPC |
| Session compressor | `shell/memory/compressor.py` | green | token-reducer with MODERATE compression, preserves last 2 turns |
| Session store | `shell/memory/store.py` | green | load/save compressed context to SQLite |
| Config wizard | `shell/config/wizard.py` | indigo | First-run setup, `chmod 600` on config file |
| Config schema | `shell/config/schema.py` | indigo | `ShellConfig` dataclass |
| Keyring | `shell/config/keyring.py` | indigo | `secretstorage` integration for API keys (phase 2) |
| tmux layout | `shell/tui/layout.py` | magenta | `libtmux` session and pane management |
| Settings panel | `shell/tui/panel.py` | indigo | `/config` overlay using prompt_toolkit prompts |
| Clipboard manager | `shell/clipboard/manager.py` | amber | `/clip` commands and TUI picker |
| Orchestrator | `shell/tasks/orchestrator.py` | purple | Multi-turn reasoning loop for the NL path: `run / spawn / done` |
| Task agent | `shell/tasks/agent.py` | purple | Autonomous worker, own turn loop, sandboxed workspace |
| Task manager | `shell/tasks/manager.py` | magenta | Spawn, pause, resume, kill, attach, checkpoint, revert |
| Sandbox | `shell/tasks/sandbox.py` | red | `bwrap` namespaces with a bash-wrapper fallback |
| Task memory | `shell/tasks/memory.py` | green | Pinned goal, per-step summaries, versioned `vN.json` snapshots |
| Reconcile | `shell/tasks/reconcile.py` | indigo | Marks tasks whose tmux window vanished as `lost` |
| Tasks panel | `shell/tasks/panel.py` | indigo | Bottom pane, live table of task status |
| Pattern watcher | `shell/skills/pattern_watcher.py` | olive | Clusters repeated commands from `audit.log` |
| Skill crystalliser | `shell/skills/crystalliser.py` | olive | Turns a cluster into a markdown skill via the LLM |
| Skill index | `shell/skills/index.py` | olive | `skills_index.json`: keywords, confidence, use counts |
| Skill loader | `shell/tasks/skills.py` | olive | Injects matching skills into a task agent's context |
| Mode switch | `shell/mode.py` | magenta | `/bash` subshell, `sable on/off/status`, session re-attach |
| Tour | `shell/tour.py` | green | `/tour` onboarding walkthrough |
| Paths | `shell/paths.py` | indigo | Resolves `~/.sable/` state locations |

---

## Task engine (`shell/tasks/`)

The natural-language path does not call the LLM once and run the answer. It
hands the goal to an **orchestrator**, which reasons in a loop, and can
delegate work to **sub-agents** that run in their own tmux windows.

### The orchestrator loop

`OrchestratorAgent` (`shell/tasks/orchestrator.py`) is what `loop.py` calls for
every AGENTIC line. Each turn the model returns exactly one action:

```json
{"action": "run",   "command": "...", "explanation": "..."}
{"action": "spawn", "name": "slug", "goal": "...", "explanation": "..."}
{"action": "done",  "explanation": "..."}
```

- **run** shows the command with its one-line reason and waits: `enter` runs it,
  `e` edits it first, `q` cancels. The command runs in a pty in the user's cwd
  with a 120 second timeout, and its output becomes the next turn's context.
- **spawn** creates a sub-agent. A timeout also triggers one automatically:
  the whole remaining goal is handed off rather than retried.
- **done** ends the loop and writes a result summary.

The loop is capped at 20 turns. Goals are wrapped in `<goal>` tags with an
instruction to ignore anything embedded in them, which is a partial defence
against prompt injection from goal text.

### Sub-agents

`TaskManager.spawn()` opens a tmux window named `task:<name>` and starts
`python -m shell.tasks.agent`. Each sub-agent gets:

- a **workspace** at `~/tasks/<name>/workspace/`, which is its cwd,
- a **sandbox** (`Sandbox`): `bwrap` namespaces where user namespaces are
  available, otherwise a bash wrapper that shadows write-capable tools and
  rejects paths outside the workspace,
- a **handoff file** with the orchestrator's recent history,
- **memory** (`TaskMemory`) that pins the goal at position 0, summarises each
  completed step, and snapshots to `vN.json`,
- **skills** matched to the goal and injected into its context.

A sub-agent runs its own turn loop (`{command, explanation, done}`), capped at
25 steps, re-reminded of its goal every 5 steps. Commands containing `docker`,
`npm`, `pip`, `yarn` or `git clone` get a 600 second timeout instead of 120.

### How results come back

Sub-agents write two files under `~/tasks/<slug>/<name>/.agentic/`:
`status.md` while running and `result.md` when finished. The orchestrator reads
them while building each turn's messages; a completed result is folded into the
context and the file is deleted so it is reported once. There is no event bus
yet, so this poll is the only channel (A1 replaces it in Phase 1).

`reconcile()` runs at startup and marks any task whose tmux window has gone as
`lost`, which is what makes an SSH disconnect recoverable.

```
you type a goal
  └─ OrchestratorAgent.run()
       ├─ turn 1: run  → confirm → pty → output into context
       ├─ turn 2: spawn → TaskManager.spawn() → tmux window task:<name>
       │                                          └─ TaskAgent loop in a sandbox
       │                                               └─ writes status.md / result.md
       ├─ turn 3: run  → (result.md folded into this turn's context)
       └─ turn 4: done → result summary
```

---

## Skills (`shell/skills/`)

Sable watches what you do and writes up the things you repeat.

1. **`PatternWatcher`** runs at `/exit`. It reads `audit.log`, groups commands
   by repo path and intent keywords, hashes each cluster, and upserts
   `skill_patterns`. Clusters seen three or more times cross the threshold.
2. **`SkillCrystalliser`** sends a crossed cluster to the LLM with a
   skill-writing prompt and saves the result as
   `~/skills/instructions/<slug>.md`: when to use it, the steps, the commands.
   This runs immediately after the watcher, unattended, so skills currently
   appear without an approval step (Phase 2 adds one).
3. **`SkillIndex`** keeps `~/skills/skills_index.json`: name, file, keywords,
   `auto_generated`, `confidence`, `use_count`, `last_used`, `needs_update`.
   Confidence starts at 0.5 for generated skills and 1.0 for hand-written ones,
   and moves +0.05 on success, -0.1 on failure, clamped to [0, 1].
4. **`TaskSkillLoader`** matches skills to a goal by keyword and injects them
   into a sub-agent's context, local skills overriding global ones by name.

Skills are plain markdown you can read, edit or delete. Everything about them
is on disk, not in a database.

**Known gap:** the confidence loop is not closed. `SkillIndex.get_ranked()` and
`record_use()` exist but nothing calls them, and `TaskSkillLoader` matches on
keywords only, so confidence is written but never read. Closing that loop is
B1, the first item of Phase 2.

---

## Data flow - end to end

```
SSH login
  └─ SSH_ORIGINAL_COMMAND set?
       ├─ yes → exec /bin/bash directly (scp/rsync/git safe)
       └─ no  → load config + session context → REPL loop

User types input
  └─ builtin? (/help, /clip, /config…)
       ├─ yes → handle and loop
       └─ no  → Ctrl+B bypass or offline?
                   ├─ yes → execute_bash()
                   └─ no  → classify()
                               ├─ BASH   → safety check → ptyprocess → audit log
                               ├─ AGENTIC → budget check → OrchestratorAgent
                               │            → turn loop: run / spawn / done
                               │            → confirm each command → ptyprocess
                               │            → write TokenEvent → compress if needed
                               └─ AMBIGUOUS → ask user [b/a] → record the answer

/exit
  └─ PatternWatcher.observe() → clusters seen 3+ times
       └─ SkillCrystalliser → ~/skills/instructions/<slug>.md

Sidebar (separate process, pane 1)
  └─ every 5 seconds: read SQLite → render 7 Rich panels → write to pane 1
  └─ each render: read clip_key file → update scroll state → delete file
```

---

## File and directory layout

```
~/.config/agentic-shell/
  config.json                  user config · chmod 600

~/.local/share/agentic-shell/
  sessions.db                  SQLite WAL database
  history                      prompt_toolkit readline history
  exit_requested               flag file · /exit drops to bash
  clip_key                     sidebar IPC · UP / DOWN / ENTER

~/.sable/
  disabled                     flag file · `sable off` writes it
  state/
    router_corrections.tsv     input<TAB>label rows from Ctrl+B and [b/a]

~/tasks/<name>/
  workspace/                   the sub-agent's sandboxed cwd
  .agentic/
    goal.txt handoff.txt       what it was asked to do
    status.md result.md        how it is going, how it went
    memory/vN.json             versioned context snapshots

~/skills/
  instructions/<slug>.md       one skill, plain markdown
  skills_index.json            keywords, confidence, use counts

/usr/local/bin/sable           installed launcher
/etc/shells                    sable registered here
/var/log/agentic-shell/
  audit.log                    all executed commands · append-only
```

Runtime state still lives under the `agentic-shell` XDG paths; `~/.sable/` holds
only what Phase 0 added. Everything moves to `~/.sable/` together in Phase 0.5,
so current installs keep their data until then.

---

## Key design decisions

| Decision | Reason |
|---|---|
| All commands in `PtyProcessUnicode` | Interactive programs (vim, htop, ncurses) need a real TTY - detecting which commands need one is fragile |
| `cd` via `os.chdir()` | Subprocess `cd` changes directory only in the child process, not the Python parent |
| SQLite WAL mode | Sidebar process reads while shell process writes simultaneously - WAL allows concurrent readers without lock contention |
| Sidebar as a separate process | Sidebar refresh must never block the shell REPL |
| Direct `httpx` - no LiteLLM | Full control over headers, streaming, and timeouts; eliminates supply chain dependency |
| Fixed `SIDEBAR_WIDTH = 44` | Dynamic terminal size queries (`CPR`) freeze inside tmux |
| `PROMPT_TOOLKIT_NO_CPR=1` | Prevents prompt_toolkit from querying cursor position, which freezes inside tmux |
| Exit flag file for `/exit` | Shell runs in a `while true` restart loop; flag signals the loop to exec bash instead of restart |
| `AGENTIC_NEW_SESSION=1` for `/new` | New sessions must not load previous session context |
| clip_key file for sidebar IPC | Sidebar is a separate process; file-based IPC is simple and reliable without requiring sockets or pipes |

---

*Architecture as of March 2026. Diagrams generated from `docs/architecture.md`. All three diagrams are available as SVG and PNG via the download buttons above each diagram.*