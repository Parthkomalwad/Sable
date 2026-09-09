# task.md Sable Implementation Tasks

---

## Phase 0 Repo Setup ✅

- [x] Scaffold all directories and empty stub files with docstrings (`shell/`, `shell/llm/`, `shell/config/`, `shell/telemetry/`, `shell/memory/`, `shell/tui/`, `tests/unit/`, `tests/integration/`, `tests/fixtures/`)
- [x] Create `__init__.py` for every package (`shell/`, `shell/llm/`, `shell/config/`, `shell/telemetry/`, `shell/memory/`, `shell/tui/`)
- [x] Create `dev_setup.sh` venv creation, pip install deps, pre-commit hooks
- [x] Create `requirements.txt` all approved dependencies pinned
- [x] Create `tests/fixtures/mock_llm.py` canned LLMResponse objects for unit tests
- [x] Create `docker/Dockerfile` (stub) Python base image, copy shell/, set login shell
- [ ] Verify Docker container starts and drops into bash

---

## Phase 1 Core Shell Loop ✅

> **Build in this exact order. Do not skip ahead.**

### Step 1: main.py ✅
- [x] Implement SSH_ORIGINAL_COMMAND bypass as first executable line (`shell/main.py`)
- [x] Add startup entry point that launches the REPL (placeholder call to `loop.start()`)
- [ ] Write `tests/unit/test_main.py` verify SSH bypass logic (mock `os.environ`, `os.execvp`)

### Step 2: executor.py ✅
- [x] Implement `cd` interception via `os.chdir()` expand `~` and env vars (`shell/executor.py`)
- [x] Implement command execution via `ptyprocess.PtyProcessUnicode.spawn()` (`shell/executor.py`)
- [x] Stream pty output to Rich Console (`shell/executor.py`)
- [ ] Write `tests/unit/test_executor.py` cd interception, ptyprocess routing

### Step 3: loop.py ✅
- [x] Implement prompt_toolkit REPL with cwd in prompt (`shell/loop.py`)
- [x] Add `FileHistory` for persistent command history (`shell/loop.py`)
- [x] Wire input to `executor.run()` for raw bash passthrough (`shell/loop.py`)

### Step 4: router.py ✅
- [x] Implement prefix mode `>>` prefix routes to LLM, everything else to bash (`shell/router.py`)
- [x] Implement auto-detect mode bash score vs NL score heuristics (`shell/router.py`)
- [x] Implement tie-breaking prompt `[b]ash or [a]gentic?` on equal scores (`shell/router.py`)
- [ ] Write `tests/unit/test_router.py` 30+ inputs with known routes including edge cases

### Step 5: llm/base.py + llm/ollama.py ✅
- [x] Define `LLMResponse` dataclass (`shell/llm/base.py`)
- [x] Define abstract `LLMBackend` base class with `async complete()` method (`shell/llm/base.py`)
- [x] Implement `build_system_prompt(cwd, user, os_info)` function (`shell/llm/base.py`)
- [x] Implement JSON parse fallback chain (`shell/llm/base.py`)
- [x] Create `shell/llm/pricing.json` with model pricing table
- [x] Implement `OllamaBackend` with NDJSON streaming, `timeout=httpx.Timeout(30.0)` (`shell/llm/ollama.py`)

### Step 6: safety.py ✅
- [x] Define `DESTRUCTIVE_PATTERNS` regex blocklist (`shell/safety.py`)
- [x] Implement `shannon_entropy()` and `looks_like_secret()` (`shell/safety.py`)
- [x] Implement confirm flow display warning with Rich, require literal `YES` (`shell/safety.py`)
- [x] Implement `strip_secrets()` for privacy mode (`shell/safety.py`)
- [ ] Implement dry-run option for file-touching commands (`shell/safety.py`)
- [ ] Write `tests/unit/test_safety.py` all blocklist patterns, entropy function, confirm flow

### Step 7: planner.py ✅
- [x] Implement plan array execution iterate `plan` list from LLM response (`shell/planner.py`)
- [x] Add Rich Tree display show all steps upfront (`shell/planner.py`)
- [x] Implement step failure prompt: `[c]ontinue [r]etry [a]bort` (`shell/planner.py`)
- [x] Wire safety check per plan step (`shell/planner.py`)

### Phase 1 Integration ✅
- [x] Wire router → LLM → safety → executor pipeline in `loop.py`
- [x] Add command display + edit before run (Rich Syntax highlight, `[Enter]/[e]/[q]`) (`shell/loop.py`)
- [x] Add offline fallback catch httpx errors, set offline flag, route to bash (`shell/loop.py`)
- [x] Add `Ctrl+B` escape hatch one-shot bypass to bash (`shell/loop.py`)

---

## Phase 2 Config, Backends, Telemetry ✅

### Config ✅
- [x] Implement `ShellConfig` dataclass with all fields and defaults (`shell/config/schema.py`)
- [ ] Write `tests/unit/test_config.py` schema validation, defaults, invalid input rejection
- [x] Implement first-run setup wizard with prompt_toolkit, `is_password=True` for API key (`shell/config/wizard.py`)
- [x] Set config file permissions to `600` programmatically (`shell/config/wizard.py`)
- [x] Wire config loading into `main.py` startup run wizard if config missing

### Additional LLM Backends ✅
- [x] Implement `OpenAIBackend` with httpx SSE streaming, `timeout=httpx.Timeout(30.0)` (`shell/llm/openai.py`)
- [x] Implement `AnthropicBackend` must include `anthropic-version: 2023-06-01` header, httpx SSE streaming (`shell/llm/anthropic.py`)

### Telemetry ✅
- [x] Define `TokenEvent` dataclass (`shell/telemetry/events.py`)
- [x] Implement SQLite WAL-mode init (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL`) (`shell/telemetry/db.py`)
- [x] Create `token_events` + `session_memory` tables (`shell/telemetry/db.py`)
- [x] Implement libtmux session spawn, 80/20 horizontal pane split (`shell/tui/layout.py`)
- [x] Implement `Ctrl+T` toggle for sidebar pane visibility (`shell/tui/layout.py`)
- [x] Auto-hide sidebar if terminal width < 100 columns (`shell/tui/layout.py`)
- [x] Implement `watch.py` poll db every 2s, render Rich panel (`shell/telemetry/watch.py`)

### Budget & Stats ✅
- [x] Implement `check_budget()` query db, return `OK/WARNING/HARD_STOP` (`shell/telemetry/db.py`)
- [x] Add budget enforcement in main loop warn at 80%, hard stop at 100% (`shell/loop.py`)
- [x] Add `/budget reset` command to clear hard-stop flag (`shell/loop.py`)
- [x] Implement `shell stats` / `/stats` command Rich table, last 7 days (`shell/loop.py`)
- [x] Implement `shell stats --csv` output (`shell/loop.py`)

### Memory ✅
- [x] Implement `should_compress()` trigger logic 2000 tokens or 5 turns (`shell/memory/compressor.py`)
- [x] Integrate `token-reducer` with `CompressionLevel.MODERATE` and `TaskContext.RAG` (`shell/memory/compressor.py`)
- [x] Preserve last 2 turns verbatim, archive older turns as JSON in db (`shell/memory/compressor.py`)
- [ ] Write `tests/unit/test_compressor.py` trigger thresholds, last 2 turns always preserved
- [x] Implement session context load/save to `session_memory` table (`shell/memory/store.py`)
- [x] Implement session resume on login load context under 500 tokens, display banner (`shell/main.py`)
- [x] Implement `shell memory` command display active context, `[c]` to clear (`shell/loop.py`)

### Keyring ✅
- [x] Implement secretstorage keyring integration for API keys (`shell/config/keyring.py`)
- [ ] Migrate API key storage from config.json to keyring (`shell/config/wizard.py`)

### Integration Tests
- [ ] Write `tests/integration/test_full_loop.py` input → route → LLM → safety → execute → log
- [ ] Write `tests/integration/test_llm_backends.py` SSE parsing, JSON fallback chain for all 3 backends
- [ ] Write `tests/integration/test_session_resume.py` context loaded and injected correctly

---

## Phase 3 Hardening ✅

- [x] Performance audit cold start under 300ms, profile and optimize imports (all heavy imports are lazy inside functions)
- [x] Implement TUI settings panel Ctrl+X / `/config` overlay with live edit (`shell/tui/panel.py`)
- [x] Implement privacy mode `strip_secrets()` applied before LLM calls (`shell/loop.py` via `config.privacy_mode`)
- [x] Implement audit log append to `/var/log/agentic-shell/audit.log`, fail silently on permission error (`shell/loop.py`)
- [x] Prompt injection hardening system prompt note: file contents are UNTRUSTED DATA (`shell/llm/base.py`)
- [x] Create `install.sh` register shell, install deps, pre-cache tiktoken encodings, create audit log
- [x] Create `uninstall.sh` restore login shell, clean up
- [x] Finalize `docker/Dockerfile` production image, login shell configured, audit log dir created
- [x] Write `README.md` setup, usage, configuration, architecture overview
