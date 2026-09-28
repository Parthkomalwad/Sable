# Contracts

Every JSON, SQL and file contract in one place. This document describes what the
code **actually parses today**, not what it will parse eventually. Anything not
yet implemented is marked as such, with the phase that implements it.

> Companion to [structure.md](structure.md) (how the code is laid out) and
> [roadmap-phases.md](roadmap-phases.md) (when each piece lands).

---

## 1. The LLM JSON contract

### 1.1 The base schema

`CLAUDE.md` fixes this shape, and it is what every backend parses into an
`LLMResponse` (`sable/llm/base.py`):

```json
{
  "command": "string",
  "explanation": "string",
  "safe": true,
  "plan": null
}
```

| Field | Type | Meaning |
|---|---|---|
| `command` | string | The shell command to run. Empty when there is nothing to run. |
| `explanation` | string | One sentence: what it does and why. |
| `safe` | bool | False when destructive or irreversible. Advisory: `policy/engine.py` decides independently and its answer wins. |
| `plan` | null or array of strings | `null` for a single command; a list for a multi-step plan, executed by `agents/planner.py`. |

Two more fields are parsed by the backends and used by the agent loops:

| Field | Type | Meaning |
|---|---|---|
| `done` | bool | The worker has achieved its goal. `command` should be empty. |
| `spawn` | null or object | `{"name": "<slug>", "goal": "<text>"}`. Delegates to a sub-agent. |

### 1.2 The parse chain

Defined in `CLAUDE.md` and implemented in `llm/base.py:parse_llm_json` and
`agents/runtime.py:parse_json_action`. In order:

1. Strip markdown fences (```` ```json ```` and ```` ``` ````).
2. `json.loads()`.
3. Re-ask the model to extract only the JSON (backends do this; see
   `llm/ollama.py`).
4. Show the raw text and ask the user to rephrase.

A parse failure never raises out of an agent turn. `parse_json_action` returns
the caller's default instead, because an unparseable response is something the
agent has to report and carry on from, not an exception that ends the run. A
response that parses to a non-object (a bare list, a string, a number) counts as
unparseable, since every caller reads keys off the result.

### 1.3 The agent action schema

Agents extend the base schema with an `action` discriminator. One action per
turn.

| action | Emitted by | Status |
|---|---|---|
| `run` | orchestrator, worker | **live** |
| `spawn` | orchestrator | **live** |
| `done` | orchestrator, worker | **live** |
| `wait` | — | **specified, not implemented** (Phase 1, PR4) |
| `ask` | — | **specified, not implemented** (Phase 1, PR4) |
| `tool` | orchestrator, worker | **live** (Phase 3.5, J1) |
| `mcp` | — | **reserved** (Phase 6, D1) |

`agents/orchestrator.py:ORCHESTRATOR_ACTIONS` is the live set. An action outside
it, including `wait` and `ask`, is rejected as unrecognised and stops the loop
with a reason rather than being guessed at.

#### Live actions

**`tool`**: call a registered tool by name (Phase 3.5, J1).

```json
{"action": "tool", "name": "echo", "args": {"text": "hi"}, "explanation": "test the tool path"}
```

Backends put the whole answer in `LLMResponse.raw`; the orchestrator and the
worker read `name` and `args` from there. Models also send the tool's name
as the action with flat arguments, `{"action": "fs.read", "path": ...}`;
`registry.normalize_action` maps that to the same call. An action that is
neither ours nor a tool is reported by name, never turned into `done`. The call is checked in order: the
tool exists, the role may use it, `args` fit its schema, then
`gate("tool:<name> <args as sorted JSON>", floor=<tool tier>)`, which applies
policy and writes the audit row. A policy `[[rule]]` can match that text to
raise a tool's tier; nothing lowers it below the tool's own. Every failure
comes back to the model as text, and a failed call is recorded with
`[exit 1]` so grading and the breaker count it. Each call that runs
publishes one `tool` event (`name`, `args`, `ok`, `duration_ms`,
`result_bytes`, `taints`). The orchestrator previews every call (`↵ run / q
cancel`); a worker is never prompted, so a `confirm`-tier tool is refused for
it. `/tools` lists what each role may call.

**`verify`** (Phase 3.5, J4): an optional field on `run` and `tool` (and on the
worker's command JSON), required by the prompts on any state-changing action.
It is a shell command that must exit 0, or one of:

```json
{"exit": 0}
{"stdout_contains": "ready"}
{"file_exists": "docker-compose.yml"}
{"http_status": {"url": "http://localhost:8080/health", "status": 200}}
```

The runtime checks it after the action succeeds (`agents/runtime.run_verify`).
A verify command goes through `gate()` with the agent's role, like any command.
A string that spells a structured check (`exit: 0`, `exit == 0`,
`stdout_contains: x`, `file_exists: path`) is read as that check, not run: the
Phase 3.5 gate saw gpt-4o-mini send `"verify": "exit: 0"`.
`http_status` accepts only `localhost` / `127.0.0.1` URLs. A failed check is appended to the output the model reads as
`{"verify": "failed", "check": ..., "got": ...}` followed by `[exit 1]`, so
`exit_code_of`, skill grading and the breaker count it as a failure.

**Recovery** (J5). After a failed step the result carries one `[reflect]` note
asking what failed, why and what to try instead. Consecutive failures climb a
ladder: retry, try an alternative, ask (the orchestrator prompts the user; a
worker queues the request in `policy_queue`), then `[step failed]` and the
counter resets. Each failure prints `↻ step failed: <check> (got: ...)` and
`next: <rung>`; the model's next action is shown as `↻ reflection: <explanation>`
and published as a `reflection` event with `{step, rung, check, got, reflection}`.
Verify's `http_status` is localhost only by design, not pending J2: it checks
the user's own services and keeps a model-chosen URL off the network. The same command (whitespace-normalised) or tool call (name
plus sorted args) runs at most twice per goal; the third is refused by the
runtime with a message to the model.

**`run`** — execute one shell command.

```json
{"action": "run", "command": "ls -la", "explanation": "list the files"}
```

**`spawn`** — delegate a self-contained goal to a sub-agent in its own tmux
window. `name` becomes the tmux window name and the directory name, so it must
contain at least one `[a-z0-9]` character; spaces become dashes.

```json
{"action": "spawn", "name": "verify-build", "goal": "run the test suite and report failures", "explanation": "long-running, better delegated"}
```

**`done`** — the goal is achieved. Ends the loop.

```json
{"action": "done", "explanation": "created hello.txt and verified its contents"}
```

#### The worker's variant

`agents/worker.py` predates the `action` discriminator and still parses the
flatter shape. A worker never spawns, so it needs no discriminator to tell
`run` from `spawn`; `done` is a boolean instead.

```json
{"command": "pytest -q", "explanation": "run the tests", "done": false}
```

Unifying the two is deliberate future work, not an oversight: it is a change to
what the model is asked to emit, which means re-tuning both prompts.

#### Specified but not implemented

These are documented so the shape is agreed before anything depends on it.
Neither is parsed today.

**`wait`** — block until a named sub-agent reaches a terminal state, instead of
looping and re-checking. `core/events/bus.py:wait_for` already provides the
mechanism.

```json
{"action": "wait", "agent": "verify-build", "timeout_s": 300, "explanation": "nothing to do until the build finishes"}
```

**`ask`** — put a typed question to the human and block on the answer. Needs the
INBOX surface (E6) to be answerable by a headless worker, which is why it waits.

```json
{"action": "ask", "question": "which database should I migrate?", "kind": "choice", "options": ["staging", "production"], "explanation": "the goal did not say"}
```

`kind` is one of `confirm`, `choice`, `text`, `secret`.

**`mcp`** — reserved for Phase 6. Not specified here; the shape follows the MCP
tool-call contract and will be written up with D1.

### 1.4 Prompt injection framing

The orchestrator wraps the user's goal in `<goal>` tags with an explicit
instruction to ignore anything embedded in the goal text
(`_build_messages`). Phase 3 (I1) extends the same treatment to command output.

---

## 2. The agent event bus

`core/events/bus.py`, table `agent_events`. Append-only. Ordering is by `id`,
never `ts`: the timestamp is for humans and ties at second resolution.

```sql
CREATE TABLE agent_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,          -- ISO 8601, human-facing only
    agent        TEXT NOT NULL,          -- the task name
    kind         TEXT NOT NULL,          -- see below
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_agent_events_agent_id ON agent_events (agent, id);
```

### 2.1 Kinds

Plain strings, not an enum: they cross process boundaries and an older reader
(the sidebar) must survive a newer runtime publishing a kind it has not heard
of. An unknown kind is carried, not rejected.

| kind | Published when | Payload |
|---|---|---|
| `spawned` | a sub-agent process was launched | `{goal}` |
| `started` | it reached its first turn | `{goal, workspace, sandbox}` |
| `status` | once per step | `{step, command, explanation, output}` (output capped at 2000 chars) |
| `completed` | the goal was achieved | `{steps, explanation, result}` |
| `failed` | it gave up | `{reason, steps}` |
| `lost` | reconcile found its window gone | `{reason, steps}` |
| `turn` | one model turn, for replay | see §3 |
| `command` | a command was executed | `{command, exit_code}` |
| `guidance` | a human steered it (Ctrl+G) | `{text}` |
| `skill_used` | a skill was injected into an agent's context | `{skills: [{name, confidence}]}`, plus `{step}` from a worker |

`completed`, `failed` and `lost` are terminal: `EventKind.TERMINAL`. After one
of them, no further events are expected from that agent.

### 2.2 Reader contract

A reader owns an integer cursor and polls `WHERE id > ?`. That is what replaced
the old read-then-unlink handshake on `result.md`, which lost the result
outright if the reader died between the read and the unlink.

Two rules callers depend on:

- **Start the cursor at `latest_id()`, not 0**, when you only want this run's
  events. Re-running a goal reuses the slug *and* the agent names, so a cursor
  at 0 folds the previous run's `completed` into the new run's first turn.
- **Capture the cursor before spawning** when you intend to `wait_for` an agent.
  A fast agent can finish before the wait starts; with an earlier cursor the
  wait sees the whole history and returns at once.

`publish()` returns the new row id, or `None` if the write failed. It never
raises: an agent reporting its own progress must not die because the report
failed. Reads propagate errors normally, since a caller tailing the bus wants to
know the tail is broken.

### 2.3 Human-readable mirrors

`~/tasks/<slug>/<name>/.agentic/status.md` and `result.md` are still written.
Since Phase 1 nothing reads them back; they exist so `cat` still explains the
system when the UI is gone.

---

## 3. Prompt replay

`agent_turns`, written once per model turn. Backs `/task replay <name>` and
`/why`.

```sql
CREATE TABLE agent_turns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    agent         TEXT NOT NULL,   -- task name, or "orchestrator"
    role          TEXT NOT NULL,   -- orchestrator | worker
    turn          INTEGER NOT NULL,-- 1-based within this run
    model         TEXT,
    system_prompt TEXT NOT NULL,
    messages_json TEXT NOT NULL,   -- the exact message list sent
    response      TEXT NOT NULL,   -- the raw text that came back
    prompt_tokens      INTEGER NOT NULL DEFAULT 0,
    completion_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX idx_agent_turns_agent_id ON agent_turns (agent, id);
```

**Everything stored is redacted first.** `system_prompt`, every message body and
`response` pass through `policy/engine.py:strip_secrets`, so a command carrying
an API key does not put that key in the replay table. Redaction happens on the
way in, not on the way out: the table itself must be safe to read and to export.

Writing a turn never raises, for the same reason publishing an event does not.

---

## 4. Config

`~/.config/agentic-shell/config.json`, parsed by
`core/config/schema.py:ShellConfig.from_dict`. Mode 600.

```json
{
  "backend": "ollama",
  "model": "llama3.1",
  "api_base": "http://localhost:11434",
  "routing_mode": "auto",
  "daily_token_budget": null,
  "session_token_budget": null,
  "privacy_mode": false,
  "setup_complete": true,
  "tasks_base_dir": "~/tasks",
  "models": {}
}
```

`backend` is one of `ollama`, `openai`, `anthropic`, `custom`; an unrecognised
value raises, a missing one defaults to `ollama`. `routing_mode` is `auto` or
`prefix`. Budgets are a positive integer or `null`.

### 4.1 Per-role models (A5)

`models` maps a role to a model name. Any role left unset falls back to `model`,
so a config written before Phase 1 behaves exactly as it did.

```json
"models": {
  "router": "llama3.1",
  "orchestrator": "claude-sonnet-5",
  "worker": "claude-sonnet-5",
  "summariser": "claude-haiku-4-5"
}
```

| Role | Read by | Status |
|---|---|---|
| `orchestrator` | `agents/orchestrator.py` | **live** |
| `worker` | `agents/worker.py` | **live** |
| `summariser` | `skills/crystalliser.py` | **live** |
| `router` | — | **accepted and stored, nothing reads it** |

`router` is accepted because the roadmap (§3.3) names it and a model-backed
router is later work, but `agents/router.py` is pure heuristics and makes no LLM
call. Setting it changes nothing today. It is listed rather than rejected so a
user who sets it early does not lose the value.

An unknown role name raises, so a typo is caught at startup rather than silently
falling back.

All model selection goes through `llm/registry.py:build_backend(config,
role=...)`, the single place backends are constructed.

---

## 5. The audit log

Two formats, both pre-existing. Neither may change shape without updating its
reader.

**`~/.local/share/agentic-shell/audit.log`** — written by
`core/audit.py:write_command`, parsed by `skills/watcher.py` to find repeated
command clusters. Tab separated:

```
<ISO 8601>\t<session_id>\t<cwd>\t<command>
```

**`/var/log/agentic-shell/audit.log`** — written by `core/audit.py:write_action`,
the system-wide ledger:

```
<ISO 8601> user=<name> action=<action> cmd=<repr> exit=<code>
```

Both swallow permission errors: an unwritable audit log must never take the
shell down.

---

## 6. Task files on disk

```
~/tasks/<slug>/                      orchestrator's shared dir, created on first spawn
  .agentic/
    result.md                        orchestrator's own summary
  <agent-name>/
    workspace/                       the agent's R/W sandbox; all its commands run here
    .agentic/
      goal.txt                       the goal, passed as --goal-file to avoid shell injection
      handoff.txt                    orchestrator context, consumed and deleted on first read
      status.md                      live status mirror
      result.md                      final summary mirror
      memory/vN.json                 TaskMemory snapshots
```

The goal travels as a file rather than an argv string because `spawn` sends it
through tmux `send_keys`, where any shell metacharacter in the goal would be
interpreted.

---

## 7. Policy rules

`policy/defaults/policy.toml`, loaded by `policy/rules.py`. TOML rather than the
YAML named in structure.md §2: `tomllib` is stdlib from Python 3.11, which this
project already requires, and the file gates every command, so it was not worth a
third-party parser.

```toml
[[destructive]]
name = "rm-rf"
pattern = "rm\\s+(-[a-zA-Z]*[rf][a-zA-Z]*\\s+)+"
category = "filesystem"
why = "recursive force delete removes data with no undo"
tier = "confirm"       # allow | confirm | deny; missing means confirm
```

`name`, `pattern`, `category` and `why` are all required in the shipped file.
`why` is what the confirm block shows, so the user sees what a rule protects
against instead of a regex.

**One path decides.** `engine.decide(command, *, tainted=False) -> Decision`
returns the tier, the rule, its reason and the file it came from.
`engine.gate(command, *, role, approved=False, tainted=False, agent=None,
model=None, goal=None) -> bool` is the only thing that acts on it: run, ask for
`YES`, queue, or refuse, then the `pre_command` hook, then one audit row. Every
path that runs a command calls `gate()` first; `test_every_repl_bash_path_is_gated`
and `test_no_legacy_policy_callers` enforce that. `role` is `user`,
`orchestrator` or `worker`; a worker is never prompted.

The order inside `decide()`: floor and user rule (§7.1), then the `sudo` floor
from `policy/privilege.py`, then the taint bump (§7.4). Unmatched is `allow`.

A missing or malformed policy file **raises at import**. It does not degrade to
an empty list, because an empty blocklist is a shell that runs `rm -rf /`
without asking.

### 7.1 Layers (Phase 3)

Three files, each optional except the first:

| File | Section | Malformed |
|---|---|---|
| `sable/policy/defaults/policy.toml` | `[[destructive]]` | raises |
| `/etc/sable/policy.toml` (admin) | `[[rule]]` | raises: the floor must not vanish on a typo |
| `~/.sable/policy.toml` (user) | `[[rule]]` | warns on stderr, then ignored |

```toml
[[rule]]
name = "no-rm-rf"
pattern = "^rm -rf"
tier = "deny"          # allow | confirm | deny; missing means confirm
why = "optional; a fallback names the rule and file"
```

The **floor** is the admin file's first match, else the defaults' first match.
A user rule wins only when its tier is strictly more severe
(`allow < confirm < deny`), so a user `allow` never loosens a floor
`confirm`. `sudo` as a command word is always at least `confirm`, from
`policy/privilege.py`, whatever any file says. So is any command or tool call that names a
credential file (`~/.ssh` private keys, `~/.aws/credentials`, `~/.gnupg/`, `.netrc`,
`.pgpass`, `.git-credentials`, docker and kube configs, Sable's own config,
`/etc/shadow`, `/etc/sudoers`); public keys and `known_hosts` are not included. `Decision.source` names the file
whose rule won, or `built-in` for `sudo`.

### 7.2 Hooks (Phase 3)

Executables in `~/.sable/hooks/`, named after the event. Same contract as
Claude Code's hooks.

| Hook | When | Payload (besides `hook`) | Exit 2 |
|---|---|---|---|
| `pre_command` | after policy allows or you confirm, before running | `command`, `role`, `tier`, `rule`, `cwd` | blocks the command |
| `post_command` | after a command runs | `command`, `role`, `cwd`, and `exit_code` (typed) or `output` (orchestrator) | nothing to block |
| `pre_spawn` | before a sub-agent starts | `name`, `goal`, `cwd` | blocks the spawn |
| `on_skill_use` | when skills are injected | `skills`, `cwd` | nothing to block |

Stdout that parses as a JSON object may carry `message` (shown to the user)
and `context` (for `post_command` in the orchestrator, appended to the output
the model reads). Other stdout is shown as text. Exit 0 allows; any other
non-zero exit, or running past 10 s, is reported on stderr and does **not**
block. A hook never sees a command policy refused, so it cannot loosen one.

### 7.3 Approval queue (Phase 3)

`policy_queue` (`sable/policy/queue.py`): `id`, `created_at`, `agent`, `command`,
`rule`, `why`, `status` = `pending | approved | rejected | used`. A worker
enqueues a `confirm` command instead of prompting; one pending row per
agent and command. `/approve <id>` sets `approved`; the worker's next proposal
of exactly that command by that agent consumes it (`used`), so an approval
runs once. `deny` is never enqueued, and `gate(approved=True)` never lifts it.

### 7.4 Taint (Phase 3)

`sable/policy/taint.py`. Every command output the orchestrator or a worker
sends back to the model is `wrap_untrusted(output)`: the fixed line in
`taint.FRAMING`, then `<output untrusted="true">`, the output, `</output>`. A
literal `</output>` inside the output is escaped to `&lt;/output&gt;`, so the
frame has exactly one close.

`is_tainting(command, cwd)` is true for `curl`, `wget` or `mcp` in command
position anywhere in a pipeline, for `cat`/`less`/`more`/`head`/`tail` of a
path resolving outside `cwd`, and for a command that does not tokenise. Once a
tainting command has run, the agent is tainted for the rest of its goal
(orchestrator) or task (worker); a new typed goal starts clean.

`decide(command, tainted=True)` and `gate(..., tainted=True)` move the tier one
step: `allow` -> `confirm`, `confirm` -> `deny`, `deny` stays. `Decision.why`
starts with `tainted context`. A bumped `allow` carries a stand-in rule named
`tainted-context` (source `taint`). Taint never blocks a turn; it only makes
acting on what was read cost a human's YES. `tests/evals/injection/` pins it:
30+ hostile outputs, a model that obeys them, zero commands executed.

### 7.5 Circuit breaker (Phase 3)

Config: `per_job_budget` = `{tokens, usd, turns, wall_s}` (any key absent or
null is unlimited; `{}` is the default) and `breaker_consecutive_failures`
(null = off). Checked by the orchestrator and every worker **before** each
turn, never during one: a running command is not killed. A trip writes a row
to `breaker_trips` (`sable/policy/breaker.py`): `id`, `created_at`, `job`,
`reason`, `status` = `tripped | reset`, and publishes a `breaker` event on the
bus. The job that tripped stops (`lost`, `FAILED`). While any row is
`tripped`, every other sub-agent **pauses** before its next turn: it prints
`[breaker] paused: ...`, sets its task `paused`, publishes a `status` event
with `paused: true`, and polls the table every 5 s with no model call and no
spend; paused time does not count toward `wall_s`. It resumes, back to
`running`, when no trip is open. The interactive orchestrator never pauses on
another job's trip and stops only on its own goal's limits. `/breaker`
lists open trips; `/breaker reset` sets them `reset`. Phase 5's `/inbox` reads
this table.

**Tool budgets (Phase 3.5, J12).** Config `tool_budgets` maps a tool name or
a prefix to `{max_calls_per_goal, max_bytes, max_cost}` (any key absent or
null is unlimited; `{}` is the default). A key covers that tool and every tool
under it: `web` counts `web.search` and `web.fetch`, not `webby`. Counts live
on the job's `Breaker`, so they reset with each goal (orchestrator) or task
(worker). `registry.call()` asks the breaker through `ToolContext.budget`
after the args are validated and before `gate()`: a call that would exceed
`max_calls_per_goal`, or whose budget is already spent, never runs and
returns `[breaker: tool <name>: <key>.<limit> limit reached ...]` to the
model. `max_bytes` and `max_cost` are known only afterwards: the call that
crosses `max_bytes` has its output cut to what the budget had left, and the
call that crosses `max_cost` (from `ToolResult.cost_usd`) keeps its output.
Any of the three trips the breaker at once: a `breaker_trips` row with the
tool named in `reason`, shown by `/breaker`, and the job stops before its next
turn. A call refused by policy after passing the budget still counts.

### 7.6 Blast radius (Phase 3, F3)

`sable/policy/blast.py`: `classify(command) -> Level`, one of `read-only`
(green), `writes` (amber), `destructive` (red), `unknown` (dim). Order: a
matched non-`allow` rule's `category` (every shipped category is
`destructive`; an unrecognised one is too); else a static per-segment
heuristic (known read-only programs, known writers, output redirects); else
`_model_level`, cached by SHA-256 of the command. That seam returns `unknown`
without a model call today, and must never return `read-only` on failure.
Shown on the orchestrator's preview header and on the `YES` confirm block.

### 7.7 Audit ledger (Phase 3, F4)

Every `gate()` decision writes one row to `audit_ledger` in `sessions.db`;
§5's `audit.log` line is unchanged and still what the skill watcher parses.

| column | meaning |
|---|---|
| `ts`, `uid` | UTC ISO time; `os.getuid()` (NULL where there is none) |
| `agent`, `role`, `model` | who: agent name (defaults to role), `user`/`orchestrator`/`worker`, model id |
| `goal`, `tier`, `rule`, `why` | why: the goal it served, and the policy decision |
| `command`, `cwd` | what: `command` and `goal` pass through `strip_secrets` first |
| `outcome` | `allowed`, `confirmed`, `approved`, `refused`, `unconfirmed`, `hook_blocked` |
| `exit_code`, `duration_ms` | filled by `core.audit.finish()` after the command runs; agents record duration only, `exit_code` stays NULL |

`/audit [--since 30m|1h|2d] [--agent NAME]` prints the rows as a table.
`--export jsonl` writes them to `~/.sable/audit/audit-<stamp>.jsonl` and
prints that path. A ledger write never raises.

### 7.8 Secret broker (Phase 3)

`sable/policy/secrets.py`. A command may contain `$SECRET:<name>` (`name` =
`[A-Za-z_][A-Za-z0-9_]*`). The model, preview, `gate()`, audit, event bus and
`agent_turns` see only the placeholder; resolution happens in the runner
(`agents/runtime.run_command`, `agents/planner`). Each placeholder is rewritten
to `"${SABLE_SECRET_<name>}"` (name case kept, so `db_pass` and `DB_PASS` never
share a variable; bare `${...}` inside double quotes) and the value
is passed in the child's environment, never in the command string or script
file. Inside single quotes, an unknown name, or an unavailable keyring: the
command does not run and the model reads `[blocked: <reason>]`. No plaintext or
env fallback. Resolved values in output are replaced by their placeholder
before the model or memory sees it. Storage: keyring item attributes
`{application: "agentic-shell", service: "secret:<name>"}`
(`core/config/keyring.py`). `/secret add <name>` (no echo), `/secret list`
(names only), `/secret rm <name>`. Raw bash lines typed by the user are not
resolved.

### 7.9 Not implemented

| Item | Status |
|---|---|
| F2: dry-run filesystem diff before a plan runs | Not implemented. Phase 8 (K5 rehearsal). |
| F5: network and cgroup limits on sub-agents | Not implemented. Phase 8. |
| `/policy explain "<cmd>"` | Not implemented. The confirm block shows the rule, reason and tier; `/audit` shows past decisions. |
| Model-assisted blast radius | A seam in `policy/blast.py` that returns `unknown`. |

What Phase 3 does not protect against is listed in
[THREAT_MODEL.md](THREAT_MODEL.md) §5.

---

## 8. Skills, corrections and aliases

Phase 2 (B1, B2, B3, B5, K3, K4). Four contracts: the `SKILL.md` file, the
`skills_index.json` entry, and two SQLite tables.

### 8.1 `SKILL.md`

`~/skills/<slug>/SKILL.md`, parsed and rendered by `skills/model.py`, which is
the only module that knows the format. Frontmatter is **TOML in a `+++` fence**,
followed by a blank line and a markdown body.

```markdown
+++
name = "deploy-api"
description = "build, push and restart the api container"
triggers = ["deploy", "api", "release"]
preconditions = ["docker is running"]
validate = "curl -sf localhost:8080/health"
status = "pending"
source = "crystallised"
+++

1. `docker build -t api .`
2. `docker push registry/api`
3. `docker compose up -d api`
```

| field | type | required | meaning |
|---|---|---|---|
| `name` | string | yes | identity; matches the folder slug and the index entry |
| `description` | string | yes | what `/skill list` and ranking show |
| `triggers` | list of strings | no | keywords the index matches a goal against |
| `preconditions` | list of strings | no | stated in the body's context, not enforced |
| `validate` | string | no | a shell command B5 runs to grade a use |
| `status` | `pending` \| `enabled` \| `disabled` | no, default `pending` | only `enabled` is ever injected |
| `source` | `user` \| `crystallised` \| `imported` | no, default `user` | Phase 8's K8 sets a trust floor by source |

Four decisions the format depends on:

- **TOML, not YAML.** CLAUDE.md's approved dependency list has no YAML parser and
  `tomllib` is stdlib. The fence is `+++` rather than `---` so a reader never
  mistakes it for YAML frontmatter that happens to parse: `triggers = ["a"]` is
  valid in both and means the same thing, which is how a format drifts into
  being half-YAML by accident.
- **`status` defaults to `pending`.** The value that stays inert is the safe one
  to assume when a field is absent.
- **Unknown keys survive** in `Skill.extra` and render back verbatim. B7 imports
  skills written against a later contract; a parser that dropped what it did not
  understand would corrupt them on the first `/skill edit`.
- **A malformed skill raises `SkillFormatError`**, naming the file, rather than
  yielding a half-built `Skill` that injects an empty body. Unlike the policy
  file this does not exit: a missing blocklist means running `rm -rf /` unasked,
  a missing skill just means doing the work by hand.

**Legacy flat files.** `~/skills/instructions/<slug>.md` is the pre-B2 shape:
bare markdown, no frontmatter. It still parses, taking its name from the
filename, its description from the first heading, and `status = "enabled"` —
a skill that already worked stays working, and sending every existing skill to
`pending` would be indistinguishable from the migration losing them.
`skills/migrate.py` copies them into the folder layout and keeps the originals.

### 8.2 `skills_index.json`

`~/skills/skills_index.json`, a JSON array managed by `skills/index.py`. Created
empty on first use.

```json
{
  "name": "deploy-api",
  "file": "/home/u/skills/deploy-api/SKILL.md",
  "keywords": ["deploy", "api"],
  "auto_generated": true,
  "confidence": 0.55,
  "use_count": 2,
  "last_used": "2026-09-13T09:41:07+00:00",
  "needs_update": false,
  "status": "enabled",
  "created_at": "2026-09-12T18:02:55+00:00"
}
```

`status` is stored in **both** the file and the index, and `_set_status` writes
the file first. The index is what `/skill list` and the startup pending-count
read cheaply; the file is what keeps the gate honest when the index is deleted.
A crash between the two writes leaves the skill withheld, which is the safe
direction to fail.

An entry written before Phase 2 has no `status` key. **A missing status reads as
`enabled`**, for the same reason a legacy flat file does.

**Confidence.** Starts at 0.5 auto-generated, 1.0 manual. `nudge(name, success)`
moves it +0.05 on success, -0.1 on failure, clamped to [0.0, 1.0]. Approval does
not touch it: approval says "you may run this", not "I vouch for it".

**Ranking is a product, not a sort**: `confidence x recency x use_count x match`.
Every factor is floored above zero (`recency >= 0.25`, `use_weight >= 1.0`), so a
newly approved skill with no uses can still be retrieved and earn its first
success. Recency decays with a 30-day half-life; `use_weight` is
`1 + sqrt(use_count)`, so a much-used skill cannot drown out a better match;
`match` is the fraction of the goal's keywords the skill claims, and a zero match
excludes the skill outright. `get_ranked()` returns **only `enabled` entries** —
that single filter is the whole of the approval gate. Ties sort by index position
so an announcement does not name a different skill each run. The weights are a
heuristic; tests pin orderings, not scores.

### 8.3 `skill_corrections` (K3)

```sql
CREATE TABLE skill_corrections (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,             -- ISO 8601
    kind      TEXT NOT NULL,             -- 'edit' | 'route'
    proposed  TEXT,                      -- what the model offered
    corrected TEXT,                      -- what the user ran instead
    withheld  INTEGER NOT NULL DEFAULT 0 -- 1 = redaction fired, both sides NULL
);
CREATE INDEX idx_skill_corrections_withheld_ts
    ON skill_corrections (withheld, ts);
```

`kind` is `edit` (an `e`-edit at the confirm prompt) or `route` (a `[b/a]`
answer, which still writes its TSV corpus row as well). Plain strings for the
same reason `EventKind` uses them.

**Rows that are kept are byte-exact.** §3's redaction rule is not applied
literally here: `policy/engine.py:strip_secrets` ends in `" ".join(tokens)` and
so normalises whitespace, which is harmless for prose and corrupting for a
command — `awk -F'\t'` comes back altered. A pair whose redaction fires anything
is therefore **withheld entirely**: `withheld = 1`, both text columns NULL. The
count is kept so `/corrections` can say why the number is lower than expected.

`record_correction` never raises and returns False without writing when the
correction is empty, changed nothing of substance, or carried a secret.

### 8.4 `skill_aliases` (K4)

```sql
CREATE TABLE skill_aliases (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    phrase           TEXT NOT NULL,        -- as the user typed it
    normalised       TEXT NOT NULL UNIQUE, -- lowercased, depunctuated, collapsed
    command          TEXT NOT NULL,
    use_count        INTEGER NOT NULL DEFAULT 0,
    promoted_offered INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
);
```

Matched in `app/repl.py` **before** `classify()`, so a hit costs no model call;
anywhere further in and it would already have cost a turn. Matching is
`difflib` over the normalised form, threshold **0.9** — high on purpose, since
silently running the wrong command because a sentence looked a bit like a stored
phrase is far worse than retyping. `normalised` is UNIQUE: re-adding a phrase
replaces its command, because two rows for one phrase make matching ambiguous.

**An alias is not a safety bypass.** Resolving one yields a command string and
nothing else; the caller runs it down the ordinary bash path where
`is_destructive` gates it. `rm -rf` behind a friendly phrase still asks.

**Promotion is offered, never taken.** At `use_count >= 3`
(`PROMOTION_THRESHOLD`) `should_offer_promotion` reports that the alias is worth
turning into a skill; `mark_promotion_offered` records that we asked, so an
ignored offer does not nag on every later use.

### 8.5 Crystallisation (B3)

A run that ends `done` with >= 3 executed steps, or a `PatternWatcher` pattern
crossing its 3x threshold, asks the summariser model for a reusable procedure.
The draft is written with `status = "pending"` and `source = "crystallised"`,
and is withheld from `get_ranked()` until a human runs `/skill approve`. Nothing
a model drafted unattended reaches another model's context without that step.

### 8.6 Not implemented

**B6, semantic skill search.** Retrieval is keyword `match` against `triggers`
and `keywords` only. Embedding-based retrieval is scheduled for a later phase
and nothing depends on it today.

---

## 9. Tools

The tool interface is `sable/tools/base.py` (`Tool`, `ToolContext`,
`ToolResult`); every call goes through `registry.call()`, which gates it with
the tool's tier as a floor. A tool may set `preview(args, ctx) -> str`: the
orchestrator's confirm block shows it under the call instead of the raw JSON
args. A preview that raises `ToolError` shows the error instead.

### 9.1 Every tool at a glance

| Tool | Tier (floor) | Taints | Notes |
|---|---|---|---|
| `echo(text)` | allow | no | tests the tool path |
| `web.search(query, k?)` | allow | yes | DuckDuckGo by default; `tools.web.search_provider` |
| `web.fetch(url, max_bytes?)` | allow | yes | public http(s) only; SSRF-checked per hop |
| `fs.read(path, start?, end?)` | allow | outside the root only | orchestrator may read outside (tainted); worker may not |
| `fs.write(path, content)` | confirm | no | diff preview; atomic |
| `fs.patch(path, diff)` | confirm | no | diff preview; applies cleanly or not at all |
| `fs.search(glob, regex?)`, `fs.tree(path?, depth?)` | allow | no | capped, skips `.git`, `node_modules` |
| `docs.man`, `docs.help`, `docs.tldr` | allow | no | local lookups; `docs.help` sandboxed |
| `docs.pkg(name)` | allow | npm only | apt-cache and pip are local |

Taint raises every tier one step for the rest of the goal, so after a web
tool each further tool call or command asks for `YES` (a worker is refused).
`/tools` prints this list for the running version.

### 9.2 web.search and web.fetch

Module: `sable/tools/web.py` (HTML to text in `sable/tools/html_text.py`).
Both are tier `allow` and both return `taints=True`, so the caller wraps the
output as untrusted and the agent is tainted for the rest of the goal.

**`web.search(query: string, k?: integer)`** returns up to `k` results
(default 5, at most 20) as numbered text:

```
1. <title>
   <url>
   <snippet>
```

The provider is config `tools.web.search_provider`:

| Provider | Needs | Notes |
|---|---|---|
| `duckduckgo` (default) | nothing | `https://html.duckduckgo.com/html/?q=`, parsed with `html.parser` |
| `searxng` | `tools.web.searxng_url` | `GET <url>/search?format=json`; the URL is trusted config, not SSRF checked |
| `brave` | keyring service `brave` | refused with a message if the key is absent |
| `tavily` | keyring service `tavily` | refused with a message if the key is absent |

An unknown provider in the config file fails config validation.

**`web.fetch(url: string, max_bytes?: integer)`** returns
`url: <final url>`, optional `[note]` lines (size cap hit, truncated, plain
http), a blank line, then the page text. At most `max_bytes` (default
200 000) are read and the text is cut to 16 000 characters. `text/html` and
XHTML are converted to text; other `text/*` and `application/json` pass
through; anything else is refused. Results are cached by URL for 15 minutes
in the process.

URL safety, checked before any request and again on every redirect hop
(at most 5, followed by hand):

- scheme must be `http` or `https` (`file://`, `ftp://` refused);
- the host is resolved and **every** address must be public
  (`ipaddress.is_global`) and not a metadata address (`169.254.169.254`,
  `fd00:ec2::254`, `100.100.100.200`), so private, loopback, link-local,
  shared and unique-local ranges are denied, including a DNS name that
  points at one;
- the connection goes to the checked IP with the original `Host` header and
  TLS SNI, so a DNS answer that changes after the check is not used.

This is stricter than vision J2's `confirm` for localhost and raw IPs: a
tool's tier is per tool, so a private target is denied, not confirmed.

The orchestrator lists every page `web.fetch` read in the goal when it
finishes: `read N page(s): <url>, ...`.

### 9.3 fs tools

`sable/tools/fs.py` (J3). Both roles see all five; a worker is never
prompted, so the two `confirm` tools are refused for it by `gate()`.

| tool | args | tier | taints | blast |
|---|---|---|---|---|
| `fs.read` | `path`, `start?`, `end?` (1-based, inclusive) | allow | only for an outside path (orchestrator) | read-only |
| `fs.write` | `path`, `content` | confirm | no | writes |
| `fs.patch` | `path`, `diff` (unified) | confirm | no | writes; destructive if the diff removes lines |
| `fs.search` | `glob`, `regex?` | allow | no | read-only |
| `fs.tree` | `path?`, `depth?` (default 2) | allow | no | read-only |

- **Confinement.** Paths are joined to `ctx.cwd` (orchestrator cwd, worker
  workspace) and resolved with `os.path.realpath`. A result outside that root,
  by `..`, an absolute path or a symlink, is a `ToolError`. The one exception:
  the orchestrator may `fs.read` an outside file, and the result has
  `taints=True`, matching `cat` of an outside file in `policy/taint.py`. A
  worker's outside read is refused. `fs.search` drops any match that resolves
  outside the root.
- **Writes are atomic.** New content goes to a temp file in the target's
  directory, then `os.replace`. The parent directory must exist. An existing
  file keeps its mode.
- **Patches.** Every hunk's context and removed lines must match the file
  exactly; a hunk may sit at another line than its header says (models
  miscount) but never before the previous hunk. Any mismatch, or a diff with
  no `@@` hunk, is a `ToolError` and the file is untouched. One file per call;
  `---`/`+++` headers are ignored.
- **Previews.** `fs.write` and `fs.patch` preview a coloured unified diff
  against the current file.
- **Caps.** Files over 2 MB are refused by `fs.read` and `fs.patch`; output is
  cut at 100 KB; `fs.search` stops at 200 results, `fs.tree` at 500 entries.
  `.git`, `node_modules`, `__pycache__` and `.venv` are skipped by search and
  not descended by tree.

### 9.4 docs tools

`sable/tools/docs.py` (J7). All four are tier `allow` and `taints=False` (local
documentation, plan section 0.3), with one exception: a `docs.pkg` answer from
`npm view` is registry metadata anyone can author, so it has `taints=True`
(`apt-cache` and `pip show` are local and stay untainted). All cap output at 16,000 characters with a
`[truncated: N more chars]` marker. They run argv lists through
`subprocess.run` with a 5 second timeout, never a shell and never a pty.

| Tool | Args | Runs |
|------|------|------|
| `docs.man` | `cmd`, `section?` | `man -P cat [section] cmd`, `MANWIDTH=80`, backspace overstrike stripped |
| `docs.help` | `cmd` | `<path> --help` only (never `-h`: `shutdown -h` halts); inside `bwrap` (read-only root, tmpfs `/tmp`, `/run` and `/var/run`, `--clearenv` with minimal PATH/HOME/LANG, `--unshare-net`, `--unshare-pid`) when available. Without bwrap it is refused for workers and runs unsandboxed only for the orchestrator, whose calls are previewed |
| `docs.tldr` | `cmd` | `tldr cmd` |
| `docs.pkg` | `name` | first success of `apt-cache show`, `pip show`, `npm view --json` |

Names must match `^[A-Za-z0-9][A-Za-z0-9._+-]*$`: no paths, spaces, shell
metacharacters or leading `-` or `.`. `docs.help` also refuses a program not on
PATH. A bad name, a missing binary (including `tldr` not installed), a timeout
or an empty lookup returns `ok=False` with a message for the model, never an
exception.
