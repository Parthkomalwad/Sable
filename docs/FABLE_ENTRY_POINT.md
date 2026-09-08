# Agentic OS — Entry Point Brief

> Read this first. It's the "what is this repo, where is it now, where do I want to take it" document — written so a fresh model session (or a fresh person) can get oriented in one pass and start proposing enhancements without re-deriving context from scratch.

---

## 1. What this project is, in one paragraph

Agentic OS is a Python **login shell** that replaces `/bin/bash` on a Linux server. When you SSH in, you land in this shell instead of bash. Every line you type is routed either to raw bash or to an LLM, which turns natural language into a shell command, shows it to you for approval, and executes it. A tmux sidebar shows live token cost, system stats, git status, and a snippet clipboard. Underneath that, it's grown a second layer: a **multi-turn agent orchestrator** that can spawn sandboxed sub-agents to run background tasks, and a **self-learning skills system** that watches your command history, detects repeated patterns, and auto-writes reusable "skill" files from them. The long-term ambition (not yet fully realized) is for the shell itself to behave like a small agentic operating system — not just "AI autocomplete for bash."

---

## 2. Current state of the repo (as of this writing)

**Phases 0–3 of the original PRD are complete.** That covers: SSH bypass, ptyprocess-based execution, the REPL, NL/bash routing, all three LLM backends (Ollama/OpenAI/Anthropic), the safety blocklist + entropy/secret redaction, multi-step plans, SQLite telemetry (WAL mode), the tmux sidebar, session memory compression via `token-reducer`, the config wizard + keyring, and `install.sh`/`uninstall.sh`.

**Beyond the original PRD**, two subsystems have been added that aren't yet reflected in `README.md` or the architecture diagrams:

- **`shell/tasks/`** — a task orchestration layer:
  - `orchestrator.py` — replaces the single LLM call in the main loop with a multi-turn reasoning loop (`run | spawn | done` actions per turn).
  - `agent.py` — a standalone `TaskAgent` that runs autonomously toward a goal in the background (`python3 -m shell.tasks.agent --task <name> --goal "<text>"`), with its own turn loop, guidance queue (you can steer it mid-run via stdin), and event logging.
  - `sandbox.py` — isolates what a spawned sub-agent can touch: `bwrap` namespace isolation when available, falling back to a bash-wrapper that blocklists write-capable commands outside the task's workspace.
  - `manager.py`, `panel.py`, `reconcile.py`, `memory.py`, `skills.py` — task bookkeeping, a status panel, state reconciliation, per-task memory, and skill lookup for tasks.
- **`shell/skills/`** — a self-learning skill system:
  - `pattern_watcher.py` — runs at `/exit`, scans the audit log, groups commands by repo + intent keywords, and tracks a `skill_patterns` table. A pattern that recurs 3+ times becomes a crystallisation candidate.
  - `crystalliser.py` — takes a candidate pattern's raw command history, sends it to the LLM with a skill-writing prompt, and writes a new markdown skill file (`skills/instructions/<slug>.md`), then updates the index.
  - `index.py` — `skills_index.json`: tracks each skill's keywords, whether it was auto-generated, a **confidence score** that nudges up on successful reuse (+0.05, capped at 1.0) and down on failure (−0.1, floored at 0.0).

This is effectively an early version of exactly what you're asking for: the shell already notices repeated behavior and turns it into reusable capability. It's just not documented, not surfaced in the UI/sidebar, and probably not fully wired into the main loop yet — worth verifying before building on top of it.

**Docs currently in the repo:**
- `PRD.md` — original product spec, Phases 0–4, feature-by-feature (does *not* cover `tasks/` or `skills/`).
- `README.md` — user-facing docs, matches Phases 0–3 only.
- `docs/ARCHITECTURE_v2.md` — three-diagram architecture walkthrough (core shell flow / intelligence+telemetry / sidebar+tmux+clipboard), also predates `tasks/` and `skills/`.
- `CLAUDE.md` — instructions for Claude Code sessions working in this repo: build order, non-negotiable implementation rules (SSH bypass must be first line, `cd` must use `os.chdir()`, ptyprocess for *all* commands, Rich-only output, WAL-mode SQLite, approved dependency list, etc).
- `task.md` — the Phase 0–3 checklist, all checked off.

---

## 3. Non-negotiable constraints (carry these into any redesign)

These come from `CLAUDE.md` and are enforced project-wide — any enhancement plan needs to respect them or explicitly propose changing them:

- SSH bypass (`SSH_ORIGINAL_COMMAND` → exec straight to `/bin/bash`) must remain the first executable line of `main.py`. Without it, `scp`/`rsync`/`git push` over SSH hang.
- `cd` is never run as a subprocess — always `os.chdir()` in-process.
- Every command runs through `PtyProcessUnicode`, no exceptions — no "list of commands that need a TTY."
- All user-facing output goes through Rich's `Console`, never bare `print()`.
- LLM JSON responses follow one fixed contract — `{command, explanation, safe, plan}` — with a strict fallback chain (strip fences → parse → re-ask model → show raw + ask user to rephrase). Never let a parse failure raise unhandled.
- httpx timeout is always explicit (`30.0`s) — default httpx timeout is 5s and will break LLM calls.
- Anthropic backend must send `anthropic-version: 2023-06-01`.
- SQLite always opened in WAL mode (`journal_mode=WAL`, `synchronous=NORMAL`) — the sidebar reads while the shell writes, concurrently.
- Approved dependency list is closed: `prompt_toolkit, pygments, ptyprocess, httpx, httpx-sse, rich, tiktoken==0.9.0, libtmux>=0.55<0.56, token-reducer, secretstorage`. No LiteLLM/LangChain/any LLM gateway — direct `httpx` calls only. Adding a new dependency needs to be flagged, not silently done.
- Bare `except Exception` is disallowed — catch specific exception types.

---

## 4. What you said you want to explore

You want to take this beyond "Phase 3 shell" into a genuinely agentic OS layer — things like self-learning agents, skills, and whatever else fits that direction. Given what's already in the repo, some directions that build naturally on existing (if under-documented) infrastructure rather than starting fresh:

- **Surface and harden the existing skills system.** `pattern_watcher.py` + `crystalliser.py` + `index.py` already form a learn-from-usage loop; it may not be wired into the main REPL/sidebar yet, and has no test coverage. Making it visible (`/skills` command? sidebar panel?) and reliable is lower-risk than inventing a new mechanism.
- **Deepen the task orchestrator into real multi-agent workflows.** `orchestrator.py`'s `run | spawn | done` loop plus `sandbox.py`'s isolation is most of the plumbing for parallel or delegated sub-agents; the gap is likely in coordination, result reconciliation (`reconcile.py` exists — check what it currently does), and UI visibility into running tasks.
- **Confidence-scored skill reuse feeding back into routing.** `router.py`'s bash/NL classifier could consult the skills index/confidence scores as another signal, closing the loop between "learned skill" and "used automatically."
- **Session-to-session memory beyond compression.** `memory/compressor.py` and `store.py` currently compress/carry context within a session; a longer-horizon memory (facts about the user's environment, past decisions, recurring project context) is a natural extension of the same subsystem.
- **Documentation debt as a first task.** Before design work starts, `README.md`, `PRD.md`, and `docs/ARCHITECTURE_v2.md` should be updated to reflect `tasks/` and `skills/` — right now anyone (human or model) reading only the README would miss half the system.

---

## 5. Suggested first move in Fable

Don't jump straight to designing new features. Start by asking Fable to:
1. Read this file plus `PRD.md`, `README.md`, `docs/ARCHITECTURE_v2.md`, and `CLAUDE.md`.
2. Read `shell/tasks/orchestrator.py`, `shell/tasks/agent.py`, `shell/tasks/reconcile.py`, and all of `shell/skills/` in full (they're small — under 1,000 lines combined) to get precise, not summarized, understanding of what already exists.
3. Produce a gap analysis: what's implemented vs. documented vs. tested vs. actually wired into the live REPL loop.
4. Only then propose an enhancement roadmap (self-learning agents, skills, whatever direction), scoped against the constraints in Section 3.

This avoids the most likely failure mode: designing a "new" self-learning skill system that duplicates `shell/skills/` because nobody read it first.
