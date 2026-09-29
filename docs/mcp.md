# MCP in Sable

Sable speaks the Model Context Protocol both ways: its agents can use tools
from MCP servers, and other agents (Claude Code, for example) can drive the
server through Sable instead of raw SSH.

## Adding servers

```
/mcp add files mcp-server-filesystem /srv          # a stdio server (a command)
/mcp add gh https://example.com/mcp                # a Streamable HTTP server (https, or http on localhost)
/mcp list                                          # servers, their tools, each tool's tier
/mcp remove files
/mcp search kubernetes                             # the official MCP Registry; --add N adds one
```

- A stdio server gets a minimal environment (PATH, HOME, locale) plus what its
  config sets. It never inherits your API keys.
- Header and env values may be `$SECRET:name`, resolved from the keyring at
  connect time and never written to config.
- Most servers are npm packages: `/mcp add` of an `npx`/`node` command tells
  you how to install Node when it is missing and saves nothing.
- Servers connect on the first goal that needs tools, not at login, so the
  shell still starts fast. A server that fails is reported once and skipped.

## Tiers and trust

Each tool becomes `mcp.<server>.<tool>`, called like any Sable tool. Every
call is previewed, checked by policy, audited and counted against the budget.
A new tool is `preview`: only the interactive shell may call it, and it runs
after you see the call and press Enter. `/mcp trust files.list_directory`
makes it `trusted`, so sub-agents may call it too; `/mcp untrust` puts it
back. A policy rule, or taint from earlier untrusted output, can still ask
for a typed `YES`.

MCP output is untrusted: after any MCP result, the rest of the goal runs one
tier stricter.

## When a server asks you something

A server can answer a call with a question (the spec's `input_required`).
The shell shows it, `[mcp files] asks: ...`, and prompts for each field;
`q` declines. The answers go back once and are audited, redacted. Workers
and the daemon have nobody at the keyboard, so such a call fails there with
"needs input; run it from the shell".

## Sable as an MCP server

```
sable --mcp-serve        # JSON-RPC on stdin/stdout; nothing listens on the network
```

Tools: `run_command`, `list_tasks`, `spawn_task`, `get_skill`,
`search_memory`. `run_command` goes through Sable's policy:

- `allow`: runs, and is audited under `mcp:<client name>`.
- `confirm`: not run. It is queued to `/inbox` and the client is told
  "queued as a7". After you approve, the same call runs once.
- `deny`: refused.

It refuses to start as root. To point Claude Code at it, add an MCP server
whose command is `sable --mcp-serve` (over SSH:
`ssh you@host sable --mcp-serve`).
