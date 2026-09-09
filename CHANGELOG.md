# Changelog

All notable changes are recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow SemVer once 1.0 ships.

## [Unreleased]

### Added
- v4 planning: `docs/vision.md`, `docs/roadmap-phases.md`, `docs/structure.md`.
- Playground for Windows/macOS hosts: `docker/Dockerfile.playground`, `scripts/playground.ps1`, `scripts/playground.sh`.
- Devcontainer, CI workflow, LICENSE (MIT), CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, `.editorconfig`.
- Public `ROADMAP.md`, `docs/branching.md`, `pyproject.toml`, issue/PR templates, Dependabot, CODEOWNERS.
- Vision: `C6` Memory Palace (rooms and tiers of agent memory, FTS5 recall, provenance, consolidation; subsumes C1–C5) scheduled in Phase 7; `I13` mode switch (`/bash` subshell, `agentic on|off|status`, re-attach, `--wrap`) scheduled in Phase 0.
- README and `docs/architecture.md` blocklist count corrected to 11.
- Vision: Pillar J agent capabilities (tool registry, web search/fetch, structured file tools, verify-after-act, reflect/retry, scratchpad, docs and system introspection tools, sandboxed Python, ask-user tool, parallel reads, per-tool budgets) scheduled as Phase 3.5.
- Vision: Pillar K everyday intelligence and rehearsal (ghost-text suggestions, explain-last-error, learn from edits, NL aliases, rehearsal mode, filesystem undo, step-up approval, signed skills, incident → runbook, self-evaluation loop, repo-aware context, session sharing) spread across Phases 1, 2, 4, 8 and 9.
- `docs/architecture-v4.md`: eight Mermaid diagrams tracing the system (layer map, request lifecycle, agent turn state machine, sub-agent spawn, skill loop, Memory Palace, daemon, processes and IPC).

### Changed
- README rewritten for the public repo: status table, quick start for Linux and the playground, roadmap, docs index.
- `task.md` archived to `docs/history/tasks-v1-v3.md`; stray `test/` directory and superseded `docs/FABLE_ENTRY_POINT.md` removed.
- Docs reorganised: `PRD.md` → `docs/specs/prd-v1.md`, `docs/superpowers/{specs,plans}` → `docs/{specs,plans}`, diagrams → `docs/assets/`, lower-case names (`architecture.md`, `vision.md`, `roadmap-phases.md`, `structure.md`, `branching.md`), `docs/README.md` index.
- `.gitattributes` enforces LF for all text files.
- `docs/vision.md` §2 re-audited against source and corrected: destructive blocklist is 11 patterns (was "13"), spinner has 186 verbs (was "~200"), sidebar poll is 5s / 1s-while-clip-key-pending, orchestrator call site is `loop.py:901`. New §2.5 records behaviour previously undocumented (extra builtins, TaskAgent's 25-step limit and goal reminders, 600s timeout escalation, two distinct handoff paths, dead API surface). New §2.6 gives copy-pasteable commands to re-verify every figure.
- `docs/roadmap-phases.md`: blocklist count corrected to 11; "~30 `except Exception: pass`" corrected to 72 across `shell/` (28 in `tasks/` + `skills/`); Phase 0 docs deliverable narrowed to `docs/architecture.md`, since `README.md` was already rewritten in `d1e5734` and needs verification rather than a rewrite.

## [0.3.0] - 2026-04 (v3: task engine + adaptive skills)

### Added
- Orchestrator agent as the natural-language path (`run | spawn | done`), timeout auto-delegation.
- Task engine: `TaskAgent`, `TaskManager` (spawn/pause/resume/kill/attach/inspect/checkpoint/revert), `Sandbox` (bwrap + bash-wrapper fallback), `reconcile`, tasks bar pane.
- Adaptive skills: `PatternWatcher`, `SkillCrystalliser`, `SkillIndex` with confidence scoring.
- `/task`, `/skill` builtins; `tasks`, `task_events`, `task_memory`, `skill_patterns` tables; audit log.
- Animated spinner with random verbs.

## [0.2.0] - 2026-03 (v2 UX)

### Added
- Rich `ls`/`cat`, powerline prompt, tab completion, history search, autosuggest.
- Snippet clipboard (`/clip`) with TUI picker and sidebar integration.
- Seven-panel sidebar, `/new`, `Ctrl+T`, `Ctrl+X` settings overlay.

## [0.1.0] - 2026-02 (v1)

### Added
- Login-shell replacement with SSH bypass, bash/NL routing, three LLM backends, safety blocklist, multi-step plans, SQLite telemetry, budgets, session memory compression, install/uninstall scripts.
