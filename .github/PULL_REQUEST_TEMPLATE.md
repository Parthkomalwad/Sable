## What

<!-- One paragraph. Link the roadmap feature ID (e.g. A1, B2, I3) if this is roadmap work. -->

Feature ID: `none`  ·  Phase gate: `docs/roadmap-phases.md` → Phase `none`

## Why

<!-- The problem or the user-visible outcome, not a restatement of the diff. -->

## How to verify

<!-- Commands a reviewer runs in the playground (`scripts/playground.ps1` / `.sh`). -->

```
.\scripts\playground.ps1 tests
```

## Checklist

- [ ] Unit tests pass locally and stay under 10 s; no LLM/network/subprocess in `tests/unit/`
- [ ] Follows `CLAUDE.md` rules (Rich only, no bare `except Exception`, ptyprocess for commands, WAL mode)
- [ ] No new dependency or the argument for it is in this PR
- [ ] If an agent can now do something autonomously: policy tier default, audit entry, `THREAT_MODEL.md` line
- [ ] Behaviour is observable (event / block / `why`-style command) per `docs/structure.md` §4
- [ ] `CHANGELOG.md` *Unreleased* updated
