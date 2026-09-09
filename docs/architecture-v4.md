# AgenticOS v4 Architecture: how a request moves through the system

This document traces the system end to end, one diagram per concern. Solid boxes exist today (v0.3); dashed boxes are v4 targets from [vision.md](vision.md) and [structure.md](structure.md). Feature IDs in parentheses point at the catalog.

Diagrams are Mermaid and render on GitHub. For the current (v0.3) walkthrough with hand-drawn diagrams see [architecture.md](architecture.md); for every table and field see [architecture-reference.md](architecture-reference.md).

---

## 1. The big picture: layers and what talks to what

Lower layers never import higher ones. Agents never touch the UI; they publish events and the UI renders them. This one rule is what lets the daemon run agents with no terminal attached and keeps the REPL responsive while agents work.

```mermaid
flowchart TB
    subgraph APP["app  (composition root)"]
        MAIN["main.py<br/>SSH bypass, bootstrap"]
        REPL["repl.py<br/>read, builtin?, route, dispatch"]
        BUILTINS["builtins/<br/>/task /skill /clip /mcp /schedule /palace ..."]
    end

    subgraph UI["ui  (everything a human sees)"]
        PROMPT["prompt/<br/>prompt_toolkit, ghost text"]
        BLOCKS["blocks.py<br/>one block per action"]
        CONFIRM["confirm.py<br/>run / edit / diff / cancel"]
        SIDEBAR["sidebar/<br/>Textual, pane 1"]
        DASH["dash/<br/>full-screen command center"]
        TMUX["tmux/<br/>panes, key IPC"]
    end

    subgraph AGENTS["agents  (the runtime)"]
        RUNTIME["runtime.py<br/>Agent(role)"]
        ROLES["roles/<br/>orchestrator, worker, reviewer"]
        ACTIONS["actions.py<br/>run spawn wait ask tool done"]
        ROUTER["router.py<br/>bash / goal / ambiguous"]
        PLANNER["planner.py<br/>plan arrays, DAGs"]
        MANAGER["manager.py<br/>spawn pause kill attach"]
        SANDBOX["sandbox.py<br/>bwrap or bash wrapper"]
    end

    subgraph TOOLS["tools  (J1)"]
        REGISTRY["registry.py"]
        WEB["web.search / fetch"]
        FS["fs.read / patch / search"]
        SYS["sys.* docker.* git.* logs.*"]
        PY["py.run"]
    end

    subgraph SKILLS["skills"]
        SINDEX["index.py<br/>confidence ranking"]
        SLOADER["loader.py"]
        SWATCH["watcher.py<br/>pattern detection"]
        SCRYST["crystalliser.py"]
    end

    subgraph MEMORY["memory  (C6 Memory Palace)"]
        PALACE["palace.py<br/>recall remember forget consolidate"]
        ROOMS["rooms: server repos user incidents procedures"]
        SESSION["session.py<br/>compression"]
    end

    subgraph POLICY["policy"]
        ENGINE["engine.py<br/>allow / confirm / deny"]
        HOOKS["hooks.py<br/>pre_command post_command pre_spawn"]
        TAINT["blast_radius.py + taint"]
        SECRETS["secrets.py<br/>redaction, $SECRET broker"]
    end

    subgraph LLM["llm"]
        BASE["base.py<br/>LLMBackend, contracts"]
        BACKENDS["ollama / openai / anthropic"]
        PROMPTS["prompts/*.md"]
    end

    subgraph CORE["core"]
        CONFIG["config/<br/>layered TOML"]
        BUS["events/bus.py<br/>agent_events (SQLite)"]
        DB["db.py<br/>WAL, migrations"]
        EXEC["executor.py<br/>PtyProcessUnicode, cd"]
        AUDIT["audit.py<br/>provenance ledger"]
    end

    subgraph DAEMON["daemon  (E1)"]
        SERVICE["service.py agenticd"]
        SCHED["schedule.py NL cron"]
        WATCHERS["watchers.py"]
        NOTIFY["notify.py"]
    end

    subgraph MCP["mcp"]
        MCLIENT["client.py"]
        MSERVER["server.py --mcp-serve"]
    end

    MAIN --> REPL --> BUILTINS
    REPL --> ROUTER
    ROUTER -->|bash| ENGINE
    ROUTER -->|goal| RUNTIME
    RUNTIME --> ROLES --> ACTIONS
    ACTIONS --> ENGINE
    ACTIONS --> REGISTRY
    ACTIONS --> MANAGER --> SANDBOX
    REGISTRY --> WEB & FS & SYS & PY
    REGISTRY --> MCLIENT
    RUNTIME --> SLOADER --> SINDEX
    RUNTIME --> PALACE --> ROOMS
    RUNTIME --> BASE --> BACKENDS
    BASE --> PROMPTS
    ENGINE --> HOOKS
    ENGINE --> TAINT
    ENGINE --> EXEC
    EXEC --> AUDIT
    EXEC --> BUS
    RUNTIME --> BUS
    REGISTRY --> BUS
    BUS --> SIDEBAR & DASH & BLOCKS
    SERVICE --> SCHED & WATCHERS & NOTIFY
    SERVICE --> RUNTIME
    SERVICE --> PALACE
    SERVICE --> SINDEX
    MSERVER --> ENGINE
    MSERVER --> MANAGER
    SWATCH --> SCRYST --> SINDEX
    CONFIG -.-> APP & UI & AGENTS & POLICY & DAEMON
    DB -.-> BUS & AUDIT & SINDEX & PALACE

    classDef today fill:#e8f5e9,stroke:#2e7d32,color:#000
    classDef target fill:#fff8e1,stroke:#f9a825,stroke-dasharray:5 3,color:#000
    class MAIN,REPL,BUILTINS,PROMPT,CONFIRM,TMUX,ROUTER,PLANNER,MANAGER,SANDBOX,SINDEX,SLOADER,SWATCH,SCRYST,SESSION,SECRETS,BASE,BACKENDS,CONFIG,DB,EXEC,AUDIT today
    class BLOCKS,SIDEBAR,DASH,RUNTIME,ROLES,ACTIONS,REGISTRY,WEB,FS,SYS,PY,PALACE,ROOMS,ENGINE,HOOKS,TAINT,PROMPTS,BUS,SERVICE,SCHED,WATCHERS,NOTIFY,MCLIENT,MSERVER target
```

Green = exists in v0.3 (possibly under a different file name, see the migration table in structure.md §5). Amber dashed = v4.

---

## 2. One keystroke to one result: the request lifecycle

What happens between pressing Enter and seeing the block close. Every arrow that crosses into `policy` is a place the user can be asked; every arrow into `bus` is something the sidebar shows.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant P as prompt (ui)
    participant R as router
    participant A as Agent(orchestrator)
    participant L as LLM backend
    participant PO as policy engine
    participant C as confirm (ui)
    participant X as executor (pty)
    participant B as event bus
    participant S as sidebar / dash

    U->>P: types a line, Enter
    P->>R: classify(line)
    alt bash (score high)
        R->>PO: evaluate(command)
        PO-->>R: allow | confirm | deny + rule
        opt confirm tier
            PO->>C: show block: command, reason, blast radius, rule
            C->>U: run / edit / cancel (YES for destructive)
            U-->>C: decision
        end
        R->>X: run in pty
        X->>B: command.started / finished (exit, duration)
    else goal (natural language)
        R->>A: new goal
        loop one action per turn (max turns, cost, wall-clock caps)
            A->>A: build context: goal, plan ledger, palace recall, skills, tainted?
            A->>L: complete(messages, tools)
            L-->>A: action: run | tool | spawn | wait | ask | done
            A->>B: agent.turn (redacted prompt stored for replay)
            alt run or tool
                A->>PO: evaluate(action, taint, role)
                PO-->>A: tier + rule
                opt confirm tier
                    PO->>C: confirm block
                    C->>U: run / edit / diff / cancel
                    U-->>C: decision (edit = a correction, learned)
                end
                A->>X: execute (pty) or tool.run()
                X-->>A: output, wrapped as untrusted data
                A->>A: verify step, reflect on failure
                A->>B: action.finished
            else spawn
                A->>B: agent.spawn
                Note over A: worker starts in its own tmux window and sandbox (see section 4)
            else ask
                A->>B: inbox.item (question)
                S->>U: INBOX badge
                U-->>A: answer
            else done
                A->>B: goal.done (summary, cost)
                A->>A: crystallise skill? remember facts?
            end
        end
    end
    B-->>S: tail events, re-render badges, cost, lanes
    S-->>U: live status
```

---

## 3. Inside one agent turn

The same loop runs for the orchestrator (interactive, in pane 0), for workers (background, own tmux window), and for daemon jobs (no terminal). Only the role's prompt, allowed actions and policy floor differ.

```mermaid
stateDiagram-v2
    [*] --> BuildContext
    BuildContext: pinned goal + plan ledger (J6)<br/>+ palace recall (C6) + ranked skills (B1)<br/>+ last N events + taint flag
    BuildContext --> CallModel
    CallModel: llm.complete(messages, tools)<br/>cheap model for router/summaries,<br/>strong model for reasoning (A5)
    CallModel --> ParseAction
    ParseAction: native tool call or JSON<br/>strip fences → parse → re-ask → raw
    ParseAction --> Policy
    Policy: evaluate(action, role, taint, path)<br/>allow / confirm / deny + rule
    Policy --> Denied: deny
    Denied --> Reflect: structured failure
    Policy --> Confirm: confirm
    Confirm --> Execute: approved (edits stored as corrections, K3)
    Confirm --> Cancelled: cancelled
    Cancelled --> Reflect
    Policy --> Execute: allow
    Execute: pty / tool / spawn / ask<br/>output wrapped as untrusted data
    Execute --> Verify
    Verify: verify predicate (J4)<br/>exit code, stdout contains, file exists, http 200
    Verify --> Record: pass
    Verify --> Reflect: fail
    Reflect: reflection turn (J5)<br/>retry → alternative → ask user → skip step
    Reflect --> BuildContext
    Record: bus event, audit row,<br/>skill confidence nudge, cost
    Record --> Budget
    Budget: turns, tokens, USD, wall-clock (I2)
    Budget --> BuildContext: within caps
    Budget --> Breaker: cap hit
    Breaker --> [*]: paused, INBOX item
    Record --> Done: action == done
    Done: crystallise? remember? runbook?
    Done --> [*]
```

---

## 4. Spawning a sub-agent

The orchestrator hands off a self-contained goal. The worker gets its own tmux window (so you can attach and steer), its own sandbox (workspace read-write, everything else read-only), and reports back over the event bus.

```mermaid
sequenceDiagram
    autonumber
    participant O as orchestrator (pane 0)
    participant M as TaskManager
    participant T as tmux
    participant DBB as SQLite (tasks, agent_events)
    participant W as worker Agent (window task:name)
    participant SB as Sandbox (bwrap)
    participant UI as sidebar / tasks bar

    O->>M: spawn(name, goal, handoff context)
    M->>DBB: INSERT tasks (status=starting)
    M->>T: new_window("task:name")
    T->>W: python -m agentic.agents.runtime --role worker --task name
    M->>DBB: UPDATE tasks.tmux_window_id
    W->>DBB: read handoff, goal, task memory snapshot
    W->>SB: wrap every command (bwrap: workspace RW, rest RO, unshare-pid)
    loop worker turns (section 3)
        W->>SB: run command
        SB-->>W: output
        W->>DBB: agent_events (status, step, cost)
        UI-->>UI: badge: running ▓▓░
    end
    W->>DBB: agent_events kind=result, tasks.status=completed
    Note over O: next orchestrator turn sees the result event in its context
    O->>O: continue, or done
    Note over UI: /task attach opens the window, /task pause sends SIGTSTP,<br/>reconcile() marks a window that vanished as lost
```

---

## 5. How the shell learns: the skill loop

Two entry points feed one index. Today only the `/exit` path exists and it writes skills with no approval; v4 adds post-task crystallisation with an INBOX approval, and closes the confidence loop.

```mermaid
flowchart LR
    A1["audit.log<br/>every executed command"] --> W["PatternWatcher (at /exit)<br/>cluster by repo + intent<br/>seen 3x? → candidate"]
    G["goal done with ≥ 3 steps<br/>(post-task, B3)"] --> Q["summariser model:<br/>reusable procedure?"]
    W --> CR["Crystalliser<br/>LLM writes SKILL.md"]
    Q --> CR
    CR --> INBOX["INBOX: approve draft skill"]
    INBOX -->|approved| IDX[("skills index<br/>confidence, use_count,<br/>last_used, source")]
    U["you write a skill<br/>or import one (B7)"] --> IDX
    IDX --> RANK["get_ranked(goal)<br/>confidence × recency × use × match<br/>(+ embeddings, B6)"]
    RANK --> CTX["injected into agent context"]
    CTX --> RUN["agent uses the skill"]
    RUN --> V{"verify /<br/>validator passed?"}
    V -->|yes| UP["confidence +0.05"]
    V -->|no| DOWN["confidence −0.10"]
    UP --> IDX
    DOWN --> IDX
    E["your edits and [b/a] answers<br/>(corrections, K3)"] --> IDX
    IDX --> DOC["skill doctor (nightly, B4)<br/>merge duplicates, retire stale,<br/>flag regressions (K10)"]
    DOC --> IDX
```

---

## 6. Memory Palace: what agents remember and how it gets there

One layer, four tiers by lifetime, rooms by subject. Everything is plain markdown on disk; SQLite only indexes it.

```mermaid
flowchart TB
    subgraph SOURCES["where memories come from"]
        S1["command output and tool results<br/>(sys.*, docker.*, web.fetch)"]
        S2["completed goals and incidents"]
        S3["your /remember, edits, preferences"]
        S4["environment fingerprint at login (C4)"]
    end

    subgraph TIERS["tiers (by lifetime)"]
        WORK["working<br/>this turn's context"]
        EPI["episodic<br/>session and task event log"]
        SEM["semantic<br/>distilled facts with provenance<br/>and validity window"]
        PROC["procedural<br/>skills"]
    end

    subgraph ROOMS["rooms (by subject) ~/.agentic/palace/"]
        R1["server/"]
        R2["repos/&lt;name&gt;/"]
        R3["user/"]
        R4["incidents/"]
        R5["procedures/ = skills/"]
    end

    S1 & S2 & S3 & S4 --> EPI
    EPI -->|"nightly consolidate() in daemon:<br/>summarise, dedupe, expire"| SEM
    SEM --> R1 & R2 & R3 & R4
    PROC --> R5
    SEM --> IDX[("FTS5 index<br/>+ optional local embeddings")]
    IDX --> RECALL["palace.recall(query, room, k)<br/>budgeted block at top of every agent context"]
    RECALL --> WORK
    WORK --> AGENT["Agent turn"]
    AGENT -->|"palace.remember(fact, room, source)"| EPI
    USER["you: /palace, /palace why, /forget"] --> SEM
    INC["incident → runbook (K9)"] --> R4
```

---

## 7. Working while you are away: the daemon

`agenticd` runs as a systemd user service. It never has a terminal, so it can only spawn workers whose every step is `allow`-tier; anything else goes to the INBOX and, if configured, to your phone.

```mermaid
flowchart LR
    subgraph TRIGGERS
        CRON["schedules.yaml<br/>NL cron (E2)"]
        WATCH["watchers (E3)<br/>disk, log pattern,<br/>service down, webhook"]
        MAINT["nightly maintenance<br/>palace consolidate, skill doctor,<br/>self-eval (K10)"]
    end
    D["agenticd<br/>service.py"]
    CRON & WATCH & MAINT --> D
    D --> PLAN["draft plan (orchestrator role, no terminal)"]
    PLAN --> REH["rehearsal (K5)<br/>run against snapshot, diff"]
    REH --> POL{"every step<br/>allow-tier?"}
    POL -->|yes| RUN["spawn worker<br/>(section 4)"]
    POL -->|no| INBOX["INBOX item"]
    RUN --> BRK{"breaker (I2)<br/>caps, consecutive failures"}
    BRK -->|ok| DONE["result event, runbook draft"]
    BRK -->|trip| PAUSE["pause all jobs"]
    INBOX & DONE & PAUSE --> N["notify (E5)<br/>ntfy / Slack / Telegram"]
    N --> PHONE["you reply: yes &lt;id&gt;"]
    PHONE --> RUN
    RUN --> BUS[("event bus")]
    BUS --> UI["next login: sidebar and /dash<br/>show what happened"]
```

---

## 8. Processes, panes and how they talk

There is no central server. Three kinds of processes share one SQLite file in WAL mode; that is the whole IPC story, which is why nothing blocks the prompt.

```mermaid
flowchart TB
    subgraph SSHD["sshd"]
        LOGIN["login shell = agentic-shell"]
    end
    LOGIN -->|"SSH_ORIGINAL_COMMAND set"| BASH["/bin/bash -c ...<br/>scp rsync git never see the agent"]
    LOGIN -->|interactive| TM["tmux session agentic-&lt;user&gt;"]

    subgraph TM["tmux session"]
        P0["pane 0: REPL process<br/>prompt, router, orchestrator, blocks"]
        P1["pane 1: sidebar process<br/>Textual, polls bus every 500 ms"]
        P2["pane 2: tasks bar process"]
        WN["window task:&lt;name&gt;<br/>one worker process each"]
        WI["window inspect:&lt;name&gt;<br/>plain bash in the task dir"]
    end

    subgraph SYSTEMD["systemd --user"]
        AD["agenticd"]
    end

    subgraph FS["~/.agentic/"]
        SQL[("state/sessions.db  (WAL)<br/>agent_events, tasks, task_events,<br/>token_events, skill index, palace index")]
        FILES["config.toml policy.yaml hooks/<br/>skills/ palace/ schedules.yaml"]
        FLAGS["exit_requested, disabled, clip_key"]
        LOGS["audit.jsonl"]
    end

    P0 <-->|read/write| SQL
    P1 -->|read| SQL
    P2 -->|read| SQL
    WN <-->|read/write| SQL
    AD <-->|read/write| SQL
    P0 & WN & AD --> LOGS
    P0 & P1 & AD --> FILES
    P0 <--> FLAGS
    P1 --> FLAGS
    P0 -->|"tmux send-keys / new-window"| WN
    P0 -.->|"/bash: subshell in pane 0 (I13)"| SUB["plain bash, exit returns"]
    EXT["Claude Code / Warp / any MCP client"] -->|"--mcp-serve"| MS["mcp server process"]
    MS -->|policy engine| SQL
```

---

## 9. Reading the diagrams together

| If you want to know… | Look at |
|---|---|
| which module owns a responsibility and who may import whom | §1 |
| where the user can be asked, and where the sidebar gets its data | §2 (every arrow into *policy* and *bus*) |
| why an agent stopped, retried, or asked | §3 |
| why a task shows *lost* or how to steer a worker | §4 |
| why a skill was picked or its confidence moved | §5 |
| where a fact came from and when it expires | §6 |
| what the daemon is allowed to do alone | §7 |
| which process is doing what, and why nothing blocks | §8 |

Every one of these has a corresponding "why" command in the shell (`/why`, `/policy explain`, `/skill why`, `/palace why`, `/task replay`, `/events tail`) per the visibility principles in [structure.md §4](structure.md).
