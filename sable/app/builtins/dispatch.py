"""Builtin dispatch: one input line in, handled or not out.

Extracted from `app/repl.py` in Phase 0.5 step 3 (docs/structure.md §5), the
last piece needed to bring that module under the roadmap gate's 250 lines.

The if-chain is kept as an if-chain rather than converted to a table. A
table reads tidier but the clauses are not uniform: some match exactly, some
by prefix, some accept an alias, and several need different slices of the
line. Encoding that in data means a dispatch mechanism more complicated than
the chain it replaces, for no behaviour change. This is a move.

The two module globals the chain used are gone: budget state lives in
`app/budget.py` and is reached through functions, and the conversation turns
list is a parameter. Nothing here mutates another module's namespace.
"""
from __future__ import annotations

import os
import sys

from sable.app import budget
from sable.app.builtins.history import _show_history
from sable.app.builtins.memory import _handle_memory_builtin
from sable.app.builtins.route import _handle_route_builtin
from sable.app.builtins.session import _start_new_session
from sable.app.builtins.skill import _handle_skill_builtin
from sable.app.builtins.stats import _show_stats, _show_stats_csv
from sable.app.builtins.task import _handle_task_builtin
from sable.core.config.schema import ShellConfig
from sable.ui.console import out as _out


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


def handle_builtin(
    line: str, db, session_id: str, config: ShellConfig,
    turns: list[dict] | None = None,
) -> bool:
    """Dispatch one input line to a builtin. False if it is not a builtin.

    `turns` is the live conversation list. It is passed in rather than read
    from a module global, so this module owns no mutable cross-module state.
    """
    turns = turns if turns is not None else []
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
        budget.reset()
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
        return _handle_task_builtin(parts, config, db, turns=turns)

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
        _handle_memory_builtin(subcmd, session_id, turns)
        return True

    return False
