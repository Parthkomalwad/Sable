# Phase 2 Implementation Plan: Self-Learning Skills That Actually Learn

> Checkbox tasks, failing-test-first, one commit per task. Style follows
> `docs/plans/2026-04-06-orchestrator-agent.md`.

**Goal:** the shell gets measurably better at a task the second and third time
you do it. A skill is ranked before it is injected, nudged after it is used,
drafted from a completed run, and never enabled without a human saying yes.

**Deliverables:** B1, B2, B3, B5, K3, K4. B6 (semantic retrieval) is explicitly
out: it needs an embedding store and a new dependency argument, and the gate
does not ask for it.

**Gate (from `docs/roadmap-phases.md` Phase 2), treated as the definition of done:**

```
Run "deploy the api" 3x (mock or real). After run 1: /skill list shows draft 'deploy-api' pending.
Approve. Run 2: orchestrator's first turn says "using skill deploy-api (0.55)". Fewer turns than run 1.
Run 3: confidence 0.60; break the deploy on purpose -> confidence drops to 0.50.
cat ~/skills/deploy-api/SKILL.md   -> readable, frontmatter valid
```

---

## 0. What this phase changes, not just adds

Phase 2 is **not purely additive**. Three existing behaviours change. Each is
called out here so the change is agreed before any code is written.

### 0.1 Auto-crystallisation stops being unattended (behaviour change)

Today, the `/exit` branch of `sable/app/builtins/dispatch.py` runs
`PatternWatcher.observe()` and then, for every pattern crossing the 3x
threshold, calls `SkillCrystalliser.crystallise(p)` immediately. The skill file
is written and added to the index with `confidence: 0.5`, and from that moment
`TaskSkillLoader` can inject it into a worker's context. **No human ever
approved it.**

The Phase 2 gate requires skills are "never auto-enabled without approval". So
this is a behaviour change plus a new approval surface.

**The approval surface (decided, see 0.2):** a *pending* state on the skill
itself, plus `/skill approve|reject`. `/inbox` is **not** invented here: it is
E6 and lands in Phase 5. Phase 5 rewires this queue into `/inbox`; the state
lives in the index now so that rewiring is a read-site change, not a redesign.

After this phase:

- Drafting still happens unattended at `/exit` (and newly after a completed
  run, B3). Drafting is cheap and produces a file.
- **Enabling** requires a human. A pending skill is inert: `SkillIndex.get_ranked()`
  never returns it and `TaskSkillLoader` never loads it.
- The next login prints one line: `N draft skills pending approval (/skill list)`.

The `/exit` path must still never block the user exiting their shell. Drafting
stays inside the existing swallow-and-continue guard; only the enabling moves.

### 0.2 Decisions taken (asked and answered before planning)

| Question | Decision |
|---|---|
| Approval surface, given `/inbox` is Phase 5 | Pending state in `SkillIndex` + `/skill approve\|reject`. No `/inbox` invented. |
| Existing flat `instructions/*.md` | Auto-migrate to folders on first use, **keep the originals** for one release (same precedent as the `shell/` compat shim). |
| B3 trigger | On a **successful** `done` with **>= 3 executed steps** only. One summariser call per completed multi-step goal. |
| K3 correction storage | Redacted through `policy/engine.py` before writing, matching the `agent_turns` rule in `contracts.md` §3. |

### 0.3 Dead API surface becomes live (no signature changes)

`SkillIndex.get_ranked()`, `record_use()` and `mark_needs_update()` have zero
callers today; verified. This phase gives them callers. `get_ranked()` gains a
ranking formula and a pending filter, so its existing 3 unit tests are updated
deliberately rather than worked around.

`TaskSkillLoader.load_relevant()` is a keyword-only stub and says so in its
docstring. Task 4 replaces the body and the docstring together.

### 0.4 Not in scope, deliberately

- **The 15 `xfail(strict)` integration placeholders are not Phase 2 work.**
  They are labelled "Phase 2: not implemented yet" but cover the full input
  loop (`test_full_loop.py`), backend SSE parsing (`test_llm_backends.py`) and
  session resume (`test_session_resume.py`). None of these is a B/K deliverable.
  The label was a generic future marker. **Task 12 re-labels them** to the phase
  that actually owns each, so the marker stops claiming this phase owes them.
- The `test_layering.py` `xfail(strict)` on three `agents -> memory/skills`
  edges stays. structure.md puts it in Phase 1 and it is overdue, but removing
  the deferred-import fallbacks breaks callers that do not inject. Phase 2 adds
  no new edges; Task 4 injects its collaborators the same way Phase 1 did.
- No test drives a real spawned worker under a real model in tmux. Task 13
  is the manual playground run that closes it for this phase's surface.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `sable/skills/model.py` | **Create** | `Skill` dataclass <-> `SKILL.md` frontmatter; parse, render, validate |
| `sable/skills/migrate.py` | **Create** | flat `instructions/*.md` -> `<slug>/SKILL.md`, originals kept |
| `sable/skills/index.py` | **Modify** | pending state, ranking formula, `nudge()`, approve/reject |
| `sable/skills/loader.py` | **Modify** | `get_ranked()`-backed, folder-aware, returns enabled skills only |
| `sable/skills/crystalliser.py` | **Modify** | writes folders, drafts as pending, `from_run()` for B3 |
| `sable/skills/validate.py` | **Create** | B5: run a skill's `validate` command, grade success |
| `sable/skills/corrections.py` | **Create** | K3: record + query redacted corrections |
| `sable/skills/aliases.py` | **Create** | K4: NL alias store, match, promote at 3 uses |
| `sable/core/db.py` | **Modify** | `skill_corrections`, `skill_aliases` tables |
| `sable/agents/worker.py` | **Modify** | nudge after run; B3 draft on done |
| `sable/agents/orchestrator.py` | **Modify** | announce used skill; capture `e`-edits; B3 draft on done |
| `sable/app/repl.py` | **Modify** | alias match before router; pending-drafts login line |
| `sable/app/builtins/skill.py` | **Modify** | `show\|edit\|disable\|stats\|approve\|reject` |
| `sable/app/builtins/dispatch.py` | **Modify** | `/exit` drafts pending, not enabled; `/corrections`; `/alias` |
| `sable/ui/sidebar/watch.py` | **Modify** | corrections counter panel |
| `sable/llm/prompts/skill_writer.md` | **Modify** | emit frontmatter |
| `sable/llm/prompts/crystallise_check.md` | **Create** | B3 "is this reusable?" |
| `docs/contracts.md` | **Modify** | SKILL.md contract, new tables, index schema |
| `CHANGELOG.md` | **Modify** | Unreleased, every task |

---

## Task 1: The `Skill` model and `SKILL.md` contract (B2)

**Files:** Create `sable/skills/model.py`, `tests/unit/test_skill_model.py`

- [x] **Step 1: failing tests.** Round-trip a `SKILL.md`: frontmatter parses to
      a `Skill`; rendering it back produces byte-identical frontmatter; unknown
      keys survive a round trip; a missing required key raises a typed
      `SkillFormatError`; a body with no frontmatter at all parses as a legacy
      skill with inferred `name` and empty `triggers`.
- [x] **Step 2: run, confirm they fail** (module does not exist).
- [x] **Step 3: implement.** Frontmatter fields per roadmap: `name`,
      `description`, `triggers`, `preconditions`, `validate`, plus `status`
      (`pending|enabled|disabled`) and `source` (`user|crystallised`). Parse
      with `tomllib` inside a `+++` fence, **not** YAML: CLAUDE.md forbids
      adding a YAML parser and `tomllib` is stdlib. Record this choice in the
      docstring, as `policy/rules.py` does for `policy.toml`.
- [x] **Step 4:** `pytest tests/unit/ -q` green. Commit: `feat(skills): Skill model and SKILL.md frontmatter contract`

## Task 2: Migration from flat files to folders (B2, I6-lite)

**Files:** Create `sable/skills/migrate.py`, `tests/unit/test_skill_migrate.py`

- [x] **Step 1: failing tests.** A flat `instructions/foo.md` becomes
      `~/skills/foo/SKILL.md` with inferred frontmatter; **the original file
      still exists**; migration is idempotent; a folder that already exists is
      not overwritten; an unreadable file is skipped and reported, not fatal.
- [x] **Step 2: run, confirm they fail.**
- [x] **Step 3: implement.** `migrate_flat_skills() -> list[MigrationResult]`.
      Infer `name` from the stem and `description` from the first heading line,
      the same two facts `loader.py` already reads today. `status` is inherited
      from the index if present, else `enabled` (a skill the user already had
      working must not silently go pending).
- [x] **Step 4:** green. Commit: `feat(skills): migrate flat skill files to folders, keeping originals`

## Task 3: Pending state and the ranking formula (B1, B3 approval)

**Files:** Modify `sable/skills/index.py`, `tests/unit/test_skill_index.py`

- [x] **Step 1: failing tests.** New: `add(..., status="pending")` is the
      default for auto-generated skills; `get_ranked()` **excludes** pending and
      disabled; `approve()` flips pending -> enabled; `reject()` removes the
      entry and leaves the folder on disk; `nudge(name, success)` moves
      confidence by the documented deltas and is a no-op on an unknown name;
      ranking orders by `confidence x recency x use_count x match` and a
      never-used skill still ranks above nothing.
      **Updated deliberately:** the 3 existing `get_ranked` tests now assert the
      new ordering and the pending filter. Note in the commit message that these
      changed and why.
- [x] **Step 2: run, confirm they fail.**
- [x] **Step 3: implement.** `nudge()` is a thin, named alias over the existing
      `record_use()` deltas rather than a second scoring path. Keep the
      documented numbers: +0.05 success, -0.10 failure, clamped to [0, 1].
      Recency is a bounded decay so an old high-confidence skill cannot
      permanently outrank a fresh relevant one.
- [x] **Step 4:** green. Commit: `feat(skills): pending approval state and confidence-ranked retrieval`

## Task 4: The loader consults the index (B1)

**Files:** Modify `sable/skills/loader.py`, `tests/unit/test_skill_loader.py` (create)

- [x] **Step 1: failing tests.** `load_relevant()` returns folder skills ranked
      by the index; a pending skill is never returned; a local task skill still
      overrides a global one by name; the returned dicts keep today's
      `{name, content, hash, source}` shape so `worker.py`'s de-duplication
      keeps working unchanged; an index that does not exist degrades to the old
      keyword match rather than raising.
- [x] **Step 2: run, confirm they fail.**
- [x] **Step 3: implement.** Replace the body **and** the stale docstring
      ("keyword-only stub in Phase 3. Phase 5 will upgrade it"). The index is an
      injected collaborator with a default, matching how `worker.py` takes
      `memory` and `skill_loader`, so no new layering edge appears.
- [x] **Step 4:** green. Commit: `feat(skills): rank loaded skills by confidence instead of keywords alone`

## Task 5: Close the feedback loop (B1, B5)

**Files:** Create `sable/skills/validate.py`; modify `sable/agents/worker.py`;
create `tests/unit/test_skill_validate.py`, `tests/unit/test_skill_feedback.py`

- [x] **Step 1: failing tests.** A skill with a `validate` command is graded by
      running it: exit 0 is success, non-zero failure; a skill **without**
      `validate` falls back to the run's own outcome; a validator that times out
      grades as failure and says so; after a run that used skill S, `nudge(S, ...)`
      is called exactly once per skill, not once per step.
- [x] **Step 2: run, confirm they fail.**
- [x] **Step 3: implement.** The validator runs through
      `agents/runtime.run_command` inside the worker's existing sandbox: it is
      model-authored text and must not run unwrapped. Nudges fire once, at
      terminal state, from the same place the `completed`/`failed` event is
      published, so the bus and the confidence loop can never disagree.
- [x] **Step 4:** green. Commit: `feat(skills): grade skill use by validator or exit code and nudge confidence`

## Task 6: Post-task crystallisation (B3)

**Files:** Modify `sable/skills/crystalliser.py`, create
`sable/llm/prompts/crystallise_check.md`, `tests/unit/test_crystallise_from_run.py`

- [x] **Step 1: failing tests.** A run that ends `done` with >= 3 executed steps
      asks the summariser once; a run with 2 steps asks nothing; a **failed**
      run asks nothing; a "no, not reusable" answer writes no file; a "yes"
      answer writes `<slug>/SKILL.md` with `status: pending` and
      `source: crystallised`; the summariser role is used, not the orchestrator's.
- [x] **Step 2: run, confirm they fail.**
- [x] **Step 3: implement.** `SkillCrystalliser.from_run(...)`. Reuses the
      existing failure posture: a generation failure writes a file carrying the
      reason rather than vanishing. Existing `crystallise()` tests stay green;
      the only change to that path is the new pending default.
- [x] **Step 4:** green. Commit: `feat(skills): draft a pending skill from a completed multi-step run`

## Task 7: `/exit` drafts, it no longer enables (behaviour change, 0.1)

**Files:** Modify `sable/app/builtins/dispatch.py`, `sable/app/repl.py`;
create `tests/unit/test_exit_crystallisation.py`

- [x] **Step 1: failing tests.** At `/exit`, a threshold-crossing pattern writes
      a **pending** skill; the message says "draft" and names the approval
      command; a pending skill is not returned by `load_relevant()`; a
      crystallisation failure still exits cleanly (the existing guard holds);
      startup prints the pending count when > 0 and prints nothing when 0.
- [x] **Step 2: run, confirm they fail.**
- [x] **Step 3: implement.** The narrow change is the status the draft is
      written with, plus the wording. Keep the exception tuple exactly as it is:
      it is already specific and already documented.
- [x] **Step 4:** green. Commit: `fix(skills)!: crystallised skills are drafts pending approval, not auto-enabled`

## Task 8: `/skill show|edit|disable|stats|approve|reject` (B2)

**Files:** Modify `sable/app/builtins/skill.py`, `tests/unit/test_skill_builtin.py`

- [x] **Step 1: failing tests.** `list` marks pending drafts and shows
      confidence; `show <slug>` renders frontmatter and body; `approve <slug>`
      enables and reports the confidence it starts at; `reject <slug>` removes
      the index entry and says the folder was kept; `disable <slug>` is
      reversible; `stats` shows use_count, confidence and last_used; an unknown
      slug is a clear message, not a traceback. Existing 12 tests stay green.
- [x] **Step 2: run, confirm they fail.**
- [x] **Step 3: implement.** Rich via `ui/console.out`, never `print()`.
- [x] **Step 4:** green. Commit: `feat(skills): /skill show, edit, disable, stats, approve and reject`

## Task 9: Learn from your edits (K3)

**Files:** Create `sable/skills/corrections.py`; modify `sable/core/db.py`,
`sable/agents/orchestrator.py`, `sable/app/builtins/dispatch.py`,
`sable/ui/sidebar/watch.py`; create `tests/unit/test_corrections.py`

- [x] **Step 1: failing tests.** An `e`-edit stores `(proposed, corrected)`;
      **both are redacted** through `policy/engine.py` before the write, so a
      key typed into an edit prompt never reaches the table; an edit that
      changes nothing stores nothing; the existing `[b/a]` recorder keeps
      writing its corpus row **and** now writes a correction; `/corrections`
      lists and deletes; the sidebar counter reads the week's count.
- [x] **Step 2: run, confirm they fail.**
- [x] **Step 3: implement.** New `skill_corrections` table. The redaction call
      is the same `redact_text` the replay log uses, so one rule governs every
      table that stores model-adjacent text.
- [x] **Step 4:** green. Commit: `feat(skills): record command edits and routing answers as corrections`

## Task 10: Natural-language aliases (K4)

**Files:** Create `sable/skills/aliases.py`; modify `sable/core/db.py`,
`sable/app/repl.py`, `sable/app/builtins/dispatch.py`;
create `tests/unit/test_aliases.py`

- [x] **Step 1: failing tests.** `/alias "restart the api" = docker compose restart api`
      stores it; the phrase matches **before** the router runs and makes no LLM
      call; matching is normalised and fuzzy at >= 0.9; a near-miss below the
      threshold falls through to the router untouched; an alias reaching 3 uses
      is **offered** for promotion, never auto-promoted; the resolved command
      still passes the destructive check before running.
- [x] **Step 2: run, confirm they fail.**
- [x] **Step 3: implement.** Matching is deterministic string work with
      `difflib` (stdlib), no new dependency. It sits in `repl.py` ahead of
      `classify()`, which is the only place it can be both instant and free.
- [x] **Step 4:** green. Commit: `feat(skills): natural-language aliases matched before the router`

## Task 11: Announce the skill in use (gate line 2)

**Files:** Modify `sable/agents/orchestrator.py`, `sable/agents/worker.py`;
create `tests/unit/test_skill_announcement.py`

- [x] **Step 1: failing tests.** When a skill is injected, the first turn's
      visible output contains `using skill <name> (<confidence>)` to two
      decimals; nothing is printed when no skill matched; the announcement is
      also a bus event so the sidebar and `/task events` can see it.
- [x] **Step 2: run, confirm they fail.**
- [x] **Step 3: implement.** This is the gate's literal wording. Publish a
      `skill_used` event kind; `contracts.md` §2.1 already promises an unknown
      kind is carried, not rejected, so the sidebar needs no change to survive it.
- [x] **Step 4:** green. Commit: `feat(agents): announce which skill an agent is using and at what confidence`

## Task 12: Re-label the misfiled integration placeholders (0.4)

**Files:** Modify `tests/integration/test_full_loop.py`,
`test_llm_backends.py`, `test_session_resume.py`

- [x] **Step 1:** No new tests. Change each `xfail` reason from "Phase 2" to the
      phase that actually owns it, with a one-line comment saying why it is not
      Phase 2 work. `strict=True` and `raises=NotImplementedError` stay, so an
      accidental implementation still fails the build.
- [x] **Step 2:** `pytest tests/integration/ -q` still 76 passed / 15 xfailed.
- [x] **Step 3:** Commit: `docs(tests): re-label integration placeholders to their owning phase`

## Task 13: Docs, contracts, and the real run

**Files:** Modify `docs/contracts.md`, `CHANGELOG.md`, `README.md`

- [x] **Step 1:** Document in `contracts.md`: the `SKILL.md` frontmatter
      contract and why it is TOML; the `skills_index.json` schema including
      `status`; `skill_corrections` and `skill_aliases` DDL; the `skill_used`
      event kind. Mark B6 as not implemented, the way §1.3 marks `wait`/`ask`.
- [x] **Step 2: RUN. Five of six lines pass; the sixth is untested.** The whole
      gate, by hand, against `gpt-4o-mini` in the playground. It found three
      real bugs that 944 green unit tests did not, which is the second time on
      this project that a live run has caught what the suite could not.

      | Gate line | Result |
      |---|---|
      | Run 1 drafts `deploy-api`, pending | pass, 4 turns, confidence 0.50 |
      | Approval enables it | pass, withheld by `get_ranked` while pending |
      | Run 2 announces the skill | pass, `◈ using skill deploy-api (0.50)`, 0.55 |
      | Run 3 reaches 0.60 | pass, `use_count=2` |
      | A deliberate break drops it to 0.50 | pass, exactly 0.50 |
      | `cat ~/skills/deploy-api/SKILL.md` | pass, valid `+++` TOML frontmatter |
      | Run 2 uses **fewer turns** than run 1 | **untested, see below** |

      The bugs, each fixed test-first and re-verified live:

      - `cd789cb` **B3 had no call site.** `SkillCrystalliser.from_run` was
        built in Task 6, covered by 18 unit tests, and called from nowhere in
        `sable/`. A completed multi-step goal drafted nothing, so gate line 1
        was unreachable by any code path. The 18 tests passed throughout
        because each called `from_run` directly, which is exactly what
        production did not do.
      - `dbdd082` **A silent command read as "still running".** A deploy
        script that wrote files and printed nothing reached the model as
        `(no output)`; it answered with a `done` that abandoned the goal after
        one of three steps, 4 runs out of 4. `_reap` had been collecting the
        exit status and discarding it.
      - `9ace438` **Confidence could only rise.** `_grade_skills` passed a
        hardcoded `True`, so the break-on-purpose line could not pass on the
        orchestrator path. The worker's `grade_skills_used` had done this
        properly since Task 5 and was called from `worker.py` alone.

      The **turn-count line is untested, not passed and not failed.** Two
      attempts were both invalid by construction: a goal naming all three
      commands leaves a skill nothing to save (4 turns either way), and a bare
      "deploy the api" is undiscoverable from the agent's cwd, so run 1
      invented a placeholder command and failed at turn one (2 turns either
      way). A valid test needs a goal that is vague *and* discoverable, so
      run 1 pays turns to explore and run 2 gets the procedure from the skill.
      It is owed, and it measures model behaviour more than it measures this
      feature, which is why the phase is being closed without it rather than
      on it.

      Three further findings, recorded rather than fixed here:

      - **Validators do not run on the orchestrator path**, by choice. The
        worker validates inside `bwrap`; the orchestrator runs unsandboxed in
        the user's real cwd, so auto-executing a model-authored `validate`
        command there needs Phase 3's policy tiers first. Grading therefore
        catches a run that failed visibly and not one the model wrongly
        believes succeeded (B5's stated purpose), and `_run_failed` says so.
      - **The same shape recurred three times**: B1, B3 and B5 were each
        built, tested and wired into the *worker*, and each was missing from
        the orchestrator, which is the path a typed goal actually takes.
        `test_loop_orchestrator_wiring.py` now asserts the REPL passes every
        collaborator the agent accepts, which is the seam unit tests cannot
        see.
      - **`sqlite3` is not installed in the playground image.** Debugging
        commands that use it fail silently and read as empty state.
- [x] **Step 3:** Commit: `docs: Phase 2 contracts for skills, corrections and aliases`

---

## Verification

```bash
# unit, in the playground (ptyprocess is Unix-only)
MSYS_NO_PATHCONV=1 docker run --rm -v "/c/Parth/Agentic_OS:/app" \
  --cap-add=SYS_ADMIN --security-opt seccomp=unconfined \
  sable-playground bash -c "cd /app && PYTHONPATH=/app pytest tests/unit/ \
  -o addopts='-p no:libtmux' -q"

# integration (~90s, foreground)
... pytest tests/integration/ -o addopts='-p no:libtmux' -q

# conventions: both must be zero
grep -rn "except Exception" sable/ --include=*.py
grep -rn "^\s*except:" sable/ --include=*.py
```

Every task ends with unit green and a `CHANGELOG.md` *Unreleased* entry.
Branch per PR, `feat/<ID>-<slug>`, squash-merge. No em dashes in touched files.
