# Agentic Shell

A Python login shell that replaces `/bin/bash` on Linux. Every command you type is routed either to bash or to a language model. The LLM generates a shell command, shows it to you for review, and executes it. A tmux sidebar shows live system stats, token cost, git status, and a scrollable snippet clipboard in real time.

---

## Features

### AI & Routing
- **Smart routing** — heuristic auto-detects natural language vs shell commands; prefix `>>` to force AI mode
- **Three LLM backends** — Ollama (local, free), OpenAI, Anthropic
- **Command preview** — see the AI-generated command before it runs; edit or cancel
- **Multi-step plans** — LLM can return a plan array; each step shown upfront with `[c]ontinue/[r]etry/[a]bort` on failure
- **Animated spinner** — visual feedback while the LLM is thinking
- **Inline cost display** — every AI call shows `✓ done in 1.2s · $0.0003 · 241 tok`

### Safety & Privacy
- **Safety blocklist** — 13 destructive patterns (rm -rf, dd, mkfs, curl|bash…) require explicit `YES` confirmation
- **AI danger detection** — model flags unsafe commands even if they don't match static patterns
- **Secret redaction** — privacy mode strips API keys, tokens, and high-entropy strings before sending to the model
- **Budget tracking** — daily and session token budgets; warn at 80%, hard-stop at 100%
- **Audit log** — every command appended to `/var/log/agentic-shell/audit.log`
- **SSH bypass** — `SSH_ORIGINAL_COMMAND` always executed via `/bin/bash` (scp, rsync, git push all work)

### Shell Experience
- **Rich ls** — directory listing with color-coded dirs/executables/symlinks, short and long (`-l`) modes
- **Syntax-highlighted cat** — `cat`, `head`, `tail` render files with monokai syntax highlighting and line numbers
- **Tab completion** — first word completes binaries from PATH; subsequent words complete filesystem paths
- **History search** — `Ctrl+R` reverse search via prompt_toolkit emacs bindings
- **Auto-suggest** — grey inline suggestion from history as you type, accepted with `→`
- **Powerline prompt** — path / git branch / time segments with ANSI 256-color
- **cd -** — returns to previous directory
- **Session memory** — context compressed with token-reducer between sessions; resumes on login
- **New sessions** — `/new` spawns a fresh tmux session without carrying over context

### Sidebar (tmux right pane)
- **✦ session** — model, uptime, current directory, today's cost + call count
- **⬡ system** — CPU %, RAM, disk usage, IP address
- **⎇ git** — branch, staged/changed/new file counts, ahead/behind remote
- **⚙ processes** — top 4 CPU consumers with visual `████░░` bars
- **◈ tokens** — today's cost/tokens/calls, 7-day history table
- **◈ clipboard** — scrollable saved snippets; Up/Down to navigate, Enter to run in main pane
- **? shortcuts** — quick reference for all commands and keybindings

### Clipboard / Snippets
- **Save commands** — `/clip add "docker ps -a" --note "list containers" --tags docker`
- **Browse & run** — `/clip` opens a full-screen TUI picker with live filter, add, delete, run
- **Sidebar integration** — snippets visible in sidebar; arrow keys scroll, Enter sends to main pane
- **Tag filtering** — filter snippets by tag in TUI with `/`
- **Use tracking** — most-used snippets float to top automatically

---

## Quick Start

```bash
# Clone and install
git clone https://github.com/Parthkomalwad/Agentic_OS ~/Agentic_OS
cd ~/Agentic_OS
bash install.sh
```

`install.sh` will:
1. Create a virtualenv at `~/.local/share/agentic-shell/venv`
2. Install all dependencies
3. Pre-cache tiktoken encodings
4. Write `/usr/local/bin/agentic-shell`
5. Register it in `/etc/shells`
6. Run `chsh` to set it as your login shell
7. Create `/var/log/agentic-shell/audit.log`
8. Launch a tmux session with the shell (left) and sidebar (right)

Log out and back in (or SSH again) — the first launch runs the setup wizard.

### Uninstall

```bash
bash uninstall.sh          # restores login shell to /bin/bash
bash uninstall.sh /bin/zsh # or another shell
```

---

## Configuration

Config stored at `~/.config/agentic-shell/config.json` (permissions: 600).

Edit inline with `/config` (or `Ctrl+X`) from the shell prompt.

| Field | Default | Description |
|-------|---------|-------------|
| `backend` | `ollama` | `ollama` / `openai` / `anthropic` |
| `model` | `llama3.1` | Model name for the selected backend |
| `api_base` | `http://localhost:11434` | Ollama server URL (ignored for cloud backends) |
| `routing_mode` | `auto` | `auto` (heuristic) or `prefix` (`>>` to invoke AI) |
| `daily_token_budget` | `null` | Stop LLM calls after N tokens/day |
| `session_token_budget` | `null` | Stop after N tokens this session |
| `privacy_mode` | `false` | Redact secrets before sending to model |

**API keys:** Set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` env vars. Ollama needs no key.

---

## Usage

```
~/Agentic_OS main 04:01 ❯ ls                    # runs as bash (Rich output)
~/Agentic_OS main 04:01 ❯ show disk usage        # routed to AI
~/Agentic_OS main 04:01 ❯ >> compress all pngs   # >> forces AI mode
```

### Built-in Commands

| Command | Description |
|---------|-------------|
| `/help` | All built-in commands |
| `/history` | Recent commands with AI costs |
| `/clip` | Open snippet clipboard TUI |
| `/clip add "cmd" --note "x" --tags a,b` | Save a snippet |
| `/clip list` | List all snippets |
| `/clip run <id>` | Run snippet by id |
| `/clip del <id>` | Delete snippet by id |
| `/stats` | Token usage for last 7 days |
| `/memory` | View/clear session context |
| `/model` | Show current backend + model |
| `/mode` | Toggle auto ↔ prefix routing |
| `/config` | Open settings overlay |
| `/new` | Start a fresh tmux session |
| `/clear` | Clear the terminal |
| `/exit` | Exit to bash |
| `/budget reset` | Clear hard-stop budget flag |

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+R` | Reverse search command history |
| `Ctrl+B` | Next command runs as raw bash (one-shot) |
| `Ctrl+T` | Toggle telemetry sidebar |
| `Ctrl+X` | Open settings panel |
| `Tab` | Complete command name or file path |
| `→` | Accept inline history suggestion |
| `>>` | Force AI prefix on any input |

---

## Architecture

```
shell/
  main.py           SSH bypass, startup, config load, session resume
  loop.py           prompt_toolkit REPL, routing, builtins, audit log
  router.py         NL vs bash heuristic classifier
  executor.py       ptyprocess execution, cd interception, Rich ls/cat
  safety.py         destructive blocklist, entropy check, secret redaction
  planner.py        multi-step plan execution with retry/abort UI
  llm/
    base.py         LLMBackend ABC, LLMResponse, system prompt, JSON fallback
    ollama.py       Ollama NDJSON streaming
    openai.py       OpenAI SSE streaming
    anthropic.py    Anthropic SSE streaming
  config/
    schema.py       ShellConfig dataclass + validation
    wizard.py       First-run interactive setup
    keyring.py      secretstorage keyring integration
  telemetry/
    db.py           SQLite WAL, token_events + session_memory + snippets tables
    events.py       TokenEvent dataclass
    watch.py        Sidebar process — panels, scroll state, tmux key bindings
  memory/
    compressor.py   token-reducer compression
    store.py        session context load/save
  clipboard/
    __init__.py
    manager.py      TUI picker (prompt_toolkit), CLI add/del/list/run
  tui/
    layout.py       libtmux split, sidebar toggle
    panel.py        settings overlay
```

---

## Security Notes

- The SSH bypass (`SSH_ORIGINAL_COMMAND`) is the first executable line of `main.py` — non-negotiable. Without it, `scp`, `rsync`, and `git push` over SSH hang.
- The system prompt marks file contents as **UNTRUSTED DATA** to mitigate prompt injection.
- Destructive commands always require typing `YES` literally.
- Audit log records every command with user, timestamp, and exit code.
- Config file permissions are always set to `600`.

---

## Dependencies

```
prompt_toolkit      REPL, key bindings, tab completion, TUI picker
pygments            Syntax highlighting
ptyprocess          PTY execution (vim, htop, ssh all work correctly)
httpx + httpx-sse   Async HTTP + SSE streaming for LLM backends
rich                Console panels, tables, syntax highlight, ls/cat rendering
tiktoken==0.9.0     Token counting
libtmux>=0.55,<0.56 tmux session management
token-reducer       Context compression between sessions
secretstorage       Linux keyring (Phase 2+)
```
