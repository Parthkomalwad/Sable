# Sable v4 Vision & Feature Brief

> **Audience:** an Opus model session tasked with turning this into concrete feature specs, design docs, and implementation plans.
> **Purpose:** define where Sable goes next from "AI-routed login shell with a task engine" to a genuinely autonomous, self-improving, richly-visualized agentic operating layer for a Linux server.
> **How to use this doc:** Sections 1–3 are ground truth about the repo (verified against source, September 2026). Section 4 is external research. Sections 5–7 are the feature catalog, priorities, and instructions for you. Don't re-invent things Section 2 says already exist extend them.

---

## 1. What Sable is

A Python login shell that replaces `/bin/bash` on a Linux server. SSH in and you land in it. Every input line is classified as **bash** (run directly), **natural language** (handed to an LLM orchestrator), or **ambiguous** (ask). It runs in a tmux layout with a live sidebar (cost, system, git, processes, clipboard) and a tasks bar. Underneath: a multi-turn **orchestrator agent** that can spawn **sandboxed sub-agents** in their own tmux windows, and a **self-learning skills** loop that watches your command history, detects repeated patterns, and crystallises them into reusable skill files with confidence scores.

The ambition for v4: **the shell is the OS-level agent runtime.** Not "AI autocomplete for bash" a persistent, always-on layer that learns your server, runs work on your behalf (interactively and unattended), exposes and consumes MCP, and shows you everything it's doing in a UI you actually want to look at.

---

## 2. What already exists (verified against source)

### 2.1 Core shell (PRD v1, phases 0–3, complete)
- `shell/main.py` SSH_ORIGINAL_COMMAND bypass (first line), config load, session resume, `reconcile()` of lost tasks.
- `shell/loop.py` prompt_toolkit REPL, powerline prompt, tab completion, history, autosuggest, builtins (`/help /history /clip /stats /memory /model /mode /config /new /clear /exit /budget /task /skill`), audit log, budget enforcement, offline fallback, Ctrl+B bypass, Ctrl+T sidebar toggle, Ctrl+X settings.
- `shell/router.py` heuristic BASH / AGENTIC / AMBIGUOUS classifier; `>>` prefix mode.
- `shell/executor.py` everything via `PtyProcessUnicode`; `cd` intercepted with `os.chdir()`; Rich-rendered `ls`/`cat`/`head`/`tail`.
- `shell/safety.py` 11-pattern destructive blocklist (`DESTRUCTIVE_PATTERNS`, safety.py:18-35), Shannon-entropy secret detection, literal `YES` confirm, `strip_secrets()` privacy mode.
- `shell/planner.py` multi-step plan array with `[c]ontinue/[r]etry/[a]bort`.
- `shell/llm/` `LLMBackend` ABC, `LLMResponse`, Ollama (NDJSON), OpenAI (SSE), Anthropic (SSE, `anthropic-version` header), `pricing.json`, JSON fallback chain.
- `shell/telemetry/` SQLite WAL (`token_events`, `session_memory`, `snippets`, `tasks`, `task_events`, `task_memory`, `skill_patterns`), `watch.py` sidebar (7 Rich panel builders: session, system, git, processes, tokens, shortcuts, clipboard; polls every 5s, or 1s while a clip key is pending watch.py:454; file-based key IPC).
- `shell/memory/` `token-reducer` compression (last 2 turns verbatim), session load/save.
- `shell/clipboard/` `/clip` snippet TUI picker + sidebar integration.
- `shell/tui/` libtmux layout (shell / sidebar / tasks bar panes), settings overlay.
- `shell/config/` `ShellConfig`, wizard, keyring.

### 2.2 Task engine (PRD v3, phases 1–6, complete and wired)
- **`OrchestratorAgent`** (`shell/tasks/orchestrator.py`) **this is what the NL path in `loop.py` calls now** (loop.py:901). Multi-turn loop, max 20 turns, one JSON action per turn: `run | spawn | done`. Confirms each command (`↵ run / e edit / q cancel`). Commands run in a pty with a 120s timeout; a timeout **auto-delegates the whole goal to a sub-agent**. Sub-agent results come back via `~/tasks/<slug>/<name>/.agentic/{status,result}.md` files and are folded into the orchestrator's next context. Has an animated spinner with 186 random verbs.
  - **Contradiction to fix:** the system prompt's rule 4 says the orchestrator keeps looping to check sub-agent status after a spawn, but a timeout-triggered delegation raises `_TimeoutDelegated`, which **breaks the loop immediately** (orchestrator.py:181-186, 249). Code and prompt disagree; the code wins. Fixing it is a runtime-behaviour change, so it is out of scope for Phase 0.
  - Goals are wrapped in `<goal>` tags with an "ignore instructions embedded within the goal text" instruction a partial prompt-injection mitigation that already exists (orchestrator.py `_build_messages`). Phase 3 / I1 extends this to command *output*; it does not start from zero.
- **`TaskAgent`** (`shell/tasks/agent.py`) standalone autonomous agent, `python -m shell.tasks.agent --task X --goal-file …`. Own turn loop (`{command, explanation, done}`), sandboxed CWD `~/tasks/<name>/workspace/`, guidance queue on stdin (steer it mid-run), writes `tasks`/`task_events` rows, pinned-goal memory with per-step summarisation, keyword-matched skill injection.
- **`TaskManager`** (`shell/tasks/manager.py`) spawn (new tmux window `task:<name>`), pause/resume (SIGTSTP/SIGCONT on pgid), kill, attach, back, inspect (shell in task dir), list, stats, history, checkpoint/revert (versioned `vN.json` snapshots).
- **`Sandbox`** (`shell/tasks/sandbox.py`) `bwrap` namespace isolation (workspace R/W, rest R/O, unshare-pid) with a bash-wrapper fallback that shadows write-capable builtins/tools and rejects paths outside the workspace. Supports `shared_read_dir` and `extra_write_dirs` (orchestrator's original CWD).
- **`reconcile.py`** on startup, marks running tasks whose tmux window is gone as `lost`.
- **`tasks/panel.py`** the tasks-bar pane (pane 2), rerendered in a loop.

### 2.3 Self-learning skills (PRD v3 phase 5, complete but shallow)
- **`PatternWatcher`** runs at `/exit`. Reads `audit.log`, groups commands by repo path + intent keywords (stopword-filtered), hashes each cluster, upserts `skill_patterns`, returns clusters with `occurrence_count >= 3` not yet crystallised.
- **`SkillCrystalliser`** sends the cluster's raw commands to the LLM with a skill-writing prompt, writes `~/skills/instructions/<slug>.md` (When to use / Steps / Commands), updates the index, marks the pattern crystallised. It runs **immediately after `PatternWatcher` at `/exit`, unattended** (loop.py:457-472): patterns crossing the threshold become skill files with **no approval step**. This matters for Phase 2, whose gate says skills are "never auto-enabled without approval" that phase is therefore a *behaviour change* to existing code, not a new addition.
- **`SkillIndex`** `~/skills/skills_index.json`: name, file, keywords, auto_generated, **confidence** (0.5 auto / 1.0 manual; +0.05 on success, −0.1 on failure), use_count, last_used, needs_update.
- **Dead API surface** (defined, zero callers anywhere in `shell/`): `SkillIndex.get_ranked()`, `SkillIndex.record_use()`, `SkillIndex.mark_needs_update()` (and the `needs_update` field), `Sandbox.intercept_write()`. Verify with `grep -rn "get_ranked\|record_use\|mark_needs_update\|intercept_write" shell/ --include=*.py`.
- **`TaskSkillLoader`** **keyword-only stub** (explicitly noted in source: "Phase 5 will upgrade it to consult `SkillIndex.get_ranked()`"). Local task skills override global ones by name.

### 2.4 Known gaps / debt (found while reading)
- `TaskSkillLoader.load_relevant()` never consults confidence the scoring loop is write-only today.
- No feedback path actually calls `SkillIndex` success/failure nudges from the agent/orchestrator after a skill is used. Confidence is defined but not moved.
- Bare `except Exception` appears **72 times across `shell/`**, of which **28 are in `shell/tasks/` + `shell/skills/`** (`grep -rn "except Exception" shell/ --include=*.py | wc -l`). Roadmap §3.11's "~30" is an undercount by more than half. Also `print()`/`sys.stdout.write` instead of Rich (12 `print(` calls in `shell/tasks/`) and `input()` for confirms, both violating CLAUDE.md conventions. Orchestrator and TaskAgent duplicate `_run_command`/`_call_llm`.
- The orchestrator only sees sub-agent results by polling files each turn; no event bus, no push, no parallel-await semantics.
- Skills are flat markdown with no validators, no dependencies, no versioning, no retirement (see SkillOps in §4).
- `docs/architecture.md` does not document the task engine or skills at all zero mentions of tasks, skills, or the orchestrator across its 8 sections. `docs/specs/prd-v1.md` likewise predates them. `README.md` **was** rewritten in commit `d1e5734` and now covers the orchestrator, sub-agents, crystallisation, confidence scoring, `/task` and `/skill`, with a component table naming every file so it needs verification against the corrections above, not a rewrite. `docs/specs/prd-v3.md` and the v3 design/plan docs in `docs/specs/` and `docs/plans/` document the engine correctly.
- Unit tests exist for orchestrator wiring, task manager, sandbox shared-read, but not for `PatternWatcher`, `SkillCrystalliser`, `SkillIndex`, `TaskMemory`, or `reconcile`.

### 2.5 Behaviour present in source but not described above

Listed separately because each one changes how a later phase must be scoped.

1. **Builtins missing from the §2.1 list**: `/help` (and `/?`), `/quit` (alias of `/exit`), `/budget reset`, plus the alternate forms `shell stats`, `shell stats --csv`, and `shell memory`. Full dispatch is in `_handle_builtin` (loop.py:445-543).
2. **`TaskAgent` step limit is 25** (`_MAX_STEPS`, agent.py:257) distinct from the orchestrator's 20-turn limit and it re-injects a goal reminder every 5 steps (agent.py:266-271) to counter drift.
3. **Per-command timeout escalates to 600s** for commands containing `docker`, `npm`, `pip`, `yarn`, or `git clone` (agent.py); the flat 120s figure in §2.2 applies to the orchestrator only.
4. **Two separate handoff mechanisms exist.** The orchestrator writes `handoff.txt` via its own `_build_handoff()` (last 8 turns, truncated to 300 chars each); `/task new` instead uses `_build_spawn_context()` in loop.py, which runs the last 8 turns through the memory compressor. Same purpose, different code paths, different output.
5. **`TaskMemory.load_snapshot()`** is reachable only through `/task revert`; `register_skill_hash`/`is_skill_seen` are used by TaskAgent for skill de-duplication.

### 2.6 How to verify this section

§2 was re-audited against source on 2026-09-09 and every figure below is a claim you can check mechanically. Run these from the repo root; each should match the stated value.

```bash
# 11 destructive patterns (not 13): count the r"..." entries in the list
sed -n '/^DESTRUCTIVE_PATTERNS/,/^]/p' shell/safety.py | grep -c '^\s*r"'

# 7 sidebar panel builders
grep -c "^def _panel_" shell/telemetry/watch.py

# 186 spinner verbs
awk '/^_SPINNER_VERBS = \[/,/^\]/' shell/tasks/orchestrator.py | grep -o "'[^']*'" | wc -l

# orchestrator instantiated at loop.py:901
grep -n "OrchestratorAgent(" shell/loop.py

# turn/step limits: 20 orchestrator, 25 agent
grep -n "_MAX_TURNS = \|_MAX_STEPS = " shell/tasks/orchestrator.py shell/tasks/agent.py

# 72 bare except Exception across shell/, 28 in tasks+skills
grep -rn "except Exception" shell/ --include=*.py | wc -l
grep -rn "except Exception" shell/tasks/ shell/skills/ | wc -l

# sidebar poll: 1s while a clip key is pending, else 5s
grep -n "time.sleep" shell/telemetry/watch.py

# auto-crystallisation runs unattended at /exit
sed -n '455,475p' shell/loop.py

# dead API surface: each should show only its own definition site
grep -rn "get_ranked\|record_use\|mark_needs_update\|intercept_write" shell/ --include=*.py

# architecture.md documents neither tasks nor skills (expect 0)
grep -ric "shell/tasks\|orchestrator\|crystallis" docs/architecture.md
```

**Claims that are judgement, not counts** and so need a human or a second model to confirm:
- The orchestrator prompt/code contradiction in §2.2 (rule 4 vs `_TimeoutDelegated`).
- That auto-crystallisation at `/exit` conflicts with Phase 2's "never auto-enabled without approval" gate.
- That `README.md` is accurate post-`d1e5734` it was checked, but against the *old* figures, so its component table and any numbers in it should be re-read alongside the corrections above.

---

## 3. Non-negotiable constraints (from `CLAUDE.md`)

Any feature spec must respect these or **explicitly** propose amending them with a reason:

1. SSH bypass is the first executable line of `main.py`.
2. `cd` = `os.chdir()`, never a subprocess.
3. Every command runs in `PtyProcessUnicode`. No "needs a TTY" allowlist.
4. All user-facing output via Rich `Console`. No `print()`.
5. LLM JSON contract `{command, explanation, safe, plan}` with the strip-fences → parse → re-ask → show-raw fallback chain. (The orchestrator already extends this with `action/spawn/done` that extension should be formalised, not left implicit.)
6. `httpx.Timeout(30.0)` explicit; Anthropic `anthropic-version: 2023-06-01`.
7. SQLite WAL mode on every connection.
8. No LiteLLM / LangChain / gateways. Direct `httpx`.
9. Approved deps: `prompt_toolkit, pygments, ptyprocess, httpx, httpx-sse, rich, tiktoken==0.9.0, libtmux>=0.55<0.56, token-reducer, secretstorage`. **New deps must be argued for.** (Candidates worth arguing: `textual` for the dashboard, `watchfiles`/`inotify` for the daemon, `sqlite-vec` or plain numpy for embeddings, `mcp` SDK or a hand-rolled JSON-RPC client.)
10. No bare `except Exception`.

---

## 4. External research what the field looks like in September 2026

Findings that should shape v4, with sources at the end.

### 4.1 Terminals have become agent runtimes
- **Warp 2.0** (open-sourced April 2026) calls itself an "Agentic Development Environment": a universal prompt that takes NL and shell in one input, **vertical tabs/pane-stacking to run several agents side by side**, **per-pane status badges** (in-progress / done / blocked / awaiting approval), an **inline code-review pane** to reply to a running agent, first-class MCP, "Active AI" suggestions wired to shell history + exit codes, **Notebooks/Workflows/Rules** management, and **Cloud Agents** triggered by webhooks/CI/Slack with nobody at the keyboard.
- **Claude Code** separates **memory, hooks, skills, subagents, plugins, and MCP** into distinct layers. Hooks are deterministic lifecycle scripts (`PreToolUse` etc.) that can block, inject context, log, enforce policy. Skills are folder-based instruction packs (`SKILL.md` + helper scripts) loaded on demand. Subagents are isolated context windows, nestable to depth 5, fanned out in parallel. Plugins bundle all of it as one installable unit.
- Landscape (amux, Pinggy): Claude Code, Codex CLI, OpenCode, Aider, Goose, Warp, Amazon Q. Gemini CLI was retired June 2026 for a closed-source rewrite. Aider's "every edit is a git commit, undo is git" is still praised. **Agent-deck / Conduit / Conductor** are TUIs whose whole job is a fleet view over many running agents: live status, tokens, cost, gate alerts, step-level drill-down.

### 4.2 Self-improving agents and memory
- **Hermes Agent (Nous Research)**: three-layer memory persistent memory, **FTS5 full-text search over its own past sessions with LLM summarisation**, and dialectic **user modelling** that deepens across sessions; agent-curated memory with periodic "nudges" to persist knowledge; **autonomous skill creation after complex tasks** and skills that self-improve during use, stored in `~/.hermes/skills/` compatible with the **agentskills.io** open standard; ~40 tools; local/Docker/SSH/Modal/Daytona execution backends; **built-in cron scheduler** ("daily reports, nightly backups, weekly audits" in NL); messaging gateway (Telegram/Discord/Slack/WhatsApp/Signal/email); subagent delegation.
- **Evermind EverOS**: records agent trajectories as **Cases**, distils repeated patterns into **Skills**, tiers memory as user/group/agent. **Cognee**: graph-native memory with `remember/recall/forget/improve` ops. **Graphiti**: temporal knowledge graph with fact validity windows and provenance. **Letta**: explicit editable memory blocks (persona/user/task/env). **ReMe**: file-based transparent memory (readable markdown + BM25/vector) humans can inspect and edit.
- **SkillOps (arXiv 2605.13716)**: Voyager-style flat skill libraries accumulate "skill technical debt" redundancy, validation gaps, interface drift, stale implementations, low utility. Proposes: skills as **contracts** (preconditions, operation, artifacts, validators, failure modes); a **skill ecosystem graph** with dependency / compatibility / redundancy / alternative edges; **five-dimensional health scoring** (utility, redundancy, compatibility, failure-risk, validation-gap); typed maintenance actions **merge / repair / retire / add_validator / add_adapter**; propagation of risk along dependency edges. Runs as a near-zero-LLM-call library maintenance pass.
- Related 2026 work: **SkillLens** (hierarchical skills, load only the sub-units needed cheaper context), **SkillBrew** (multi-objective curation of skill banks), model-aware skill alignment (a skill written for one model may not fit another).

### 4.3 MCP in 2026
- Spec **2026-07-28**: protocol is now **stateless** (no sessions, no `initialize` handshake version/capabilities ride in `_meta` on every request); `server/discover` RPC; `subscriptions/listen` single stream for change notifications; **Tasks** moved to an official extension (`tasks/get` polling, `tasks/update` for client→server input, unsolicited task handles); **Multi Round-Trip Requests (MRTR)** replace server-initiated sampling/elicitation server returns `resultType: "input_required"` with `inputRequests`, client retries with `inputResponses`; **Extensions** framework; **MCP Apps**; `ttlMs`/`cacheScope` on list results; OpenTelemetry trace propagation in `_meta`; Roots/Sampling/Logging **deprecated**; HTTP+SSE deprecated for Streamable HTTP.
- **MCP Registry** (launched Sept 2025) has ~2,000 verified servers. The awesome-devops-ai list has 474 DevOps/SRE MCP servers and agents.

### 4.4 Agentic DevOps / self-healing
- The "fail-and-notify" model is being replaced by agents that detect failures, read logs, patch, test, and open PRs. AWS DevOps Agent (GA) and Databricks Genie ZeroOps are background agents that monitor and fix. OpenChoreo exposes MCP servers so agents deploy components, and ships an SRE agent. Successful teams draw **three permission tiers: autonomous / human-approval / never-autonomous**.

### 4.5 TUI tooling
- **Textual** (built on Rich, same author) gives reactive widgets, CSS-like layout, DataTable, Tree, Markdown, Log, Sparkline, workers for streaming the standard for agent dashboards in Python (DeerFlow's workbench, OpenChainsaw's lanes UI). It's the obvious dependency to argue for if the sidebar/tasks bar grow beyond static Rich panels.

---

## 5. Feature catalog for v4

Organised into eight pillars. Each item has an ID for referencing in specs, an effort guess (S/M/L/XL), and a note on what it builds on. **Items marked ★ are the ones I'd put in the first wave.**

### Pillar A Orchestration & multi-agent runtime

| ID | Feature | Effort | Builds on |
|---|---|---|---|
| A1 ★ | **Event bus + agent mailbox.** Replace file-polling of `status.md`/`result.md` with an `agent_events` SQLite table (or a UNIX socket) that orchestrator, sub-agents, sidebar, and daemon all read/write. Enables push updates, parallel awaits, and @mention-style messaging between agents. | M | orchestrator, agent, db |
| A2 ★ | **Unify `OrchestratorAgent` and `TaskAgent` into one `Agent` runtime** with roles (orchestrator / worker / reviewer / watcher). One `_run_command`, one `_call_llm`, one action schema (`run / spawn / wait / ask / delegate_mcp / done`), Rich output, no bare excepts. | L | tasks/* |
| A3 | **Nested delegation** (depth-capped, like Claude Code's 5), fan-out of N parallel workers with a join step, and DAG plans (`plan` becomes a graph, not a list). | L | A2, planner |
| A4 | **Reviewer/critic agent**: an optional second model pass that grades a worker's result against its goal before the orchestrator accepts it (cheap model as judge). | M | A2 |
| A5 | **Model routing per role**: cheap/local model for classification, summarisation, skill matching; strong model for orchestration. Config gains `models: {router, orchestrator, worker, summariser}`. | S | config, llm |
| A6 | **Structured tool calls instead of JSON-in-text** where the backend supports native tool use (Anthropic/OpenAI); keep the JSON fallback for Ollama. | M | llm/* |
| A7 | **Interrupt & steer**: `Ctrl+G` opens a guidance prompt to any running agent (already exists via stdin for TaskAgent surface it in the UI). | S | agent guidance queue |
| A8 | **Workspace git-snapshots**: every sub-agent step is a commit in its workspace (aider-style); `/task <n> diff`, `/task <n> undo`. Replaces ad-hoc `vN.json` context snapshots with real file-state history. | M | TaskMemory, manager |

### Pillar B Self-learning skills (the "gets better the more you use it" story)

| ID | Feature | Effort | Builds on |
|---|---|---|---|
| B1 ★ | **Close the confidence loop.** After any skill is injected into a task/orchestrator context, record whether the task succeeded; call `SkillIndex` nudges. Upgrade `TaskSkillLoader.load_relevant()` to `SkillIndex.get_ranked()` (confidence × recency × use_count × keyword/embedding match). | S | skills/index, tasks/skills |
| B2 ★ | **Skill = folder, not a file** (agentskills.io / Claude Code format): `SKILL.md` frontmatter (name, description, triggers, preconditions, validators) + optional `run.sh` / helper scripts. Makes skills executable, shareable, and importable from the community ecosystem. | M | crystalliser, index |
| B3 ★ | **Post-task crystallisation** (Hermes-style): when an orchestrator/task run completes with ≥N steps, ask the summariser model "is this a reusable procedure?" and draft a skill immediately not only from 3× audit-log repetition at `/exit`. | M | crystalliser |
| B4 | **Skill health & maintenance pass** (SkillOps): nightly job computes utility / redundancy / failure-risk / staleness per skill; proposes merge / retire / repair; `/skill doctor` shows the report; auto-applies low-risk actions. | M | index, daemon (E1) |
| B5 | **Skill validators**: a skill can declare a check command (`validate: docker ps | grep api`) run after use to auto-grade success instead of relying on exit codes. | S | B2 |
| B6 | **Semantic skill retrieval**: embed skill descriptions (local model via Ollama `/api/embeddings`, stored in SQLite as blobs; cosine in numpy) so "spin up the API" matches a skill named `deploy-backend`. | M | index |
| B7 | **Skill marketplace / import-export**: `/skill import <git-url or path>`, `/skill publish`, agentskills.io compatibility, signed manifests. | M | B2 |
| B8 | **Model-aware skill variants** (research direction): keep per-model notes in a skill when a procedure only works with certain backends. | S | B2 |

### Pillar C Memory & knowledge

| ID | Feature | Effort | Builds on |
|---|---|---|---|
| C1 ★ | **Server knowledge base**: a persistent, human-readable `~/.sable/knowledge/` of markdown facts the agent learns about *this machine* (services, ports, repo layouts, cron jobs, where logs live, quirks), plus a SQLite FTS5 index over it and over all past sessions (Hermes "session search"). Injected into every orchestration as retrieved context. | M | memory/* |
| C2 | **User model**: what the user prefers (editor, package manager, confirmation appetite, verbosity), updated by an explicit `/remember` and by inference. Editable memory blocks à la Letta. | S | C1 |
| C3 | **Episodic → semantic promotion**: sessions summarised nightly into facts; facts with provenance and validity windows (Graphiti-style "this was true from X to Y"). | M | C1, E1 |
| C4 | **Environment fingerprint on login**: OS, installed tools, running services, disk pressure cached with TTL, so the orchestrator never guesses whether `docker compose` v2 exists. | S | main |
| C5 | **`/forget`, `/memory why <fact>`** inspect provenance and delete. Transparency is the feature. | S | C1 |
| C6 ★ | **Memory Palace**: one addressable memory layer that every agent reads from and writes to, organised as *rooms* (`server/`, `repos/<name>/`, `user/`, `incidents/`, `procedures/` = skills) and *tiers* (working = current turn context; episodic = per-session and per-task event log; semantic = distilled facts with provenance and validity window; procedural = skills). Storage is plain markdown under `~/.sable/palace/` plus an FTS5 index and optional local embeddings. API: `palace.recall(query, room?, k)`, `palace.remember(fact, room, source)`, `palace.forget(id)`, `palace.consolidate()` (nightly: episodic → semantic, merge duplicates, expire stale). Injected as a budgeted block at the top of every orchestrator/worker context; `/palace` browses rooms, `/palace why <fact>` shows provenance. **Not present today**: current memory is only per-session compression (`memory/`) and per-task snapshots (`tasks/memory.py`); nothing survives across tasks or is queryable. C1–C5 become the rooms and operations of C6. | L | memory/*, skills/, A1 |

### Pillar D MCP (both directions)

| ID | Feature | Effort | Builds on |
|---|---|---|---|
| D1 ★ | **MCP client** (spec 2026-07-28, stateless, Streamable HTTP + stdio): `/mcp add <name> <cmd|url>`, tools listed to the orchestrator as an `action: "mcp"` option, MRTR `input_required` surfaced as an elicitation prompt in the shell. Hand-rolled JSON-RPC over `httpx` fits the no-gateway rule. | L | orchestrator, llm |
| D2 | **MCP server mode**: expose the shell itself `run_command` (sandboxed), `list_tasks`, `spawn_task`, `get_skill`, `search_memory` so Claude Code / Warp / Cloud Agents can drive this server through Sable's safety layer instead of raw SSH. Uses the Tasks extension for long-running spawns. | L | D1, A1 |
| D3 | **Registry browse**: `/mcp search kubernetes` hits the MCP Registry, one-key install of verified servers. | S | D1 |
| D4 | **Per-tool permission tiers** (autonomous / confirm / never) for MCP tools, same UI as the safety blocklist. | S | D1, F1 |

### Pillar E Autonomy: daemon, scheduling, self-healing

| ID | Feature | Effort | Builds on |
|---|---|---|---|
| E1 ★ | **`sabled` background daemon** (systemd user service): runs scheduled jobs, skill maintenance, memory consolidation, watchers independent of an SSH session. Communicates via the event bus (A1). | M | A1 |
| E2 ★ | **NL cron**: `/schedule "every night at 2am, back up postgres and prune docker images"` → the orchestrator drafts a plan, you approve once, the daemon runs it as a TaskAgent and delivers a report. `/schedule list/pause/run-now`. | M | E1, agent |
| E3 | **Watchers / triggers**: file changes, log patterns, disk > 90 %, service down, webhook received → spawn a goal. Three tiers: autonomous / approval-required / notify-only. | M | E1 |
| E4 | **Self-healing runbooks**: pair a watcher with a skill (e.g. "nginx 502 → check upstream, restart if healthy, else page"). Every autonomous action is logged with before/after state and is reversible where possible. | L | E3, B2, A8 |
| E5 | **Notifications out**: Slack/Telegram/email/ntfy webhook for task completion, approvals needed, watcher fires. Approve from your phone by replying (`yes <task-id>`). | M | E1 |
| E6 | **Approval inbox**: `/inbox` lists everything waiting on a human decision (elicitations, tier-2 actions, crystallised-skill proposals). | S | A1 |

### Pillar F Safety, governance, trust

| ID | Feature | Effort | Builds on |
|---|---|---|---|
| F1 ★ | **Policy engine + hooks**: `~/.sable/policy.yaml` with allow/confirm/deny rules by command pattern, path, tool, agent role, time-of-day; plus deterministic lifecycle hooks (`pre_command`, `post_command`, `pre_spawn`, `on_skill_use`) that run scripts and can block/inject the Claude Code hooks model. Replaces the hard-coded 11-pattern list with data. | M | safety |
| F2 | **Dry-run & diff preview**: for file-touching commands, show what would change (`--dry-run` where tools support it; otherwise snapshot+diff in a temp overlay). (PRD v1 F6 listed this; never built.) | M | executor |
| F3 | **Blast-radius estimation**: before running, the safety layer tags a command with scope (single file / dir / system / network / destructive) and the UI colours it. Cheap-model classification cached by command hash. | S | safety, A5 |
| F4 | **Full provenance ledger**: every autonomous action records who (agent role, model), why (goal + reasoning summary), what (command, diff), outcome queryable via `/audit` and exported as JSONL. Extends the existing audit log. | S | audit log |
| F5 | **Sandbox upgrades**: per-task network policy (off / allowlist), CPU/mem limits via cgroups, optional Docker/Podman backend as an alternative to bwrap. | M | sandbox |
| F6 | **Secret broker**: agents never see raw secrets; a placeholder (`$SECRET:db_pass`) is substituted at exec time from the keyring. | M | keyring, privacy mode |

### Pillar G UI / UX (make it something you're proud to screenshot)

| ID | Feature | Effort | Builds on |
|---|---|---|---|
| G1 ★ | **Textual-based command center** (`/dash` or `Ctrl+D`, full-screen, and a slimmer live version replacing `watch.py`): agent lanes with **status badges** (thinking / running / blocked / awaiting-approval / done), per-agent token+cost sparklines, live log tail per lane, a tree of orchestrator → sub-agents, and an approval queue. Textual is the one new dependency worth a strong argument. | XL | telemetry, tasks/panel |
| G2 ★ | **Blocks in the main pane** (Warp-style): each input+output is a visually distinct block with a header (command, exit code, duration, cost if AI), collapsible, copyable, and re-runnable; `Ctrl+↑/↓` to jump between blocks. | L | loop, executor |
| G3 | **Streaming reasoning panel**: while the orchestrator thinks, stream its explanation tokens into a dim side region instead of a spinner; keep the verbs as the fallback. | M | llm streaming |
| G4 | **Inline diff & plan preview**: multi-step plans render as a DAG/tree with per-step status; file-changing steps show a unified diff before approval. | M | planner, F2 |
| G5 | **Command palette** (`Ctrl+P`): fuzzy-search builtins, skills, snippets, tasks, MCP tools, recent directories one launcher. | M | clipboard picker |
| G6 | **Themes + layout presets**: `/theme`, saved tmux layouts (focus / fleet / minimal), sidebar panel picker, true-colour palettes, Nerd-font icons with ASCII fallback. | S | tui/layout, watch |
| G7 | **Notebooks/runbooks view**: skills and scheduled jobs render as readable runbooks with "run step" buttons. | M | B2, E2, G1 |
| G8 | **Rich `git`, `docker`, `systemctl`, `ps` renderers** (like the existing `ls`/`cat`): tables, colour, and an "explain this" hotkey on any output block. | M | executor |
| G9 | **Web companion (optional, later)**: read-only dashboard served on localhost over the same SQLite, for the phone/browser. | L | G1 |

### Pillar H Platform, quality, ecosystem

| ID | Feature | Effort | Builds on |
|---|---|---|---|
| H1 ★ | **Docs & tests parity for v3**: README/ARCHITECTURE cover tasks + skills; unit tests for PatternWatcher, Crystalliser, SkillIndex, TaskMemory, reconcile; integration test that runs a 2-agent orchestration end-to-end with `mock_llm`. | M | tests/ |
| H2 | **Plugin system**: a plugin = skills + hooks + MCP server defs + renderers + sidebar panels in one folder; `/plugin install <path|url>`. Mirrors Claude Code plugins. | L | B2, F1, G1 |
| H3 | **Eval harness**: a benchmark of ~50 server tasks (Docker-based) with pass/fail checks; run per backend/model; results in the dashboard. Makes "does the new skill loop help?" measurable. | M | tests/integration |
| H4 | **OpenTelemetry export** of token events and agent spans (MCP now standardises `traceparent` in `_meta`). | S | telemetry |
| H5 | **Multi-host**: register other servers you SSH to; the orchestrator can spawn a TaskAgent on a remote host over SSH (the SSH bypass makes this natural). | L | A2, sandbox |
| H6 | **One-line installer + Docker demo + asciinema/VHS-recorded demos** for the README. | S | install.sh |

### Pillar J Agent capabilities: tools beyond "run a bash command"

Today an agent has exactly one move per turn: emit a shell command. Everything below widens that so agents can find information, act precisely, check their own work, and recover, which is what makes them *autonomous* rather than just *unattended*. All tools flow through the policy engine (F1) and the taint rules (I1): anything read from the web or a file marks the context tainted, and the next action is judged one tier stricter.

| ID | Feature | Effort | Builds on |
|---|---|---|---|
| J1 ★ | **Tool registry.** One `Tool` interface (`name, description, schema, tier, run()`), a registry that exposes builtin tools and MCP tools identically, and a per-role allowlist. Native tool-use on Anthropic/OpenAI; JSON action `{"action":"tool","name":…,"args":…}` on Ollama. Every call is a bus event with args, duration, result size, cost. `/tools` lists what the current role may use and at which tier. | M | A2, F1, D1 |
| J2 ★ | **Web search + fetch.** `web.search(query, k)` and `web.fetch(url, mode=text\|markdown\|raw, max_bytes)` over `httpx` (allowed dependency). Search providers as config: `duckduckgo` (HTML endpoint, no key, default), `searxng` (self-hosted URL), `brave` / `tavily` (API key in keyring). Fetched pages are converted to text, truncated to a token budget, cached by URL with TTL, and wrapped as `<web untrusted="true" url=…>` so the model treats them as data. Policy defaults: search = `allow`, fetch = `allow` for `https`, `confirm` for `http`/IPs/localhost, `deny` for file:// and cloud-metadata ranges. Everything an agent read is listed in the block footer ("read 3 pages: …") so you always know where an idea came from. | M | J1, I1 |
| J3 ★ | **Structured file tools.** `fs.read(path, range)`, `fs.write`, `fs.patch(unified diff)`, `fs.search(glob, regex)`, `fs.tree`. Replaces fragile `sed -i`/heredoc commands; patches are shown as diffs in the confirm block and are the unit of undo (A8). Sandbox-aware: workers get their workspace, orchestrators the CWD, both policy-tiered by path. | M | J1, F1 |
| J4 ★ | **Verify-after-act.** Every `run`/`tool` action may carry `verify`: a command or predicate the runtime executes afterwards (`exit == 0`, `stdout contains`, `file exists`, `http 200`). Failures are fed back as a structured failure, not raw text, and count toward skill confidence (B5). The orchestrator prompt is changed to *require* a verify step for anything that changes state. | S | A2, B5 |
| J5 ★ | **Reflect and recover.** On a failed step the runtime injects a short reflection turn (`what failed, why, what to try instead`) with the last N events, before the next action. Bounded retries per step with escalation: retry → alternative approach → ask user → mark step failed and continue plan. Repeated identical commands are rejected by the runtime, not left to the prompt. | S | A2, A5 |
| J6 | **Scratchpad and plan ledger.** A per-goal `plan.md` (todo list with status) the agent updates each turn and the UI renders as the plan tree (G4). Keeps long goals coherent past the 20-turn limit and survives compression and hand-off to sub-agents. | S | A2, tasks/memory.py |
| J7 | **Docs and help lookup tools.** `docs.man(cmd)`, `docs.help(cmd)` (runs `--help` sandboxed), `docs.tldr`, `docs.pkg(name)` (apt/pip/npm metadata). Cheap, local, no taint; cuts hallucinated flags dramatically. | S | J1 |
| J8 | **System introspection tools.** Structured, read-only, `allow`-tier: `sys.services()` (systemd), `sys.ports()`, `sys.procs()`, `sys.disk()`, `docker.ps/logs/inspect`, `git.status/log/diff`, `logs.tail(unit\|file, grep, since)`. Returns JSON, not text to parse, so plans are grounded in real state and the Memory Palace (C6) can ingest facts from them directly. | M | J1, C6 |
| J9 | **Sandboxed Python tool.** `py.run(code, timeout)` in a bwrap-isolated interpreter with the workspace mounted, for parsing, math, JSON/YAML transforms and small scripts that are painful in bash. Output size-capped; no network unless policy allows. | S | J1, sandbox |
| J10 | **Ask-user as a tool.** Formalise the existing `ask` action: typed questions (`confirm`, `choice`, `text`, `secret`) that land in the INBOX (E6) and, when the daemon is running, can be answered from the phone (E5). Sub-agents block on the answer instead of guessing. | S | A2, E6 |
| J11 | **Parallel tool calls and speculative reads.** Read-only tools (search, fetch, fs.read, sys.*) may be issued as a batch in one turn and run concurrently; writes stay sequential. Halves wall-clock on research-heavy goals. | S | J1, A3 |
| J12 | **Per-tool budgets and rate limits.** `tools.web.max_calls_per_goal`, `max_bytes`, `max_cost`; trips route to the circuit breaker (I2). Prevents a curious agent from crawling the internet at 3am. | S | I2 |

### Pillar K Everyday intelligence, rehearsal, and learning from you

The catalog above is mostly about *goals*: things you type a few times a day. This pillar is about the other 200 interactions a day (typing commands, hitting errors) and about making autonomy something you are comfortable switching on.

| ID | Feature | Effort | Builds on |
|---|---|---|---|
| K1 ★ | **Ghost-text suggestions.** As you type, a small local model (or the cheap role model) proposes the completion from history, cwd, last exit code, git state and the palace; shown as dim inline text, accepted with `→`, dismissed by typing on. Never blocks the keystroke path: suggestions arrive asynchronously with a 150 ms budget and are dropped if late. Off by default on cloud backends unless `ui.ghost_text = "cloud"`. | M | router, C6, A5, ui/prompt |
| K2 ★ | **Explain-last-error.** After any non-zero exit, one dim line: `? explain   ! fix`. `?` renders a short explanation block; `!` proposes a fix as a normal confirm block. The failing command, exit code and last 40 lines of output are the only context sent. | S | executor, ui/blocks |
| K3 ★ | **Learn from your edits.** When you press `e` and change a proposed command, or answer `[b/a]` on an ambiguous line, the pair (proposed, corrected) is stored as a correction. Corrections feed the router corpus (I3), the user model (C2 / palace `user/`), and skill confidence (B1). Sidebar shows "learned 12 corrections this week"; `/corrections` lists and lets you delete. | S | loop confirm flow, I3, C6 |
| K4 | **Natural-language aliases.** `/alias "restart the api" = docker compose restart api`. Matched deterministically (normalised text, fuzzy ≥ 0.9) *before* the router, so it is instant and free; aliases with ≥ 3 uses are offered for promotion to a skill. | S | router, B2 |
| K5 ★ | **Rehearsal mode.** Before applying a plan, run it against a disposable copy of the affected paths (overlayfs or btrfs snapshot where available, else a temp copy inside bwrap) and show the resulting filesystem diff plus each step's exit code. Then `apply` re-runs the same steps for real, or `abort`. Default `on` for plans with ≥ 2 state-changing steps and for all daemon jobs; `rehearse` is also an explicit action the orchestrator may request. Network-affecting steps are flagged as "cannot be rehearsed". | L | A2, A8, F2, sandbox |
| K6 | **Filesystem undo across sessions.** Snapshot any directory an agent is about to write to (same mechanism as K5), keep a ring of N snapshots per path, `/undo <step\|task>` restores. Git covers repos; this covers `/etc`, `$HOME`, data directories. | M | K5, A8 |
| K7 | **Step-up approval for deny-tier.** Instead of a flat refusal, a `deny` action can offer a step-up: TOTP code, hardware key (FIDO2 via `ssh-keygen -Y` challenge), or phone push. Grants are single-use, logged with the second factor used, and never available to the daemon. | M | F1, E5 |
| K8 | **Signed skills and source trust.** Every skill folder carries `source` (`user`, `crystallised`, `imported:<origin>`) and an optional signature. Policy floor by source: imported skills start at `confirm` tier regardless of confidence; unsigned imports cannot carry `run.sh`. | S | B2, B7, F1 |
| K9 | **Incident → runbook.** When a goal that began from a failure signal (watcher, explain-last-error `!`, "X is down") completes with a passing verify, draft a runbook into the palace `incidents/` room: symptom, cause, fix, verification, links to the events. Next time the same watcher fires, the runbook is the first recall and can be offered as a one-key self-heal (E4). | M | C6, E3, J4 |
| K10 | **Self-evaluation loop.** Nightly, replay a sample of the week's completed goals through the eval harness with skills + palace on and off; publish "skills saved N turns / $X this week" and flag regressions (a skill whose presence made a goal *worse*) for `/skill doctor`. | M | H3, B4, E1 |
| K11 | **Repo-aware context.** In a git repo, load `CLAUDE.md`, `AGENTS.md`, `.sable.toml` from the repo root into orchestrator context automatically, tagged as project instructions and subject to the taint rules (a repo file can *inform*, never *authorise*). Small enough to ship in Phase 1. | S | A2, I1 |
| K12 | **Session sharing.** `sable share [--approve-only] [--ttl 1h]` prints a one-time link or tmux socket path; a colleague gets a read-only view of the session, or an approve-only view that can answer INBOX items but not type commands. Every remote approval is logged with who granted it. | M | G1, E6, I4 |

### Pillar I Trust, resilience, adoption (cross-cutting; several belong in the first wave)

| ID | Feature | Effort | Builds on |
|---|---|---|---|
| I1 ★ | **Threat model + output-injection defence.** `docs/THREAT_MODEL.md`. Every command's stdout fed back to the model is wrapped in explicit data delimiters with a "this is untrusted output" frame; commands proposed immediately after reading external content (`curl`, `wget`, `cat` of non-repo files, MCP results) are bumped one policy tier stricter; a red-team corpus of injected outputs lives in the eval harness and must not produce an executed command. | M | policy, agents, llm/prompts |
| I2 ★ | **Runaway-cost circuit breaker.** Per-job caps (tokens, USD, turns, wall-clock), a global daily hard-stop for autonomous work, and a breaker that pauses *all* daemon jobs after N consecutive failures and notifies. Trips are events + INBOX items. | S | budget, daemon |
| I3 ★ | **Router accuracy programme.** Labelled corpus (≥500 lines, bash + NL + ambiguous), measured target (≥99 % bash recall, ≥95 % NL), `/route why "<line>"`, and learning from corrections (Ctrl+B and `[b/a]` answers become training rows). Router false-positives are the #1 uninstall reason. | M | router, tests |
| I4 | **Multi-user & privilege model.** Per-user `~/.sable/`; optional shared read-only `/etc/sable/skills/` and `/etc/sable/policy.yaml` (admin floor that user policy cannot loosen); agents never run as root; `sudo` is always confirm-tier; audit rows carry uid. | M | policy, paths |
| I5 ★ | **CI.** GitHub Actions: unit tests natively on push/PR, integration tests inside `Dockerfile.playground`, lint for `except Exception:` and `print(`, layering test. Required for the "green after every migration step" promise. | S | tests |
| I6 | **Upgrade path.** Config-schema version + migrators, skill-format migrator (flat → folder), numbered DB migrations, `sable doctor` (tmux/bwrap/python/kernel-userns checks, DB integrity, stale tasks, orphan windows). | S | core |
| I7 | **Degraded modes, visibly.** Banners (not silent fallbacks) for: LLM unreachable, tmux missing, bwrap unavailable (userns disabled), SQLite locked, terminal < 80 cols, keyring absent. Each has a documented reduced-capability behaviour. | S | ui, core |
| I8 | **Export / import / sync of `~/.sable/`.** `sable export` tarball and `sable sync <git-remote>` for skills + knowledge + policy (never secrets, never state). Doubles as team mode and new-server bootstrap. | S | paths |
| I9 | **Prompt replay.** Store exact (redacted) messages sent per turn; `/task <n> replay` and `/why` show what the model saw when it decided. | S | bus, audit |
| I10 | **Onboarding.** `/tour`, a no-API-key demo goal on mock-LLM, wizard explains confirm tiers before the first AI command, first-run "what can I say" cheat-sheet. | S | wizard, ui |
| I11 | **Devcontainer.** `.devcontainer/` on `Dockerfile.playground` one-click VS Code on Windows for you and for Opus. | S | docker |
| I12 | **Licence, CONTRIBUTING, CHANGELOG, SECURITY.md** before anything public. | S | |
| I13 ★ | **Mode switch: plain Linux ↔ agentic, in one keystroke or command.** Today `/exit` drops to bash via the `exit_requested` flag and prints "run sable to return"; `.bashrc` auto-launches unless `AGENTIC_SHELL_NO_AUTO` is set. Missing: a *temporary* escape and a clean way back. Add (a) `/bash` (alias `/plain`, key `Ctrl+\`): open a plain interactive bash subshell in the same pane with the sidebar hidden and prompt tagged `[plain]`; typing `exit` returns to the agentic prompt with session context intact; (b) `sable off` / `sable on` / `sable status`: persistent toggle via `~/.sable/disabled`, honoured by the `.bashrc` launcher and by `install.sh`'s restart loop, so a disabled shell logs straight into bash until re-enabled; (c) `sable` (no args) from plain bash re-enters or re-attaches the existing tmux session instead of starting a second one; (d) `sable --wrap`: run as a subshell inside an existing bash without `chsh` or `/etc/shells`, the low-commitment install path. All four print a one-line banner saying which mode you are in and how to switch back. | S | loop.py, install.sh, tui/layout.py |

---

## 6. Recommended first wave (what to ask Opus to spec first)

Order chosen so each step is demonstrable on its own and de-risks the next:

0. **I5 + I11 + I12** CI, devcontainer, licence. Half a day; everything after this is verifiable.
1. **H1 + I3** document + test what exists; router corpus and accuracy gate. Opus needs the tests to refactor safely.
2. **A2 + A1** unify the agent runtime and add the event bus. Everything else (dashboard, daemon, MCP) hangs off these two.
3. **B1 + B3 + B2** make the skills loop actually learn: rank by confidence, crystallise after tasks, folder-format skills.
4. **F1 + I1 + I2** policy engine + hooks, threat model with output-injection defence, cost circuit breaker. Autonomy (E) without these is irresponsible.
5. **G1 + G2** the command center and blocks. This is the "proud to show" milestone; it also becomes the surface for approvals (E6) and MCP elicitations (D1).
6. **E1 + E2** daemon + NL cron. First unattended value.
7. **D1** MCP client. Then D2 (server mode) once A1 is stable.
8. **C1** server knowledge base with FTS5 search.

Second wave: A3, A8, B4–B6, E3–E5, F2–F5, G3–G8, H2–H3, I4, I6–I10.

---

## 7. Instructions for the Opus session

You are being handed this brief to produce the **v4 PRD, design docs, and phased implementation plans** in the same style as `docs/specs/prd-v3.md` and `docs/plans/2026-04-04-agentcos-v3.md` (TDD, checkbox tasks, commit per task).

Before writing anything:
1. Read `CLAUDE.md`, `docs/roadmap-phases.md`, `docs/structure.md`, `docs/specs/prd-v3.md`, `docs/specs/2026-04-06-orchestrator-agent-design.md`.
2. Read in full: `shell/loop.py`, `shell/tasks/*.py`, `shell/skills/*.py`, `shell/telemetry/db.py`, `shell/llm/base.py`, `shell/tui/layout.py`, `shell/telemetry/watch.py`. They are small.
3. Produce a **gap audit**: for every §2 claim, confirm or correct it against source.

Then, for the first wave in §6:
4. Write **one design doc per pillar item** (or per tightly-coupled pair like A1+A2), each containing: problem, user-visible behaviour with example transcripts, data model changes (SQL), module/function signatures, JSON/action contracts, UI mockups as ASCII, failure modes, tests, and how it respects §3. Where a §3 constraint must bend (e.g. adding `textual`), write the argument explicitly and stop for approval.
5. Write a **single v4 PRD** that sequences those designs into phases with acceptance criteria that can be verified by a command.
6. Write **implementation plans** with checkbox tasks, failing-test-first, one commit per task, matching the v3 plan format.

Style rules for what you produce:
- Extend existing modules; don't create parallel implementations of things in §2.
- Prefer SQLite tables + files over new services. The sidebar/dashboard must never block the REPL (separate process, WAL reads).
- Every autonomous capability must ship with its policy-tier default and an audit entry.
- Every skill/memory artefact must be human-readable on disk (ReMe principle) markdown/JSON, never opaque blobs, except embeddings.
- Keep cold start under 300 ms (lazy imports); the daemon carries anything heavy.

---

## Sources

Terminals / agents
- [Best Terminal AI Coding Agents in 2026 amux](https://amux.io/blog/best-terminal-ai-coding-agents-2026/)
- [Top 5 CLI coding agents in 2026 Pinggy](https://pinggy.io/blog/top_cli_based_ai_coding_agents/) · [Best open-source CLI coding agents](https://pinggy.io/blog/best_open_source_cli_coding_agents/)
- [Warp Guide 2026: Agent Mode, MCP, Open Source DeployHQ](https://www.deployhq.com/guides/warp) · [Warp's Universal Agent Support AI Catchup](https://aicatchup.com/tools/warp-agentic-development-environment) · [What makes Warp 2.0 different Medium](https://medium.com/vibecodingpub/what-makes-warp-2-0-different-than-other-agentic-systems-3a3f53479bdb)
- [Claude Code Skills vs Hooks vs Subagents vs MCP Totalum](https://www.totalum.app/blog/claude-code-skills-totalum) · [Claude Code subagents playbook Totalum](https://www.totalum.app/blog/claude-code-subagents-totalum) · [Hooks, Subagents & Skills guide ofox](https://ofox.ai/blog/claude-code-hooks-subagents-skills-complete-guide-2026/)
- [Open-source agent orchestrators Augment Code](https://www.augmentcode.com/tools/open-source-agent-orchestrators) · [awesome-cli-coding-agents](https://github.com/bradAGI/awesome-cli-coding-agents) · [awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators) · [Conduit multi-agent TUI](https://getconduit.sh/) · [OpenChainsaw agent TUI](https://openchainsaw.com/docs/agent-tui/)

Self-improving agents / memory / skills
- [Hermes Agent GitHub](https://github.com/NousResearch/hermes-agent) · [Hermes breakdown TechJack](https://techjacksolutions.com/ai-tools/hermes/hermes-breakdown/)
- [Best open-source agent memory frameworks 2026 EverMind](https://evermind.ai/blogs/best-open-source-agent-memory-frameworks-2026) · [Cognee memory guide](https://www.cognee.ai/blog/guides/open-source-memory-frameworks-llm-agents) · [Self-evolving agents 2026 Medium](https://evoailabs.medium.com/self-evolving-agents-open-source-projects-redefining-ai-in-2026-be2c60513e97)
- [SkillOps: skill libraries as self-maintaining ecosystems (arXiv 2605.13716)](https://arxiv.org/html/2605.13716v1) · [Agent Skills for LLMs: architecture, acquisition, security (arXiv 2602.12430)](https://arxiv.org/html/2602.12430v4) · [Memory for autonomous LLM agents (arXiv 2603.07670)](https://arxiv.org/html/2603.07670v1) · [SkillBrew (arXiv 2605.29440)](https://arxiv.org/pdf/2605.29440) · [SkillLens (arXiv 2605.08386)](https://arxiv.org/pdf/2605.08386) · [Model-aware skill alignment (arXiv 2605.30723)](https://arxiv.org/pdf/2605.30723) · [Voyager skill libraries Beancount research log](https://beancount.io/bean-labs/research-logs/2026/05/08/voyager-open-ended-embodied-agent-lifelong-learning)

MCP
- [MCP 2026-07-28 Key Changes](https://modelcontextprotocol.io/specification/2026-07-28/changelog) · [2026-07-28 release post](https://blog.modelcontextprotocol.io/posts/2026-07-28/) · [MCP roadmap](https://blog.modelcontextprotocol.io/posts/mcp-roadmap/) · [Everything your team needs to know about MCP in 2026 WorkOS](https://workos.com/blog/everything-your-team-needs-to-know-about-mcp-in-2026) · [MCP adoption statistics 2026](https://www.digitalapplied.com/blog/mcp-adoption-statistics-2026-model-context-protocol)

Agentic DevOps
- [awesome-devops-ai (474 tools, July 2026)](https://github.com/hammadhaqqani/awesome-devops-ai) · [Rise of Agentic DevOps: self-healing pipelines](https://pauloguevarac.github.io/posts/Rise-of-Agentic-DevOps-Self-Healing-Pipelines/) · [Agentic DevOps 2026 DevOpsBoys](https://devopsboys.com/blog/agentic-devops-autonomous-infrastructure-management-2026) · [AWS DevOps Agent](https://aws.amazon.com/blogs/aws/aws-devops-agent-helps-you-accelerate-incident-response-and-improve-system-reliability-preview) · [Agentic self-healing for data & AI pipelines (arXiv 2608.01955)](https://arxiv.org/html/2608.01955v1)

TUI
- [Python Textual Real Python](https://realpython.com/python-textual/) · [Textual/Rich dashboards Medium](https://medium.com/@Nexumo_/10-textual-rich-dashboards-that-replace-spreadsheets-5bad588ca263)
