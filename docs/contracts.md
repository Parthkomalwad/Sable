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
| `mcp` | — | **reserved** (Phase 6, D1) |

`agents/orchestrator.py:ORCHESTRATOR_ACTIONS` is the live set. An action outside
it, including `wait` and `ask`, is rejected as unrecognised and stops the loop
with a reason rather than being guessed at.

#### Live actions

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
```

`name`, `pattern`, `category` and `why` are all required. `why` exists so Phase
3's `/policy explain` can say what a rule protects against instead of echoing a
regex.

A missing or malformed policy file **raises at import**. It does not degrade to
an empty list, because an empty blocklist is a shell that runs `rm -rf /`
without asking.
