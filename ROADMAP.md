# Roadmap

Public milestones. Each links to the detailed phase in [docs/roadmap-phases.md](docs/roadmap-phases.md), which carries deliverables, the test gate that closes the phase, and the prompt used to build it. Feature IDs come from [docs/vision.md](docs/vision.md).

Progress is tracked in [GitHub Projects](https://github.com/Parthkomalwad/sable/projects) and milestone labels `v0.4` … `v1.0`.

## Shipped

- [x] **v0.1**: Login shell, bash/NL routing, Ollama/OpenAI/Anthropic, safety blocklist, plans, SQLite telemetry, budgets, session memory, installer
- [x] **v0.2**: Rich shell UX, sidebar, snippet clipboard, settings overlay
- [x] **v0.3**: Orchestrator agent, sandboxed sub-agents, task manager, adaptive skills (pattern → crystallise → confidence)

## v0.4: Foundations

- [x] Project hygiene: CI, devcontainer, playground for Windows/macOS, licence, security policy (`I5 I11 I12`)
- [x] Docs & tests parity for the task engine and skills; mock-LLM mode (`H1`)
- [x] Router accuracy corpus (≥99 % bash recall) and `/route why` (`I3`)
- [x] Onboarding `/tour` (`I10`)
- [x] Mode switch: `/bash` to drop to plain Linux and back, `sable on|off`, `--wrap` non-login mode (`I13`)
- [x] Restructure into the `sable/` package with an enforced layering rule [structure.md](docs/structure.md)

## v0.5: Agent runtime

- [x] One `Agent` runtime with roles (orchestrator / worker / reviewer) (`A2`)
- [x] SQLite event bus; sidebar and agents share one stream (`A1`)
- [x] Per-role model routing (cheap for routing/summaries, strong for reasoning) (`A5`)
- [x] Steer a running agent (`Ctrl+G`), prompt replay, visible degraded modes (`A7 I9 I7`)
- [x] Repo-aware context: `CLAUDE.md` / `AGENTS.md` / `.sable.toml` loaded from the repo root (`K11`)
## v0.6: Skills that learn

- [x] Confidence-ranked skill retrieval and success/failure feedback (`B1`)
- [x] Folder skills (`SKILL.md` + scripts, agentskills.io compatible) (`B2`)
- [x] Crystallise a skill right after a multi-step task, approve from the inbox (`B3`)
- [x] Validators that auto-grade a skill run (`B5`)
- [x] Learn from your edits and routing answers; natural-language aliases (`K3 K4`)
## v0.7: Policy & trust

- [x] Policy engine (`policy.toml`: allow / confirm / deny) and lifecycle hooks (`F1`)
- [x] Threat model and output-injection defence with a red-team eval corpus (`I1`)
- [x] Cost circuit breaker for autonomous work (`I2`)
- [x] Blast-radius tagging, provenance ledger, secret broker, multi-user model (`F3 F4 F6 I4`)
- [x] Agent tools: registry, web search and fetch, structured file edits, verify-after-act, reflect and retry, docs lookup, per-tool budgets (`J1 J2 J3 J4 J5 J7 J12`)
## v0.8: Command center

- [x] Warp-style blocks in the main pane (`G2`)
- [x] Textual sidebar and full-screen `/dash` with agent lanes and approval inbox (`G1`)
- [x] Streaming reasoning, `Ctrl+P` palette, themes (`G3 G5 G6`)
- [x] Ghost-text suggestions as you type; `? explain  ! fix` after any failed command (`K1 K2`)
## v0.9: Autonomy

- [x] `sabled` daemon, natural-language cron, watchers (`E1 E2 E3`)
- [x] Notifications and approvals from your phone (`E5 E6`)
## v1.0: Ecosystem

- [x] MCP client (spec 2026-07-28) and server mode (`D1 D2 D4`)
- [x] Memory Palace: rooms and tiers of memory shared by every agent, full-text recall, provenance, nightly consolidation (`C6`, subsumes `C1 C2 C3 C5`)
- [x] Export / import / sync of skills and knowledge; upgrade migrations and `sable doctor` (`I8 I6`)
- [x] Plan graphs, a reviewer agent, rehearsal on a copy, undo across sessions, sub-agent limits, step-up approval (TOTP or phone), signed skills (`A3 A4 A8 F2 F5 K5 K6 K7 K8`)
- [x] Eval harness (mock by default), plugin system, OpenTelemetry, skill doctor and publish, multi-host (`H3 H2 H4 B4 B7 H5`)
- [x] Incident to runbook drafts, a weekly self-check from Sable's own records, session sharing with approve-only links (`K9 K10 K12`)
## Later

- [ ] FIDO2 step-up, public-key skill signatures
- [ ] Semantic skill search (`B6`), self-healing runbooks that run on their own (`E4`), web companion (`G9`)
