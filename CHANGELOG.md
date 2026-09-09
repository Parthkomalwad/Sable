# Changelog

All notable changes are recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow SemVer once 1.0 ships.

## [Unreleased]

### Added
- Unit tests for `PatternWatcher`, `SkillCrystalliser`, `SkillIndex`, `TaskMemory` and `reconcile` (H1), all offline: LLM, libtmux and tmux are mocked.
- `tests/fixtures/mock_llm.py` gains orchestrator-mode (run, run, spawn, run, done) and worker-mode (`{command, explanation, done}`) canned scripts. Script position is derived from the conversation, because `OrchestratorAgent` builds a fresh backend on every turn.
- `SABLE_MOCK_LLM=1` runs the whole shell against the mock backend with zero API calls, documented in the README "No API key?" section.
- `tests/integration/test_orchestrator_spawn.py`: the orchestrator spawns one sub-agent through the mock backend and folds its result back into the next turn's context. Runs without Docker, tmux or an API key.
- Router accuracy programme (I3): `tests/fixtures/router_corpus.tsv` (539 labelled lines) and `tests/unit/test_router_accuracy.py`, gating bash recall at 0.99 and agentic at 0.95.
- `/route why "<line>"` prints the bash score, the NL score, every rule that fired and the decisive reason.
- Ctrl+B and `[b/a]` answers append `input<TAB>label` rows to `~/.sable/state/router_corrections.tsv`, in the same format as the corpus.
- `shell/paths.py` resolves the new `~/.sable/` state locations.
- Mode switch (I13): `/bash` (alias `/plain`, key Ctrl+\) opens a plain bash subshell in the same pane with the sidebar hidden and a `[plain]` prompt; `exit` returns to Sable with session context intact.
- `sable on|off|status` toggles the agentic layer via `~/.sable/disabled`, honoured by the `.bashrc` launcher, the wrapper and the tmux restart loop, which fall through to bash with "sable is off, run: sable on".
- `sable` with no arguments re-attaches a running `sable-<user>` tmux session instead of starting a second one; `sable --wrap` runs inside an existing bash; `install.sh --wrap-only` skips chsh and /etc/shells.
- `sable --version` and `sable --help`.
- `/tour` (I10): a seven-screen walkthrough of routing, confirmation, the YES word, tasks, skills, the plain-bash escape and the rest. Calls no model, so it runs with `SABLE_MOCK_LLM=1` or no backend at all. Its routing examples are asserted against the real router, so the tour cannot drift from behaviour.
- The first-run wizard explains the three confirm tiers before the first AI command, and points at `/tour`.
- `docs/architecture.md` gains task-engine and skills sections: the orchestrator action contract, sub-agent spawn and sandbox, how results flow back through status.md and result.md, the crystallisation pipeline, and the `~/tasks/`, `~/skills/` and `~/.sable/` layouts.
- v4 planning: `docs/vision.md`, `docs/roadmap-phases.md`, `docs/structure.md`.
- Playground for Windows/macOS hosts: `docker/Dockerfile.playground`, `scripts/playground.ps1`, `scripts/playground.sh`.
- Devcontainer, CI workflow, LICENSE (MIT), CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, `.editorconfig`.
- Public `ROADMAP.md`, `docs/branching.md`, `pyproject.toml`, issue/PR templates, Dependabot, CODEOWNERS.
- Vision: `C6` Memory Palace (rooms and tiers of agent memory, FTS5 recall, provenance, consolidation; subsumes C1–C5) scheduled in Phase 7; `I13` mode switch (`/bash` subshell, `sable on|off|status`, re-attach, `--wrap`) scheduled in Phase 0.
- README and `docs/architecture.md` blocklist count corrected to 11.
- Vision: Pillar J agent capabilities (tool registry, web search/fetch, structured file tools, verify-after-act, reflect/retry, scratchpad, docs and system introspection tools, sandboxed Python, ask-user tool, parallel reads, per-tool budgets) scheduled as Phase 3.5.
- Vision: Pillar K everyday intelligence and rehearsal (ghost-text suggestions, explain-last-error, learn from edits, NL aliases, rehearsal mode, filesystem undo, step-up approval, signed skills, incident → runbook, self-evaluation loop, repo-aware context, session sharing) spread across Phases 1, 2, 4, 8 and 9.
- `docs/architecture-v4.md`: eight Mermaid diagrams tracing the system (layer map, request lifecycle, agent turn state machine, sub-agent spawn, skill loop, Memory Palace, daemon, processes and IPC).

### Fixed
- **The playground had no telemetry sidebar or tasks bar.** `docker/playground-entry.sh` launched a single bare tmux pane, while `install.sh` builds three on a real machine, so the Phase 0 gate item "sidebar visible" could not be met. It now creates the same layout: shell, a 48-column telemetry sidebar and a tasks bar. Panes are sized by a `client-attached` / `client-resized` hook, because tmux resizes the session to the attaching client and would otherwise squeeze splits made beforehand; the sidebar collapses under 120 columns and returns above it. The hook addresses panes by id, since tmux renumbers indices as panes are created.
- **The SSH bypass did not fire for `ssh host cmd`, `scp` or `rsync`.** sshd runs the login shell as `sable -c "<command>"` and leaves `SSH_ORIGINAL_COMMAND` unset; that variable is only populated behind `ForceCommand` or an `authorized_keys` `command=`. The guard checked the env var alone, so a non-interactive SSH command landed in the interactive REPL instead of bash. It now takes the command from `argv` when invoked as `-c`, falling back to the env var. Found by running the Phase 0 gate in the playground, covered by `tests/unit/test_ssh_bypass.py` (6 of its 8 tests fail against the old guard).

### Changed
- README claims verified against the code. Two were wrong: `/skill show` does not exist (the subcommands are list, new and edit), and the skill walkthrough implied confidence-ranked retrieval, which is not wired up yet. Both corrected, and the Phase 0 builtins added to the table.
- Router rewritten for accuracy: bash recall rose from 0.790 to 0.997 and agentic from 0.930 to 0.977 on the corpus. `shutil.which()` is now a supporting signal rather than a decisive one, since whether `make` or `cargo` is installed is a property of the machine, not the user's intent; a `COMMON_COMMANDS` vocabulary carries that knowledge instead. Adds env-assignment, path-invocation, heredoc and variable detection, and treats a command name followed by three or more plain English words as a goal (`kill the nginx process`) rather than a command.
- `looks_like_secret()` checks `SECRET_PATTERNS` before falling back to entropy, so structured credentials are detected; an AWS access key scored 3.68 against a 4.5 entropy threshold and was previously missed by the standalone predicate.
- `pytest.ini_options`: `-p no:libtmux` in addopts, because libtmux ships a pytest plugin that applies marks to fixtures and pytest 9 rejects it at collection, so the whole suite failed to run in the playground image. The bare `timeout` key is gone too; it belongs to the pytest-timeout dev extra and warned on every run without it.
- `tests/unit/test_executor.py` now passes: it had 3 failures on main that no one saw, because ptyprocess is Unix-only so the file never ran on Windows. All three were stale assertions, not bugs: cd prints its error rather than returning it, `ls -la` is intentionally Rich-rendered instead of spawning a pty, and the pty mock crashed in `sys.stdin.fileno()` under pytest's captured stdin before reaching its assertion.
- Tests that asserted absent behaviour were aligned to the code and given reasons: `chmod 777` and `kill -9` are deliberately not in the blocklist, `to_dict()` is a superset of older config files, and `from_dict()` defaults a missing backend to ollama.
- **Project renamed to Sable.** Command is `sable`, tmux session `sable-<user>`, image `sable`, package `sable-shell`, target package `sable/`, daemon `sabled`, future home `~/.sable/`. Runtime state paths (`~/.config/agentic-shell`, `~/.local/share/agentic-shell`, `/var/log/agentic-shell`), the keyring id and `AGENTIC_NEW_SESSION` are unchanged until the Phase 0.5 migration, so existing installs keep their data.
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
