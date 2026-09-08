# Agentic Shell v2 UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform `shell/loop.py` to render a Powerline-style prompt, `✦ thinking...` indicator, styled explanation+command preview, and `✓/✗` post-execution timing summary.

**Architecture:** All changes are confined to `shell/loop.py`. Five targeted additions: `_render_prompt()`, a `✦ thinking...` write before the LLM call, a rewritten `_display_command_preview()`, timing instrumentation around `execute_bash()`, and last-exit-code tracking for the prompt cursor color. No new dependencies uses only `sys.stdout.write`, `subprocess`, `time`, and existing `prompt_toolkit HTML()`.

**Tech Stack:** Python 3, prompt_toolkit (already imported), subprocess (already imported), time (stdlib), sys.stdout.write (already used throughout)

---

### Task 1: Add `_render_prompt()` Powerline segments

**Files:**
- Modify: `shell/loop.py` (add function after `_out()`, replace prompt string in `start()`)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_loop_prompt.py`:

```python
"""Tests for _render_prompt() Powerline prompt."""
import sys
import os
import importlib

# Patch subprocess before importing loop to avoid git calls in import
import subprocess as _sp
_real_run = _sp.run

def test_render_prompt_no_git(monkeypatch, tmp_path):
    """Prompt renders path+time segments when not in a git repo."""
    # Make git return empty (no branch)
    def fake_run(cmd, **kwargs):
        class R:
            stdout = ""
            returncode = 1
        return R()
    monkeypatch.setattr(_sp, "run", fake_run)

    # Reload to pick up monkeypatch
    import shell.loop as loop_mod
    importlib.reload(loop_mod)

    result = loop_mod._render_prompt("/home/user/project", 0)
    # Must contain path and cursor
    assert "/home/user/project" in result or "~" in result
    assert "❯" in result


def test_render_prompt_with_git(monkeypatch):
    """Prompt includes branch name when git returns one."""
    import subprocess as sp

    def fake_run(cmd, **kwargs):
        class R:
            stdout = "main"
            returncode = 0
        return R()
    monkeypatch.setattr(sp, "run", fake_run)

    import shell.loop as loop_mod
    importlib.reload(loop_mod)

    result = loop_mod._render_prompt("/home/user/project", 0)
    assert "main" in result


def test_render_prompt_red_cursor_on_failure(monkeypatch):
    """❯ uses red color when last_exit != 0."""
    import subprocess as sp

    def fake_run(cmd, **kwargs):
        class R:
            stdout = ""
            returncode = 1
        return R()
    monkeypatch.setattr(sp, "run", fake_run)

    import shell.loop as loop_mod
    importlib.reload(loop_mod)

    result = loop_mod._render_prompt("/home/user", 1)
    # red = ANSI 203
    assert "203" in result
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/$USER && rtk pytest tests/unit/test_loop_prompt.py -v
```

Expected: `AttributeError: module 'shell.loop' has no attribute '_render_prompt'`

- [ ] **Step 3: Add `_render_prompt()` to `shell/loop.py`**

Add this function after the `_out()` helper (around line 24):

```python
def _render_prompt(cwd: str, last_exit: int) -> str:
    """Return a Powerline-style prompt string for prompt_toolkit HTML().

    Segments: [path block] [git branch block] [time block] ❯
    Uses ANSI 256-color codes via HTML() spans.
    Falls back gracefully if git is unavailable.
    """
    import subprocess
    import time as _time

    home = str(Path.home())
    display_cwd = cwd.replace(home, "~") if cwd.startswith(home) else cwd

    # Git branch (silent fail)
    branch = ""
    try:
        res = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=1
        )
        branch = res.stdout.strip()
    except Exception:
        pass

    hhmm = _time.strftime("%H:%M")

    # Path segment soft blue bg (#005f87 = 24)
    path_seg = (
        '\033[48;5;24m\033[97m'   # blue bg, bright white fg
        f' {display_cwd} '
        '\033[0m'
        '\033[38;5;24m\033[48;5;55m\ue0b0\033[0m'  # powerline arrow (Unicode or space fallback)
    )

    # Git segment soft purple bg (55)
    git_seg = ""
    if branch:
        git_seg = (
            '\033[48;5;55m\033[97m'
            f'  {branch} '
            '\033[0m'
            '\033[38;5;55m\033[48;5;236m\ue0b0\033[0m'
        )

    # Time segment dark grey bg (236)
    time_seg = (
        '\033[48;5;236m\033[2;37m'
        f' {hhmm} '
        '\033[0m '
    )

    # Cursor white normally, red if last exit non-zero
    if last_exit != 0:
        cursor = '\033[38;5;203m❯\033[0m'
    else:
        cursor = '\033[0;37m❯\033[0m'

    return path_seg + git_seg + time_seg + cursor + ' '
```

- [ ] **Step 4: Run test to verify it passes**

```bash
rtk pytest tests/unit/test_loop_prompt.py -v
```

Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
rtk git add shell/loop.py tests/unit/test_loop_prompt.py && rtk git commit -m "feat: add _render_prompt() Powerline prompt segments"
```

---

### Task 2: Track last exit code and wire prompt into `start()`

**Files:**
- Modify: `shell/loop.py` `start()` function

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_loop_prompt.py`:

```python
def test_last_exit_global_default():
    """_last_exit starts at 0."""
    import shell.loop as loop_mod
    # After module load, last exit should be 0
    assert loop_mod._last_exit == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
rtk pytest tests/unit/test_loop_prompt.py::test_last_exit_global_default -v
```

Expected: `AttributeError: module 'shell.loop' has no attribute '_last_exit'`

- [ ] **Step 3: Add `_last_exit` global and wire prompt in `start()`**

In `shell/loop.py`, after the `_budget_hard_stop: bool = False` line (around line 29), add:

```python
_last_exit: int = 0
```

In `start()`, replace the `session.prompt(...)` call (around line 416–420):

```python
# BEFORE:
user_input = session.prompt(
    HTML(f'<ansigreen>{cwd}</ansigreen> <ansicyan>❯</ansicyan> '),
    in_thread=True
)

# AFTER:
user_input = session.prompt(
    _render_prompt(cwd, _last_exit),
    in_thread=True
)
```

Note: `session.prompt()` accepts a plain string (with ANSI codes) directly no HTML() wrapper needed since `_render_prompt()` returns raw ANSI.

Also add `global _last_exit` at the top of `start()`:

```python
def start(config: ShellConfig, session_id: str, session_context: str = "") -> None:
    global _bypass_next, _last_exit
```

- [ ] **Step 4: Run test to verify it passes**

```bash
rtk pytest tests/unit/test_loop_prompt.py -v
```

Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
rtk git add shell/loop.py tests/unit/test_loop_prompt.py && rtk git commit -m "feat: wire _last_exit tracking and Powerline prompt into start()"
```

---

### Task 3: Add `✦ thinking...` indicator before LLM call

**Files:**
- Modify: `shell/loop.py` agentic branch inside `start()`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_loop_thinking.py`:

```python
"""Tests for the ✦ thinking... stdout indicator."""
import sys
import io


def test_thinking_indicator_written_before_llm(monkeypatch):
    """✦ thinking... is written to stdout before asyncio.run(_call_llm(...))."""
    import shell.loop as loop_mod

    written = []
    original_write = sys.stdout.write

    def capture_write(text):
        written.append(text)
        return original_write(text)

    # We only need to verify _write_thinking() writes the right bytes
    monkeypatch.setattr(sys.stdout, "write", capture_write)
    loop_mod._write_thinking()
    sys.stdout.write("\r" + " " * 20 + "\r")  # simulate overwrite

    combined = "".join(written)
    assert "thinking" in combined


def test_thinking_uses_purple_color():
    """thinking... text uses soft purple ANSI code 141."""
    import shell.loop as loop_mod
    import io

    buf = io.StringIO()
    import sys
    old = sys.stdout
    sys.stdout = buf
    try:
        loop_mod._write_thinking()
    finally:
        sys.stdout = old

    output = buf.getvalue()
    assert "141" in output  # soft purple
    assert "thinking" in output
```

- [ ] **Step 2: Run test to verify it fails**

```bash
rtk pytest tests/unit/test_loop_thinking.py -v
```

Expected: `AttributeError: module 'shell.loop' has no attribute '_write_thinking'`

- [ ] **Step 3: Add `_write_thinking()` and call it before `asyncio.run()`**

Add this function after `_render_prompt()` in `shell/loop.py`:

```python
def _write_thinking() -> None:
    """Print ✦ thinking... in soft purple. Caller overwrites with \\r after LLM responds."""
    sys.stdout.write('\033[38;5;141m  ✦ thinking...\033[0m')
    sys.stdout.flush()
```

In `start()`, find the line:

```python
        try:
            response = asyncio.run(_call_llm(backend, line, cwd, config, session_context))
        except KeyboardInterrupt:
            _out("cancelled")
            continue
```

Replace with:

```python
        try:
            _write_thinking()
            response = asyncio.run(_call_llm(backend, line, cwd, config, session_context))
            sys.stdout.write('\r' + ' ' * 20 + '\r')
            sys.stdout.flush()
        except KeyboardInterrupt:
            sys.stdout.write('\r' + ' ' * 20 + '\r')
            sys.stdout.flush()
            _out("cancelled")
            continue
```

- [ ] **Step 4: Run test to verify it passes**

```bash
rtk pytest tests/unit/test_loop_thinking.py -v
```

Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
rtk git add shell/loop.py tests/unit/test_loop_thinking.py && rtk git commit -m "feat: add ✦ thinking... indicator before LLM call"
```

---

### Task 4: Rewrite `_display_command_preview()` with styled output

**Files:**
- Modify: `shell/loop.py` `_display_command_preview()`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_loop_preview.py`:

```python
"""Tests for styled _display_command_preview()."""
import sys
import io
from unittest.mock import MagicMock


def _make_response(explanation="I'll list files.", command="ls -la", safe=True):
    r = MagicMock()
    r.explanation = explanation
    r.command = command
    r.safe = safe
    return r


def test_preview_shows_purple_marker(monkeypatch, capsys):
    """✦ marker in soft purple appears before explanation."""
    import shell.loop as loop_mod

    # Simulate user pressing Enter (run as-is)
    monkeypatch.setattr("builtins.input", lambda _: "")

    loop_mod._display_command_preview(_make_response())
    captured = capsys.readouterr()
    assert "141" in captured.out  # soft purple ANSI
    assert "✦" in captured.out


def test_preview_shows_dollar_command(monkeypatch, capsys):
    """$ command line appears in bright white."""
    import shell.loop as loop_mod

    monkeypatch.setattr("builtins.input", lambda _: "")

    loop_mod._display_command_preview(_make_response(command="docker ps"))
    captured = capsys.readouterr()
    assert "docker ps" in captured.out
    assert "$" in captured.out


def test_preview_enter_returns_command(monkeypatch):
    """Pressing Enter returns the original command."""
    import shell.loop as loop_mod

    monkeypatch.setattr("builtins.input", lambda _: "")
    result = loop_mod._display_command_preview(_make_response(command="ls"))
    assert result == "ls"


def test_preview_q_returns_none(monkeypatch):
    """Pressing q cancels and returns None."""
    import shell.loop as loop_mod

    monkeypatch.setattr("builtins.input", lambda _: "q")
    result = loop_mod._display_command_preview(_make_response())
    assert result is None


def test_preview_edit_returns_edited(monkeypatch):
    """Pressing e then typing returns the edited command."""
    import shell.loop as loop_mod

    responses = iter(["e", "ls -la /tmp"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))
    result = loop_mod._display_command_preview(_make_response(command="ls"))
    assert result == "ls -la /tmp"


def test_preview_confirm_bar_shown(monkeypatch, capsys):
    """Confirm bar text appears after command."""
    import shell.loop as loop_mod

    monkeypatch.setattr("builtins.input", lambda _: "")
    loop_mod._display_command_preview(_make_response())
    captured = capsys.readouterr()
    assert "run" in captured.out
    assert "edit" in captured.out
    assert "cancel" in captured.out
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
rtk pytest tests/unit/test_loop_preview.py -v
```

Expected: Most tests FAIL because current `_display_command_preview()` lacks `✦` and color codes.

- [ ] **Step 3: Rewrite `_display_command_preview()`**

Replace the existing function in `shell/loop.py`:

```python
def _display_command_preview(response) -> str | None:
    """Display styled explanation + command preview, then prompt for confirm/edit/cancel.

    Output format:
      ✦ <explanation>

      $ <command>

      ↵ run   e edit   q cancel  ›
    """
    PURPLE = '\033[38;5;141m'
    BRIGHT_WHITE = '\033[1;37m'
    DIM = '\033[2;37m'
    RESET = '\033[0m'

    sys.stdout.write('\n')
    sys.stdout.write(f'{PURPLE}  ✦{RESET} {response.explanation}\n')
    sys.stdout.write('\n')
    sys.stdout.write(f'  {BRIGHT_WHITE}$ {response.command}{RESET}\n')
    sys.stdout.write('\n')
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
        sys.stdout.write(f'  edit> ')
        sys.stdout.flush()
        try:
            edited = input('').strip()
        except (EOFError, KeyboardInterrupt):
            return None
        return edited or response.command

    return response.command
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
rtk pytest tests/unit/test_loop_preview.py -v
```

Expected: 6 tests PASS

- [ ] **Step 5: Commit**

```bash
rtk git add shell/loop.py tests/unit/test_loop_preview.py && rtk git commit -m "feat: styled ✦ explanation + command preview with confirm bar"
```

---

### Task 5: Add post-execution timing summary (`✓/✗ done in Xs`)

**Files:**
- Modify: `shell/loop.py` `start()` function, agentic and bash execution paths

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_loop_timing.py`:

```python
"""Tests for post-execution ✓/✗ timing summary."""
import sys
import io


def test_print_exec_result_success(capsys):
    """✓ done in Xs printed on exit code 0."""
    import shell.loop as loop_mod
    loop_mod._print_exec_result(0, 1.23)
    captured = capsys.readouterr()
    assert "✓" in captured.out
    assert "1.2s" in captured.out
    assert "114" in captured.out  # soft green ANSI


def test_print_exec_result_failure(capsys):
    """✗ exit N (Xs) printed on non-zero exit code."""
    import shell.loop as loop_mod
    loop_mod._print_exec_result(1, 2.55)
    captured = capsys.readouterr()
    assert "✗" in captured.out
    assert "exit 1" in captured.out
    assert "2.6s" in captured.out or "2.5s" in captured.out
    assert "203" in captured.out  # soft red ANSI


def test_print_exec_result_zero_decimals(capsys):
    """Timing shown with one decimal place."""
    import shell.loop as loop_mod
    loop_mod._print_exec_result(0, 0.1)
    captured = capsys.readouterr()
    assert "0.1s" in captured.out
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
rtk pytest tests/unit/test_loop_timing.py -v
```

Expected: `AttributeError: module 'shell.loop' has no attribute '_print_exec_result'`

- [ ] **Step 3: Add `_print_exec_result()` and wire into `start()`**

Add this function after `_write_thinking()` in `shell/loop.py`:

```python
def _print_exec_result(exit_code: int, elapsed: float) -> None:
    """Print ✓ done in Xs or ✗ exit N (Xs) after command execution.

    Only called for agentic commands pure bash gets no chrome.
    """
    GREEN = '\033[38;5;114m'
    RED = '\033[38;5;203m'
    RESET = '\033[0m'

    if exit_code == 0:
        sys.stdout.write(f'\n  {GREEN}✓ done in {elapsed:.1f}s{RESET}\n\n')
    else:
        sys.stdout.write(f'\n  {RED}✗ exit {exit_code}  ({elapsed:.1f}s){RESET}\n\n')
    sys.stdout.flush()
```

In `start()`, find the agentic execution block (around line 498):

```python
        exit_code, _ = execute_bash(command, cwd)
        if exit_code != 0:
            _out(f"exit {exit_code}")

        _log_event(db, session_id, response, command, exit_code, line)
        _audit_log("agentic", command, exit_code)
```

Replace with:

```python
        import time as _time
        _t0 = _time.monotonic()
        exit_code, _ = execute_bash(command, cwd)
        _elapsed = _time.monotonic() - _t0
        _last_exit = exit_code
        _print_exec_result(exit_code, _elapsed)

        _log_event(db, session_id, response, command, exit_code, line)
        _audit_log("agentic", command, exit_code)
```

Also update `_last_exit` for bash commands. Find the bash execution block (around line 460):

```python
        if route == Route.BASH:
            if is_destructive(line):
                if not confirm_destructive(line):
                    _audit_log("destructive_blocked", line)
                    continue
            exit_code, _ = execute_bash(line, cwd)
            _audit_log("bash", line, exit_code)
            if exit_code != 0:
                _out(f"exit {exit_code}")
            continue
```

Replace with:

```python
        if route == Route.BASH:
            if is_destructive(line):
                if not confirm_destructive(line):
                    _audit_log("destructive_blocked", line)
                    continue
            exit_code, _ = execute_bash(line, cwd)
            _last_exit = exit_code
            _audit_log("bash", line, exit_code)
            if exit_code != 0:
                _out(f"exit {exit_code}")
            continue
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
rtk pytest tests/unit/test_loop_timing.py -v
```

Expected: 3 tests PASS

- [ ] **Step 5: Run full unit test suite**

```bash
rtk pytest tests/unit/ -v
```

Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
rtk git add shell/loop.py tests/unit/test_loop_timing.py && rtk git commit -m "feat: add ✓/✗ post-execution timing summary for agentic commands"
```

---

### Task 6: Push and smoke test

- [ ] **Step 1: Push to origin**

```bash
rtk git push
```

- [ ] **Step 2: On the Linux server, pull and restart**

```bash
cd ~/path/to/AgenticOS && git pull origin main
tmux kill-server
agentic-shell
```

- [ ] **Step 3: Verify each feature manually**

Check:
1. Prompt renders with path + time + `❯` (no freeze)
2. Git branch appears when inside a git repo
3. Type a natural language request → `✦ thinking...` appears immediately
4. LLM response shows `✦ explanation` then `$ command` then confirm bar
5. Press Enter → command runs → `✓ done in Xs` appears
6. Run a failing command → `✗ exit 1 (Xs)` appears
7. `❯` turns red after a failed command, white after success
8. Pure bash commands (`ls`, `pwd`) show zero extra chrome

---

## Self-Review Against Spec

Spec section → Plan task coverage:

| Spec requirement | Task |
|-----------------|------|
| Powerline path block (soft blue bg) | Task 1 |
| Git branch block (soft purple bg) | Task 1 |
| Time block (dark grey bg) | Task 1 |
| `❯` white/red based on last exit | Task 2 |
| `✦ thinking...` before LLM call | Task 3 |
| Overwrite thinking with `\r` after response | Task 3 |
| `✦` explanation text, indented | Task 4 |
| `$ command` bright white | Task 4 |
| `↵ run   e edit   q cancel  ›` confirm bar | Task 4 |
| `e` → inline edit | Task 4 |
| `q` → cancelled | Task 4 |
| `✓ done in Xs` on success | Task 5 |
| `✗ exit N (Xs)` on failure | Task 5 |
| Pure bash: zero chrome | Task 5 (agentic-only `_print_exec_result`) |
| Color palette (ANSI 256) | Tasks 1, 3, 4, 5 |
| No new dependencies | All tasks only stdlib + existing imports |
| `_render_prompt()` in loop.py | Task 1 |
| `_last_exit` tracking | Task 2, 5 |
