# Changelog

All notable changes are recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow SemVer once 1.0 ships.

## [Unreleased]

### Added
- v4 planning: `docs/VISION_v4_FEATURE_BRIEF.md`, `docs/ROADMAP_v4.md`, `docs/STRUCTURE_v4.md`.
- Playground for Windows/macOS hosts: `docker/Dockerfile.playground`, `scripts/playground.ps1`, `scripts/playground.sh`.
- Devcontainer, CI workflow, LICENSE (MIT), CONTRIBUTING, SECURITY.

## [0.3.0] — 2026-04 (v3: task engine + adaptive skills)

### Added
- Orchestrator agent as the natural-language path (`run | spawn | done`), timeout auto-delegation.
- Task engine: `TaskAgent`, `TaskManager` (spawn/pause/resume/kill/attach/inspect/checkpoint/revert), `Sandbox` (bwrap + bash-wrapper fallback), `reconcile`, tasks bar pane.
- Adaptive skills: `PatternWatcher`, `SkillCrystalliser`, `SkillIndex` with confidence scoring.
- `/task`, `/skill` builtins; `tasks`, `task_events`, `task_memory`, `skill_patterns` tables; audit log.
- Animated spinner with random verbs.

## [0.2.0] — 2026-03 (v2 UX)

### Added
- Rich `ls`/`cat`, powerline prompt, tab completion, history search, autosuggest.
- Snippet clipboard (`/clip`) with TUI picker and sidebar integration.
- Seven-panel sidebar, `/new`, `Ctrl+T`, `Ctrl+X` settings overlay.

## [0.1.0] — 2026-02 (v1)

### Added
- Login-shell replacement with SSH bypass, bash/NL routing, three LLM backends, safety blocklist, multi-step plans, SQLite telemetry, budgets, session memory compression, install/uninstall scripts.
