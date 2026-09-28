# Phase 5 Implementation Plan: Autonomy (v0.9)

> Checkbox tasks, failing-test-first, one PR per task, a live gate run at the
> end. Style follows the Phase 3, 3.5 and 4 plans.

**Goal:** the server does useful work while you are not logged in, and tells
you about it. Nothing runs unattended that policy would not run unattended.

**Deliverables:** E1, E2, E3, E5, E6.

**Gate (from `docs/roadmap-phases.md` Phase 5, adjusted for the decisions below):**

```
/schedule "every 2 minutes write the date to ~/heartbeat.log" -> approve once; /exit; wait 5 min; cat shows >= 2 lines
/schedule "nightly prune docker images" -> the plan has `docker image prune` (confirm tier) -> the job lands in /inbox, never runs on its own
/inbox lists it; /inbox approve N runs that one step once
/watch disk / 90 -> a fake full disk fires a notify-only event
Kill the daemon mid-job -> restart -> reconcile marks the run lost, schedules resume
Set the ntfy topic -> the phone gets "job X done"; an approval push, answered from the phone, approves exactly one pending item
A replayed or expired approval token is refused and logged
```

---

## 0. Decisions (asked and answered 2026-09-28)

| Question | Decision |
|---|---|
| How does `sabled` run? | **systemd user unit.** `sable daemon install` writes `~/.config/systemd/user/sabled.service` and prints the `loginctl enable-linger` hint; `sable daemon run` is the same loop in the foreground, used by the playground entry script and hosts without systemd. |
| How are schedules parsed? | **The model drafts a 5-field cron expression; a stdlib matcher runs it.** No `croniter`. Rows live in SQLite, not `schedules.yaml` as `structure.md` sketched, so no YAML parser is added and the daemon reads one store. |
| Notification channel | **ntfy** over plain `httpx` POST. Slack, Telegram and email are later adapters behind the same `send()` call. |
| Phone approval auth | **Single-use token, outbound only.** Each approval push carries a random 128-bit token (`secrets.token_urlsafe(16)`), valid 1 hour, used once. The push's action button publishes `yes <id> <token>` to a private reply topic the daemon subscribes to; no port is opened on the server. The ntfy access token is stored in the keyring. Only `confirm`-tier items can be approved remotely; `never` stays never. |
| New dependencies | **None.** |

### 0.1 A scheduled job runs its approved plan, not a model

The model is used once, at `/schedule` time, to turn the sentence into
`{cron, plan, summary}`. You approve that exact plan. At run time the daemon
executes those commands verbatim through the policy engine; no model call
happens unattended, so a job cannot drift, cost money or be prompt-injected
by output it reads. A plan step that policy rates `confirm` is queued to
`/inbox` and the run waits there; `never` fails the run. This is stricter than
the roadmap's "spawn a worker", and it is the safer reading of
`structure.md` rule 5 (daemon = `allow` tier only, everything else queued).
Model-driven jobs can come later, behind the same queue.

### 0.2 One store, one surface

- `schedules`, `job_runs`, `watchers`, `approval_tokens` are new tables in the
  existing WAL database, created by their modules like `policy_queue` is.
- Everything waiting on a human is read from the tables that already exist
  (`policy_queue` pending, open `breaker_trips`) plus the new ones. `/inbox`,
  the sidebar INBOX count and `/dash` read it through `sable/ui/state.py`.
- The daemon publishes `job.started`, `job.finished`, `job.queued`,
  `watch.fired`, `notify.sent`, `approval.remote` on the event bus, so `/dash`
  and `/audit` see daemon work with no new UI code.

### 0.3 Not in scope

E4 self-healing runbooks, C4 environment fingerprint (`/env`), inbound
webhooks (would need an open port), Slack/Telegram/email adapters,
model-driven scheduled jobs, the nightly skill health pass (a later task can
schedule it through E2 once E2 exists).

---

## Task 0: `sabled` foundation (E1, serial, first)

Files: `sable/daemon/service.py`, `sable/daemon/jobs.py`, `sable/app/main.py`
(subcommand only), `scripts/playground.sh` entry.

- [ ] Failing tests: a tick with no work does nothing; a second daemon refuses
      to start (`fcntl.flock` on `~/.sable/sabled.lock`); `run_plan` runs `allow`
      steps, queues a `confirm` step and stops, fails on `never`; every step is
      audited as `write_command("daemon", ...)`.
- [ ] `sable daemon run|install|status|stop`. `install` writes the unit
      (`Restart=on-failure`, `ExecStart=<python> -m sable.app.main daemon run`),
      never runs `systemctl` itself, prints the two commands to run.
- [ ] Loop: one tick every 5 s (sleep to the next boundary, not drift). A tick
      calls registered handlers; T1 and T2 register theirs. One handler raising
      is logged and does not kill the loop.
- [ ] `jobs.run_plan(name, steps)`: headless, uses the existing pty runner with
      a timeout per step, records a `job_runs` row (`running`, `ok`, `failed`,
      `waiting`, `lost`), publishes events.
- [ ] Startup calls `reconcile` and marks `running` rows from a dead daemon
      `lost`.
- [ ] Playground entry script starts `sable daemon run` in the background.

## Task 1: `/schedule` NL cron (E2)

Files: `sable/daemon/schedule.py`, `sable/daemon/cron.py`,
`sable/app/builtins/schedule.py`, `sable/llm/prompts/schedule.md`.

- [ ] `cron.matches(expr, datetime)`: 5 fields, `*`, lists, ranges, steps,
      names for months and days. Table test against known cases; bad
      expressions raise `ValueError` with the field named.
- [ ] `/schedule "<sentence>"`: one model call returns
      `{cron, plan, summary}` (JSON fallback chain as in CLAUDE.md); show the
      cron in words, each step with its policy tier; `↵ approve  q cancel`.
- [ ] `/schedule list|pause N|resume N|run-now N|rm N`.
- [ ] Daemon handler: at each minute boundary, due rows run via `run_plan`;
      a job already running is not started twice.

## Task 2: Watchers (E3)

Files: `sable/daemon/watchers.py`, `sable/app/builtins/watch.py`.

- [ ] Kinds: `disk <path> <pct>`, `file <path>` (mtime), `log <path> <regex>`
      (tail from the last offset), `http <url> <status>` (localhost only, as
      `verify` is). Each check is a pure function over injected readers, so
      unit tests need no real disk or network.
- [ ] Tier per watcher: `notify` (default), `run` (an approved plan, via
      `run_plan`, so policy still gates it), `approve` (queues to `/inbox`).
- [ ] Debounce: a watcher fires once per state change, not every tick.
- [ ] `/watch add|list|rm`.

## Task 3: ntfy notifications and phone approvals (E5)

Files: `sable/daemon/notify.py`, `sable/daemon/approvals.py`,
`sable/app/builtins/notify.py`.

- [ ] `notify.send(title, body, actions=())`: `httpx.post` with
      `timeout=httpx.Timeout(30.0)`; failures are logged, never raised into the
      loop. Secrets are redacted from bodies with the existing secret scanner.
- [ ] `/notify setup` asks for server, topic, reply topic and access token;
      the token goes to the keyring, never to config. `/notify test`.
- [ ] Approvals: for each new `policy_queue` item the daemon sends a push with
      an action button that publishes `yes <id> <token>`. The daemon streams
      the reply topic (`GET /<topic>/json`, outbound). A reply approves only if
      the token matches that id, is unused and under 1 hour old
      (`secrets.compare_digest`); the token is then burned. Everything else is
      refused and audited as `approval.remote` with the reason.
- [ ] Tests: good token approves once; replay, wrong id, expired, `never`-tier
      and malformed replies are refused.

## Task 4: `/inbox` (E6)

Files: `sable/app/builtins/inbox.py`, `sable/ui/state.py`.

- [ ] `/inbox`: one list over pending `policy_queue`, open breaker trips,
      waiting `job_runs`, crystallised-skill proposals; each with age and source.
- [ ] `/inbox approve N`, `/inbox reject N`, `/inbox show N`. Approve and
      reject go through `queue.decide_request` and `breaker.reset`, as `/dash`
      does.
- [ ] Approving a waiting job step lets the next daemon tick resume that run
      from that step.

## Task 5: Docs and the live gate run

- [ ] `docs/daemon.md`: install, the unit, ntfy setup, what runs unattended and
      what does not.
- [ ] `scripts/gate_phase5.py` in the playground against gpt-4o-mini; the
      phone line is checked by reading the ntfy topic over HTTP, and marked as
      such. Record every run in `docs/history/phase-5-gate-<date>.md`.
- [ ] `ROADMAP.md` ticks v0.9; CHANGELOG.

Order: Task 0 alone, then Tasks 1 to 4 in parallel worktrees, then Task 5.
