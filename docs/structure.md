# AgenticOS v4 Repository Structure, Configuration Model & Visibility Principles

> Companion to [roadmap-phases.md](roadmap-phases.md) and [vision.md](vision.md).
> This doc answers three questions: **how should the code be organised so it scales**, **how is behaviour driven by configuration rather than hard-coding**, and **how does the user always know what is happening**.
> The physical move is scheduled as **Phase 0.5** in the roadmap and must be done with tests green before and after.

---

## 1. Why restructure

The current `shell/` tree grew phase-by-phase and shows it:

| Problem | Evidence |
|---|---|
| `loop.py` is a god-module (~950 lines): REPL, routing dispatch, 15 builtins, audit log, budget checks, backend factory, orchestrator hand-off | `_handle_builtin`, `_build_backend`, `_write_audit_log` all live there and are imported *back* by `tasks/orchestrator.py` and `tasks/agent.py` (circular dependency) |
| Two agent implementations with 70 % shared code | `tasks/orchestrator.py` and `tasks/agent.py` each own `_run_command`, `_call_llm`, spinner, JSON parsing |
| Domain concepts split across unrelated packages | skills live in `tasks/skills.py` **and** `skills/`; memory in `memory/` **and** `tasks/memory.py`; panels in `tui/`, `telemetry/watch.py`, **and** `tasks/panel.py` |
| Behaviour hard-coded that should be data | 13 destructive regexes in `safety.py`, spinner verbs, system prompts, 120 s timeouts, 20-turn limit, 3× pattern threshold, confidence nudges, sidebar width 44, poll interval 5 s |
| Five state directories | `~/.config/agentic-shell`, `~/.local/share/agentic-shell`, `~/tasks`, `~/skills`, `/var/log/agentic-shell` |
| No single place to see "what is the system doing right now" | status is scattered across tmux windows, `status.md` files, sqlite rows, and stdout |

Adding MCP, a daemon, a policy engine, and a Textual UI on top of this layout would make each of these worse.

---

## 2. Target layout

Domain-first packages under a single `agentic/` namespace (rename from `shell/` the project is no longer just a shell). Every package has one job, depends only on packages *above* it in this list, and never imports from `ui/` or `app/`.

```
agentic/
  __init__.py
  __main__.py            python -m agentic  → app.main

  core/                  ── foundation, zero LLM knowledge ──────────────────
    paths.py             ~/.agentic/* resolution, XDG compat, first-run mkdir
    config/
      schema.py          ShellConfig + nested sections (dataclasses)
      loader.py          layered load: defaults → /etc → ~/.agentic → repo → env → CLI
      wizard.py          first-run setup
      keyring.py
    events/
      bus.py             publish / tail / wait_for over agent_events (SQLite)
      types.py           Event dataclasses + kind enum (single source of truth)
    db.py                WAL connection factory + migrations/ (numbered .sql)
    migrations/
      001_v1.sql … 00N_agent_events.sql
    executor.py          PtyProcessUnicode runner, cd interception, timeouts
    audit.py             provenance ledger (who/why/what/outcome)
    errors.py            typed exceptions (no bare Exception anywhere)

  llm/                   ── model access ────────────────────────────────────
    base.py              LLMBackend ABC, LLMResponse, tool-use capability flag
    ollama.py  openai.py  anthropic.py
    registry.py          build_backend(config.models.<role>)
    contracts.py         the JSON action schemas (single command, plan, agent action)
    prompts/             *.md system prompts, loaded by name editable without code
    pricing.json

  policy/                ── what is allowed ─────────────────────────────────
    engine.py            evaluate(command|tool, context) → allow|confirm|deny + reason
    rules.py             rule model, loader for policy.yaml
    hooks.py             pre_command / post_command / pre_spawn / on_skill_use runners
    blast_radius.py      scope tagging (cheap model, cached)
    secrets.py           entropy check, redaction, $SECRET: broker
    defaults/policy.yaml the shipped 13 destructive patterns, as data

  agents/                ── the runtime ─────────────────────────────────────
    runtime.py           Agent(role=…): one turn loop, one _run_command, one _call_llm
    roles/
      orchestrator.py    prompt + allowed actions + turn limit for this role
      worker.py
      reviewer.py
    actions.py           run / spawn / wait / ask / mcp / done handlers
    sandbox.py           bwrap + bash-wrapper fallback
    manager.py           spawn / pause / resume / kill / attach (tmux windows)
    reconcile.py
    planner.py           plan arrays → DAG execution
    router.py            BASH / AGENTIC / AMBIGUOUS classifier

  skills/                ── procedural memory ───────────────────────────────
    model.py             Skill dataclass ⇄ SKILL.md frontmatter (contract fields)
    index.py             ranking (confidence × recency × use × match)
    loader.py            global + task-local resolution, injection formatting
    watcher.py           audit-log pattern detection
    crystalliser.py      draft skill from a run
    doctor.py            health pass: redundancy / staleness / retire (Phase 9)

  memory/                ── declarative + episodic memory ───────────────────
    session.py           per-session context + token-reducer compression
    task.py              pinned-goal task memory + snapshots
    knowledge.py         ~/.agentic/knowledge/*.md + FTS5
    user_model.py

  mcp/                   ── protocol ────────────────────────────────────────
    client.py            stateless JSON-RPC, stdio + streamable HTTP, MRTR
    server.py            --mcp-serve: run_command / spawn_task / … behind policy
    registry.py          /mcp search

  daemon/                ── unattended work ─────────────────────────────────
    service.py           agenticd main loop (bus drain, schedules, maintenance)
    schedule.py          NL cron rows → jobs
    watchers.py          file / log / metric / webhook triggers
    notify.py            ntfy / slack / telegram / email

  ui/                    ── everything a human sees; imports from all above ──
    console.py           the one Rich Console + theme loading
    theme/               *.toml palettes; Nerd-font / ASCII glyph sets
    prompt/
      session.py         prompt_toolkit PromptSession, completers, key bindings
      powerline.py
    blocks.py            input/output block model + renderer
    confirm.py           the ↵ run / e edit / d diff / q cancel surface (used by all roles)
    spinner.py           verbs list loaded from data/
    renderers/           ls, cat, git, docker, systemctl, ps
    sidebar/             Textual app for pane 1 (widgets: agents, inbox, cost, git, system, clip)
    dash/                Textual full-screen command center
    tmux/                layout.py, panes, key IPC
    palette.py           Ctrl+P

  app/                   ── composition root ────────────────────────────────
    main.py              SSH bypass FIRST LINE, then bootstrap()
    bootstrap.py         load config → migrate db → start bus → reconcile → layout → REPL
    repl.py              the loop: read → builtin? → route → dispatch (thin)
    builtins/            one file per command: task.py skill.py clip.py mcp.py schedule.py …
    cli.py               argparse: --wrap, --mcp-serve, --daemon, --mock-llm, --version

  data/
    spinner_verbs.txt
    intent_stopwords.txt

tests/
  unit/<package>/        mirrors agentic/ one-to-one
  integration/
  fixtures/mock_llm.py   canned responses per role
  evals/                 Phase 9 task bank

docs/
  ARCHITECTURE.md        (replaces v2; diagrams regenerated)
  contracts.md           every JSON/SQL/file contract in one place
  config-reference.md    generated from schema.py
  specs/  plans/         design docs + checkbox plans (unchanged workflow)
  assets/  history/

.github/workflows/ci.yml   .devcontainer/   docker/   scripts/
install.sh  uninstall.sh  pyproject.toml  LICENSE  CONTRIBUTING.md  CHANGELOG.md  SECURITY.md
```

### Dependency rule (enforced by a unit test)
`core` → `llm` → `policy` → `agents` → `skills` / `memory` / `mcp` / `daemon` → `ui` → `app`.
Lower layers never import higher ones. `agents` never imports `ui`; it **publishes events** and `ui` renders them. This single rule is what removes the current `tasks → loop` circular import and what lets the daemon run agents with no terminal attached.

---

## 3. Configuration model everything that is a number, list, or prompt becomes data

### 3.1 One home
```
~/.agentic/
  config.toml            user config (was config.json; TOML for comments + sections)
  policy.yaml            allow / confirm / deny rules, tiers
  hooks/                 executable scripts by lifecycle name
  prompts/               overrides for agentic/llm/prompts/*.md (same filename wins)
  themes/                user palettes
  skills/<slug>/SKILL.md
  knowledge/*.md
  schedules.yaml         NL cron, written by /schedule, editable by hand
  mcp.toml               servers
  tasks/<name>/…         workspaces + .agentic/ mirrors
  state/
    sessions.db          all tables, WAL
    history
    audit.jsonl          provenance ledger (also mirrored to /var/log when writable)
  logs/agenticd.log
```
`core/paths.py` resolves everything; old XDG paths are symlinked for one release, then removed.

### 3.2 Layered config, visible provenance
Precedence (lowest → highest): built-in defaults → `/etc/agentic/config.toml` → `~/.agentic/config.toml` → `./.agentic.toml` in the current repo → `AGENTIC_*` env vars → CLI flags.
`/config show` prints every effective value **with the layer it came from**, e.g.
```
agents.orchestrator.max_turns = 20      (default)
models.orchestrator          = claude-sonnet-5   (~/.agentic/config.toml)
policy.default_tier          = confirm  (./.agentic.toml)
```
That single feature answers "why did it do that?" for configuration the same way `/audit` does for actions.

### 3.3 Sections (draft `config.toml`)
```toml
[models]              # per-role; any role falls back to `default`
default      = "llama3.1"
router       = "llama3.1"
orchestrator = "claude-sonnet-5"
worker       = "claude-sonnet-5"
summariser   = "claude-haiku-4-5"

[backends.anthropic]  api_base = "https://api.anthropic.com"   # key in keyring
[backends.ollama]     api_base = "http://localhost:11434"

[routing]   mode = "auto"   prefix = ">>"   ambiguity = "ask"   # ask | bash | agentic

[agents]
max_turns          = 20
command_timeout_s  = 120
delegate_on_timeout = true
max_parallel_workers = 4
max_depth          = 3
sandbox            = "bwrap"        # bwrap | wrapper | docker | none
network            = "allow"        # allow | deny | allowlist

[policy]
default_tier = "confirm"            # allow | confirm | deny  for unmatched commands
confirm_word = "YES"
dry_run_file_changes = false

[skills]
auto_crystallise = "propose"        # off | propose | auto
pattern_threshold = 3
confidence = { initial_auto = 0.5, initial_manual = 1.0, on_success = 0.05, on_failure = -0.10 }

[memory]
compress_after_tokens = 2000
keep_last_turns = 2
knowledge_enabled = true

[budget]  daily_tokens = 0  session_tokens = 0  warn_at = 0.8

[daemon]  enabled = false  maintenance_time = "02:30"
[notify]  channel = "ntfy"  ntfy_topic = ""

[ui]
theme = "default-dark"
glyphs = "nerd"                     # nerd | ascii
sidebar = { enabled = true, width = 44, panels = ["agents","inbox","cost","git","system","clip"], refresh_ms = 500 }
blocks  = { collapse_over_lines = 40, show_cost = true, show_duration = true }
stream_reasoning = true
spinner_verbs = "fun"               # fun | plain | off
```
Every key has a default in `schema.py`; `docs/config-reference.md` is generated from the dataclass docstrings so the reference can never drift.

### 3.4 Prompts and rules are files
System prompts (`llm/prompts/orchestrator.md`, `worker.md`, `skill_writer.md`, `router.md`) and the destructive-pattern list (`policy/defaults/policy.yaml`) ship as data. A user overrides by dropping a same-named file in `~/.agentic/prompts/` or editing `policy.yaml`. `/prompt show orchestrator` prints the effective prompt with its source path.

---

## 4. Visibility principles the user always knows what is happening

These are UX rules every feature spec must satisfy. They're what turn "an agent did something" into "I watched it and could have stopped it".

1. **Every state change is an event, every event is visible.** Agents, daemon, policy engine, and skill index all publish to the bus. The sidebar AGENTS panel, `/dash`, `/task <n> events`, and `/audit` are four views over the same stream. Nothing happens "silently".
2. **Before, during, after always three moments shown.**
   - *Before*: the confirm block shows the command, the one-line reason, the blast-radius colour, the policy tier that applied and **which rule**, and which skill (if any) it came from.
   - *During*: a badge (`thinking · running 12s · waiting on worker-2 · blocked: needs approval`), streamed reasoning text, live output.
   - *After*: block header with exit code, duration, cost; if a skill was used, its confidence delta; if a sub-agent ran, a one-line result.
3. **Every "why" has a command.** `/why` (last decision), `/config show` (value + layer), `/prompt show <role>`, `/policy explain "<cmd>"` (which rule would fire), `/skill why <slug>` (why it was matched), `/memory why <fact>` (provenance). The user never has to read source to understand behaviour.
4. **Approvals are one surface.** Confirm-tier commands, sub-agent asks, MCP elicitations, crystallised-skill proposals, schedule approvals all land in **INBOX** (sidebar count + `/inbox` list + `/dash` queue). Same keys everywhere: `↵ approve · e edit · d diff · q reject · ? explain`.
5. **Autonomy is opt-in per tier and per surface.** Defaults: interactive = `confirm`; daemon = `allow`-tier only, everything else queued. Changing that is a one-line policy edit that `/config show` and `/policy explain` make visible.
6. **Cost is always on screen.** Per-block cost, session total in the prompt segment, today's total in the sidebar, per-agent in `/dash`. Budget warnings are blocks, not log lines.
7. **Nothing swallows errors.** Typed exceptions → an error block with the exception name, the action that failed, and a `/why` pointer. `except Exception: pass` is banned by a lint test.
8. **Human-readable on disk.** Skills, knowledge, policy, schedules, prompts, audit all plain text under `~/.agentic/`. If the UI is gone, `cat` still explains the system.
9. **Progressive disclosure.** Default view is calm: one block per action, badges, counts. Detail is one key away (`Tab` expands a block, `/dash` opens lanes, `?` explains). Power users get `--verbose` and `/events tail`.
10. **Consistent keys and colours across every surface** (REPL, sidebar, dash, palette): green read-only · amber writes · red destructive · purple AI-generated · blue policy/system. Defined once in `ui/theme/`.

---

## 5. Migration plan (Phase 0.5 do after tests exist, before the agent-runtime refactor)

Mechanical move first, behaviour change later. Every step ends with `pytest tests/unit` green.

1. **Baseline**: Phase 0 tests merged; add `tests/unit/test_layering.py` (import-graph rule from §2) it will fail initially and becomes the migration's finish line.
2. **`git mv` packages** into the `agentic/` tree per §2 table below; leave `shell/__init__.py` as a **compat shim** that re-exports the old paths with a `DeprecationWarning` for one release so `install.sh`, the Dockerfiles, and tmux `send_keys` commands keep working.
3. **Split `loop.py`** into `app/repl.py`, `app/builtins/*.py`, `core/audit.py`, `llm/registry.py`, `ui/prompt/*`. No logic changes pure extraction with tests pinned.
4. **Extract data**: destructive patterns → `policy/defaults/policy.yaml`; prompts → `llm/prompts/*.md`; verbs/stopwords → `data/`; numeric constants → `config/schema.py` defaults.
5. **Paths**: `core/paths.py` + symlink migration on first run; update `install.sh`, Dockerfiles, `uninstall.sh`.
6. **Config format**: `config.json` → `config.toml` with automatic one-time conversion; `/config show` with layer provenance.
7. **Delete shim** in the release after next.

| Current | Target |
|---|---|
| `shell/main.py` | `agentic/app/main.py` (bypass stays line 1) |
| `shell/loop.py` | `agentic/app/repl.py` + `app/builtins/*` + `core/audit.py` + `llm/registry.py` + `ui/prompt/*` |
| `shell/router.py` | `agentic/agents/router.py` |
| `shell/executor.py` | `agentic/core/executor.py` + `ui/renderers/{ls,cat}.py` |
| `shell/safety.py` | `agentic/policy/{engine,secrets}.py` + `policy/defaults/policy.yaml` |
| `shell/planner.py` | `agentic/agents/planner.py` |
| `shell/llm/*` | `agentic/llm/*` |
| `shell/config/*` | `agentic/core/config/*` |
| `shell/telemetry/db.py, events.py` | `agentic/core/db.py`, `core/migrations/`, `core/events/types.py` |
| `shell/telemetry/watch.py` | `agentic/ui/sidebar/` |
| `shell/memory/*` | `agentic/memory/session.py` |
| `shell/clipboard/*` | `agentic/app/builtins/clip.py` + `ui/palette.py` |
| `shell/tui/layout.py` | `agentic/ui/tmux/layout.py` |
| `shell/tui/panel.py` | `agentic/app/builtins/config.py` + `ui/…` |
| `shell/tasks/orchestrator.py, agent.py` | `agentic/agents/runtime.py` + `agents/roles/*` (Phase 1 merges them) |
| `shell/tasks/manager.py, reconcile.py, sandbox.py` | `agentic/agents/{manager,reconcile,sandbox}.py` |
| `shell/tasks/memory.py` | `agentic/memory/task.py` |
| `shell/tasks/skills.py` | `agentic/skills/loader.py` |
| `shell/tasks/panel.py` | `agentic/ui/sidebar/agents_panel.py` |
| `shell/skills/*` | `agentic/skills/{watcher,crystalliser,index}.py` |

---

## 6. What this buys you

- **Scaling**: MCP, daemon, policy, and the Textual UI each get a package with a clear contract instead of being bolted onto `loop.py`. New builtins are one file each. New agent roles are one file each. New renderers are one file each.
- **Testability**: the layering rule makes `agents/` testable with no terminal, and `ui/` testable with a fake bus.
- **Config-driven**: anything a user might reasonably want to change is a file under `~/.agentic/` and visible through `/config show` with provenance.
- **Visibility**: one event stream, four views, a `/why` for every decision, one INBOX for every approval.

---

## 7. Prompt for Opus (Phase 0.5)

> Read `docs/structure.md` in full, then `CLAUDE.md`. Implement §5 migration steps 1–4 only, as a sequence of small commits, each leaving `pytest tests/unit` green. Do not change runtime behaviour, prompts, or thresholds extraction and moves only. Add `tests/unit/test_layering.py` enforcing the §2 dependency rule and make it pass by the end. Update `CLAUDE.md`'s project-structure section to match. Stop and report before steps 5–7.
