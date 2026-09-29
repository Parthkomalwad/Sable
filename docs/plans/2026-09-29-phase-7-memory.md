# Phase 7 Implementation Plan: Memory Palace and portability (v1.0, part 2)

> Checkbox tasks, failing-test-first, one PR per task, a live gate run at the
> end. Style follows the Phase 3 to 6 plans.

**Goal:** Sable remembers what it learns about a server, can show where each
fact came from, forgets on request, and can be checked, upgraded and moved to
another machine.

**Deliverables:** C6 (subsumes C1, C2, C3, C5), I6, I8.

**Gate (from `docs/roadmap-phases.md` Phase 7, adjusted for the decisions below):**

```
Session 1: "where does myapp write its logs?" -> reads /etc/myapp/app.conf, answers /var/log/myapp; a fact is saved
Session 2 (fresh): same question -> answered from memory, zero commands run
/palace why <fact> -> the session, the goal and the command that produced it
/remember "deploys go out on Tuesdays" -> /palace user lists it; /forget <id> -> recall no longer finds it
Two near-duplicate facts -> consolidation keeps one, with both sources
A fact learned after reading a web page is marked untrusted and injected as data
sable doctor on an old home dir (config without schema_version) -> reports, migrates, reports clean
sable export on home A, sable import on home B -> /skill list and /palace match; no secrets, no state
```

---

## 0. Decisions (2026-09-28 and 2026-09-29)

| Question | Decision |
|---|---|
| Search | **SQLite FTS5**, stdlib `sqlite3` (FTS5 is present in the playground's SQLite 3.45 and on Windows 3.50). Local embeddings through Ollama are **deferred**: add them only if the gate shows FTS recall falling short. |
| Storage | **Markdown files are the truth.** One fact per file under `~/.sable/palace/<room>/<id>.md` with a small `key: value` header (no YAML parser). The FTS index lives in the session database and can be rebuilt from the files at any time (`sable doctor --reindex`). |
| Rooms and tiers | Rooms: `server/`, `repos/<name>/`, `user/`, `incidents/`. Procedures stay where they are, in skills. Tiers: `episodic` (seen once, from a goal) and `semantic` (confirmed, or written by you). Working memory is the turn context and needs no storage. |
| Who writes facts | You, with `/remember`. The orchestrator, through a `facts` list on its `done` action; those land `episodic` with provenance (session, goal, the commands that ran). A fact written while the agent was tainted is stored `untrusted: yes` and always injected framed as data. |
| Consolidation | **Rules, no model**, in the daemon at `maintenance_time` (default 02:30): merge near-duplicates (normalised token overlap of 0.8 or more, keeping every source), promote an episodic fact seen in two separate sessions to semantic, drop episodic facts older than 30 days, expire facts past `valid_to`. |
| Sync | `sable sync <git-remote>` for skills, palace and policy. Never secrets (keyring), never state (DB, logs, sessions). |
| New dependencies | **None.** |

### 0.1 Memory is untrusted input too

A recalled fact came from a model reading a machine, so it can be wrong or
planted. Recall is injected as one budgeted block (about 800 tokens), framed
like repo context ("notes from memory, may be stale; verify before acting on
them"), and `untrusted` facts carry that label per line. A fact never grants
permission: policy decides every command exactly as before.

### 0.2 Not in scope

Embeddings, a user-preference inference engine (C2 is `/remember` into
`user/` only), C4 environment fingerprint, cross-host palace merge beyond
git sync.

---

## Task 0: The palace store (C6, serial, first)

Files: `sable/memory/palace.py`.

- [ ] Failing tests: `remember` writes a file and an index row; `recall`
      ranks by FTS relevance within a room or across rooms and respects `k`;
      `forget` removes both; `why` returns the header; the index rebuilds
      from files alone; ids are stable; room names are sanitised (no `..`).
- [ ] `remember(text, room, source: dict, tier="episodic", untrusted=False,
      valid_to=None) -> id`, `recall(query, room=None, k=8) -> list[Fact]`,
      `forget(id)`, `why(id) -> Fact`, `rooms()`, `reindex()`.
- [ ] Text is redacted with the existing secret scanner before it is stored.

## Task 1: Agents read and write memory (C6, C1)

Files: `sable/agents/orchestrator.py`, `sable/agents/worker.py`,
`sable/agents/context.py`, `sable/llm/prompts/orchestrator.md`.

- [ ] A recall block for the goal at the top of every orchestrator and worker
      context, budgeted, framed as data (0.1).
- [ ] `done` may carry `"facts": ["..."]`; each is remembered `episodic`
      with provenance `{session, goal, commands}` and `untrusted` when the
      agent was tainted.
- [ ] The prompt tells the model to answer from memory when a fact answers
      the goal, and to say so.

## Task 2: `/palace`, `/remember`, `/forget` (C5, C2)

Files: `sable/app/builtins/palace.py`.

- [ ] `/palace` (rooms and counts), `/palace <room>`, `/palace find <text>`,
      `/palace why <id>`, `/remember <text> [--room user]` (semantic, source
      "you"), `/forget <id>`.

## Task 3: Nightly consolidation (C3)

Files: `sable/memory/consolidate.py`, a daemon handler.

- [ ] The rules in section 0, idempotent, each change logged and published as
      `palace.consolidated`. Runs once a day at `maintenance_time`, and on
      demand with `/palace consolidate`.

## Task 4: `sable doctor` and migrations (I6)

Files: `sable/core/doctor.py`, `sable/core/config/migrate.py`,
`sable/app/main.py` (subcommand).

- [ ] `schema_version` in config; numbered migrators, each a pure function
      with a test; the current config becomes version 2, a config without the
      key is version 1.
- [ ] `sable doctor`: python and tmux and bwrap, user namespaces, DB
      integrity (`PRAGMA integrity_check`), stale tasks, orphan tmux windows,
      palace index versus files, config version. Reports, then `--fix`
      applies migrations and reindexes; it never deletes user data.

## Task 5: `sable export`, `import`, `sync` (I8)

Files: `sable/core/portable.py`, `sable/app/main.py` (subcommands).

- [ ] `export [file]`: a tarball of skills, palace and user policy and hooks,
      with a manifest. It refuses paths outside those roots and never includes
      the keyring, the DB, logs or `config.json` secrets.
- [ ] `import <file>`: shows what it adds and what it would overwrite, asks,
      then writes; the palace is reindexed. Rejects absolute paths, `..` and
      links in the archive.
- [ ] `sync <git-remote>`: the same set in a git work tree under
      `~/.sable/sync`, pull then push, conflicts reported, never forced.

## Task 6: Docs and the live gate run

- [ ] `docs/memory.md`; `scripts/gate_phase7.py` in the playground against
      gpt-4o-mini; runs recorded in `docs/history/phase-7-gate-<date>.md`;
      `ROADMAP.md` ticks the memory line; CHANGELOG.

Order: Task 0 alone; then Tasks 1, 2, 3 in parallel (they only call the
palace API) with Tasks 4 and 5 alongside (independent); then Task 6.
