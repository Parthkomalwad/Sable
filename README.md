<div align="center">

# AgenticOS

**A Linux login shell that understands plain English, runs sandboxed agents, and learns your server.**

[![CI](https://github.com/Parthkomalwad/Agentic_OS/actions/workflows/ci.yml/badge.svg)](https://github.com/Parthkomalwad/Agentic_OS/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![Status: v0.3 alpha](https://img.shields.io/badge/status-v0.3%20alpha-orange)](ROADMAP.md)
[![Platform: Linux](https://img.shields.io/badge/platform-Linux-lightgrey)](#requirements)

</div>

```
~/api  main  14:02 ❯ deploy the api and run the smoke tests

  ✦ Using skill deploy-backend (confidence 0.85). Plan: build → migrate → restart → verify

  $ docker compose build api                              34s · exit 0
  ⚠ alembic upgrade head          touches DB · confirm tier
    ↵ run   e edit   q cancel  ›

  ◈ spawning agent: smoke-tests   →  tmux window task:smoke-tests
  ✓ done in 2m 11s · $0.004 · 1,930 tok
```

SSH into a box running AgenticOS and every line you type is either **bash** (executed directly) or **a goal** (handed to an orchestrator agent that plans, asks before anything risky, spawns sandboxed sub-agents for long work, and remembers the procedure as a reusable skill). A tmux sidebar shows agents, cost, git, system load, and a snippet clipboard live.

---

## Why

Terminal AI tools in 2026 are clients you run on your laptop. AgenticOS is the other half: the **server-side** runtime. It lives where the work happens, owns the safety layer, and accumulates knowledge about *that machine* so the tenth deploy takes one line and zero babysitting.

## Status

| Area | State |
|---|---|
| Login shell, bash/NL routing, three LLM backends (Ollama · OpenAI · Anthropic) | ✅ stable |
| Safety blocklist, secret redaction, budgets, audit log | ✅ stable |
| Orchestrator agent, sandboxed sub-agents (bwrap), task manager, tmux tasks bar | ✅ working, v0.3 |
| Self-learning skills (pattern → crystallise → confidence score) | ⚠️ working but shallow; deepened in v0.6 |
| Event bus, policy engine + hooks, Textual dashboard, daemon/NL cron, MCP client/server | 🚧 planned, see [ROADMAP.md](ROADMAP.md) |

This is alpha software that runs as your **login shell**. `/exit` and the SSH bypass are bulletproof; the rest is evolving. Read [SECURITY.md](SECURITY.md) before installing on anything you care about.

## Quick start

**On a Linux server (Ubuntu/Debian):**
```bash
git clone https://github.com/Parthkomalwad/Agentic_OS ~/Agentic_OS && cd ~/Agentic_OS
bash install.sh          # venv, deps, /etc/shells, chsh, audit log, tmux layout
# log out and back in; the first-run wizard picks a backend
```

**On Windows/macOS (Docker playground: full tmux, sandbox and local sshd):**
```powershell
.\scripts\playground.ps1 -Rebuild      # or scripts/playground.sh
```
or open the repo in VS Code → *Reopen in Container*.

**Try it without an API key:** set `AGENTIC_MOCK_LLM=1` (Phase 0 deliverable) or point at a local Ollama: `ollama pull llama3.1`.

Uninstall: `bash uninstall.sh` restores `/bin/bash`.

## What it does

<details>
<summary><b>Routing & agents</b></summary>

- Heuristic classifier decides bash vs natural language; `>>` forces AI, `Ctrl+B` forces bash, ambiguous lines ask.
- Orchestrator loop: one action per turn. `run` a command (shown first, `↵ / e / q`), `spawn` a sub-agent for long or parallel work, or `done`.
- Sub-agents run in their own tmux window and a `bwrap` sandbox (workspace R/W, everything else R/O); `/task list|attach|pause|resume|kill|inspect|checkpoint|revert`.
- Timed-out commands are auto-delegated to a background agent instead of blocking your prompt.
</details>

<details>
<summary><b>Safety</b></summary>

- 13 destructive patterns (`rm -rf`, `dd`, `mkfs`, `curl | bash`…) require typing `YES`; the model's own `safe:false` flag is honoured too.
- Privacy mode strips API keys and high-entropy strings before anything leaves the box.
- Daily/session token budgets: warn at 80 %, hard-stop at 100 %.
- Append-only audit log of every executed command; `SSH_ORIGINAL_COMMAND` (scp, rsync, git) never touches the agent path.
</details>

<details>
<summary><b>Self-learning skills</b></summary>

- At `/exit`, command history is clustered by repo + intent; a pattern seen 3× is crystallised by the LLM into `~/skills/<slug>.md`.
- Skills carry a confidence score (+0.05 on success, −0.10 on failure) and are injected into future agent contexts. `/skill list|show`.
</details>

<details>
<summary><b>Shell experience & sidebar</b></summary>

- Rich `ls`/`cat`, powerline prompt, tab completion, `Ctrl+R` history, inline autosuggest, `cd -`.
- Sidebar panels: session/cost · system · git · processes · 7-day tokens · clipboard · shortcuts. `Ctrl+T` toggles.
- `/clip` snippet manager with a full-screen picker; `/config` live settings; `/memory`, `/stats`, `/new`.
</details>

## Architecture

```
SSH ─► main.py (bypass) ─► REPL ─► router ─┬─ bash ──► safety ──► pty ──► audit
                                           └─ goal ──► orchestrator ─┬─ run   ──► confirm ──► pty
                                                                     ├─ spawn ──► TaskAgent (tmux window, bwrap)
                                                                     └─ done  ──► skills.crystallise?
        sidebar (separate process) ◄── SQLite WAL ◄── token_events · tasks · task_events · skill_patterns
```

Full walkthrough with diagrams: [docs/architecture.md](docs/architecture.md) · task engine & skills: [docs/specs/prd-v3.md](docs/specs/prd-v3.md) · target v4 layout: [docs/structure.md](docs/structure.md)

```
shell/
  main.py  loop.py  router.py  executor.py  safety.py  planner.py
  llm/        base · ollama · openai · anthropic · pricing.json
  tasks/      orchestrator · agent · manager · sandbox · memory · reconcile · panel
  skills/     pattern_watcher · crystalliser · index
  telemetry/  db (SQLite WAL) · events · watch (sidebar)
  memory/  clipboard/  config/  tui/
tests/        unit (no I/O, <10 s) · integration (Docker) · fixtures/mock_llm.py
```

## Roadmap

v4 turns the shell into a full agent runtime. Headline milestones below; details, gates and dates are in [ROADMAP.md](ROADMAP.md):

- [ ] **Unified agent runtime + event bus:** one `Agent` with roles, push updates instead of file polling
- [ ] **Skills that actually learn:** confidence-ranked retrieval, post-task crystallisation, folder skills (agentskills.io)
- [ ] **Policy engine + hooks + threat model:** allow/confirm/deny as data, output-injection defence, cost circuit breaker
- [ ] **Command center:** Warp-style blocks, Textual dashboard with agent lanes and an approval inbox
- [ ] **Daemon + NL cron + notifications:** unattended work with phone approvals
- [ ] **MCP client & server:** use the ecosystem; let Claude Code / Warp drive your server *through* the safety layer
- [ ] **Server knowledge base:** FTS5 memory of what the agent learned about this machine

## Requirements

Linux (Ubuntu 22.04+ tested), Python 3.11+, tmux, optionally `bubblewrap` for kernel-level sandboxing. Dependencies are deliberately few (see [pyproject.toml](pyproject.toml)); no LLM gateways.

## Configuration

`~/.config/agentic-shell/config.json` (mode 600), editable live with `/config`. Keys: `backend`, `model`, `api_base`, `routing_mode` (`auto`|`prefix`), `daily_token_budget`, `session_token_budget`, `privacy_mode`, `tasks_base_dir`. API keys via `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` or the Linux keyring.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/branching.md](docs/branching.md). Implementation rules live in [CLAUDE.md](CLAUDE.md) they apply to humans too. Good first issues are labelled `good first issue`; each roadmap feature has an ID (`A1`, `B2` …) you can reference.

## Documentation index

| Doc | What |
|---|---|
| [ROADMAP.md](ROADMAP.md) | Public milestones |
| [docs/roadmap-phases.md](docs/roadmap-phases.md) | Phase-by-phase plan with test gates and playground setup |
| [docs/vision.md](docs/vision.md) | Verified current state, research, 60-feature catalog |
| [docs/structure.md](docs/structure.md) | Target package layout, config model, visibility principles |
| [docs/architecture.md](docs/architecture.md) | Current architecture with diagrams |
| [docs/specs/prd-v1.md](docs/specs/prd-v1.md) · [docs/specs/prd-v3.md](docs/specs/prd-v3.md) | Original and v3 product specs |
| [docs/specs/](docs/specs/) · [docs/plans/](docs/plans/) | Design docs and implementation plans per feature |
| [CHANGELOG.md](CHANGELOG.md) · [SECURITY.md](SECURITY.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Project hygiene |

## License

[MIT](LICENSE) © 2026 Parth Komalwad
