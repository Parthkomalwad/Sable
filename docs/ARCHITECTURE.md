# AgenticOS — Full Architecture Document

> This document is intended for generating system diagrams. It describes every component, data flow, and integration point in the system.

---

## 1. System Overview

AgenticOS is a Python login shell that replaces `/bin/bash` on a Linux server. When a user SSHs in, they land in this shell instead of bash. The shell intercepts every line of input and routes it either to the operating system (bash) or to a large language model (LLM). The LLM returns a shell command, which is shown to the user for review before execution. A persistent tmux sidebar displays live system stats, token costs, git status, and a snippet clipboard.

**Core loop:**
```
User types → Router classifies → Bash OR LLM
                                     ↓
                              LLM returns JSON
                                     ↓
                         Preview shown to user
                                     ↓
                    User confirms → Command executed
```

---

## 2. Entry Point — `shell/main.py`

### Responsibilities
- SSH bypass (first executable line — non-negotiable)
- Load config from `~/.config/agentic-shell/config.json`
- Run first-run wizard if config missing
- Load session context from SQLite
- Print welcome banner
- Launch REPL loop

### SSH Bypass
```
Incoming SSH connection
        ↓
SSH_ORIGINAL_COMMAND set? ──yes──→ os.execvp("/bin/bash", command)
        ↓ no
Normal shell startup
```
This ensures scp, rsync, and git push over SSH always work — the agentic shell is never in their path.

### Session Resume
On every startup (except `/new` sessions), the shell loads compressed context from the `session_memory` SQLite table for the current user. This is shown as "session resumed (N chars)" and passed to the LLM as prior context.

---

## 3. REPL Loop — `shell/loop.py`

The main interactive loop. Built on `prompt_toolkit.PromptSession`.

### Prompt
Powerline-style with three segments:
- **Path** — blue background, current working directory (~ substituted)
- **Git branch** — purple background, current branch (silent fail if not a git repo)
- **Time** — dark grey background, HH:MM
- **Cursor** — white normally, red if last exit code was non-zero

### Input Processing Pipeline
```
User input
    ↓
_handle_builtin() ── matches /help, /clip, /history, etc. ──→ handled, continue
    ↓ not builtin
Ctrl+B bypass? ──yes──→ execute_bash() directly
    ↓ no
Offline mode? ──yes──→ execute_bash() directly
    ↓ no
classify(line) ──→ BASH / AGENTIC / AMBIGUOUS
    ↓ AMBIGUOUS
Prompt user [b]ash or [a]gentic?
    ↓
BASH path:
    is_destructive()? ──yes──→ confirm_destructive() ──no──→ skip
    execute_bash()
    audit_log()
    ↓
AGENTIC path:
    check_and_enforce_budget()
    ThinkingSpinner.start()
    _call_llm() ──→ LLMResponse
    ThinkingSpinner.stop()
    ↓
    response.plan? ──yes──→ execute_plan()
    ↓ no
    _display_command_preview()
    ↓
    AI flagged unsafe OR regex match? ──yes──→ confirm_destructive()
    execute_bash()
    _print_exec_result()  (✓ done in Xs · $0.000X · N tok)
    _log_event() ──→ SQLite
    audit_log()
    _save_turns_if_needed() ──→ compress + SQLite
```

### Key Bindings
- `Ctrl+B` — set `_bypass_next = True` (one-shot bash mode)
- `Ctrl+T` — call `toggle_sidebar()` via libtmux
- `Ctrl+X` — insert `/config` into buffer and execute
- `Ctrl+R` — reverse history search (from emacs bindings)

### Builtins (`_handle_builtin`)
Every line starting with `/` is checked here before routing.

| Command | Handler |
|---------|---------|
| `/help`, `/?` | Print `_HELP_TEXT` |
| `/clear` | `os.system("clear")` |
| `/exit`, `/quit` | Touch exit flag file → `SystemExit(0)` |
| `/model` | Print backend + model name |
| `/mode` | Toggle `config.routing_mode` auto ↔ prefix |
| `/new` | `_start_new_session()` |
| `/config` | `render_settings_panel(config)` |
| `/budget reset` | Clear `_budget_hard_stop` flag |
| `/stats` | `_show_stats(db)` |
| `/history`, `/hist` | `_show_history(db)` |
| `/clip` or `/clip ...` | `open_picker(db)` or `run_clip_command(...)` |
| `/memory` | `_show_memory(session_id)` |

### New Session (`/new`)
Creates a new tmux session with:
- Pane 0 (left) — shell process with exit-flag-aware restart loop
- Pane 1 (right) — sidebar watch process
Switches client to new session. Old session stays alive (SSH client is attached to it).
Sets `AGENTIC_NEW_SESSION=1` so the new shell does not load previous session context.

---

## 4. Router — `shell/router.py`

Classifies each line as `BASH`, `AGENTIC`, or `AMBIGUOUS`.

### Logic
- **Prefix mode:** if `routing_mode == "prefix"`, only `>>` prefix lines go to AI; everything else is BASH
- **Auto mode:** heuristic scoring
  - Shell-like signals: starts with known binary, contains flags (`-x`), pipes, redirects → BASH
  - NL signals: question words, verbs ("show", "create", "find"), no shell operators → AGENTIC
  - Low confidence on either side → AMBIGUOUS (user prompted)

---

## 5. Executor — `shell/executor.py`

All command execution goes through here.

### Special Cases (intercepted before pty)
- **`cd`** — calls `os.chdir()` directly (subprocess cd has no effect on parent process). Handles `cd -` with `_prev_cwd` module-level state.
- **`ls [flags] [path]`** — rendered with Rich Columns (short) or Rich Table (long `-l`). Colors: dirs=blue+"/", executables=green+"*", symlinks=purple, files=white.
- **`cat/head/tail <file>`** — rendered with Rich Syntax (monokai theme, line numbers). Language auto-detected from extension.

### PTY Execution
Everything else runs in `PtyProcessUnicode` — a real PTY so interactive programs (vim, htop, ssh, ncurses) work correctly. Output streams directly to stdout.

---

## 6. Safety — `shell/safety.py`

### Destructive Pattern Blocklist
13 regex patterns checked before every command execution (both bash and agentic paths):
- `rm -rf <anything>`
- `dd if=... of=/dev/...`
- `mkfs`
- `fdisk /dev/...`
- `> /dev/sd*` or `> /dev/nvme*`
- `curl ... | bash/sh`
- `wget ... | bash/sh`
- `shutdown`
- `reboot`
- `iptables -F`

### Confirmation Flow
```
is_destructive(command) == True
        ↓
Print: ⚠ reason + command
Prompt: "type YES to confirm: "
        ↓
Input == "YES"? ──no──→ skip command
        ↓ yes
Execute
```

### Secret Redaction (Privacy Mode)
When `privacy_mode = true`, text is scanned before sending to LLM:
- Pattern matching: AWS keys, JWTs, private keys, Bearer tokens, API keys, service account JSON
- Entropy check: tokens ≥ 20 chars with Shannon entropy > 4.5 are redacted
- Replaced with `[REDACTED]`

---

## 7. LLM Layer — `shell/llm/`

### Base (`base.py`)
- `LLMBackend` abstract class with `complete(messages, system) -> LLMResponse`
- `LLMResponse` dataclass: `command`, `explanation`, `safe`, `plan`, `prompt_tokens`, `completion_tokens`, `cost_usd`, `model`
- `build_system_prompt(cwd, user, os_info)` — includes CWD, username, OS, marks file contents as UNTRUSTED DATA
- JSON parse with fallback chain: strip markdown fences → `json.loads()` → re-ask model → show raw text

### JSON Contract (all backends must return)
```json
{
  "command": "string — the shell command to run",
  "explanation": "string — one sentence for the user",
  "safe": true,
  "plan": null
}
```
`plan` is `null` for single commands, or `["cmd1", "cmd2", ...]` for multi-step.

### Backends
| Backend | Protocol | Streaming |
|---------|----------|-----------|
| `ollama.py` | HTTP POST `/api/chat` | NDJSON chunks |
| `openai.py` | HTTP POST `/v1/chat/completions` | SSE via httpx-sse |
| `anthropic.py` | HTTP POST `/v1/messages` | SSE, requires `anthropic-version: 2023-06-01` header |

All backends use `httpx.Timeout(30.0)` explicitly. Cost calculated from `pricing.json` per-model token prices.

---

## 8. Planner — `shell/planner.py`

When the LLM returns a `plan` array, this module executes it.

### Flow
```
Plan received (list of commands)
        ↓
Show all steps in Rich Panel upfront
Prompt: [Enter] run  q cancel
        ↓
For each step:
    is_destructive()? ──yes──→ confirm_destructive()
    execute_bash(cmd)
    Show: ○ → ✓ (success) or ✗ (failure)
    On failure: [c]ontinue [r]etry [a]bort
        ↓
Show completion Rule
```

---

## 9. Telemetry Database — `shell/telemetry/db.py`

Single SQLite file: `~/.local/share/agentic-shell/sessions.db`
Always opened with `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL`.

### Tables

**`token_events`** — one row per LLM call
```
id, timestamp, session_id, action_type, nl_input, command,
prompt_tokens, completion_tokens, total_tokens, cost_usd, model, exit_code
```

**`session_memory`** — compressed context snapshots
```
id, session_id, username, compressed, raw_turns, token_count, created_at
```

**`snippets`** — saved commands (clipboard)
```
id, command, note, tags, use_count, created_at
```

### Key Methods
- `write_event(TokenEvent)` — insert telemetry row
- `get_today_stats()` → `{calls, tokens, cost}`
- `get_stats(days=7)` → per-day aggregates
- `check_budget(config, session_id)` → `"OK"` / `"WARNING"` / `"HARD_STOP"`
- `save_session_memory(...)` / `get_latest_session_memory(username)`
- `add_snippet(command, note, tags)` → id
- `list_snippets(tag="")` → list of dicts ordered by use_count DESC
- `delete_snippet(id)` → bool
- `increment_use(id)`

---

## 10. Sidebar — `shell/telemetry/watch.py`

A separate Python process running in tmux pane 1 (right side). Polls every 5 seconds (1 second after a key press). Renders all panels using Rich to a `StringIO` buffer, then writes `\033[2J\033[H` (clear) + frame to stdout.

### Panels (top to bottom)
```
✦ session   — model, uptime, CWD, today cost+calls
⬡ system    — CPU%, RAM MB/total (%), disk used/total (%), IP
⎇ git       — branch, staged/changed/new counts, ↑ahead ↓behind
⚙ processes — top 4 CPU procs with ████░░ bars
◈ tokens    — today cost/tokens/calls, 7-day table
◈ clipboard — scrollable snippet list with ▶ selection marker
? shortcuts — commands + keybindings reference
```

### Clipboard Scroll State
```python
_clip_selected: int  # index of highlighted snippet (module-level)
_CLIP_KEY_FILE: str  # path to key-press state file
```

### Key File Protocol
tmux key bindings in the sidebar pane write to `~/.local/share/agentic-shell/clip_key`:
- Up arrow → writes `UP`
- Down arrow → writes `DOWN`
- Enter → writes `ENTER`

Each render cycle, watch.py reads and deletes this file, then calls `_handle_clip_key()` which mutates `_clip_selected` or sends the selected snippet's command to pane 0 via `tmux send-keys`.

### tmux Key Bindings Setup (`_setup_tmux_clip_keys`)
Called once at sidebar startup. Binds Up/Down/Enter session-wide with `if-shell` condition:
- If active pane index == 1 (sidebar) → write to clip_key file
- Else → pass key through normally

---

## 11. Session Memory — `shell/memory/`

### Compressor (`compressor.py`)
Uses `token-reducer` library to compress conversation turns. Always preserves last 2 turns verbatim. Compressed text stored in SQLite `session_memory` table.

### Store (`store.py`)
- `load_session_context(username)` — reads latest compressed context from DB
- `save_session_context(session_id, compressed, turns, token_count)` — writes to DB

### Context Flow
```
Session ends (or every AI turn)
        ↓
compress(turns) → compressed string
        ↓
save_session_context() → SQLite

Next session startup (if AGENTIC_NEW_SESSION not set)
        ↓
load_session_context(username) → compressed string
        ↓
Shown as "session resumed (N chars)"
        ↓
Passed to LLM as prior context messages
```

---

## 12. Clipboard / Snippets — `shell/clipboard/manager.py`

### CLI Interface (from `shell/loop.py` builtin)
```
/clip                → open_picker(db)
/clip list           → _cmd_list(db)
/clip add "cmd"      → _cmd_add(args, db)  [prompts note+tags if not given]
/clip del <id>       → _cmd_del(args, db)
/clip run <id>       → _cmd_run(args, db)  [tmux send-keys to pane 0]
```

### TUI Picker (`open_picker`)
Full-screen `prompt_toolkit.Application` with:
- Live filter (`/` key) — filters by note, tags, or command text
- `↑↓` navigation
- `a` — inline add form (command → note → tags sequentially)
- `d` — delete selected (immediate, with message)
- `Enter` — send selected command to main tmux pane + increment use_count + exit
- `q` / `Escape` — quit without running

### Run Flow (from sidebar or TUI)
```
Snippet selected (Enter)
        ↓
tmux display-message -p "#S" → session name
        ↓
db.increment_use(snippet_id)
        ↓
tmux send-keys -t <session>:0.0 "<command>" Enter
        ↓
Command appears and runs in main pane
```

---

## 13. Configuration — `shell/config/`

### Schema (`schema.py`)
`ShellConfig` dataclass with: `backend`, `model`, `api_base`, `routing_mode`, `daily_token_budget`, `session_token_budget`, `privacy_mode`, `setup_complete`.

### Wizard (`wizard.py`)
First-run interactive setup. Prompts for backend, model, API key, routing mode. Saves to `~/.config/agentic-shell/config.json` with `chmod 600`.

### Settings Overlay (`tui/panel.py`)
`/config` opens a Rich-styled interactive panel using `prompt_toolkit` prompts. Each field shown with current value; blank input keeps current. Saves on exit.

---

## 14. tmux Layout — `shell/tui/layout.py`

### Session Structure
```
tmux session "agentic-NNNN"
├── Window 0
│   ├── Pane 0 (left, ~80% width)  — shell REPL
│   └── Pane 1 (right, 48 cols)    — sidebar watch process
```

### Sidebar Toggle (`toggle_sidebar`)
Uses libtmux to show/hide pane 1. State tracked in environment.

### Startup Script (install.sh)
```bash
tmux new-session -d -s $SESSION -x $COLS -y $ROWS
tmux split-window -h -t $SESSION:0.0 -l 48
tmux swap-pane ...            # sidebar goes to pane 1 (right)
tmux send-keys → sidebar      # starts watch.py loop
tmux send-keys → shell        # starts shell.main loop with exit-flag handling
tmux set-option mouse on
tmux set-option history-limit 50000
```

---

## 15. Data Flow Summary

```
SSH Login
    ↓
main.py: SSH bypass check → load config → load session context
    ↓
loop.py: PromptSession REPL starts
    ↓
User types input
    ↓
┌──────────────────────────────────────────────────────┐
│  Builtin? → handle and loop                          │
│  Bash route? → safety check → ptyprocess → audit log │
│  AI route? → LLM call → preview → safety → ptyprocess│
│            → telemetry DB → session memory           │
└──────────────────────────────────────────────────────┘
    ↓
Sidebar (separate process, every 5s)
    reads SQLite → renders Rich panels → writes to tmux pane 1
    reads clip_key file → updates scroll state
```

---

## 16. File & Directory Map

```
~/.config/agentic-shell/
  config.json              — user config (chmod 600)

~/.local/share/agentic-shell/
  sessions.db              — SQLite WAL database
  venv/                    — Python virtualenv
  history                  — prompt_toolkit readline history
  exit_requested           — flag file for /exit to drop to bash
  clip_key                 — sidebar key-press state file (UP/DOWN/ENTER)

/usr/local/bin/agentic-shell  — installed launcher script
/etc/shells                   — agentic-shell registered here
/var/log/agentic-shell/
  audit.log                — all commands: timestamp, user, action, exit code
```

---

## 17. Key Design Decisions

| Decision | Reason |
|----------|--------|
| All commands run in ptyprocess, not subprocess | Interactive programs (vim, htop, ssh) need a real TTY |
| `cd` intercepted via `os.chdir()` | Subprocess `cd` changes directory only in child process |
| SQLite WAL mode | Sidebar process reads while shell process writes — no lock contention |
| Sidebar is a separate process | Sidebar refresh must not block the shell REPL |
| No LiteLLM / LangChain | Direct httpx calls — no hidden abstractions, full control over headers and streaming |
| Static `SIDEBAR_WIDTH = 44` | Dynamic terminal size queries (`CPR`) freeze inside tmux |
| `PROMPT_TOOLKIT_NO_CPR=1` | Prevents prompt_toolkit from querying cursor position (freezes in tmux) |
| Exit flag file for `/exit` | Shell runs inside a `while true` restart loop; flag tells loop to exec bash instead of restart |
| `AGENTIC_NEW_SESSION=1` for `/new` | New sessions should not load previous session context |
| Clip key file for sidebar navigation | Sidebar is a separate process; IPC via file is simple and reliable without sockets |
