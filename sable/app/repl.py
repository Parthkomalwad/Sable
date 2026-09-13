"""Main REPL loop."""
from __future__ import annotations

import os
import platform
from pathlib import Path

os.environ["PROMPT_TOOLKIT_NO_CPR"] = "1"

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.formatted_text import HTML, ANSI
from prompt_toolkit.key_binding import merge_key_bindings
from prompt_toolkit.key_binding.bindings.emacs import load_emacs_bindings
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory

from sable.core.config.schema import ShellConfig

from sable.app import budget
from sable.app.builtins.dispatch import handle_builtin as _handle_builtin

# Still used directly by the loop: Ctrl+B records a router correction.
from sable.app.builtins.route import _record_router_correction

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



# Module-level reference updated by start() so builtins can access active turns
_active_turns: list[dict] = []






















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
    _active_turns = turns  # the dispatcher is handed this each turn

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

            if _handle_builtin(line, db, session_id, config, turns=_active_turns):
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

            if not budget.check_and_enforce(db, config, session_id):
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
