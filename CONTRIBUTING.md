# Contributing

## Where to start

1. Read `CLAUDE.md` — the non-negotiable implementation rules (SSH bypass first, `os.chdir` for `cd`, ptyprocess for everything, Rich only, WAL mode, no bare `except Exception`, approved dependencies only).
2. Read `docs/ROADMAP_v4.md` for what is being built and in what order, and `docs/STRUCTURE_v4.md` for where code belongs.
3. Pick a phase gate or a feature ID (`A1`, `B2`, … from `docs/VISION_v4_FEATURE_BRIEF.md`) and open an issue naming it before starting large work.

## Development environment

The shell is Linux-only. From Windows or macOS use the playground:

```
scripts/playground.ps1 -Rebuild     # or scripts/playground.sh
scripts/playground.ps1 tests
```

or open the repo in VS Code and choose **Reopen in Container** (`.devcontainer/`).

On Linux/WSL: `bash scripts/dev_setup.sh`, then `pytest tests/unit/`.

## Rules for pull requests

- Unit tests must pass and stay under 10 seconds; no LLM calls, no subprocesses, no I/O in `tests/unit/` — use `tests/fixtures/mock_llm.py`.
- One logical change per PR. Refactors and behaviour changes are separate PRs.
- New dependencies require a written argument in the PR (see the approved list in `CLAUDE.md`).
- Anything that changes what an agent may do autonomously must add a policy tier default, an audit entry, and a line in `docs/THREAT_MODEL.md`.
- Every user-visible behaviour must be observable: an event on the bus, a block on screen, or a `/why`-style command. See `docs/STRUCTURE_v4.md` §4.
- Update `CHANGELOG.md` under *Unreleased*.

## Commit style

Short imperative subject (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`), body explains *why*. Follow the existing `git log`.

## Reporting security issues

See `SECURITY.md`. Do not open public issues for vulnerabilities.
