# Phase 4 Implementation Plan: Blocks and the Command Center (v0.8)

> Checkbox tasks, failing-test-first, one PR per task, a live gate run at the
> end. Style follows the Phase 3 and 3.5 plans.

**Goal:** the screen is the product. Approvals, agent status and cost are
visible at a glance, and every command reads as a block.

**Deliverables:** G1, G2, G3, G5, G6, K1, K2.

**Gate (from `docs/roadmap-phases.md` Phase 4, adjusted for the decisions below):**

```
Spawn 3 parallel workers -> sidebar shows 3 badges updating live; /dash shows lanes with tails
A worker hits a confirm-tier command -> INBOX (1); approve from /dash; worker continues
Every command is a numbered block (cmd, duration, exit, cost); /block 3 rerun re-runs it
Resize the terminal to 90 cols -> sidebar hides, nothing freezes (PROMPT_TOOLKIT_NO_CPR still set)
Cold start still < 300 ms (time python -m sable.app.main --version)
Type `git sta` -> dim `tus` within 150 ms, -> accepts; history in this directory ranks first
Run a failing command -> `? explain  ! fix` appears; `!` gives a confirm block with a fix
```

---

## 0. Decisions (asked and answered 2026-09-28)

| Question | Decision |
|---|---|
| Add `textual` for the sidebar and `/dash`? | **Yes.** See 0.1. |
| How far do blocks go? | **Blocks in normal scrollback.** Each command gets a numbered header and footer; `/block N copy\|rerun\|show` works on any past block. The terminal keeps native scroll, search, copy and full-screen programs. True collapse and `Ctrl+Up/Down` jumping are not possible in scrollback and are dropped from the gate. |
| Ghost text source | **History first**, weighted by the current directory and recency, local and instant. A model-based mode can come later behind a setting, off by default. |

### 0.1 The case for `textual` (CLAUDE.md asks for it in writing)

- **Same maintainers as Rich**, which Sable already depends on for all output;
  Textual renders with Rich, so styles and colours are shared, not duplicated.
- **The alternative is a hand-built widget toolkit.** Lanes, a focusable
  approval list, sparklines, tables that resize and a tree are each a small
  project on prompt_toolkit; together they are the bulk of the phase and the
  most likely source of layout bugs.
- **No cold-start cost.** The sidebar and `/dash` run as their own processes;
  the shell never imports `textual`. `test_cold_start` keeps that honest.
- **Pinned** to a minor version, like `libtmux`, and added to the approved list
  in CLAUDE.md, `pyproject.toml` and `requirements.txt` in one PR.

### 0.2 One read-only data layer

The sidebar and `/dash` read the same things: agents and their status (the
event bus and `tasks`), the inbox (`policy_queue` pending + open
`breaker_trips`), cost per agent (`token_events`), recent log lines. One module,
`sable/ui/state.py`, answers those questions as plain dataclasses over WAL
reads with short-lived connections. Both UIs render it; neither writes the DB
except through the existing functions (`queue.decide_request`,
`breaker.reset`).

### 0.3 Not in scope

G4 plan DAG, G7 runbooks, G8 rich renderers, G9 web companion, model-based
ghost text, true block collapse.

---

## Task 0: `textual` and the shared state layer (serial, first)

- [x] Pin `textual` in `pyproject.toml`, `requirements.txt`, and the approved
      list in CLAUDE.md with a one-line reason.
- [x] `sable/ui/state.py`: `agents()`, `inbox()`, `cost_by_agent()`,
      `tail(agent, n)`, `tree()`: read-only, WAL, never raises on a missing
      table, tested against a temp DB.
- [x] A test that `python -m sable.app.main --version` does not import
      `textual` (import-time guard, not a timing test).

Tasks 1 to 5 then run in parallel.

## Task 1: Blocks in scrollback (G2)

- [x] Every command run from the REPL (typed bash and agent commands) prints a
      header `#N  <cmd>` and a footer `exit · duration · cost` in a consistent
      style; blast colour on the header for agent commands.
- [x] A `blocks` table: number, cwd, command, exit, duration, cost, the last
      200 lines of output (redacted with `redact_text`).
- [x] `/block N show|copy|rerun`; `rerun` goes through `gate()` like any
      command. `/block` lists the last 20.
- [x] Full-screen programs (vim, htop, ssh) are not wrapped in a footer that
      would scribble over them: detect alternate-screen use and print only the
      footer after.

## Task 2: Textual sidebar (G1, sidebar half)

- [x] Replace `ui/sidebar/watch.py` with a Textual app: AGENTS (status badges:
      thinking / running / blocked / awaiting approval / done), INBOX count,
      COST sparkline, GIT, SYSTEM. Reads `ui/state.py` on a timer; never blocks
      the REPL; separate process in the tmux layout.
- [x] Hides itself below 90 columns and comes back when widened.
- [x] `agents_panel.py` folded in or kept, whichever is simpler; say which.

## Task 3: `/dash` command center (G1, full-screen half)

- [x] `/dash` (and `Ctrl+D` if free) opens a full-screen Textual app as a child
      process and returns to the prompt on quit.
- [x] Agent lanes with live log tails, the orchestrator to sub-agent tree,
      token and cost per lane.
- [x] **Approval queue:** pending `policy_queue` items with rule and reason;
      approve and reject call `queue.decide_request`; open breaker trips with
      reset calling `breaker.reset`. No other writes.

## Task 4: Streaming reasoning (G3)

- [x] `call_llm` accepts an optional token callback; the OpenAI, Anthropic and
      Ollama backends already stream and call it as text arrives.
- [x] While the orchestrator thinks, its explanation streams as a dim line in
      place of the spinner; the spinner remains when a backend cannot stream.
- [x] Only the explanation field is shown, not raw JSON; the parse chain is
      unchanged.

## Task 5: Palette, themes, ghost text, explain-last-error (G5, G6, K1, K2)

- [x] `Ctrl+P`: fuzzy search over builtins, skills, snippets and tasks; Enter
      inserts or runs, as the item's kind says.
- [x] `/theme <name>` and layout presets (focus / fleet / minimal) applied to
      the tmux layout.
- [x] K1: an `AutoSuggest` that ranks history by same directory first, then
      recency; returns within 150 ms or suggests nothing.
- [x] K2: after a non-zero exit, one dim line `? explain  ! fix`. `?` sends the
      command, exit code and last 40 lines (redacted) and prints a short
      explanation block; `!` asks for a fix and shows it as a normal confirm
      block through `gate()`.

## Task 6: Docs and the live gate run

- [x] `docs/contracts.md` for blocks, `ui/state.py`, the dash's writes.
- [x] `scripts/gate_phase4.py`: the gate lines above on a pty in the playground
      against the live model; transcripts in `docs/history/`. Visual lines the
      harness cannot judge (badges, sparklines) are captured as screen dumps
      and marked for a human look rather than claimed.

> **Gate run 2026-09-28, `openai/gpt-4o-mini`: all six lines passed on the first run.** Blocks and `/block N rerun`; ghost text in 52 ms; `? explain` and `! fix`; `/dash` approve with `a` then `y`; the sidebar's five sections at 120 columns and hidden at 80; cold start median 100 ms. Not covered: a real sub-agent continuing after a `/dash` approval (needs tmux), and badge colours and the sparkline, which need a human look. Transcripts in `docs/history/phase-4-gate-2026-09-28.md`.
>
> **Finding, not yet fixed:** the `! fix` transcript shows the orchestrator re-proposing the same command after each `q` (cancel). A cancel appends `[user cancelled ...]` and the model tries the same thing again; the third-identical refusal only counts commands that ran. Two cancels in a row should end the goal.
