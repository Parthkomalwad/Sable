# Orchestration and safety

How Sable splits big goals, checks its work, tries risky changes on a copy,
undoes them, and when it will run a normally blocked command.

## Plan graphs

A goal with parts that can run at the same time becomes a plan graph: each
lane is a sub-agent, and a join lane waits for the others.

```
◈ plan graph
└ count-markdown: count the markdown files in /app/docs
└ count-python: count the python files in /app/sable/agents
  └ write-results: write both counts to join.txt  (after count-markdown, count-python)
```

A lane that fails blocks the lanes after it, not its siblings. At most 8
lanes, 5 deep. `/dash` shows lane status.

## The reviewer

Before a goal that changed something is reported done, a second model call
reads the goal and what ran, and answers `pass`, `concerns` or `fail`, with
a reason you see. A first `fail` goes back to the agent to fix; a second one
asks you. The reviewer runs nothing. `review: off` in config turns it off.

## Rehearsal

A change of two or more steps (a plan, or one command chained with `&&`) runs
first on a copy of the files it touches. Sable binds each copy over the real
path inside a `bwrap` sandbox, so the commands see the usual paths and only
the copies change. You see each step's result and the diff, then choose
`a apply` or `q abort`.

- Steps that act outside files (`systemctl`, network, package installs) are
  shown as "not rehearsed", never faked.
- Without a working `bwrap`, rehearsal says it is unavailable and asks as
  before. In a container running as root it needs `NET_ADMIN`.
- Daemon jobs are rehearsed too. With `rehearse: always`, a job whose
  rehearsal fails or is unavailable waits in `/inbox`; with the default
  `auto` it runs as before when rehearsal is unavailable.

## Undo

Before a step changes files, Sable copies the paths it will touch to
`~/.sable/snapshots/<id>/`.

```
/undo list        every snapshot, newest first
/undo             the last change in this session
/undo 12          any snapshot, from any session
/task diff NAME   what a sub-agent's last step changed
```

Undo shows the diff and asks first. Files come back byte for byte with their
modes, files the step created are removed, and nothing outside the snapshot
is touched. Limits: 50 MB or 20,000 files per snapshot, and a path that holds
the snapshot store itself (such as `~`) is refused. A write Sable cannot see
in the command text (inside a script, for example) gets no undo point.

## Step-up approval

Some commands never run (`rm -rf /`, a fork bomb, `chmod -R 777 /`). With
step-up set up, the interactive shell offers one way through: a 6-digit code
from your authenticator app, or a tap on your phone.

```
/stepup setup     prints the secret and otpauth:// link for your app
/stepup status
/stepup off
```

A grant covers that exact command, once, within 2 minutes. A code never
works twice, three wrong codes lock step-up for ten minutes, and sub-agents,
the daemon and `--mcp-serve` are never offered it. Every attempt is audited
without the code.

## Limits for sub-agents

A sub-agent can run with no network and with memory, CPU and process caps:

```json
"limits": {"mem_mb": 2048, "cpu_s": 600, "procs": 256, "network": false}
```

`sable doctor` and `/task stats NAME` show which limits this host can
enforce. Where one cannot be applied, Sable says so instead of dropping it.

## Signed skills

Skills you write or approve are signed with a key kept in the keyring, or in
`~/.sable/skill-signing.key` (mode 600) on a server without one. An imported
or hand-edited skill is unsigned, and while an agent follows it every command
is one tier stricter. A skill changed after signing is not used at all.
`/skill list` shows which is which; `/skill sign NAME` signs one after you
have read it.
