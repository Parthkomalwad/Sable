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
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory

from sable.core.config.schema import ShellConfig

# Builtin handlers, one module per command family (docs/structure.md §5
# step 3). Imported under their original private names because
# _handle_builtin dispatches to them unchanged.
from sable.app.builtins.history import _show_history
from sable.app.builtins.memory import _handle_memory_builtin
from sable.app.builtins.route import _handle_route_builtin, _record_router_correction
from sable.app.builtins.session import _start_new_session
from sable.app.builtins.skill import _handle_skill_builtin
from sable.app.builtins.stats import _show_stats, _show_stats_csv
from sable.app.builtins.task import _handle_task_builtin

# The audit writers live in core/audit.py so the orchestrator can append to
# the ledger without importing the REPL.
from sable.core.audit import write_action as _audit_log
from sable.core.audit import write_command as _write_audit_log

from sable.ui.console import out as _out
from sable.ui.prompt.session import (
    _ShellCompleter,
    _make_key_bindings,
    _render_prompt,
)







# One-shot bash bypass flag set by Ctrl+B, cleared after one command
_bypass_next: bool = False
_offline_mode: bool = False
_budget_hard_stop: bool = False
_last_exit: int = 0


def set_offline_mode(offline: bool) -> None:
    global _offline_mode
    _offline_mode = offline




# Backend construction lives in llm/registry.py so that agents/ and skills/
# can build one without importing the REPL (docs/structure.md §5 step 3).
# Re-exported under the old private names because this module's tests and
# call sites still reach for them.
def _get_os_info() -> str:
    try:
        return f"{platform.system()} {platform.release()}"
    except Exception:
        return "Linux"



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







# Module-level reference updated by start() so builtins can access active turns
_active_turns: list[dict] = []










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
            from sable.skills.watcher import PatternWatcher
            from sable.skills.crystalliser import SkillCrystalliser
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
            from sable.ui.settings_panel import render_settings_panel
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
        from sable.app.tour import run_tour
        run_tour()
        return True

    if cmd in ("/bash", "/plain"):
        from sable.app.mode import run_plain_subshell
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
        from sable.ui.clipboard.manager import run_clip_command, open_picker
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












def _save_turns_if_needed(
    turns: list[dict], session_id: str, config: ShellConfig, force: bool = False
) -> None:
    """Save compressed session context after every AI turn."""
    if not turns:
        return
    try:
        from sable.memory.compressor import compress
        from sable.memory.session import save_session_context
        import tiktoken
        compressed = compress(turns)
        enc = tiktoken.get_encoding("cl100k_base")
        token_count = len(enc.encode(compressed))
        save_session_context(session_id, compressed, turns, token_count)
    except Exception:
        pass


def start(config: ShellConfig, session_id: str, session_context: str = "") -> None:
    global _bypass_next, _last_exit

    from sable.core.executor import execute_bash
    from sable.agents.router import classify, Route
    from sable.policy.engine import is_destructive, confirm_destructive

    db = None
    try:
        from sable.core.db import Database
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
            from sable.agents.manager import TaskManager
            from sable.agents.orchestrator import OrchestratorAgent
            from sable.core.db import DB_PATH
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
