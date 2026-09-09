"""Main REPL loop."""
from __future__ import annotations

import os
import platform
import sys
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


# One-shot bash bypass flag set by Ctrl+B, cleared after one command
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
        # Whatever is already typed was misrouted by definition: the user
        # reached for the bypass. Record it as a bash correction (I3).
        pending = event.app.current_buffer.text.strip()
        if pending:
            _record_router_correction(pending, "bash")
        _out("[bash mode] next command runs directly")

    @kb.add("c-t")
    def _ctrl_t(event) -> None:
        try:
            from shell.tui.layout import toggle_sidebar
            toggle_sidebar()
        except Exception:
            pass

    @kb.add("c-\\")
    def _ctrl_backslash(event) -> None:
        """Drop to a plain bash subshell for as long as the user wants."""
        event.app.current_buffer.set_document(
            __import__("prompt_toolkit.document", fromlist=["Document"]).Document("/bash")
        )
        event.app.current_buffer.validate_and_handle()

    @kb.add("c-x")
    def _ctrl_x(event) -> None:
        event.app.current_buffer.set_document(
            __import__("prompt_toolkit.document", fromlist=["Document"]).Document("/config")
        )
        event.app.current_buffer.validate_and_handle()

    return kb


def _mock_backend_or_none(mode: str = "orchestrator"):
    """Return a MockLLMBackend when SABLE_MOCK_LLM is set, else None.

    Lets the whole shell run with zero API calls for demos and the playground.
    The fixture lives under tests/, which is not importable from an installed
    copy, so an ImportError here falls back to the real backend rather than
    breaking startup.
    """
    if os.environ.get("SABLE_MOCK_LLM", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    try:
        from tests.fixtures.mock_llm import MockLLMBackend
    except ImportError:
        _out("[sable] SABLE_MOCK_LLM is set but the mock backend is unavailable")
        return None
    return MockLLMBackend(mode=mode)


def _build_backend(config: ShellConfig, mock_mode: str = "orchestrator"):
    """Return the configured LLM backend, or the mock when SABLE_MOCK_LLM is set.

    mock_mode selects which canned script the mock plays: "orchestrator" for
    the REPL's reasoning loop, "worker" for a TaskAgent.
    """
    # Checked before importing the real backends so the mock path does not
    # need httpx installed.
    mock = _mock_backend_or_none(mock_mode)
    if mock is not None:
        return mock

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


def _write_audit_log(session_id: str, cwd: str, command: str) -> None:
    """Append one line to audit.log. Format: ISO\tsession_id\tcwd\tcommand"""
    from shell.telemetry.db import AUDIT_LOG_PATH
    from datetime import datetime, timezone
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now(timezone.utc).isoformat()}\t{session_id}\t{cwd}\t{command}\n"
    try:
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except (PermissionError, OSError):
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
            _out("Warning: Budget at 80%+ approaching limit.")
    except Exception:
        pass
    return True


_HELP_TEXT = (
    "\nSable - Built-in Commands\n"
    "----------------------------------\n"
    "  /new           Start a fresh tmux session\n"
    "  /clear         Clear the terminal screen\n"
    "  /exit          Exit the shell\n"
    "  /config        Open settings panel (Ctrl+X)\n"
    "  /mode          Toggle routing: auto / prefix\n"
    "  /model         Show current LLM model\n"
    "  /stats         Last 7 days token usage\n"
    "  /history       recent commands with AI costs\n"
    "  /clip           Snippet clipboard (add/run/del)\n"
    "  /task           Manage background agents\n"
    "  /skill          Manage skill files\n"
    "  /route why \"<line>\"  Explain how a line would be routed\n"
    "  /bash           Plain bash subshell, exit returns here (also /plain, Ctrl+\\)\n"
    "  /tour           Guided walkthrough of what Sable does\n"
    "  /budget reset  Clear hard-stop budget flag\n"
    "  /memory                  View current context\n"
    "  /memory versions         List all saved snapshots\n"
    "  /memory revert <id>      Restore a snapshot\n"
    "  /memory clear            Clear active context\n"
    "\n"
    "  >> text        Force agentic (prefix mode)\n"
    "  Ctrl+B         Next command runs as raw bash\n"
    "  Ctrl+T         Toggle telemetry sidebar\n"
)



def _get_recent_turns() -> list[dict]:
    """Return the current active turns list (module-level reference)."""
    return _active_turns


def _build_spawn_context(turns: list[dict], goal: str) -> str:
    """Build a compressed context summary to hand off to a spawned agent.

    Takes last 8 turns from the orchestrator session, compresses them,
    and prepends a header explaining why this agent was spawned.
    """
    if not turns:
        return ""
    recent = turns[-8:]
    try:
        from shell.memory.compressor import compress
        summary = compress(recent)
    except Exception:
        summary = "\n".join(
            f"{t.get('role','user')}: {str(t.get('content',''))[:200]}"
            for t in recent
        )
    return (
        f"You were spawned by the orchestrator to: {goal}\n\n"
        f"Recent orchestrator session history (compressed):\n{summary}"
    )


# Module-level reference updated by start() so builtins can access active turns
_active_turns: list[dict] = []


def _handle_task_builtin(parts: list[str], config, db, turns: list[dict] | None = None) -> bool:
    """Handle /task subcommands. Return True if handled."""
    from shell.tasks.manager import TaskManager
    manager = TaskManager(config=config, db=db)

    if not parts:
        _out("usage: /task <new|list|attach|back|clean|pause|resume|kill|inspect|stats|history|checkpoint|revert>")
        return True

    sub = parts[0]

    if sub == "back":
        manager.back()
        return True

    if sub == "clean":
        import shutil as _shutil
        from pathlib import Path as _P
        tasks_base = _P(getattr(config, "tasks_base_dir", "~/tasks")).expanduser()
        if not tasks_base.exists():
            _out("no tasks folder found")
            return True
        folders = [f for f in tasks_base.iterdir() if f.is_dir()]
        if not folders:
            _out("no task folders to delete")
            return True
        _out(f"  will delete {len(folders)} task folder(s):")
        for f in folders:
            _out(f"    {f.name}/")
        try:
            answer = input("  type YES to confirm: ").strip()
        except (EOFError, KeyboardInterrupt):
            return True
        if answer != "YES":
            _out("cancelled")
            return True
        for f in folders:
            try:
                _shutil.rmtree(f)
                _out(f"  deleted {f.name}/")
            except Exception as exc:
                _out(f"  failed to delete {f.name}/: {exc}")
        # Also clear tasks from DB
        try:
            db._conn.execute("DELETE FROM tasks")
            db._conn.commit()
        except Exception:
            pass
        _out("done all task folders deleted")
        return True

    if sub == "list":
        tasks = manager.list_tasks()
        if not tasks:
            _out("no tasks")
        for t in tasks:
            _out(f"  [{t['status']}] {t['name']} {t['goal'][:60]}")
        return True

    if sub == "new" and len(parts) >= 3:
        name = parts[1]
        goal = " ".join(parts[2:])
        try:
            # Build context handoff from recent orchestrator turns
            context = _build_spawn_context(turns=_get_recent_turns(), goal=goal)
            manager.spawn(name, goal, context=context)
            _out(f"task '{name}' spawned")
            if context:
                _out(f"  context: {len(context)} chars of session history handed off")
        except Exception as exc:
            _out(f"[error] {exc}")
        return True

    if len(parts) >= 2:
        name = parts[1]
        if sub == "attach":
            manager.attach(name)
        elif sub == "pause":
            manager.pause(name)
            _out(f"task '{name}' paused")
        elif sub == "resume":
            manager.resume(name)
            _out(f"task '{name}' resumed")
        elif sub in ("kill", "done"):
            manager.kill(name)
            _out(f"task '{name}' killed")
        elif sub == "inspect":
            manager.inspect(name)
        elif sub == "stats":
            s = manager.stats(name)
            _out(f"  tokens: {s['prompt_tokens']}p / {s['completion_tokens']}c  cost: ${s['cost_usd']:.4f}")
        elif sub == "history":
            for row in manager.history(name):
                _out(f"  {row['timestamp']}  {row['model']}  ${row['cost_usd']:.4f}")
        elif sub == "checkpoint":
            v = manager.checkpoint(name)
            _out(f"checkpoint v{v} saved")
        elif sub == "revert" and len(parts) >= 3:
            version = int(parts[2].lstrip("v"))
            manager.revert(name, version)
            _out(f"context reverted to v{version}")
        else:
            _out(f"unknown /task subcommand: {sub}")
        return True

    _out(f"usage: /task {sub} <name>")
    return True


def _handle_skill_builtin(parts: list[str]) -> bool:
    """Handle /skill subcommands. Return True if handled."""
    import subprocess
    from pathlib import Path
    skills_dir = Path.home() / "skills" / "instructions"
    skills_dir.mkdir(parents=True, exist_ok=True)

    if not parts or parts[0] == "list":
        files = list(skills_dir.glob("*.md"))
        if not files:
            _out("no skills found")
        for f in files:
            _out(f"  {f.stem}")
        return True

    if parts[0] == "new" and len(parts) >= 2:
        name = parts[1]
        path = skills_dir / f"{name}.md"
        path.write_text(f"# {name}\n\n<!-- describe when to use this skill -->\n")
        _out(f"created {path}")
        return True

    if parts[0] == "edit" and len(parts) >= 2:
        name = parts[1]
        path = skills_dir / f"{name}.md"
        editor = os.environ.get("EDITOR", "nano")
        subprocess.run([editor, str(path)])
        return True

    _out("usage: /skill <list|new <name>|edit <name>>")
    return True


def _record_router_correction(line: str, label: str) -> None:
    """Append one "input<TAB>label" row to the router corrections file.

    Written whenever the user overrides a routing decision: Ctrl+B (this line
    was bash, not a goal) or an answer to the [b/a] prompt. These rows are the
    training data for the router accuracy programme (I3); the corpus in
    tests/fixtures/router_corpus.tsv is the curated version of the same shape.
    """
    from shell import paths

    text = line.strip()
    if not text:
        return
    try:
        paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(paths.ROUTER_CORRECTIONS, "a", encoding="utf-8") as handle:
            handle.write(text + "\t" + label + "\n")
    except (PermissionError, OSError):
        pass


def _handle_route_builtin(argument: str, config: ShellConfig) -> bool:
    """Handle `/route why "<line>"`. Returns True if handled."""
    from shell.router import explain

    argument = argument.strip()
    if not argument.startswith("why"):
        _out('usage: /route why "<line>"')
        return True

    target = argument[len("why"):].strip().strip('"').strip("'")
    if not target:
        _out('usage: /route why "<line>"')
        return True

    exp = explain(target, mode=getattr(config, "routing_mode", "auto"))

    PURPLE = "[38;5;141m"
    GREEN = "[38;5;114m"
    YELLOW = "[38;5;179m"
    DIM = "[2;37m"
    RESET = "[0m"
    colour = {"bash": GREEN, "agentic": PURPLE, "ambiguous": YELLOW}[exp.route.value]

    _out("")
    _out(f"  {DIM}line{RESET}   {exp.line}")
    _out(f"  {DIM}route{RESET}  {colour}{exp.route.value}{RESET}")
    _out(f"  {DIM}score{RESET}  bash {exp.bash_score}  vs  nl {exp.nl_score}")
    _out("")
    if exp.reasons:
        _out(f"  {DIM}rules that fired{RESET}")
        for side, reason, points in exp.reasons:
            tag = f"{GREEN}bash{RESET}" if side == "bash" else f"{PURPLE}nl  {RESET}"
            score = f"+{points}" if points else "  "
            _out(f"    {tag} {score}  {reason}")
    else:
        _out(f"  {DIM}no scoring rules fired{RESET}")
    _out("")
    _out(f"  {DIM}decision{RESET}  {exp.decisive}")
    _out("")
    return True


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
        # Run pattern watcher at exit
        try:
            from shell.skills.pattern_watcher import PatternWatcher
            from shell.skills.crystalliser import SkillCrystalliser
            watcher = PatternWatcher()
            patterns = watcher.observe()
            if patterns:
                crystalliser = SkillCrystalliser(config=config)
                for p in patterns:
                    path = crystalliser.crystallise(p)
                    _out(f"[skill] auto-generated: {path.name}")
        except Exception:
            pass
        raise SystemExit(0)

    if cmd == "/model":
        _out(f"backend: {config.backend}  model: {config.model}")
        return True

    if cmd == "/mode":
        current = getattr(config, "routing_mode", "auto")
        new_mode = "prefix" if current == "auto" else "auto"
        config.routing_mode = new_mode
        if new_mode == "prefix":
            _out("Routing mode: prefix prefix your request with >> to send to LLM")
        else:
            _out("Routing mode: auto shell auto-detects bash vs natural language")
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

    if cmd == "/tour":
        from shell.tour import run_tour
        run_tour()
        return True

    if cmd in ("/bash", "/plain"):
        from shell.mode import run_plain_subshell
        run_plain_subshell(os.getcwd())
        return True

    if cmd == "/route" or cmd.startswith("/route "):
        return _handle_route_builtin(cmd[len("/route"):], config)

    if cmd == "/task" or cmd.startswith("/task "):
        parts = cmd[len("/task"):].strip().split()
        return _handle_task_builtin(parts, config, db, turns=_active_turns)

    if cmd == "/skill" or cmd.startswith("/skill "):
        parts = cmd[len("/skill"):].strip().split()
        return _handle_skill_builtin(parts)

    if cmd == "/clip" or cmd.startswith("/clip "):
        from shell.clipboard.manager import run_clip_command, open_picker
        remainder = cmd[len("/clip"):].strip()
        if not remainder:
            open_picker(db)
        else:
            run_clip_command(remainder, db)
        return True

    if cmd == "shell stats --csv":
        _show_stats_csv(db)
        return True

    if cmd in ("/memory", "shell memory") or cmd.startswith("/memory "):
        subcmd = cmd[len("/memory"):].strip()
        _handle_memory_builtin(subcmd, session_id, _active_turns)
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
        _out("\nToken Usage Last 7 Days")
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


def _handle_memory_builtin(subcmd: str, session_id: str, turns: list[dict]) -> None:
    """Handle /memory subcommands: versions, revert <id>, show, clear."""
    from shell.memory.store import (
        load_session_context, save_session_context,
        list_versions, load_version,
    )
    username = os.environ.get("USER", os.environ.get("USERNAME", "user"))

    if subcmd == "versions" or subcmd == "list":
        versions = list_versions(username)
        if not versions:
            _out("No saved memory versions.")
            return
        _out("\n  ID   tokens  saved-at              preview")
        _out("  " + "─" * 70)
        for v in versions:
            ts = (v["created_at"] or "")[:19]
            _out(f"  {v['id']:<5} {v['token_count']:<7} {ts}  {v['preview'][:50]}")
        _out("\n  use: /memory revert <id>  to restore a version")
        return

    if subcmd.startswith("revert"):
        parts = subcmd.split()
        if len(parts) < 2:
            _out("usage: /memory revert <id>")
            return
        try:
            vid = int(parts[1])
        except ValueError:
            _out(f"invalid id: {parts[1]}")
            return
        snap = load_version(vid, username)
        if snap is None:
            _out(f"version {vid} not found")
            return
        # Restore raw turns into active session
        raw = snap["raw_turns"]
        turns.clear()
        turns.extend(raw)
        # Also save as new snapshot so it shows in versions list
        save_session_context(session_id, snap["compressed"], raw, len(snap["compressed"].split()))
        _out(f"reverted to version {vid} {len(raw)} turns restored")
        _out(f"context preview: {snap['compressed'][:200]}")
        return

    if subcmd == "clear":
        turns.clear()
        save_session_context(session_id, "", [], 0)
        _out("Session context cleared.")
        return

    # Default: show current context
    ctx = load_session_context(username)
    if not ctx:
        _out("No session context saved yet.")
        _out("use: /memory versions  to list all snapshots")
        return
    _out("\nActive session context:")
    _out("─" * 60)
    _out(ctx[:1000])
    if len(ctx) > 1000:
        _out("... (truncated)")
    _out("\nuse: /memory versions | /memory revert <id> | /memory clear")


def _start_new_session() -> None:
    import subprocess, shutil, time
    if not shutil.which("tmux"):
        _out("tmux not found restarting shell process.")
        os.execv(sys.executable, [sys.executable, "-m", "shell.main"])
        return

    _out("Starting new session...")
    try:
        result = subprocess.run(["tmux", "display-message", "-p", "#S"], capture_output=True, text=True)
        current_session = result.stdout.strip()
    except Exception:
        current_session = ""

    new_name = f"sable-{int(time.time()) % 10000}"
    install_dir = os.environ.get("PYTHONPATH", "")
    venv_python = os.environ.get("AGENTIC_PYTHON", sys.executable)

    try:
        # Create session sized to current terminal
        import shutil as _shutil
        _ts = _shutil.get_terminal_size((220, 50))
        subprocess.run(["tmux", "new-session", "-d", "-s", new_name,
                        "-x", str(_ts.columns), "-y", str(_ts.lines)], check=True)

        # Capture the main pane ID, then split using IDs (avoids numbering ambiguity)
        main_pane = subprocess.run(
            ["tmux", "display-message", "-t", f"{new_name}:0.0", "-p", "#{pane_id}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        # Split right: telemetry sidebar (48 cols)
        tele_pane = subprocess.run(
            ["tmux", "split-window", "-h", "-t", main_pane, "-l", "48", "-P", "-F", "#{pane_id}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        # Split main pane bottom: tasks panel (~30%)
        task_lines = max(8, _ts.lines * 30 // 100)
        task_pane = subprocess.run(
            ["tmux", "split-window", "-v", "-t", main_pane, "-l", str(task_lines), "-P", "-F", "#{pane_id}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        # Layout: main_pane=top-left shell, tele_pane=right, task_pane=bottom-left
        subprocess.run(["tmux", "send-keys", "-t", tele_pane,
            f"trap '' INT; clear; while true; do PYTHONPATH={install_dir} PROMPT_TOOLKIT_NO_CPR=1 {venv_python} -m shell.telemetry.watch; sleep 2; done",
            "Enter"], check=True)

        subprocess.run(["tmux", "send-keys", "-t", task_pane,
            f"trap '' INT; clear; while true; do PYTHONPATH={install_dir} PROMPT_TOOLKIT_NO_CPR=1 {venv_python} -m shell.tasks.panel; sleep 2; done",
            "Enter"], check=True)

        # Shell in main pane AGENTIC_NEW_SESSION=1 skips session resume
        subprocess.run(["tmux", "send-keys", "-t", main_pane,
            f"trap '' INT; EXIT_FLAG=$HOME/.local/share/agentic-shell/exit_requested; while true; do rm -f \"$EXIT_FLAG\"; clear; PYTHONPATH={install_dir} PROMPT_TOOLKIT_NO_CPR=1 NO_TMUX=1 AGENTIC_NEW_SESSION=1 {venv_python} -m shell.main; AGENTIC_NEW_SESSION=''; if [ -f \"$EXIT_FLAG\" ]; then rm -f \"$EXIT_FLAG\"; echo 'dropping to bash run agentic-shell to return'; exec /bin/bash; fi; echo '[shell exited restarting in 2s]'; sleep 2; done",
            "Enter"], check=True)

        subprocess.run(["tmux", "select-pane", "-t", main_pane], check=True)
        subprocess.run(["tmux", "switch-client", "-t", new_name], check=True)
        # Do NOT kill the old session the SSH client is attached to it.
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

    # Track conversation turns for session continuity
    global _active_turns
    turns: list[dict] = []
    _active_turns = turns  # keep module-level ref in sync for builtins

    try:
        while True:
            try:
                try:
                    cwd = os.getcwd()
                except FileNotFoundError:
                    # cwd was deleted fall back to home
                    os.chdir(os.path.expanduser("~"))
                    cwd = os.getcwd()
                    _out("[cwd deleted moved to home]")
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
                # The user just labelled this line for us (I3).
                _record_router_correction(line, route.value)

            if route == Route.BASH:
                if is_destructive(line):
                    if not confirm_destructive(line):
                        _audit_log("destructive_blocked", line)
                        continue
                exit_code, _ = execute_bash(line, cwd)
                _last_exit = exit_code
                _audit_log("bash", line, exit_code)
                _write_audit_log(session_id, cwd, line)
                if exit_code != 0:
                    _out(f"exit {exit_code}")
                continue

            if not _check_and_enforce_budget(db, config, session_id):
                exit_code, _ = execute_bash(line, cwd)
                if exit_code != 0:
                    _out(f"exit {exit_code}")
                continue

            # NL path: hand off to OrchestratorAgent reasoning loop
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
                turns.append({"role": "user", "content": line})
                turns.append({"role": "assistant", "content": f"[orchestrator handled: {line}]"})
                _save_turns_if_needed(turns, session_id, config)
            except KeyboardInterrupt:
                _out("[interrupted]")
                continue
    finally:
        _save_turns_if_needed(turns, session_id, config)
