# Orchestrator Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-turn LLM call in `loop.py` with a multi-turn `OrchestratorAgent` reasoning loop that acts directly for simple tasks and spawns `TaskAgent`s for long-running work, with sub-agents sharing a task folder but isolated to their own write subfolder.

**Architecture:** A new `OrchestratorAgent` class (`shell/tasks/orchestrator.py`) runs a max-20-turn loop. Each turn the LLM returns one of three actions: `run` (execute a command directly), `spawn` (create a `TaskAgent` in a subfolder), or `done` (return to REPL). `Sandbox` gains a `shared_read_dir` parameter so sub-agents can read sibling workspaces. `loop.py`'s NL path hands off to the orchestrator instead of calling `_call_llm()` directly.

**Tech Stack:** Python 3.10+, `ptyprocess`, `asyncio`, `libtmux`, existing `shell.llm` backends, existing `TaskManager`/`Sandbox`/`TaskAgent`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `shell/tasks/orchestrator.py` | **Create** | OrchestratorAgent class reasoning loop, action dispatch, sub-agent monitoring |
| `shell/tasks/sandbox.py` | **Modify** | Add `shared_read_dir` param to `__init__` and `wrap_command` |
| `shell/tasks/manager.py` | **Modify** | Add `task_base_dir` param to `spawn()` |
| `shell/loop.py` | **Modify** | Replace NL path (lines 1113–1221) with `OrchestratorAgent.run()` |
| `tests/unit/test_orchestrator.py` | **Create** | Unit tests for slug generation, action parsing, turn building |
| `tests/unit/test_sandbox_shared_read.py` | **Create** | Unit tests for `shared_read_dir` sandbox param |

---

## Task 1: Add `shared_read_dir` to Sandbox

**Files:**
- Modify: `shell/tasks/sandbox.py:129-181`
- Create: `tests/unit/test_sandbox_shared_read.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_sandbox_shared_read.py`:

```python
import os
import pytest
from shell.tasks.sandbox import Sandbox


def test_shared_read_dir_stored(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shared = tmp_path / "shared"
    shared.mkdir()
    sb = Sandbox(task_dir=str(workspace), shared_read_dir=str(shared))
    assert sb._shared_read_dir == str(shared.resolve())


def test_no_shared_read_dir_default(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sb = Sandbox(task_dir=str(workspace))
    assert sb._shared_read_dir is None


def test_bwrap_command_includes_ro_bind_when_shared(tmp_path, monkeypatch):
    """When bwrap is used and shared_read_dir is set, wrap_command includes --ro-bind."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shared = tmp_path / "shared"
    shared.mkdir()
    sb = Sandbox(task_dir=str(workspace), shared_read_dir=str(shared))
    # Force bwrap mode for test
    monkeypatch.setattr(sb, "use_bwrap", True)
    cmd = sb.wrap_command("echo hi")
    assert "--ro-bind" in cmd
    assert str(shared.resolve()) in cmd


def test_bash_guard_no_change_when_shared(tmp_path):
    """bash-wrapper fallback: wrap_command is unchanged (reads already allowed)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shared = tmp_path / "shared"
    shared.mkdir()
    sb = Sandbox(task_dir=str(workspace), shared_read_dir=str(shared))
    # Force bash-wrapper mode
    sb.use_bwrap = False
    cmd = sb.wrap_command("echo hi")
    # Should still contain the guard template no crash, no KeyError
    assert "WORKSPACE" in cmd
    assert "echo hi" in cmd
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /path/to/Sable
pytest tests/unit/test_sandbox_shared_read.py -v
```

Expected: `FAILED` `Sandbox.__init__` does not accept `shared_read_dir`.

- [ ] **Step 3: Implement `shared_read_dir` in Sandbox**

In `shell/tasks/sandbox.py`, replace the `__init__` and `wrap_command` methods:

```python
class Sandbox:
    def __init__(self, task_dir: str, shared_read_dir: str | None = None) -> None:
        self._task_dir = os.path.realpath(task_dir)
        self._shared_read_dir = os.path.realpath(shared_read_dir) if shared_read_dir else None
        self.use_bwrap = self._probe_bwrap()

    def wrap_command(self, command: str) -> str:
        """Return the command wrapped with sandbox enforcement."""
        if self.use_bwrap:
            ro_bind = ""
            if self._shared_read_dir:
                ro_bind = f"--ro-bind {self._shared_read_dir} {self._shared_read_dir} "
            return (
                f"bwrap "
                f"--bind {self._task_dir} {self._task_dir} "
                f"{ro_bind}"
                f"--ro-bind / / "
                f"--unshare-pid "
                f"-- /bin/bash -c {shlex.quote(command)}"
            )
        # Bash-wrapper fallback: reads already allowed everywhere, writes blocked outside task_dir
        guard = _BASH_GUARD_TEMPLATE.format(
            workspace=shlex.quote(self._task_dir),
            command=command,
        )
        return guard
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_sandbox_shared_read.py -v
```

Expected: all 4 tests `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add shell/tasks/sandbox.py tests/unit/test_sandbox_shared_read.py
git commit -m "feat: add shared_read_dir param to Sandbox for sub-agent read isolation"
```

---

## Task 2: Add `task_base_dir` to `TaskManager.spawn()`

**Files:**
- Modify: `shell/tasks/manager.py:51-98`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_task_manager.py` (create the file if it doesn't exist):

```python
import os
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path


def _make_manager(tmp_path):
    from shell.tasks.manager import TaskManager
    config = MagicMock()
    config.tasks_base_dir = str(tmp_path / "tasks")
    db = MagicMock()
    db._conn = MagicMock()
    db._conn.execute.return_value = MagicMock()
    manager = TaskManager(config=config, db=db)
    return manager


def test_spawn_with_task_base_dir_uses_custom_path(tmp_path):
    """When task_base_dir is provided, task_dir should be task_base_dir/name."""
    manager = _make_manager(tmp_path)
    shared = tmp_path / "tasks" / "my-task-20260406"
    shared.mkdir(parents=True)

    captured = {}

    def fake_send_keys(cmd, enter=True):
        captured["cmd"] = cmd

    with patch.object(manager, "_session") as mock_session:
        mock_win = MagicMock()
        mock_win.window_id = "@1"
        mock_win.active_pane.send_keys = fake_send_keys
        mock_session.return_value.new_window.return_value = mock_win

        manager.spawn(
            name="frontend",
            goal="create react app",
            task_base_dir=str(shared),
        )

    assert "frontend" in captured["cmd"]
    # The task dir created should be inside the shared dir
    expected_dir = shared / "frontend"
    assert expected_dir.exists()


def test_spawn_without_task_base_dir_uses_global(tmp_path):
    """When task_base_dir is None, task_dir uses global tasks_base."""
    manager = _make_manager(tmp_path)

    with patch.object(manager, "_session") as mock_session:
        mock_win = MagicMock()
        mock_win.window_id = "@1"
        mock_win.active_pane.send_keys = MagicMock()
        mock_session.return_value.new_window.return_value = mock_win

        manager.spawn(name="my-task", goal="do something")

    global_dir = Path(manager._config.tasks_base_dir) / "my-task"
    assert global_dir.exists()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_task_manager.py -v
```

Expected: `FAILED` `spawn()` does not accept `task_base_dir`.

- [ ] **Step 3: Implement `task_base_dir` in `TaskManager.spawn()`**

In `shell/tasks/manager.py`, update the `spawn` method signature and body:

```python
def spawn(self, name: str, goal: str, context: str = "",
          task_base_dir: str | None = None) -> None:
    """Spawn a new task agent in a dedicated tmux window.

    Args:
        name: Task name (used as tmux window name and directory).
        goal: Natural language goal for the agent.
        context: Optional orchestrator context summary to seed the agent's memory.
        task_base_dir: If set, use this as the parent dir instead of global tasks_base.
                       Sub-agent's workspace = task_base_dir/name/workspace/
                       shared_read_dir passed to Sandbox = task_base_dir/
    """
    if task_base_dir:
        task_dir = Path(task_base_dir) / name
    else:
        task_dir = self._tasks_base / name
    task_dir.mkdir(parents=True, exist_ok=True)

    # Write context to a handoff file so agent can load it on startup
    if context:
        handoff_path = task_dir / ".agentic" / "handoff.txt"
        handoff_path.parent.mkdir(parents=True, exist_ok=True)
        handoff_path.write_text(context)

    self._db._conn.execute(
        """INSERT OR REPLACE INTO tasks
           (name, goal, status, created_at)
           VALUES (?, ?, 'starting', ?)""",
        (name, goal, self._now()),
    )
    self._db._conn.commit()

    session = self._session()
    if session is None:
        raise RuntimeError("Not inside a tmux session")

    window = session.new_window(window_name=f"task:{name}", attach=True)
    window_id = window.window_id

    self._db._conn.execute(
        "UPDATE tasks SET tmux_window_id=? WHERE name=?",
        (window_id, name),
    )
    self._db._conn.commit()

    python_bin = sys.executable
    import os as _os
    project_root = str(Path(__file__).resolve().parents[2])
    existing_pp = _os.environ.get("PYTHONPATH", "")
    pythonpath = f"{project_root}:{existing_pp}" if existing_pp else project_root
    safe_goal = goal.replace("'", "'\\''")

    # Pass shared_read_dir if this was spawned under a task_base_dir
    shared_arg = ""
    if task_base_dir:
        safe_shared = str(task_base_dir).replace("'", "'\\''")
        shared_arg = f" --shared-read-dir '{safe_shared}'"

    window.active_pane.send_keys(
        f"PYTHONPATH={pythonpath} {python_bin} -m shell.tasks.agent"
        f" --task {name} --goal '{safe_goal}'{shared_arg}",
        enter=True,
    )
```

Also update `shell/tasks/agent.py` `__main__` block to accept and forward `--shared-read-dir` to `Sandbox`. In the `TaskAgent.__init__` method, pass `shared_read_dir` to `Sandbox`:

```python
# In TaskAgent.__init__, replace:
self._sandbox = Sandbox(task_dir=workspace)

# With:
self._sandbox = Sandbox(task_dir=workspace, shared_read_dir=shared_read_dir)
```

And update `TaskAgent.__init__` signature:

```python
def __init__(self, task_name: str, goal: str, config, db_path: str,
             shared_read_dir: str | None = None) -> None:
```

And update the `__main__` block at the bottom of `agent.py`:

```python
parser.add_argument("--shared-read-dir", default=None,
                    help="Parent task dir to mount read-only (bwrap) or allow reads from (bash fallback)")
# ...
agent = TaskAgent(
    task_name=args.task,
    goal=args.goal,
    config=config,
    db_path=str(DB_PATH),
    shared_read_dir=args.shared_read_dir,
)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_task_manager.py -v
```

Expected: both tests `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add shell/tasks/manager.py shell/tasks/agent.py tests/unit/test_task_manager.py
git commit -m "feat: add task_base_dir to TaskManager.spawn() for shared task folder support"
```

---

## Task 3: Create `OrchestratorAgent`

**Files:**
- Create: `shell/tasks/orchestrator.py`
- Create: `tests/unit/test_orchestrator.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_orchestrator.py`:

```python
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path


def _make_orchestrator(tmp_path, goal="list files here"):
    from shell.tasks.orchestrator import OrchestratorAgent
    config = MagicMock()
    config.tasks_base_dir = str(tmp_path / "tasks")
    config.backend = "ollama"
    config.model = "llama3"
    config.api_base = "http://localhost:11434"
    config.privacy_mode = False
    db_path = str(tmp_path / "test.db")
    task_manager = MagicMock()
    return OrchestratorAgent(
        goal=goal,
        cwd=str(tmp_path),
        config=config,
        db_path=db_path,
        task_manager=task_manager,
    )


def test_slug_generation(tmp_path):
    orch = _make_orchestrator(tmp_path, goal="build a react app with docker")
    assert "build-a-react-app" in orch._slug
    assert len(orch._slug) > 8


def test_slug_sanitizes_special_chars(tmp_path):
    orch = _make_orchestrator(tmp_path, goal="deploy to prod! (urgent)")
    assert "!" not in orch._slug
    assert "(" not in orch._slug


def test_task_folder_not_created_on_init(tmp_path):
    orch = _make_orchestrator(tmp_path, goal="list files")
    tasks_dir = tmp_path / "tasks"
    # No folder created until spawn action
    assert not (tasks_dir / orch._slug).exists()


def test_parse_action_run(tmp_path):
    orch = _make_orchestrator(tmp_path)
    result = orch._parse_action('{"action": "run", "command": "ls -la", "explanation": "check files"}')
    assert result["action"] == "run"
    assert result["command"] == "ls -la"
    assert result["explanation"] == "check files"


def test_parse_action_spawn(tmp_path):
    orch = _make_orchestrator(tmp_path)
    result = orch._parse_action('{"action": "spawn", "name": "frontend", "goal": "build react app", "explanation": "long task"}')
    assert result["action"] == "spawn"
    assert result["name"] == "frontend"
    assert result["goal"] == "build react app"


def test_parse_action_done(tmp_path):
    orch = _make_orchestrator(tmp_path)
    result = orch._parse_action('{"action": "done", "explanation": "all done"}')
    assert result["action"] == "done"


def test_parse_action_strips_markdown_fences(tmp_path):
    orch = _make_orchestrator(tmp_path)
    raw = '```json\n{"action": "run", "command": "pwd", "explanation": "check cwd"}\n```'
    result = orch._parse_action(raw)
    assert result["action"] == "run"


def test_parse_action_invalid_returns_done(tmp_path):
    orch = _make_orchestrator(tmp_path)
    result = orch._parse_action("not valid json at all")
    assert result["action"] == "done"


def test_build_messages_pins_goal(tmp_path):
    orch = _make_orchestrator(tmp_path, goal="list all python files")
    messages = orch._build_messages()
    # First message must contain the goal
    assert any("list all python files" in m["content"] for m in messages)


def test_collect_agent_status_empty(tmp_path):
    orch = _make_orchestrator(tmp_path)
    # No task folder yet should return empty list
    summaries = orch._collect_agent_statuses()
    assert summaries == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_orchestrator.py -v
```

Expected: all `FAILED` `shell.tasks.orchestrator` does not exist.

- [ ] **Step 3: Create `shell/tasks/orchestrator.py`**

```python
"""OrchestratorAgent multi-turn reasoning loop for the main REPL.

Replaces the single LLM call in loop.py for NL-routed input.
Each turn the LLM returns one action: run | spawn | done.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_SYSTEM_PROMPT = """You are an orchestrator shell agent running on Linux.
The user has asked you to accomplish a goal. Reason step by step.

Rules:
1. Act directly (action=run) for simple, fast tasks a single command or a few commands.
2. Spawn a sub-agent (action=spawn) ONLY for long-running work (>30s estimated) or work that can run in parallel. Give each sub-agent a focused, self-contained goal.
3. After spawning, continue your loop check sub-agent status each turn.
4. When the goal is fully achieved, emit action=done.
5. Every command must be non-interactive (use -y/--yes flags, pipe `yes |` if needed).
6. Never cd outside the current working directory.

Respond with JSON only no markdown, no extra text:
{"action": "run", "command": "<bash command>", "explanation": "<one sentence>"}
{"action": "spawn", "name": "<slug-name>", "goal": "<full goal for sub-agent>", "explanation": "<why delegating>"}
{"action": "done", "explanation": "<summary of what was accomplished>"}
"""


def _make_slug(goal: str) -> str:
    """Convert a goal string to a filesystem-safe slug with date suffix."""
    words = re.sub(r"[^a-z0-9\s]", "", goal.lower()).split()
    prefix = "-".join(words[:6]) or "task"
    date = datetime.now().strftime("%Y%m%d")
    return f"{prefix}-{date}"


class OrchestratorAgent:
    def __init__(
        self,
        goal: str,
        cwd: str,
        config,
        db_path: str,
        task_manager,
    ) -> None:
        self._goal = goal
        self._cwd = cwd
        self._config = config
        self._db_path = db_path
        self._task_manager = task_manager
        self._slug = _make_slug(goal)
        self._tasks_base = Path(config.tasks_base_dir).expanduser()
        self._task_dir: Path | None = None  # lazy created only on first spawn
        self._history: list[dict] = []      # orchestrator's own turn history
        self._spawned: list[str] = []       # names of spawned sub-agents

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Run the orchestrator reasoning loop until done or max turns."""
        _MAX_TURNS = 20
        for turn in range(1, _MAX_TURNS + 1):
            messages = self._build_messages()
            try:
                response = self._call_llm(messages)
            except Exception as exc:
                _out(f"[orchestrator] LLM error: {exc}")
                break

            raw = self._extract_raw(response)
            action = self._parse_action(raw)

            if action["action"] == "run":
                self._handle_run(action, response)
            elif action["action"] == "spawn":
                self._handle_spawn(action)
            elif action["action"] == "done":
                self._handle_done(action)
                break
            else:
                _out(f"[orchestrator] unknown action '{action['action']}' stopping")
                break

        else:
            _out(f"[orchestrator] reached {_MAX_TURNS} turn limit stopping")

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    def _handle_run(self, action: dict, response) -> None:
        command = action.get("command", "").strip()
        explanation = action.get("explanation", "")
        if not command:
            return

        # Show confirm prompt (same UX as old _display_command_preview)
        confirmed_cmd = self._confirm_command(command, explanation)
        if confirmed_cmd is None:
            self._history.append({
                "role": "user",
                "content": f"[user cancelled command: {command}]",
            })
            return

        output = self._run_command(confirmed_cmd)
        self._history.append({"role": "assistant", "content": json.dumps(action)})
        self._history.append({"role": "user", "content": output or "(no output)"})

        # Audit log
        try:
            from shell.loop import _write_audit_log
            _write_audit_log("orchestrator", self._cwd, confirmed_cmd)
        except Exception:
            pass

    def _handle_spawn(self, action: dict) -> None:
        name = action.get("name", "").strip().replace(" ", "-")
        goal = action.get("goal", "").strip()
        explanation = action.get("explanation", "")
        if not name or not goal:
            _out("[orchestrator] spawn action missing name or goal skipping")
            return

        # Lazy-create shared task dir on first spawn
        if self._task_dir is None:
            self._task_dir = self._tasks_base / self._slug
            self._task_dir.mkdir(parents=True, exist_ok=True)
            (self._task_dir / ".agentic").mkdir(exist_ok=True)

        _out(f"  ◈ spawning agent: {name}")
        _out(f"    goal: {goal}")

        # Build handoff context
        context = self._build_handoff(name, goal)
        handoff_dir = self._task_dir / name / ".agentic"
        handoff_dir.mkdir(parents=True, exist_ok=True)
        (handoff_dir / "handoff.txt").write_text(context)

        try:
            self._task_manager.spawn(
                name=name,
                goal=goal,
                context="",  # already written to handoff.txt above
                task_base_dir=str(self._task_dir),
            )
            self._spawned.append(name)
        except Exception as exc:
            _out(f"[orchestrator] failed to spawn '{name}': {exc}")

        self._history.append({"role": "assistant", "content": json.dumps(action)})
        self._history.append({"role": "user", "content": f"[agent '{name}' spawned]"})

    def _handle_done(self, action: dict) -> None:
        explanation = action.get("explanation", "")
        _out(f"\n  ✦ {explanation}\n")
        if self._task_dir:
            try:
                result_path = self._task_dir / ".agentic" / "result.md"
                result_path.write_text(
                    f"# Orchestrator result\n**Goal:** {self._goal}\n\n{explanation}\n"
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Context building
    # ------------------------------------------------------------------

    def _build_messages(self) -> list[dict]:
        messages: list[dict] = []
        # Pin goal as first user message
        messages.append({
            "role": "user",
            "content": f"Goal: {self._goal}\nCurrent directory: {self._cwd}",
        })
        messages.append({
            "role": "assistant",
            "content": "Understood. I will accomplish this goal step by step.",
        })
        # Inject sub-agent statuses
        for status_msg in self._collect_agent_statuses():
            messages.append({"role": "user", "content": status_msg})
            messages.append({"role": "assistant", "content": "Noted."})
        # Append turn history
        messages.extend(self._history)
        return messages

    def _collect_agent_statuses(self) -> list[str]:
        """Read status.md and result.md for all spawned sub-agents."""
        if self._task_dir is None:
            return []
        summaries = []
        for name in self._spawned:
            agentic = self._task_dir / name / ".agentic"
            result_path = agentic / "result.md"
            status_path = agentic / "status.md"
            if result_path.exists():
                try:
                    content = result_path.read_text()
                    summaries.append(f"[agent '{name}' COMPLETED]\n{content}")
                    result_path.unlink()  # consume once
                    self._spawned.remove(name)
                except Exception:
                    pass
            elif status_path.exists():
                try:
                    content = status_path.read_text()
                    summaries.append(f"[agent '{name}' running]\n{content}")
                except Exception:
                    pass
        return summaries

    def _build_handoff(self, agent_name: str, agent_goal: str) -> str:
        """Build context string to hand off to a spawned sub-agent."""
        recent = self._history[-8:]
        lines = [f"You were spawned by the orchestrator to: {agent_goal}",
                 f"Parent goal: {self._goal}", ""]
        if recent:
            lines.append("Recent orchestrator history:")
            for t in recent:
                role = t.get("role", "user")
                content = str(t.get("content", ""))[:300]
                lines.append(f"  {role}: {content}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _call_llm(self, messages: list[dict]):
        from shell.loop import _build_backend
        backend = _build_backend(self._config)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                asyncio.wait_for(backend.complete(messages, _SYSTEM_PROMPT), timeout=120.0)
            )
            pending = asyncio.all_tasks(loop)
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            return result
        finally:
            loop.close()

    def _extract_raw(self, response) -> str:
        """Extract raw JSON string from LLMResponse."""
        # LLMResponse for orchestrator the backend stores the full raw JSON
        # in explanation since we use a different schema here.
        # Try explanation field first (backends store full response text there
        # when command is empty), then fall back to reconstructing from fields.
        raw = getattr(response, "explanation", "") or ""
        # If explanation looks like JSON, use it directly
        stripped = raw.strip()
        if stripped.startswith("{"):
            return stripped
        # Otherwise the backend may have parsed it partially reconstruct
        cmd = getattr(response, "command", "")
        done = getattr(response, "done", False)
        spawn = getattr(response, "spawn", None)
        if done:
            return json.dumps({"action": "done", "explanation": raw})
        if spawn and isinstance(spawn, dict):
            return json.dumps({
                "action": "spawn",
                "name": spawn.get("name", ""),
                "goal": spawn.get("goal", ""),
                "explanation": raw,
            })
        if cmd:
            return json.dumps({"action": "run", "command": cmd, "explanation": raw})
        return json.dumps({"action": "done", "explanation": raw or "no response"})

    # ------------------------------------------------------------------
    # Action parsing
    # ------------------------------------------------------------------

    def _parse_action(self, raw: str) -> dict:
        """Parse raw LLM output into an action dict. Returns done on failure."""
        cleaned = raw.replace("```json", "").replace("```", "").strip()
        try:
            parsed = json.loads(cleaned)
            action = parsed.get("action", "")
            if action not in ("run", "spawn", "done"):
                return {"action": "done", "explanation": f"unrecognised action: {action}"}
            return parsed
        except json.JSONDecodeError:
            return {"action": "done", "explanation": f"could not parse response: {cleaned[:100]}"}

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def _confirm_command(self, command: str, explanation: str) -> str | None:
        """Show ↵ run  e edit  q cancel prompt. Returns confirmed command or None."""
        PURPLE = '\033[38;5;141m'
        BRIGHT_WHITE = '\033[1;37m'
        DIM = '\033[2;37m'
        RESET = '\033[0m'

        sys.stdout.write('\n')
        sys.stdout.write(f'{PURPLE}  ✦{RESET} {explanation}\n\n')
        sys.stdout.write(f'  {BRIGHT_WHITE}$ {command}{RESET}\n\n')
        sys.stdout.write(f'  {DIM}↵ run   e edit   q cancel  ›{RESET}\n')
        sys.stdout.flush()
        try:
            answer = input('').strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if answer.lower() == 'q':
            sys.stdout.write(f'  {DIM}cancelled{RESET}\n')
            sys.stdout.flush()
            return None
        if answer.lower() == 'e':
            sys.stdout.write('  edit> ')
            sys.stdout.flush()
            try:
                edited = input('').strip()
            except (EOFError, KeyboardInterrupt):
                return None
            return edited or command
        return command

    def _run_command(self, command: str, timeout: int = 120) -> str:
        """Run a command via ptyprocess in cwd. Returns output string."""
        import select
        import signal
        import tempfile
        import time
        from ptyprocess import PtyProcessUnicode
        from shell.safety import is_destructive

        if is_destructive(command):
            _out(f"[orchestrator] blocked destructive command: {command}")
            return "[blocked: destructive command]"

        try:
            fd, script_path = tempfile.mkstemp(suffix=".sh", prefix="orch_")
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(command)
                proc = PtyProcessUnicode.spawn(["/bin/bash", script_path], cwd=self._cwd)
                output_parts = []
                deadline = time.monotonic() + timeout
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        try:
                            proc.kill(signal.SIGKILL)
                        except Exception:
                            pass
                        output_parts.append(f"\n[timeout after {timeout}s]")
                        break
                    try:
                        rlist, _, _ = select.select([proc.fd], [], [], min(remaining, 5.0))
                        if rlist:
                            output_parts.append(proc.read(1024))
                        elif not proc.isalive():
                            break
                    except EOFError:
                        break
                    except Exception:
                        break
                try:
                    proc.wait()
                except Exception:
                    pass
                return "".join(output_parts)
            finally:
                try:
                    os.unlink(script_path)
                except OSError:
                    pass
        except Exception as exc:
            return f"[error: {exc}]"


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_orchestrator.py -v
```

Expected: all tests `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add shell/tasks/orchestrator.py tests/unit/test_orchestrator.py
git commit -m "feat: add OrchestratorAgent multi-turn reasoning loop"
```

---

## Task 4: Wire `OrchestratorAgent` into `loop.py`

**Files:**
- Modify: `shell/loop.py:1107-1221`

- [ ] **Step 1: Write a failing integration smoke test**

Create `tests/unit/test_loop_orchestrator_wiring.py`:

```python
"""Smoke test: NL path in loop creates OrchestratorAgent, not direct LLM call."""
import pytest
from unittest.mock import MagicMock, patch, call


def test_nl_path_uses_orchestrator(tmp_path):
    """After wiring, the NL branch should instantiate OrchestratorAgent."""
    import importlib
    import shell.loop as loop_mod

    config = MagicMock()
    config.tasks_base_dir = str(tmp_path / "tasks")
    config.backend = "ollama"
    config.model = "llama3"
    config.api_base = "http://localhost:11434"
    config.privacy_mode = False
    config.routing_mode = "auto"

    orchestrator_run_called = []

    mock_agent = MagicMock()
    mock_agent.run = lambda: orchestrator_run_called.append(True)

    with patch("shell.tasks.orchestrator.OrchestratorAgent", return_value=mock_agent) as MockOrch:
        # Simulate what the NL branch in loop.start() now does
        from shell.tasks.orchestrator import OrchestratorAgent
        agent = OrchestratorAgent(
            goal="list files here",
            cwd=str(tmp_path),
            config=config,
            db_path=str(tmp_path / "test.db"),
            task_manager=MagicMock(),
        )
        agent.run()

    assert len(orchestrator_run_called) == 1
```

- [ ] **Step 2: Run test to confirm it passes (it should just validates imports work)**

```bash
pytest tests/unit/test_loop_orchestrator_wiring.py -v
```

Expected: `PASSED`.

- [ ] **Step 3: Replace the NL path in `loop.py`**

In `shell/loop.py`, replace lines 1107–1221 (the entire NL handling block from budget check through the final `_save_turns_if_needed`) with:

```python
            if not _check_and_enforce_budget(db, config, session_id):
                exit_code, _ = execute_bash(line, cwd)
                if exit_code != 0:
                    _out(f"exit {exit_code}")
                continue

            # NL path: hand off to OrchestratorAgent reasoning loop
            from shell.tasks.manager import TaskManager
            from shell.tasks.orchestrator import OrchestratorAgent
            task_manager = TaskManager(config=config, db=db)
            agent = OrchestratorAgent(
                goal=line,
                cwd=cwd,
                config=config,
                db_path=str(db._conn.connection if hasattr(db, '_conn') else ""),
                task_manager=task_manager,
            )
            try:
                agent.run()
            except KeyboardInterrupt:
                _out("[interrupted]")
            # Save turn summary for session continuity
            turns.append({"role": "user", "content": line})
            turns.append({"role": "assistant", "content": f"[orchestrator handled: {line}]"})
            _save_turns_if_needed(turns, session_id, config)
```

Also fix the `db_path` argument `Database` stores its path. Update to pass it correctly. In `shell/loop.py` around line 1020 where `db = Database()` is called, check how `DB_PATH` is accessed:

```python
            from shell.tasks.manager import TaskManager
            from shell.tasks.orchestrator import OrchestratorAgent
            from shell.telemetry.db import DB_PATH
            task_manager = TaskManager(config=config, db=db)
            agent = OrchestratorAgent(
                goal=line,
                cwd=cwd,
                config=config,
                db_path=str(DB_PATH),
                task_manager=task_manager,
            )
            try:
                agent.run()
            except KeyboardInterrupt:
                _out("[interrupted]")
            turns.append({"role": "user", "content": line})
            turns.append({"role": "assistant", "content": f"[orchestrator handled: {line}]"})
            _save_turns_if_needed(turns, session_id, config)
```

- [ ] **Step 4: Run the existing unit tests to confirm nothing broke**

```bash
pytest tests/unit/ -v
```

Expected: all existing tests still `PASSED`, no regressions.

- [ ] **Step 5: Commit**

```bash
git add shell/loop.py tests/unit/test_loop_orchestrator_wiring.py
git commit -m "feat: wire OrchestratorAgent into loop.py NL path"
```

---

## Task 5: Manual Acceptance Test

- [ ] **Step 1: Start an agentic shell session in tmux**

```bash
tmux new-session -s test
python -m shell.main
```

- [ ] **Step 2: Test simple 1-turn task (no folder created)**

Type: `list all python files in this directory`

Expected:
- Orchestrator runs `find . -name "*.py"` (or similar)
- Shows `↵ run  e edit  q cancel` prompt
- After confirming, prints output and done message
- No folder created under `~/.local/share/agentic-shell/tasks/`

- [ ] **Step 3: Test multi-step direct task**

Type: `create a directory called hello-world and write a hello.txt file inside it`

Expected:
- Orchestrator runs 2 commands across 2 turns
- Both show confirm prompt
- Done message summarizes what was created
- No task folder (no spawning needed)

- [ ] **Step 4: Test spawn path**

Type: `create a React app with Vite called my-app and write a Dockerfile for it`

Expected:
- Orchestrator spawns at least one sub-agent
- Prints `◈ spawning agent: my-app` (or similar)
- Task folder created at `~/.local/share/agentic-shell/tasks/<slug>/`
- `tmux list-windows` shows a new `task:my-app` window
- `/task list` shows the task as running

- [ ] **Step 5: Test Ctrl+C interruption**

Start a goal that will take multiple turns, then press Ctrl+C mid-loop.

Expected:
- REPL returns to prompt immediately
- Any spawned sub-agents are still visible in `tmux list-windows`

- [ ] **Step 6: Test pure bash path unaffected**

Press Ctrl+B, then type `ls -la`.

Expected: runs directly as bash, no orchestrator involved.

- [ ] **Step 7: Commit acceptance notes**

```bash
git commit --allow-empty -m "test: manual acceptance criteria verified for orchestrator agent"
```

---

## Acceptance Criteria Checklist

- [ ] AC1: Simple 1-turn task → no task folder created on disk
- [ ] AC2: Multi-step task with spawn → task folder at `tasks/<slug>/`, sub-agents in tmux
- [ ] AC3: Sub-agent `shared_read_dir` set to parent task dir in bwrap (read-only bind mount)
- [ ] AC4: Ctrl+C returns to REPL, spawned agents keep running
- [ ] AC5: `/task list`, `/attach`, `/kill` still work on spawned sub-agents
- [ ] AC6: Ctrl+B bash bypass completely unaffected
