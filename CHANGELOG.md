# Changelog

All notable changes are recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow SemVer once 1.0 ships.

## [Unreleased]

### Added
- `sable/llm/registry.py` and `sable/core/audit.py` (Phase 0.5 step 3): `build_backend` and the two audit writers, extracted from `app/repl.py`. This removes the circular import the layering rule was written to catch, since `agents/` and `skills/` previously imported the REPL to reach them. Both gain direct unit tests they never had: 23 for backend selection and API-key precedence, 9 pinning the tab-separated audit format that `skills/watcher.py` parses.
- CI runs the new gate items (I5): the integration job now exercises the live SSH bypass and the tmux layout as named steps, runs the full integration suite, and runs the playground entrypoint's `tests` path so a break in `docker/playground-entry.sh` is caught. The `print(` rule and the bypass-is-first assertion are blocking rather than warnings, and a new blocking check asserts the bypass reads argv as well as the environment, which is the exact shape of the Phase 0 bug. `except Exception` stays non-blocking and now reports its count (72) as a Phase 1 baseline.
- `tests/integration/test_playground_layout.py` (I5): builds the same three-pane tmux session `docker/playground-entry.sh` builds, at 160x45, and asserts both geometry and rendered content. The sidebar is 48 columns and full height, the tasks bar is 12 rows and only as wide as the shell pane, the three tile the window with no gaps, the sidebar paints the session panel with the configured model and the system panel, the tasks bar paints its TASKS/GOAL header, and neither pane shows a traceback. Panes are addressed by pane id, never index, because tmux renumbers indices as panes are created. Verified to be a real gate: collapsed back to the single bare pane the playground shipped with, 7 of its 10 tests fail.
- `tests/integration/test_ssh_bypass_live.py` (I5): the SSH bypass exercised end to end against a real sshd, with the real login shell, using the real `ssh` and `scp` clients. Asserts exact remote output, no REPL banner leaking into a non-interactive session, exit-code propagation, stream separation, piped stdin, an scp round trip in both the SFTP and legacy (`-O`) modes, and the `SSH_ORIGINAL_COMMAND` branch. Verified to be a real gate: with the argv branch of the guard reverted to env-only, 7 of its 8 tests fail.
- `tests/unit/test_layering.py` (Phase 0.5 step 1): enforces the `docs/structure.md` §2 dependency rule, core to llm to policy to agents to skills/memory/mcp/daemon to ui to app. Function-level imports count, since deferring an import into a function is exactly how the current tree hides its cycle. The rule skips itself until the `sable/` tree exists, so it goes green as the migration lands; a companion test asserts the documented `shell/tasks` and `shell/skills` to `shell.loop` cycle is still present, so the file always tests something real, and tells you to delete it once the cycle is gone.
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
- CI's convention checks follow the package to `sable/`. They grepped `shell/`, which after the move holds only the compat shim, so the `print(` rule would have scanned one file and passed vacuously and the two bypass checks would have crashed on a missing `shell/main.py`. The `print(` exemption moved with its file: `shell/tasks/agent.py` is now `sable/agents/worker.py`.
- The 15 unimplemented integration placeholders in `test_full_loop.py`, `test_llm_backends.py` and `test_session_resume.py` are marked `xfail(strict=True, raises=NotImplementedError)` instead of failing the run. They were red on main and CI tolerated it. Strict means an XPASS fails the build, so a Phase 2 implementation cannot land while leaving the marker behind.
- **Phase 0.5 step 2: the package moved from `shell/` to `sable/`**, organised by domain per `docs/structure.md` §2: `core/` (paths, db, events, executor, config), `llm/`, `policy/`, `agents/` (the former `tasks/`, plus router and planner), `skills/`, `memory/`, `ui/` (sidebar, tmux, clipboard) and `app/` (main, repl, mode, tour). All 44 modules moved with `git mv`, so history follows them. Files whose §2 target is a split (`loop.py`, `executor.py`, `safety.py`) moved whole to their primary destination; step 3 carves them up. No behaviour change.
- `shell/__init__.py` is now a compatibility shim re-exporting every old path with a `DeprecationWarning`, kept for one release (`docs/structure.md` §5 step 7 removes it). It matters because `install.sh`, both Dockerfiles, `/etc/shells` and any already-running tmux session invoke `python -m shell.main`, `python -m shell.telemetry.watch` and `python -m shell.tasks.panel` by name, so an installed machine keeps working across the upgrade. The shim aliases rather than re-executes, so `shell.x` and `sable.y` are the same module object and module-level singletons are not duplicated.
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
