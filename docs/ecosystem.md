# Measuring, extending and sharing Sable

## `sable eval`: the task suite

```
sable eval                    # mock backend: no model is called, nothing is paid for
sable eval --only log-rotate
sable eval --backend ollama   # a local model
```

25 server tasks under `evals/tasks/` (logs, configs, disk, cron, archives,
permissions and more), each with a setup script, a check script and a goal.
Every task runs in a fresh temporary home. Previews are accepted and a typed
`YES` is refused, so a task that needs a confirm-tier step fails honestly.
Results go to a table in the database, a markdown file, and `/dash`.

A paid backend (`--backend openai` or `anthropic`) runs only when you name it
on the command line. The mock backend proves the harness end to end; a real
model is what measures the agent.

## Skills: doctor, publish, install

```
/skill doctor                 # failing, stale, duplicate, unsigned or tampered skills
/skill publish rotate-logs    # one skill as an archive with a manifest
/skill install https://example.com/rotate-logs.tar.gz
```

The doctor only reports; it never changes a skill. An installed skill
arrives pending, imported and unsigned: read it with `/skill show`, then
`/skill approve`. Installs are https only, capped at 5 MB, and checked like
`sable import` (paths, links, hashes).

## Plugins

A plugin is a folder with `plugin.toml`: an MCP server, and optionally skills
and hooks.

```toml
[plugin]
name = "hello"
version = "0.1.0"
description = "an example"

[server]
command = ["python3", "server.py"]

[tools]
trusted = ["read_only_tool"]   # a request: each one is asked about
```

`/plugin add <folder>` shows everything and asks; tools start in preview,
requested trusted tools are asked about one by one, skills arrive pending and
unsigned, and each hook is shown before it is installed. `/plugin remove`
takes away exactly what the plugin installed. No plugin code runs inside
Sable itself.

## OpenTelemetry

```json
"otel": {"endpoint": "http://localhost:4318", "headers": {}, "service_name": "sable"}
```

With an endpoint set, spans for each goal, turn, command, tool call and model
call (tokens and cost included, text redacted) are posted as OTLP/HTTP JSON
to `/v1/traces`. Off by default. A collector that is down never slows a goal.

## Other servers

```
/host add prod-1 ops@10.0.0.5
/host test prod-1
@prod-1 df -h
@all uptime
```

Each command goes over SSH to `sable --mcp-serve` on that server, so that
server's own policy, audit and inbox apply: a risky command is queued there
("queued on prod-1 as a1; approve it on that host"). Connect to a new server
once by hand first so SSH knows its host key.

## Runbooks from incidents

When a goal that started from a problem (a watcher, `!` fix, or "X is down")
ends with a passing check, Sable drafts a runbook into memory: symptom,
checks, fix, verify. When the same alert fires again, `/inbox` and the phone
push offer "run runbook", which runs the fix as a rehearsed, policy-gated
plan. Nothing runs without approval.

## Weekly self-check

Every Sunday at `maintenance_time`, or with `sable selfcheck`, Sable
summarises the week from its own records: goals done, steps and spend with and
without a matched skill, skills that started failing, and the latest eval
run. It makes no model calls.

## Sharing

```
sable share --approve-only --name alice --ttl 1h
sable share --read-only --ttl 30m
sable share --list
sable share --stop
```

Approve-only sends each waiting inbox item to a share topic with approve and
reject buttons; each works once, expires, and stops working when the share
ends. The audit names the approver. Read-only sends a text snapshot of
`/dash` every 5 minutes. Blocked commands are never offered, and nothing
listens on the network.
