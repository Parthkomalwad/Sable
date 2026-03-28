"""Main REPL loop."""
from __future__ import annotations

import asyncio
import os
import platform
import sys
import threading
from pathlib import Path

os.environ["PROMPT_TOOLKIT_NO_CPR"] = "1"

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.formatted_text import HTML, ANSI
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.bindings.emacs import load_emacs_bindings
from prompt_toolkit.completion import Completer, Completion, PathCompleter, merge_completers
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory

from shell.config.schema import ShellConfig


class _ShellCompleter(Completer):
    """Tab completer: first word = command from PATH, rest = filesystem paths."""

    def __init__(self) -> None:
        self._path_completer = PathCompleter(expanduser=True)
        self._bins: list[str] = []
        self._bins_loaded = False

    def _load_bins(self) -> None:
        """Lazily collect all executable names from PATH directories."""
        if self._bins_loaded:
            return
        seen: set[str] = set()
        for directory in os.environ.get("PATH", "").split(":"):
            try:
                for entry in os.scandir(directory):
                    if entry.is_file(follow_symlinks=True) and os.access(entry.path, os.X_OK):
                        seen.add(entry.name)
            except (PermissionError, FileNotFoundError):
                pass
        self._bins = sorted(seen)
        self._bins_loaded = True

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        words = text.split()

        # If no words typed yet, or only one word being typed → complete command name
        if not words or (len(words) == 1 and not text.endswith(" ")):
            self._load_bins()
            prefix = words[0] if words else ""
            for name in self._bins:
                if name.startswith(prefix):
                    yield Completion(name, start_position=-len(prefix))
            return

        # Otherwise complete the current argument as a filesystem path
        yield from self._path_completer.get_completions(document, complete_event)


def _out(text: str) -> None:
    """Write text to stdout and flush. Never blocks."""
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


_SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']


class _ThinkingSpinner:
    """Animated spinner that runs in a background thread during LLM calls."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        # Clear the spinner line
        sys.stdout.write('\r' + ' ' * 30 + '\r')
        sys.stdout.flush()

    def _run(self) -> None:
        PURPLE = '\033[38;5;141m'
        RESET = '\033[0m'
        i = 0
        while not self._stop.is_set():
            frame = _SPINNER_FRAMES[i % len(_SPINNER_FRAMES)]
            sys.stdout.write(f'\r{PURPLE}  {frame} thinking...{RESET}')
            sys.stdout.flush()
            self._stop.wait(0.08)
            i += 1


def _print_exec_result(exit_code: int, elapsed: float, cost_usd: float = 0.0, total_tokens: int = 0) -> None:
    """Print ✓ done in Xs or ✗ exit N (Xs) after command execution.

    Only called for agentic commands — pure bash gets no chrome.
    """
    GREEN = '\033[38;5;114m'
    RED = '\033[38;5;203m'
    DIM = '\033[38;5;238m'
    RESET = '\033[0m'

    if total_tokens > 0:
        if cost_usd > 0:
            cost_part = f'  {DIM}·   ${cost_usd:.4f} · {total_tokens} tok{RESET}'
        else:
            cost_part = f'  {DIM}·   {total_tokens} tok{RESET}'
    else:
        cost_part = ''

    if exit_code == 0:
        sys.stdout.write(f'\n  {GREEN}✓ done in {elapsed:.1f}s{RESET}{cost_part}\n\n')
    else:
        sys.stdout.write(f'\n  {RED}✗ exit {exit_code}  ({elapsed:.1f}s){RESET}{cost_part}\n\n')
    sys.stdout.flush()


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

    # Path segment — soft blue bg (#005f87 = 24)
    path_seg = (
        '\033[48;5;24m\033[97m'   # blue bg, bright white fg
        f' {display_cwd} '
        '\033[0m'
        '\033[38;5;24m\033[48;5;55m\ue0b0\033[0m'  # powerline arrow (Unicode or space fallback)
    )

    # Git segment — soft purple bg (55)
    git_seg = ""
    if branch:
        git_seg = (
            '\033[48;5;55m\033[97m'
            f'  {branch} '
            '\033[0m'
            '\033[38;5;55m\033[48;5;236m\ue0b0\033[0m'
        )

    # Time segment — dark grey bg (236)
    time_seg = (
        '\033[48;5;236m\033[2;37m'
        f' {hhmm} '
        '\033[0m '
    )

    # Cursor — white normally, red if last exit non-zero
    if last_exit != 0:
        cursor = '\033[38;5;203m❯\033[0m'
    else:
        cursor = '\033[0;37m❯\033[0m'

    return path_seg + git_seg + time_seg + cursor + ' '


# One-shot bash bypass flag — set by Ctrl+B, cleared after one command
_bypass_next: bool = False
_offline_mode: bool = False
_budget_hard_stop: bool = False
_last_exit: int = 0


def set_offline_mode(offline: bool) -> None:
    global _offline_mode
    _offline_mode = offline


def _make_key_bindings(db=None) -> KeyBindings:
    kb = KeyBindings()

    @kb.add("c-b")
    def _ctrl_b(event) -> None:
        global _bypass_next
        _bypass_next = True
        _out("[bash mode] next command runs directly")

    @kb.add("c-t")
    def _ctrl_t(event) -> None:
        try:
            from shell.tui.layout import toggle_sidebar
            toggle_sidebar()
        except Exception:
            pass

    @kb.add("c-x")
    def _ctrl_x(event) -> None:
        event.app.current_buffer.set_document(
            __import__("prompt_toolkit.document", fromlist=["Document"]).Document("/config")
        )
        event.app.current_buffer.validate_and_handle()

    return kb


def _build_backend(config: ShellConfig):
    from shell.llm.ollama import OllamaBackend
    from shell.llm.openai import OpenAIBackend
    from shell.llm.anthropic import AnthropicBackend

    if config.backend == "ollama":
        return OllamaBackend(base_url=config.api_base or "http://localhost:11434", model=config.model)
    elif config.backend == "openai":
        api_key = os.environ.get("OPENAI_API_KEY") or getattr(config, "api_key", "") or ""
        return OpenAIBackend(api_key=api_key, model=config.model)
    elif config.backend == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY") or getattr(config, "api_key", "") or ""
        return AnthropicBackend(api_key=api_key, model=config.model)
    else:
        return OllamaBackend(base_url=config.api_base or "http://localhost:11434", model=config.model)


def _get_os_info() -> str:
    try:
        return f"{platform.system()} {platform.release()}"
    except Exception:
        return "Linux"


async def _call_llm(backend, user_input: str, cwd: str, config: ShellConfig, session_context: str = ""):
    import httpx
    from shell.llm.base import build_system_prompt

    system = build_system_prompt(
        cwd=cwd,
        user=os.environ.get("USER", os.environ.get("USERNAME", "user")),
        os_info=_get_os_info(),
    )

    nl_input = user_input
    if config.privacy_mode:
        from shell.safety import strip_secrets
        nl_input, count = strip_secrets(user_input)
        if count:
            _out(f"[redacted {count} secret pattern(s)]")

    messages = []
    if session_context:
        messages.append({"role": "user", "content": f"[Previous session context]\n{session_context}"})
        messages.append({"role": "assistant", "content": "Understood, I have the context from your previous session."})
    messages.append({"role": "user", "content": nl_input})

    try:
        response = await backend.complete(messages, system)
        if _offline_mode:
            set_offline_mode(False)
            _out("[model back online]")
        return response
    except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError):
        set_offline_mode(True)
        _out("[model offline — running in manual mode]")
        return None


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


def _audit_log(action: str, command: str, exit_code: int | None = None) -> None:
    import datetime, getpass
    log_path = "/var/log/agentic-shell/audit.log"
    try:
        user = getpass.getuser()
    except Exception:
        user = "unknown"
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    line = f"{timestamp} user={user} action={action} cmd={command!r} exit={exit_code}\n"
    try:
        with open(log_path, "a") as f:
            f.write(line)
    except (PermissionError, OSError):
        pass


def _log_event(db, session_id: str, response, command: str, exit_code: int, nl_input: str) -> None:
    try:
        from datetime import datetime, timezone
        from shell.telemetry.events import TokenEvent
        event = TokenEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            session_id=session_id,
            action_type="nl_route",
            nl_input=nl_input,
            command=command,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            total_tokens=response.prompt_tokens + response.completion_tokens,
            cost_usd=response.cost_usd,
            model=getattr(response, "model", None),
            exit_code=exit_code,
        )
        db.write_event(event)
    except Exception:
        pass


def _check_and_enforce_budget(db, config: ShellConfig, session_id: str) -> bool:
    global _budget_hard_stop
    if _budget_hard_stop:
        _out("Budget exhausted. Use '/budget reset' to continue.")
        return False
    if db is None:
        return True
    try:
        status = db.check_budget(config, session_id)
        if status == "HARD_STOP":
            _budget_hard_stop = True
            _out("Budget limit reached. LLM calls disabled. Use '/budget reset' to clear.")
            return False
        elif status == "WARNING":
            _out("Warning: Budget at 80%+ — approaching limit.")
    except Exception:
        pass
    return True


_HELP_TEXT = (
    "\nAgentic Shell - Built-in Commands\n"
    "----------------------------------\n"
    "  /new           Start a fresh tmux session\n"
    "  /clear         Clear the terminal screen\n"
    "  /exit          Exit the shell\n"
    "  /config        Open settings panel (Ctrl+X)\n"
    "  /mode          Toggle routing: auto / prefix\n"
    "  /model         Show current LLM model\n"
    "  /stats         Last 7 days token usage\n"
    "  /history       recent commands with AI costs\n"
    "  /budget reset  Clear hard-stop budget flag\n"
    "  /memory        View/clear session context\n"
    "\n"
    "  >> text        Force agentic (prefix mode)\n"
    "  Ctrl+B         Next command runs as raw bash\n"
    "  Ctrl+T         Toggle telemetry sidebar\n"
)


def _handle_builtin(line: str, db, session_id: str, config: ShellConfig) -> bool:
    global _budget_hard_stop
    cmd = line.strip()

    if cmd in ("/help", "/?"):
        sys.stdout.write(_HELP_TEXT + "\n")
        sys.stdout.flush()
        return True

    if cmd == "/clear":
        os.system("clear")
        return True

    if cmd in ("/exit", "/quit"):
        import pathlib
        pathlib.Path.home().joinpath(".local", "share", "agentic-shell", "exit_requested").touch()
        raise SystemExit(0)

    if cmd == "/model":
        _out(f"backend: {config.backend}  model: {config.model}")
        return True

    if cmd == "/mode":
        current = getattr(config, "routing_mode", "auto")
        new_mode = "prefix" if current == "auto" else "auto"
        config.routing_mode = new_mode
        if new_mode == "prefix":
            _out("Routing mode: prefix — prefix your request with >> to send to LLM")
        else:
            _out("Routing mode: auto — shell auto-detects bash vs natural language")
        return True

    if cmd == "/new":
        _start_new_session()
        return True

    if cmd in ("/config", "Ctrl+X"):
        try:
            from shell.tui.panel import render_settings_panel
            new_config = render_settings_panel(config)
            if new_config is not None:
                for field in vars(new_config):
                    setattr(config, field, getattr(new_config, field))
        except Exception as exc:
            _out(f"Settings panel error: {exc}")
        return True

    if cmd == "/budget reset":
        _budget_hard_stop = False
        _out("Budget hard-stop cleared.")
        return True

    if cmd in ("/stats", "shell stats"):
        _show_stats(db)
        return True

    if cmd in ("/history", "/hist"):
        _show_history(db)
        return True

    if cmd == "shell stats --csv":
        _show_stats_csv(db)
        return True

    if cmd in ("/memory", "shell memory"):
        _show_memory(session_id)
        return True

    return False


def _show_history(db, limit: int = 20) -> None:
    """Show recent command history with token costs from telemetry DB."""
    if db is None:
        _out("Telemetry not available.")
        return

    PURPLE = '\033[38;5;141m'
    DIM    = '\033[2;37m'
    GREEN  = '\033[38;5;114m'
    RESET  = '\033[0m'
    SEP    = '\033[38;5;238m' + '─' * 60 + RESET

    try:
        rows = db._conn.execute(
            """
            SELECT timestamp, action_type, nl_input, command, total_tokens, cost_usd
            FROM token_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
    except Exception as exc:
        _out(f"History error: {exc}")
        return

    if not rows:
        _out("No history yet.")
        return

    sys.stdout.write(f"\n{PURPLE}  Recent Commands{RESET}\n")
    sys.stdout.write(f"  {SEP}\n")

    for i, (ts, action_type, nl_input, command, total_tokens, cost_usd) in enumerate(reversed(rows), 1):
        try:
            date_part = ts[:10]
            time_part = ts[11:16]
            ts_display = f"{date_part} {time_part}"
        except Exception:
            ts_display = ts[:16] if ts else "?"

        if action_type == "nl_route" and nl_input:
            display = nl_input[:50]
            if len(nl_input) > 50:
                display += "…"
            if cost_usd and cost_usd > 0:
                cost_str = f"{DIM}${cost_usd:.4f} · {total_tokens} tok{RESET}"
            else:
                cost_str = f"{DIM}{total_tokens} tok{RESET}" if total_tokens else f"{GREEN}AI{RESET}"
        else:
            display = (command or nl_input or "?")[:50]
            cost_str = f"{DIM}bash{RESET}"

        sys.stdout.write(
            f"  {DIM}{i:>2}{RESET}  {DIM}{ts_display}{RESET}  "
            f"{display:<52}  {cost_str}\n"
        )

    sys.stdout.write(f"  {SEP}\n")

    try:
        today = db.get_today_stats()
        if today["calls"] > 0:
            sys.stdout.write(
                f"  {DIM}{today['calls']} AI calls today · ${today['cost']:.4f} total{RESET}\n"
            )
    except Exception:
        pass

    sys.stdout.write("\n")
    sys.stdout.flush()


def _show_stats(db) -> None:
    if db is None:
        _out("Telemetry not available.")
        return
    try:
        rows = db.get_stats(days=7)
        _out("\nToken Usage — Last 7 Days")
        _out(f"{'Day':<12} {'Calls':>6} {'Tokens':>8} {'Cost':>10}")
        _out("-" * 40)
        for r in rows:
            _out(f"{r['day']:<12} {r['calls']:>6} {r['tokens'] or 0:>8} ${r['cost']:.4f}" if r["cost"] else f"{r['day']:<12} {r['calls']:>6} {r['tokens'] or 0:>8} $0.0000")
        _out("")
    except Exception as exc:
        _out(f"Stats error: {exc}")


def _show_stats_csv(db) -> None:
    if db is None:
        _out("day,calls,tokens,cost")
        return
    try:
        rows = db.get_stats(days=7)
        _out("day,calls,tokens,cost")
        for r in rows:
            _out(f"{r['day']},{r['calls']},{r['tokens'] or 0},{r['cost'] or 0:.6f}")
    except Exception as exc:
        _out(f"Stats error: {exc}")


def _show_memory(session_id: str) -> None:
    from shell.memory.store import load_session_context
    username = os.environ.get("USER", os.environ.get("USERNAME", "user"))
    ctx = load_session_context(username)
    if not ctx:
        _out("No session context loaded.")
        return
    _out("\nActive session context:")
    _out("─" * 60)
    _out(ctx[:1000])
    if len(ctx) > 1000:
        _out("... (truncated)")
    _out("")
    try:
        answer = input("[c]lear context or Enter to keep: ").strip().lower()
        if answer == "c":
            from shell.memory.store import save_session_context
            save_session_context(session_id, "", [], 0)
            _out("Context cleared.")
    except (EOFError, KeyboardInterrupt):
        pass


def _start_new_session() -> None:
    import subprocess, shutil, time
    if not shutil.which("tmux"):
        _out("tmux not found — restarting shell process.")
        os.execv(sys.executable, [sys.executable, "-m", "shell.main"])
        return

    _out("Starting new session...")
    try:
        result = subprocess.run(["tmux", "display-message", "-p", "#S"], capture_output=True, text=True)
        current_session = result.stdout.strip()
    except Exception:
        current_session = ""

    new_name = f"agentic-{int(time.time()) % 10000}"
    install_dir = os.environ.get("PYTHONPATH", "")
    venv_python = os.environ.get("AGENTIC_PYTHON", sys.executable)

    try:
        # Create session sized to current terminal
        import shutil as _shutil
        _ts = _shutil.get_terminal_size((220, 50))
        subprocess.run(["tmux", "new-session", "-d", "-s", new_name,
                        "-x", str(_ts.columns), "-y", str(_ts.lines)], check=True)

        # Pane 0 = shell (left), split right for telemetry (pane 1, 45 cols)
        subprocess.run(["tmux", "split-window", "-h", "-t", f"{new_name}:0.0", "-l", "48"], check=True)
        subprocess.run(["tmux", "swap-pane", "-s", f"{new_name}:0.0", "-t", f"{new_name}:0.1"], check=True)

        # Telemetry in pane 1 (right after swap)
        subprocess.run(["tmux", "send-keys", "-t", f"{new_name}:0.1",
            f"trap '' INT; while true; do PYTHONPATH={install_dir} PROMPT_TOOLKIT_NO_CPR=1 {venv_python} -m shell.telemetry.watch; sleep 2; done",
            "Enter"], check=True)

        # Shell in pane 0 (left after swap) — AGENTIC_NEW_SESSION=1 skips session resume
        subprocess.run(["tmux", "send-keys", "-t", f"{new_name}:0.0",
            f"trap '' INT; EXIT_FLAG=$HOME/.local/share/agentic-shell/exit_requested; while true; do rm -f \"$EXIT_FLAG\"; clear; PYTHONPATH={install_dir} PROMPT_TOOLKIT_NO_CPR=1 NO_TMUX=1 AGENTIC_NEW_SESSION=1 {venv_python} -m shell.main; AGENTIC_NEW_SESSION=''; if [ -f \"$EXIT_FLAG\" ]; then rm -f \"$EXIT_FLAG\"; echo 'dropping to bash — run agentic-shell to return'; exec /bin/bash; fi; echo '[shell exited — restarting in 2s]'; sleep 2; done",
            "Enter"], check=True)

        subprocess.run(["tmux", "select-pane", "-t", f"{new_name}:0.0"], check=True)
        subprocess.run(["tmux", "switch-client", "-t", new_name], check=True)
        # Do NOT kill the old session — the SSH client is attached to it.
        # Killing it would drop the connection. User can kill old sessions manually.
    except Exception as exc:
        _out(f"Failed to create new session: {exc}")


def _save_turns_if_needed(
    turns: list[dict], session_id: str, config: ShellConfig, force: bool = False
) -> None:
    """Save compressed session context after every AI turn."""
    if not turns:
        return
    try:
        from shell.memory.compressor import compress
        from shell.memory.store import save_session_context
        import tiktoken
        compressed = compress(turns)
        enc = tiktoken.get_encoding("cl100k_base")
        token_count = len(enc.encode(compressed))
        save_session_context(session_id, compressed, turns, token_count)
    except Exception:
        pass


def start(config: ShellConfig, session_id: str, session_context: str = "") -> None:
    global _bypass_next, _last_exit

    from shell.executor import execute_bash
    from shell.router import classify, Route
    from shell.safety import is_destructive, confirm_destructive
    from shell.planner import execute_plan

    db = None
    try:
        from shell.telemetry.db import Database
        db = Database()
    except Exception:
        pass

    history_file = Path.home() / ".local" / "share" / "agentic-shell" / "history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    kb = _make_key_bindings(db=db)
    os.environ.setdefault("PROMPT_TOOLKIT_NO_CPR", "1")
    session = PromptSession(
        history=FileHistory(str(history_file)),
        key_bindings=merge_key_bindings([load_emacs_bindings(), kb]),
        completer=_ShellCompleter(),
        complete_while_typing=False,
        auto_suggest=AutoSuggestFromHistory(),
    )

    backend = _build_backend(config)
    # Track conversation turns for session continuity
    turns: list[dict] = []

    try:
        while True:
            try:
                cwd = os.getcwd()
                user_input = session.prompt(
                    ANSI(_render_prompt(cwd, _last_exit)),
                    in_thread=True
                )
            except EOFError:
                break

            line = user_input.strip()
            if not line:
                continue

            if _handle_builtin(line, db, session_id, config):
                continue

            if _bypass_next:
                _bypass_next = False
                exit_code, _ = execute_bash(line, cwd)
                if exit_code != 0:
                    _out(f"exit {exit_code}")
                continue

            if _offline_mode:
                exit_code, _ = execute_bash(line, cwd)
                if exit_code != 0:
                    _out(f"exit {exit_code}")
                continue

            route = classify(line, mode=config.routing_mode)

            if route == Route.AMBIGUOUS:
                try:
                    choice = session.prompt(
                        HTML("<ansiyellow>[b]ash or [a]gentic? </ansiyellow>")
                    ).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    choice = "b"
                route = Route.AGENTIC if choice == "a" else Route.BASH

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

            if not _check_and_enforce_budget(db, config, session_id):
                exit_code, _ = execute_bash(line, cwd)
                if exit_code != 0:
                    _out(f"exit {exit_code}")
                continue

            _spinner = _ThinkingSpinner()
            _spinner.start()
            try:
                response = asyncio.run(_call_llm(backend, line, cwd, config, session_context))
                _spinner.stop()
            except KeyboardInterrupt:
                _spinner.stop()
                _out("cancelled")
                continue

            if response is None:
                exit_code, _ = execute_bash(line, cwd)
                if exit_code != 0:
                    _out(f"exit {exit_code}")
                continue

            if response.plan:
                last_exit = execute_plan(response.plan, cwd, description=line)
                sys.stdout.write(f'  \033[38;5;238m·   ${response.cost_usd:.4f} · {response.prompt_tokens + response.completion_tokens} tok\033[0m\n')
                sys.stdout.flush()
                _log_event(db, session_id, response, str(response.plan), last_exit, line)
                turns.append({"role": "user", "content": line})
                turns.append({"role": "assistant", "content": f"plan: {response.plan}"})
                _save_turns_if_needed(turns, session_id, config)
                continue

            command = _display_command_preview(response)
            if command is None:
                continue

            ai_flagged = not response.safe
            regex_flagged = is_destructive(command)
            if ai_flagged or regex_flagged:
                reason = "AI flagged as potentially unsafe" if ai_flagged else "matched destructive pattern"
                if not confirm_destructive(command, reason=reason):
                    continue

            import time as _time
            _t0 = _time.monotonic()
            exit_code, _ = execute_bash(command, cwd)
            _elapsed = _time.monotonic() - _t0
            _last_exit = exit_code
            _print_exec_result(exit_code, _elapsed, cost_usd=response.cost_usd, total_tokens=response.prompt_tokens + response.completion_tokens)

            _log_event(db, session_id, response, command, exit_code, line)
            _audit_log("agentic", command, exit_code)

            # Track turn for session continuity
            turns.append({"role": "user", "content": line})
            turns.append({"role": "assistant", "content": f"command: {command}\nexplanation: {response.explanation}"})
            _save_turns_if_needed(turns, session_id, config)
    finally:
        _save_turns_if_needed(turns, session_id, config)
