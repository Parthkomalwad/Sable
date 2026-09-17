"""Main REPL loop."""
from __future__ import annotations

import os
import platform
import sqlite3
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
    except (OSError, AttributeError):
        return "Linux"



# Module-level reference updated by start() so builtins can access active turns
_active_turns: list[dict] = []






















def _crystalliser_or_none(config):
    """A `SkillCrystalliser` for the orchestrator to draft with (B3).

    Built here rather than inside the agent because `agents` sits below
    `skills` in the layering rule, the same inversion `skill_loader`,
    `skill_index` and `corrections_db` already use.

    Returns None if it cannot be built, which gives the pre-B3 behaviour of
    drafting nothing. A shell that will not start because the skills
    directory is unreadable would be a poor trade for a feature that only
    ever runs after the work is finished.
    """
    try:
        from sable.skills.crystalliser import SkillCrystalliser
        return SkillCrystalliser(config=config)
    except (ImportError, OSError, ValueError):
        return None


def _match_alias(db, line: str):
    """Resolve a line to a stored alias, or None. Never raises.

    Imported lazily so that a broken or absent alias store degrades to "no
    aliases" rather than stopping the REPL from starting.
    """
    if db is None:
        return None
    try:
        from sable.skills.aliases import match_alias
        return match_alias(db, line)
    except (ImportError, sqlite3.Error):
        return None


def _after_alias_use(db, alias_hit: dict) -> None:
    """Count the use and, at the threshold, offer promotion to a skill.

    Offered, never taken: a skill is text a model reads and acts on, so a
    human decides. The offer is recorded so an ignored one does not reappear
    on every later use.
    """
    try:
        from sable.skills.aliases import (
            mark_promotion_offered, record_use, should_offer_promotion,
        )
    except ImportError:
        return

    phrase = alias_hit["phrase"]
    record_use(db, phrase)
    if should_offer_promotion(db, phrase):
        _out(f"[alias] '{phrase}' has been used 3 times.")
        _out("        /skill new to turn it into a skill, or ignore this.")
        mark_promotion_offered(db, phrase)


def pending_skills_banner() -> str:
    """One line naming how many drafted skills are waiting for approval.

    Reported at login rather than at `/exit`, where the drafting happens:
    a message shown to a closing shell is one the user cannot act on. A
    draft nobody is told about is the same as no draft at all.

    Returns an empty string when there is nothing to say, so a fresh
    install is not greeted by a count of zero, or by an error from an
    index that does not exist yet.
    """
    try:
        from sable.skills.index import SkillIndex

        pending = SkillIndex().pending()
    except (OSError, ValueError, ImportError):
        # ValueError covers a corrupt index (JSONDecodeError subclasses it).
        # A broken index is not a reason to refuse to start a shell.
        return ""

    count = len(pending)
    if count == 0:
        return ""

    noun = "draft skill" if count == 1 else "draft skills"
    return (f"[skill] {count} {noun} pending approval "
            f"(/skill list to review)")


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
    except (ImportError, OSError, ValueError, TypeError):
        # Compression or tiktoken failing loses continuity next login, not
        # this session's work.
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
    except (sqlite3.Error, OSError):
        # The shell runs without telemetry rather than refusing to start. The
        # I7 banner reports the same condition at startup.
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

    # Skills drafted at the last `/exit` are inert until someone approves
    # them, so this is where the user finds out they exist: a shell they
    # can act in, rather than one that is closing. Silent when there are
    # none (Task 7).
    _pending_line = pending_skills_banner()
    if _pending_line:
        _out(_pending_line)

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

            # K4: an alias resolves before the router is consulted. This is
            # the only place it can be both instant and free; anywhere later
            # and the line has already cost a classification or a turn.
            alias_hit = _match_alias(db, line)
            if alias_hit is not None:
                resolved = alias_hit["command"]
                _out(f"[alias] {alias_hit['phrase']} -> {resolved}")
                # An alias is not a safety bypass: the resolved command goes
                # down the ordinary bash path and is gated the same way.
                if is_destructive(resolved):
                    if not confirm_destructive(resolved):
                        _audit_log("destructive_blocked", resolved)
                        continue
                exit_code, _ = execute_bash(resolved, cwd)
                _last_exit = exit_code
                _audit_log("alias", resolved, exit_code)
                _write_audit_log(session_id, cwd, resolved)
                if exit_code != 0:
                    _out(f"exit {exit_code}")
                _after_alias_use(db, alias_hit)
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
                _record_router_correction(line, route.value, db=db)

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
            # The composition root for a typed goal. Constructing these here
            # rather than inside OrchestratorAgent is what keeps the
            # `agents -> skills` edge out of the layering rule, the same
            # inversion `corrections_db` and the worker's collaborators use.
            #
            # "orchestrator" is a task name with no task-local skills
            # directory, which is correct: a typed goal has no workspace, so
            # only global approved skills apply.
            from sable.skills.index import SkillIndex
            from sable.skills.loader import TaskSkillLoader

            _tasks_base = str(Path(config.tasks_base_dir).expanduser())
            agent = OrchestratorAgent(
                goal=line,
                cwd=cwd,
                config=config,
                db_path=str(DB_PATH),
                task_manager=task_manager,
                session_id=session_id,
                corrections_db=db,
                skill_loader=TaskSkillLoader("orchestrator", _tasks_base),
                skill_index=SkillIndex(),
                crystalliser=_crystalliser_or_none(config),
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
