<div align="center">

<img src="docs/assets/sable-mark.svg" alt="sable" width="360">

<br>

### A Linux login shell that understands plain English, runs sandboxed agents, and learns your server.

[![CI](https://github.com/Parthkomalwad/sable/actions/workflows/ci.yml/badge.svg)](https://github.com/Parthkomalwad/sable/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-8B7CF6.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Platform: Linux](https://img.shields.io/badge/platform-Linux-FCC624?logo=linux&logoColor=black)](#requirements)
[![Status](https://img.shields.io/badge/status-v0.3%20alpha-E7B24B)](ROADMAP.md)
[![Backends](https://img.shields.io/badge/LLM-Ollama%20%C2%B7%20OpenAI%20%C2%B7%20Anthropic-8B7CF6)](#configuration)

[Quick start](#quick-start) ·
[See it work](#see-it-work) ·
[Why Sable](#why-sable) ·
[Architecture](#architecture) ·
[Roadmap](#roadmap) ·
[Docs](docs/README.md)

<br>

<img src="docs/assets/sable-hero.svg" alt="Sable session: a goal typed in English, a skill picked, commands previewed, a sub-agent spawned, cost shown in the sidebar" width="880">

</div>

<br>

SSH into a box running Sable and every line you type is either **bash** (runs as-is) or **a goal**. A goal goes to an orchestrator agent that plans it, shows you every command before running it, hands long work to sandboxed sub-agents in their own tmux windows, and keeps the procedure as a skill it will reuse next time. A sidebar shows agents, cost, git and system load, live.

Sable is the shell that asks first. Nothing runs that you did not see; anything destructive needs the word `YES`; `scp`, `rsync` and `git push` never touch the agent path.

> **Alpha.** Sable runs as your *login shell*. The SSH bypass and `/exit` are bulletproof; the rest is evolving. Read [SECURITY.md](SECURITY.md) before installing on a machine you care about.

<br>

## Quick start

<table>
<tr>
<td width="50%" valign="top">

**Linux server** (Ubuntu / Debian)

```bash
git clone https://github.com/Parthkomalwad/sable ~/sable
cd ~/sable && bash install.sh
# log out, log in. The wizard picks a backend.
```

`install.sh` creates a venv, registers `sable` in `/etc/shells`, runs `chsh`, creates the audit log and launches the tmux layout. `bash uninstall.sh` puts `/bin/bash` back.

</td>
<td width="50%" valign="top">

**Windows / macOS** (Docker playground)

```powershell
.\scripts\playground.ps1 -Rebuild   # or scripts/playground.sh
```

Full Ubuntu with tmux, `bubblewrap` sandbox and a local `sshd`, source bind-mounted from this repo. Or open the folder in VS Code and choose **Reopen in Container**.

</td>
</tr>
</table>

**No API key?** Run a local model: `ollama pull llama3.1` (the default backend), or set `SABLE_MOCK_LLM=1` for the canned demo (v0.4).

<br>

## See it work

<details open>
<summary><b>1 · Type bash, get bash. Type English, get a plan.</b></summary>

```text
~/api ❯ git status                         # scored as bash → runs instantly
~/api ❯ show me the biggest files here     # scored as language → agent
  ✦ Listing the 10 largest files in this directory by size
  $ du -ah . | sort -rh | head -n 10
    ↵ run   e edit   q cancel  ›
```

Ambiguous lines ask `[b]ash or [a]gentic?`. Force either way: `>> text` sends to the agent, `Ctrl+B` sends the next line straight to bash.
</details>

<details>
<summary><b>2 · Nothing runs without you seeing it. Dangerous things need a YES.</b></summary>

```text
~/api ❯ clean up old docker images and free some disk
  ✦ Removing dangling images and stopped containers
  $ docker system prune -af
    ↵ run   e edit   q cancel  ›  e
    edit> docker system prune -f
  ✓ 0 · 1.8s

~/api ❯ wipe the tmp dir
  ⚠ DESTRUCTIVE  rm -rf /tmp/*
  Type YES to run, anything else to cancel › YES
```

Eleven destructive patterns (`rm -rf`, `dd`, `mkfs`, `curl | bash`, …) always require the literal word `YES`. The model's own `safe: false` flag is honoured too. Every executed command lands in an append-only audit log.
</details>

<details>
<summary><b>3 · Long work goes to a sub-agent. You keep your prompt.</b></summary>

```text
~/proj ❯ set up a python 3.12 venv, install the requirements and run the test suite
  $ python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
    … [timeout after 120s]
  [orchestrator] command timed out, delegating remaining goal to sub-agent
  ◈ spawning agent: set-up-python-venv-20260908 → tmux window task:set-up-python-venv-20260908

~/proj ❯ /task list
  NAME                          STATUS    STEPS  CREATED
  set-up-python-venv-20260908   running   4      2 min ago

~/proj ❯ /task set-up-python-venv-20260908 attach      # jump into its window
~/proj ❯ /task set-up-python-venv-20260908 pause       # SIGTSTP, resume later
```

Sub-agents run in their own tmux window inside a `bwrap` sandbox: their workspace is read-write, everything else read-only. `/task list · attach · pause · resume · kill · inspect · stats · checkpoint · revert`. You can steer a running agent by typing into its window.
</details>

<details>
<summary><b>4 · Do something three times and it becomes a skill.</b></summary>

```text
~/api ❯ /exit
  ✦ 1 new pattern detected in ~/api (build → migrate → restart, seen 3×)
  ✦ crystallised skill: deploy-api  → ~/skills/instructions/deploy-api.md

# next session
~/api ❯ deploy the api
  ✦ Using skill deploy-api (confidence 0.55)   ← ranked in, fewer turns
```

Skills are plain markdown you can read and edit. Confidence moves +0.05 on success and −0.10 on failure. `/skill list · show`.
</details>

<details>
<summary><b>5 · The sidebar and the clipboard.</b></summary>

`Ctrl+T` toggles a live sidebar: session and cost, system, git, top processes, 7-day token history, saved snippets, shortcuts. Save any command with `/clip add "docker ps -a" --note containers --tags docker`, then run it from the sidebar with arrow keys and Enter, or from the full-screen picker (`/clip`, live filter with `/`).
</details>

<details>
<summary><b>Built-in commands and keys</b></summary>

| Command | | Key | |
|---|---|---|---|
| `/help` | all builtins | `Ctrl+B` | next line is raw bash |
| `/task …` | manage sub-agents | `Ctrl+T` | toggle sidebar |
| `/skill …` | list / show skills | `Ctrl+X` | settings overlay |
| `/clip …` | snippets | `Ctrl+R` | reverse history search |
| `/stats` | 7-day token table | `Tab` | complete command or path |
| `/memory` | view / clear session context | `→` | accept inline suggestion |
| `/config` `/model` `/mode` | settings, backend, routing | `>>` | force agent mode |
| `/budget reset` `/new` `/exit` | | | |
</details>

<br>

## Why Sable

Most 2026 terminal AI tools are **clients you run on your laptop**. Sable is the **server side**: it lives where the work happens, owns the safety layer, and accumulates knowledge about *that machine*, so the tenth deploy takes one line and zero babysitting.

| | Sable | Warp / Claude Code / Codex CLI | Aider / Goose |
|---|:-:|:-:|:-:|
| Runs as the login shell on the server | ✅ | ❌ client | ❌ client |
| `scp` / `rsync` / `git push` unaffected (SSH bypass) | ✅ | n/a | n/a |
| Every command previewed, destructive ones gated | ✅ | partial | partial |
| Sub-agents in kernel sandboxes (`bwrap`) | ✅ | cloud or none | none |
| Learns reusable skills from *your* command history | ✅ | manual skills | ❌ |
| Live cost / agent / system sidebar in tmux | ✅ | GUI | ❌ |
| Works fully offline with Ollama | ✅ | ❌ | ✅ |
| Zero LLM gateways, direct `httpx` | ✅ | n/a | n/a |

<br>

## Architecture

```mermaid
flowchart LR
    SSH([SSH login]) --> M[main.py<br/>SSH bypass first]
    M -->|SSH_ORIGINAL_COMMAND| B0["/bin/bash"]
    M --> R[REPL<br/>prompt_toolkit]
    R --> RT{router}
    RT -->|bash| S[safety<br/>blocklist · YES]
    RT -->|goal| O[orchestrator<br/>run · spawn · done]
    O --> S
    S --> P[pty<br/>ptyprocess]
    O -->|spawn| T[TaskAgent<br/>tmux window · bwrap]
    O -->|done| K[skills<br/>watch · crystallise · score]
    P --> A[(audit.log)]
    P --> DB[(SQLite WAL)]
    T --> DB
    K --> DB
    DB --> SB[sidebar<br/>separate process]
    O <-->|httpx / SSE| LLM[[Ollama · OpenAI · Anthropic]]
```

| Layer | Modules | Notes |
|---|---|---|
| Entry & loop | `main.py` `loop.py` `router.py` | SSH bypass is the first executable line; REPL, builtins, routing |
| Execution | `executor.py` `safety.py` `planner.py` | everything through a pty; `cd` intercepted in-process; 11-pattern blocklist |
| Agents | `tasks/orchestrator.py` `agent.py` `manager.py` `sandbox.py` | multi-turn loop, sub-agents, bwrap with bash-wrapper fallback |
| Skills | `skills/pattern_watcher.py` `crystalliser.py` `index.py` | audit-log clustering, LLM-written skills, confidence index |
| Models | `llm/base.py` `ollama.py` `openai.py` `anthropic.py` | one `LLMBackend` ABC, streaming, JSON fallback chain |
| State & UI | `telemetry/` `memory/` `clipboard/` `tui/` | SQLite WAL, token-reducer compression, sidebar, tmux layout |

Deeper: [docs/architecture-v4.md](docs/architecture-v4.md) (request lifecycle, agent turn, spawn, skills, memory, daemon, processes) · [docs/architecture.md](docs/architecture.md) (v0.3 diagrams) · [docs/specs/prd-v3.md](docs/specs/prd-v3.md) (task engine & skills) · [docs/structure.md](docs/structure.md) (where v4 is going).

<br>

## Roadmap

v4 turns Sable into a full agent runtime. Milestones (full plan with test gates in [ROADMAP.md](ROADMAP.md)):

| | Milestone | What you will notice |
|---|---|---|
| ✅ | v0.1 – v0.3 | shell, safety, three backends, sub-agents, first skills |
| 🔧 | **v0.4 Foundations** | CI, playground, router accuracy, `/tour`, `/bash` to drop to plain Linux and back, clean package layout |
| ⏳ | v0.5 Agent runtime | one `Agent` with roles, event bus, steer with `Ctrl+G`, `/task replay`, repo-aware context |
| ⏳ | v0.6 Skills that learn | confidence-ranked retrieval, skills right after a task, folder skills, learn from your edits |
| ⏳ | v0.7 Policy, trust and tools | `policy.yaml` tiers, hooks, output-injection defence, web search, structured file edits, verify-after-act |
| ⏳ | v0.8 Command center | Warp-style blocks, Textual dashboard, approval inbox, ghost-text, explain-last-error |
| ⏳ | v0.9 Autonomy | `sabled` daemon, natural-language cron, approve from your phone |
| ⏳ | v1.0 Ecosystem | MCP client and server, Memory Palace, rehearsal mode, evals, plugins |

<br>

## Configuration

`~/.config/agentic-shell/config.json` (mode 600, moves to `~/.sable/` in v0.4), editable live with `/config` or `Ctrl+X`.

| Key | Default | Meaning |
|---|---|---|
| `backend` | `ollama` | `ollama` · `openai` · `anthropic` |
| `model` | `llama3.1` | model name for that backend |
| `api_base` | `http://localhost:11434` | Ollama URL (ignored for cloud backends) |
| `routing_mode` | `auto` | `auto` heuristic or `prefix` (`>>` only) |
| `daily_token_budget` / `session_token_budget` | `null` | warn at 80 %, hard stop at 100 % |
| `privacy_mode` | `false` | redact keys and high-entropy strings before sending |
| `tasks_base_dir` | `~/tasks` | sub-agent workspaces |

API keys: `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` env vars, or the Linux keyring.

## Requirements

Linux (Ubuntu 22.04+ tested), Python 3.11+, tmux. Optional: `bubblewrap` for kernel-level sandboxing (falls back to a bash wrapper). Ten dependencies, no LLM gateways: see [pyproject.toml](pyproject.toml).

## Contributing

Issues and PRs welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/branching.md](docs/branching.md); implementation rules are in [CLAUDE.md](CLAUDE.md) and apply to humans and AI sessions alike. Each roadmap feature has an ID (`A1`, `B2` …) you can reference in branches and PRs.

## Documentation

| | |
|---|---|
| [docs/README.md](docs/README.md) | index of everything below |
| [ROADMAP.md](ROADMAP.md) · [docs/roadmap-phases.md](docs/roadmap-phases.md) | milestones, phase gates, playground setup |
| [docs/vision.md](docs/vision.md) | verified current state, research, feature catalog (pillars A to K) |
| [docs/structure.md](docs/structure.md) | target layout, config model, visibility principles |
| [docs/architecture-v4.md](docs/architecture-v4.md) · [docs/architecture.md](docs/architecture.md) · [docs/specs/](docs/specs/) · [docs/plans/](docs/plans/) | architecture, design docs, build plans |
| [CHANGELOG.md](CHANGELOG.md) · [SECURITY.md](SECURITY.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | project hygiene |

<br>

<div align="center">

<sub>sable · the shell that asks first</sub>

[MIT](LICENSE) © 2026 Parth Komalwad

</div>
