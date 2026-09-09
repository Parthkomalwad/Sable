# Roadmap

Public milestones. Each links to the detailed phase in [docs/roadmap-phases.md](docs/roadmap-phases.md), which carries deliverables, the test gate that closes the phase, and the prompt used to build it. Feature IDs come from [docs/vision.md](docs/vision.md).

Progress is tracked in [GitHub Projects](https://github.com/Parthkomalwad/Agentic_OS/projects) and milestone labels `v0.4` … `v1.0`.

## Shipped

- [x] **v0.1**: Login shell, bash/NL routing, Ollama/OpenAI/Anthropic, safety blocklist, plans, SQLite telemetry, budgets, session memory, installer
- [x] **v0.2**: Rich shell UX, sidebar, snippet clipboard, settings overlay
- [x] **v0.3**: Orchestrator agent, sandboxed sub-agents, task manager, adaptive skills (pattern → crystallise → confidence)

## v0.4: Foundations *(current)*

- [ ] Project hygiene: CI, devcontainer, playground for Windows/macOS, licence, security policy (`I5 I11 I12`)
- [ ] Docs & tests parity for the task engine and skills; mock-LLM mode (`H1`)
- [ ] Router accuracy corpus (≥99 % bash recall) and `/route why` (`I3`)
- [ ] Onboarding `/tour` (`I10`)
- [ ] Mode switch: `/bash` to drop to plain Linux and back, `agentic on|off`, `--wrap` non-login mode (`I13`)
- [ ] Restructure into the `agentic/` package with an enforced layering rule [structure.md](docs/structure.md)

## v0.5: Agent runtime

- [ ] One `Agent` runtime with roles (orchestrator / worker / reviewer) (`A2`)
- [ ] SQLite event bus; sidebar and agents share one stream (`A1`)
- [ ] Per-role model routing (cheap for routing/summaries, strong for reasoning) (`A5`)
- [ ] Steer a running agent (`Ctrl+G`), prompt replay, visible degraded modes (`A7 I9 I7`)
- [ ] Repo-aware context: `CLAUDE.md` / `AGENTS.md` / `.agentic.toml` loaded from the repo root (`K11`)
## v0.6: Skills that learn

- [ ] Confidence-ranked skill retrieval and success/failure feedback (`B1`)
- [ ] Folder skills (`SKILL.md` + scripts, agentskills.io compatible) (`B2`)
- [ ] Crystallise a skill right after a multi-step task, approve from the inbox (`B3`)
- [ ] Validators that auto-grade a skill run (`B5`)
- [ ] Learn from your edits and routing answers; natural-language aliases (`K3 K4`)
## v0.7: Policy & trust

- [ ] Policy engine (`policy.yaml`: allow / confirm / deny) and lifecycle hooks (`F1`)
- [ ] Threat model and output-injection defence with a red-team eval corpus (`I1`)
- [ ] Cost circuit breaker for autonomous work (`I2`)
- [ ] Blast-radius tagging, provenance ledger, secret broker, multi-user model (`F3 F4 F6 I4`)
- [ ] Agent tools: registry, web search and fetch, structured file edits, verify-after-act, reflect and retry, docs lookup, per-tool budgets (`J1 J2 J3 J4 J5 J7 J12`)
## v0.8: Command center

- [ ] Warp-style blocks in the main pane (`G2`)
- [ ] Textual sidebar and full-screen `/dash` with agent lanes and approval inbox (`G1`)
- [ ] Streaming reasoning, `Ctrl+P` palette, themes (`G3 G5 G6`)
- [ ] Ghost-text suggestions as you type; `? explain  ! fix` after any failed command (`K1 K2`)
## v0.9: Autonomy

- [ ] `agenticd` daemon, natural-language cron, watchers (`E1 E2 E3`)
- [ ] Notifications and approvals from your phone (`E5 E6`)
## v1.0: Ecosystem

- [ ] MCP client (spec 2026-07-28) and server mode (`D1 D2 D4`)
- [ ] Memory Palace: rooms and tiers of memory shared by every agent, full-text recall, provenance, nightly consolidation (`C6`, subsumes `C1 C2 C3 C5`)
- [ ] Export / import / sync of skills and knowledge; upgrade migrations and `agentic doctor` (`I8 I6`)
- [ ] Eval harness, plugin system, OpenTelemetry (`H3 H2 H4`)
- [ ] Incident → runbook drafts, nightly self-evaluation, session sharing with approve-only links (`K9 K10 K12`)
## Later

- [ ] Rehearsal mode: plans run against a snapshot first, show the filesystem diff, then `apply` (`K5`)
- [ ] Filesystem undo across sessions; step-up approval (TOTP / FIDO2 / phone) for deny-tier; signed skills (`K6 K7 K8`)
- [ ] DAG plans and reviewer agents (`A3 A4`), git-snapshot undo per agent step (`A8`), skill doctor (`B4`), semantic skill search (`B6`), self-healing runbooks (`E4`), multi-host (`H5`), web companion (`G9`)
