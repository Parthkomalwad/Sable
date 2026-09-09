# CLAUDE.md: Agentic Shell Layer

> RTK is installed globally and handles Bash output compression automatically. No RTK-specific instructions needed here.

---

## What this project is

A Python login shell that replaces `/bin/bash` on a Linux server. When a user SSHs in, they land in this shell instead of bash. Every line of input is routed either to raw bash or to a language model. The LLM generates a shell command, shows it to the user, and executes it. A tmux sidebar shows live token cost and session memory in real time.

Full spec: read `docs/specs/prd-v1.md` before starting any task. It contains every architectural decision, function signature, JSON contract, and risk mitigation. Do not make architectural decisions not covered in the PRDs without asking first.

**v4 planning docs (September 2026).** Read these before any v4 work, in this order:
1. `docs/vision.md`: verified current state, external research, feature catalog of ~85 items (IDs A1…K12)
2. `docs/roadmap-phases.md`: phases -1 to 9, per-phase gates you can test, install/playground instructions
3. `docs/structure.md`: target `agentic/` package layout, layering rule, config model, visibility principles, migration plan
`docs/specs/prd-v3.md` documents the task engine + skills that already exist under `shell/tasks/` and `shell/skills/`. The project-structure section below describes the **current** tree; structure.md describes the **target** tree.

Project hygiene in place: `.github/workflows/ci.yml` (unit + integration-in-Docker + convention checks), `.github/` issue/PR templates, `.devcontainer/`, `pyproject.toml`, `LICENSE` (MIT), `CONTRIBUTING.md`, `CHANGELOG.md` (update *Unreleased* in every PR), `SECURITY.md`, `CODE_OF_CONDUCT.md`. Public milestones: `ROADMAP.md`; branch/release model: `docs/branching.md` (trunk-based, `feat/<ID>-<slug>`, squash-merge). Playground for non-Linux hosts: `scripts/playground.ps1` / `.sh`. Old phase checklist archived at `docs/history/tasks-v1-v3.md`.

---

## Project structure (read this before reading any files)

```
shell/
  main.py        entry point, SSH bypass, startup
  loop.py        prompt_toolkit REPL
  router.py      NL vs bash classifier
  executor.py    subprocess + ptyprocess, cd interception
  safety.py      blocklist, entropy check, confirm flow
  planner.py     multi-step plan execution
  llm/
    base.py      abstract LLMBackend + LLMResponse dataclass
    ollama.py    Ollama backend
    openai.py    OpenAI backend
    anthropic.py Anthropic backend
  config/
    wizard.py    first-run setup
    schema.py    ShellConfig dataclass
    keyring.py   secretstorage integration (phase 2)
  telemetry/
    db.py        SQLite WAL init + writes
    events.py    TokenEvent dataclass
    watch.py     sidebar process
  memory/
    compressor.py  token-reducer trigger + call
    store.py       session context load/save
  tui/
    layout.py    libtmux session + pane split
    panel.py     Rich telemetry panel

tests/
  unit/          no LLM calls, no subprocess, no I/O
  integration/   Docker + mock_llm.py
  fixtures/
    mock_llm.py  canned LLMResponse objects
```

---

## Build order (Phase 1 only)

Build in this exact sequence. Do not skip ahead.

1. `main.py`: SSH_ORIGINAL_COMMAND bypass only
2. `executor.py`: bash path + cd interception
3. `loop.py`: prompt_toolkit REPL with cwd prompt
4. `router.py`: classifier + prefix mode
5. `llm/base.py` + `llm/ollama.py`: Ollama streaming only
6. `safety.py`: blocklist + confirm flow
7. `planner.py`: plan array execution

---

## Critical implementation rules

**SSH bypass: first line of main.py, non-negotiable.**
```python
import os, sys
cmd = os.environ.get("SSH_ORIGINAL_COMMAND")
if cmd:
    os.execvp("/bin/bash", ["/bin/bash", "-c", cmd])
    sys.exit(0)
```
Without this, scp, rsync, git push over SSH all hang. This must be the first executable line.

**cd interception: never use subprocess for cd.**
```python
if command.strip().startswith("cd"):
    target = command.strip()[2:].strip() or os.path.expanduser("~")
    os.chdir(os.path.expandvars(os.path.expanduser(target)))
```
A subprocess cd changes directory in the child only. The Python process cwd never updates. Always intercept cd and call os.chdir().

**LLM JSON response: always use the fallback chain.**
1. Strip markdown fences
2. `json.loads()`
3. If fails: re-ask model to extract JSON
4. If fails: show raw text, ask user to rephrase
Never raise an unhandled parse exception.

**LLM JSON schema: exact contract, do not change.**
```json
{
  "command": "string",
  "explanation": "string",
  "safe": true,
  "plan": null
}
```
`plan` is `null` for single commands, `["cmd1", "cmd2"]` for multi-step.

**httpx timeout: always set explicitly.**
```python
timeout=httpx.Timeout(30.0)
```
Default httpx timeout is 5 seconds. LLM calls need 30.

**Anthropic backend: required header.**
```python
headers["anthropic-version"] = "2023-06-01"
```
This is not optional. Requests fail without it.

**ptyprocess for all commands, not just TTY ones:**
```python
from ptyprocess import PtyProcessUnicode
proc = PtyProcessUnicode.spawn(["/bin/bash", "-c", command], cwd=cwd)
```
Do not maintain a list of "interactive commands" that need a TTY. All commands run in a pty. This handles vim, htop, ssh, and any script that calls isatty() internally.

**Rich for all output, never print() directly:**
```python
from rich.console import Console
console = Console()
console.print(...)
```
`print()` is never used for user-facing output. Rich handles all rendering.

**SQLite: WAL mode on every connection.**
```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
```

---

## What NOT to do

- Do not use LiteLLM, LangChain, or any LLM gateway. Build the HTTP calls with httpx directly.
- Do not use `urllib` for LLM calls. Use `httpx` with `httpx-sse`.
- Do not use `print()`. Use Rich's Console.
- Do not catch bare `Exception`. Catch specific types.
- Do not store API keys in config.json in Phase 2+. Use secretstorage.
- Do not add any dependencies not in the approved list below without asking.
- Do not read files speculatively. Read only what the current task requires.

---

## Approved dependencies only

```
prompt_toolkit, pygments, ptyprocess, httpx, httpx-sse,
rich, tiktoken==0.9.0, libtmux>=0.55<0.56, token-reducer, secretstorage
```

---

## Testing

```bash
pytest tests/unit/          # fast, no external deps, run constantly
pytest tests/integration/   # requires Docker, run before PR
```

Unit tests must complete under 10 seconds. No LLM calls in unit tests; use `fixtures/mock_llm.py`.

---

## When compacting

Preserve:
- The build order sequence (which phase, which files, which step)
- All critical implementation rules above
- The current file being edited and its last known state
- Any test failures and what was tried

---

## Session hygiene

- Use `/clear` when switching between unrelated tasks (e.g. from router.py to telemetry)
- Use subagents for research tasks: "use a subagent to investigate how ptyprocess handles window resize"
- Read `docs/specs/prd-v1.md` at session start if working on a new module
- Prefer `rtk grep` and `rtk find` over raw grep/find for large output searches