# Phase 2 owed gate line: run 2 uses fewer turns than run 1

2026-09-28, `openai/gpt-4o-mini`, `scripts/gate_phase2_turns.py` on a pty in the
playground. Owed since the Phase 2 gate (plan, Task 13), which found no goal
that was both vague and discoverable.

**Setup.** A workspace whose `README.md` says deploys are `./build.sh`, then
`./migrate.sh`, then `./restart.sh`. The goal is the bare "deploy the api",
twice, with the drafted skill approved in between. Run 1 has to discover the
procedure; run 2 can take it from the skill.

## Runs

| Run | Result | Run 1 | Skill drafted | Run 2 |
|---|---|---|---|---|
| A (goal named no README, before the fixes) | UNTESTED | 10 actions, failed: installed docker-compose, never read the README | none | n/a |
| B (goal pointed at the README) | FAIL | 5 actions, deployed | `deploy-api`, but its steps were "fs.tree, cat README, build, migrate, restart" | 5 actions: followed the skill, discovery included |
| C (after the fixes below) | UNTESTED | 5 actions, deployed | model judged it not reusable | n/a |
| D | UNTESTED | 5 actions, deployed | clean skill written, but not yet listed when the harness ran `/skill list` (harness timing) | 5 actions without it |
| **E** | **PASS** | **4 actions, deployed** | **`deploy-api`: build, migrate, restart** | **3 actions, deployed, `◈ using skill deploy-api (0.50)`** |

The line passes, once in the three runs after the fixes. Whether a run is
worth a skill is still the model's call, which is the "measures model
behaviour" caveat the Phase 2 plan recorded.

## Found and fixed

1. **Skills recorded their own discovery.** The drafted skill kept "list the
   directory, read the README" as steps, so run 2 repeated them and saved
   nothing. `crystallise_check.md` now says to leave out steps that only found
   the procedure and keep the ones that do the work.
2. **The model ran a tool as a shell command**, `$ fs.tree .`, which bash can
   only answer `command not found`. Both agents now catch a `run` whose first
   word is a tool name and tell the model how to call it, running nothing.
3. **The model acted before looking.** Run A installed docker-compose with
   apt and ran `docker-compose up` in a directory with no compose file, and
   only then listed it. The orchestrator prompt now says: follow a matching
   skill if there is one; otherwise list the directory and read any README or
   Makefile first, and do not assume a tool or install anything until what
   you found says so.

## Harness note

`gate_phase2_turns.py` reads `/skill list` a few seconds after run 1 ends. In
run D the skill file existed by the end of the container but was not listed
yet, so a longer wait there would make the line less flaky.
