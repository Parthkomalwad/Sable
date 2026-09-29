# Phase 9 Implementation Plan: Ecosystem, measurement and sharing (v1.0, part 4)

> Checkbox tasks, failing-test-first, one PR per task, a live gate run at the
> end. Style follows the Phase 3 to 8 plans.

**Goal:** Sable can be measured, extended, observed, pointed at more than one
server, learn runbooks from incidents, and let a teammate approve from their
phone.

**Deliverables:** H3, B4, B7, H2, H4, H5, K9, K10, K12.

**Gate:**

```
sable eval (mock backend) runs the task suite in the playground and writes a results table; nothing is paid for
/skill doctor flags a skill made to fail on purpose
/plugin add <folder> -> its MCP tools appear at the plugin's tiers; a call runs
With otel.endpoint set, traces for a goal arrive at a local collector
@hostb df -h runs on a second container over SSH, under hostb's own policy
A disk watcher fires, a fix passes its verify -> a runbook is drafted; the next identical alert offers it
sable share --approve-only: a link approves one inbox item once; the audit row names the approver
```

---

## 0. Decisions (2026-09-28 and 2026-09-29)

| Question | Decision |
|---|---|
| Benchmarks cost | **Never a paid API by default.** `sable eval` runs against the mock backend (scripted replies) or a local Ollama model. A paid backend runs only when the user passes `--backend openai` or `anthropic` explicitly. The nightly self-check (K10) makes **no model calls**: it reports from logs Sable already keeps. |
| Plugins | A plugin is a folder with `plugin.toml`: an MCP server definition (Phase 6) plus optional skills and hooks, and default tiers per tool. No Python is imported into Sable. |
| Telemetry | OTLP/HTTP JSON posted with `httpx` to `otel.endpoint`; off by default; batched, redacted, never blocking a goal. |
| Multi-host | `@host` runs through SSH to the remote `sable --mcp-serve`, so the remote host's own policy, audit and inbox apply. |
| Sharing | Through ntfy (Phase 5): approve-only links carry single-use tokens; read-only is a periodic `/dash` text snapshot to a private topic. Nothing listens on the network. |
| New dependencies | **None.** |

### 0.1 Not in scope

A public skill marketplace server, plugin UI renderers and sidebar panels, a
live web view for sharing, spawning full sub-agents on remote hosts (H5 runs
goals and commands remotely through the MCP server; remote workers are later).

---

## Task 0: The eval harness (H3, serial, first)

Files: `evals/tasks/<name>/{task.toml,setup.sh,check.sh}`, `sable/evals/runner.py`,
`sable/app/main.py` (`sable eval`), the mock backend's scripts for each task.

- [ ] A task: a goal sentence, a setup script, a check script (exit 0 = pass),
      a timeout. Start with 25 server tasks (logs, configs, disk, cron,
      services as files, users and permissions, archives); the suite can grow
      to 50 later.
- [ ] `sable eval [--backend mock|ollama|openai|anthropic] [--only NAME]`:
      each task in a fresh temp HOME and work dir, the goal run headless
      (previews accepted, typed YES refused, so confirm-tier steps fail the
      task honestly), the check run, and one row per task: pass, steps,
      tokens, cost, seconds. Results appended to the DB and written as a
      markdown table. Default backend: mock. Refuses a paid backend unless it
      is named on the command line.
- [ ] `/dash` shows the latest eval run.

## Task 1: Skill doctor (B4)

- [ ] `/skill doctor`: per skill, recent failure rate, last used, duplicates
      (token overlap, as consolidation does), signature state; proposes repair,
      merge or retire. It never deletes; `/skill disable` stays the action.

## Task 2: Skill publish and install (B7)

- [ ] `/skill publish <name> [file]` packs one skill folder with a manifest
      and its signature; `/skill install <file-or-https-url>` shows every file,
      asks, then installs it as `imported:<origin>` and unsigned. Reuses the
      Phase 7 archive checks (paths, links, sizes, hashes).

## Task 3: Plugins (H2)

- [ ] `plugin.toml` schema; `/plugin add <folder>`, `/plugin list`,
      `/plugin remove`: registers the MCP server through `sable.mcp.servers`,
      applies the plugin's per-tool tiers (never looser than Sable's
      defaults without `/mcp trust`), installs bundled skills as imported and
      hooks only after showing them and asking.

## Task 4: OpenTelemetry (H4)

- [ ] Spans for goal, turn, command, tool call and model call, with token and
      cost attributes, redacted; a background batcher posts OTLP/HTTP JSON to
      `otel.endpoint`; failures are dropped with one log line.

## Task 5: Multi-host (H5)

- [ ] `/host add NAME user@host`, `/host list`, `/host rm`; `@NAME <goal or
      command>` and `@all ...` call the remote `sable --mcp-serve` over SSH
      (`run_command` for bash lines, a remote goal otherwise), results grouped
      per host; a queued (confirm-tier) remote command says to approve it on
      that host.

## Task 6: Incident to runbook (K9)

- [ ] When a goal that started from a failure signal (a watcher, `!` fix, or
      "X is down") ends with a passing verify, draft a runbook (symptom,
      checks, fix, verify) into the palace `incidents` room with provenance.
- [ ] When the same watcher fires again, the push and `/inbox` offer "run the
      runbook", which runs as a plan (rehearsed and policy-gated).

## Task 7: Nightly self-check (K10), no model calls

- [ ] From the audit ledger, token events and skill stats: goals completed,
      steps and spend with and without a matched skill, skills that started
      failing, and the latest eval run if any. Weekly summary on the bus, in
      `/dash`, and as one push. Regressions go to the skill doctor.

## Task 8: `sable share` (K12)

- [ ] `sable share --approve-only [--ttl 1h] [--name NAME]`: each pending
      inbox item is pushed to the share topic with single-use, expiring
      approve and reject links; the approver's name is recorded in the audit.
- [ ] `sable share --read-only`: a text snapshot of `/dash` every 5 minutes to
      a private topic until the TTL ends. `sable share --stop`.

## Task 9: Docs and the live gate run

- [ ] `docs/ecosystem.md`; `scripts/gate_phase9.py` (benchmark lines on the
      mock backend; other lines as in earlier gates); runs recorded in
      `docs/history/`; `ROADMAP.md`; CHANGELOG; the docs site.

Order: Task 0 alone; then Tasks 1, 2, 3, 4, 5, 6, 8 in parallel; Task 7 after
Tasks 0 and 1; then Task 9.
