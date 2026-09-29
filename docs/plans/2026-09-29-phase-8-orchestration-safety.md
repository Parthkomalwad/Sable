# Phase 8 Implementation Plan: Deeper orchestration, rehearsal and safety (v1.0, part 3)

> Checkbox tasks, failing-test-first, one PR per task, a live gate run at the
> end. Style follows the Phase 3 to 7 plans.

**Goal:** Sable can split a big goal into parallel lanes, check its own work,
try risky changes on a copy first, undo what it changed, and unlock a blocked
command only with a second factor.

**Deliverables:** A3, A4, A8, F2, F5, K5, K6, K7, K8.

**Gate (from `docs/roadmap-phases.md` Phase 8, adjusted for the decisions below):**

```
"migrate the DB and run tests in parallel with linting" -> a plan graph with three lanes and a join; /dash shows it; the reviewer's verdict is shown
A 3-step plan editing a test copy of /etc/nginx is rehearsed first: diff and per-step exit codes, then apply
/undo restores those files byte for byte
A deny-tier command offers step-up: a valid TOTP code runs it once; the same code again, a wrong code, and a daemon request are refused
A sub-agent started without network cannot curl out; one over its memory limit is stopped, and the gate records which limits the host supports
An unsigned imported skill runs one tier stricter than a signed one
```

---

## 0. Decisions (2026-09-28 and 2026-09-29)

| Question | Decision |
|---|---|
| Snapshots and undo | **Plain copies, no root.** Before a step changes files, the paths it will touch are copied into `~/.sable/snapshots/<id>/` with a manifest (path, mode, sha256). `/undo` copies them back. A ring of 20 snapshots per path; a path over 50 MB is refused with a clear message, never skipped silently. Plain copies instead of git, because `/etc` is not a repository and a copy needs nothing installed. |
| Rehearsal | **A copy, mounted where the real path is.** The touched paths are copied to a temp dir and the plan runs inside `bwrap` with each copy bound over its real path, so the commands see the usual paths and change only the copies. Then the diff and each step's exit code are shown, and you choose apply or abort. Without `bwrap` rehearsal is unavailable and the plan says so. Steps that act outside the filesystem (`systemctl`, network) are marked "not rehearsable" and are not faked. |
| Which paths a step touches | The step's declared `touches` list, plus paths parsed from the command (existing paths and redirection targets), plus the paths of `fs.*` tools. When nothing can be found the step is marked "unknown footprint" and rehearsal says so. |
| Step-up approval | **TOTP (RFC 6238, stdlib `hmac`) or a phone push** (the Phase 5 approval path). Setup prints the `otpauth://` URI and the secret for your authenticator app; no QR library. Each grant is for one command, once, within 2 minutes. Never available to the daemon or to workers. |
| Resource limits | Network off per sub-agent with `bwrap --unshare-net`. Memory, CPU time and process count with `setrlimit` in the child (stdlib `resource`). Where a limit cannot be applied, Sable says so. No cgroups dependency. |
| Signed skills | HMAC-SHA256 with a local key in the keyring. Skills you write or approve are signed; imported or edited-outside-Sable skills are unsigned and run one policy tier stricter. Public-key signing is later. |
| New dependencies | **None.** |

### 0.1 Not in scope

Docker or Podman sandbox backends, cgroup v2 controllers, FIDO2, public-key
skill signatures, snapshotting databases or block devices.

---

## Task 0: Plan graphs (A3, serial, first)

Files: `sable/agents/graph.py`, `sable/agents/orchestrator.py`,
`sable/llm/prompts/orchestrator.md`, `sable/ui/state.py` (read only).

- [ ] Failing tests: a graph validates (ids unique, `needs` exist, no cycles,
      depth at most 5); ready lanes are those whose needs are done; a failed
      lane stops its dependants and not its siblings.
- [ ] A `graph` action: `{"lanes": [{"id", "goal", "needs": []}]}`. Ready
      lanes start as sub-agents through the existing spawn path; the
      orchestrator waits on the bus for completions and starts the next.
      The join is a lane that needs the others.
- [ ] Lane state is published so `/dash` and the sidebar show it.

## Task 1: Reviewer agent (A4)

Files: `sable/agents/reviewer.py`, `sable/llm/prompts/reviewer.md`.

- [ ] Before a goal with changes is reported done, one call to the reviewer
      model (`models.reviewer`, default the orchestrator's) with the goal, the
      steps and their output. Verdict JSON `{"verdict": "pass|concerns|fail",
      "why": "..."}` through the JSON fallback chain.
- [ ] `fail` sends the work back once with the reason; a second `fail` asks
      you. The reviewer has no tools and runs nothing.

## Task 2: Snapshots and undo (A8, K6)

Files: `sable/core/snapshots.py`, `sable/agents/footprint.py`,
`sable/app/builtins/undo.py`.

- [ ] Footprint: declared `touches`, parsed paths and redirections, fs tool
      paths; tests for common commands (`sed -i`, `cp`, `mv`, `>`, `tee`).
- [ ] `take(paths) -> id`, `restore(id)`, `diff(id)`, ring and size limits,
      modes and missing files restored exactly (a file created by the step is
      removed on undo).
- [ ] Every state-changing step is snapshotted first; `/undo` (last step),
      `/undo <snapshot>`, `/task diff <n>`, `/task undo <n>`.

## Task 3: Rehearsal (F2, K5), after Task 2

Files: `sable/agents/rehearse.py`, the plan and graph paths.

- [ ] Copy the footprint to a temp dir, run the plan in `bwrap` with each
      copy bound over its real path, collect exit codes, diff copy against
      original, then `apply / abort`. Apply runs the plan for real, with
      snapshots.
- [ ] On by default for plans with 2 or more state-changing steps and for
      every daemon job; `rehearse: off` in config turns it off.
- [ ] Not rehearsable and unknown footprint are shown, never hidden.

## Task 4: Resource limits (F5)

Files: `sable/agents/sandbox.py`, `sable/agents/limits.py`.

- [ ] `network: off` per task adds `--unshare-net`; `limits: {mem_mb, cpu_s,
      procs}` applied with `setrlimit` in the child; defaults in config.
- [ ] `sable doctor` and `/task stats` show which limits are in force on
      this host.

## Task 5: Step-up approval (K7)

Files: `sable/policy/stepup.py`, `sable/app/builtins/stepup.py`,
`sable/policy/engine.py`.

- [ ] RFC 6238 TOTP with the RFC test vectors; one step of clock drift
      allowed; a code is never accepted twice.
- [ ] A deny-tier command in the interactive shell offers `step-up (code or
      phone) / cancel` when step-up is set up; the grant covers that exact
      command once. Workers, the daemon and `--mcp-serve` never get the
      offer. Every attempt audited with the factor used.
- [ ] `/stepup setup`, `/stepup status`, `/stepup off`.

## Task 6: Signed skills and source trust (K8)

Files: `sable/skills/signing.py`, `sable/skills/index.py`.

- [ ] `source` per skill (`user`, `crystallised`, `imported:<origin>`);
      signature over the skill folder's files; sign on create and approve.
- [ ] Unsigned or tampered skills run one tier stricter; `/skill list`
      shows signed / unsigned; `/skill sign <name>` after you have read it.

## Task 7: Docs and the live gate run

- [ ] `docs/orchestration.md`; `scripts/gate_phase8.py` in the playground
      against gpt-4o-mini; runs recorded in `docs/history/`; `ROADMAP.md`;
      CHANGELOG; the docs site.

Order: Task 0 alone; then Tasks 1, 2, 4, 5, 6 in parallel; Task 3 after
Task 2; then Task 7.
