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
import sqlite3
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
    "  /corrections   Commands you corrected (list, delete <id>)\n"
    "  /alias \"phrase\" = <command>   Name a command in your own words\n"
    "  /route why \"<line>\"  Explain how a line would be routed\n"
    "  /why [agent]   What the model saw when it last decided\n"
    "  /inbox [show|approve|reject K]  Everything waiting on you, in one list\n"
    "  /approve [id]  Commands sub-agents are waiting on you to allow\n"
    "  /dash          Full-screen command center: lanes, approvals, breaker (q quits)\n"
    "  /tools       Tools each agent role may call, and at which tier\n"
    "  /audit [--since 1h] [--agent NAME] [--export jsonl]  Who ran what, why, outcome\n"
    "  /schedule \"<sentence>\"  Recurring job (list, pause|resume|run-now|rm N)\n"
    "  /secret add|list|rm <name>  Keyring secrets, used as $SECRET:name\n"
    "  /task replay <n>     Every turn of one agent, as the model saw it\n"
    "  /task events <n>     The agent's event stream\n"
    "  /task guide <n> <text>  Steer a running agent (also Ctrl+G)\n"
    "  /bash           Plain bash subshell, exit returns here (also /plain, Ctrl+\\)\n"
    "  /tour           Guided walkthrough of what Sable does\n"
    "  /theme <name>  Colours: default, mono, high-contrast\n"
    "  /layout <name>  tmux panes: focus, fleet, minimal\n"
    "  /budget reset  Clear hard-stop budget flag\n"
    "  /memory                  View current context\n"
    "  /memory versions         List all saved snapshots\n"
    "  /memory revert <id>      Restore a snapshot\n"
    "  /memory clear            Clear active context\n"
    "\n"
    "  >> text        Force agentic (prefix mode)\n"
    "  Ctrl+B         Next command runs as raw bash\n"
    "  Ctrl+G         Steer a running agent\n"
    "  Ctrl+P         Palette: builtins, skills, snippets, tasks\n"
    "  ?  /  !        After a failed command: explain it / propose a fix\n"
    "  Ctrl+T         Toggle telemetry sidebar\n"
)


_ALIAS_USAGE = 'usage: /alias "<phrase>" = <command>  |  /alias delete "<phrase>"'


def _strip_quotes(text: str) -> str:
    """Remove one matching pair of surrounding quotes, if present."""
    text = text.strip()
    for quote in ('"', "'"):
        if len(text) >= 2 and text.startswith(quote) and text.endswith(quote):
            return text[1:-1]
    return text


def _handle_alias_builtin(argument: str, db) -> bool:
    """Handle `/alias`, `/alias "<phrase>" = <cmd>` and `/alias delete "<phrase>"`.

    The phrase is quoted and the command is not, so the first `=` outside the
    quoted phrase separates them. A command may itself contain `=`
    (`make FOO=bar`), which is why the split is on the first one after the
    closing quote rather than on every one.
    """
    from sable.skills.aliases import add_alias, delete_alias, list_aliases

    if not argument:
        rows = list_aliases(db)
        if not rows:
            _out("no aliases yet")
            _out(_ALIAS_USAGE)
        for row in rows:
            uses = f"{row['use_count']} use" + ("s" if row["use_count"] != 1 else "")
            _out(f"  {row['phrase']}  ->  {row['command']}  ({uses})")
        return True

    if argument.startswith("delete"):
        phrase = _strip_quotes(argument[len("delete"):])
        if not phrase:
            _out(_ALIAS_USAGE)
        elif delete_alias(db, phrase):
            _out(f"deleted alias {phrase!r}")
        else:
            _out(f"no alias {phrase!r}")
        return True

    if "=" not in argument:
        _out(_ALIAS_USAGE)
        return True

    phrase_part, _, command_part = argument.partition("=")
    phrase = _strip_quotes(phrase_part)
    command = command_part.strip()

    if not phrase or not command:
        _out(_ALIAS_USAGE)
        return True

    if add_alias(db, phrase, command):
        _out(f"alias {phrase!r} -> {command}")
    else:
        _out("could not store that alias")
    return True


def _handle_approve_builtin(argument: str, db) -> bool:
    """`/approve`, `/approve <id>`, `/approve reject <id>`. Always True.

    What sub-agents asked to run and could not, because nobody can type YES
    in their window. An approved command runs the next time that agent
    proposes it, once.
    """
    from sable.policy import queue

    if db is None:
        _out("approvals need the session database, which is unavailable")
        return True

    parts = argument.split()
    approve = True
    if parts and parts[0] == "reject":
        approve, parts = False, parts[1:]
    if parts:
        if len(parts) != 1 or not parts[0].isdigit():
            _out("usage: /approve [reject] <id>")
        elif queue.decide_request(db._conn, int(parts[0]), approve=approve):
            _out(f"{'approved' if approve else 'rejected'} #{parts[0]}"
                 + (": it runs when the agent proposes it again" if approve else ""))
        else:
            _out(f"no pending request #{parts[0]}")
        return True

    waiting = queue.pending(db._conn)
    if not waiting:
        _out("nothing waiting for approval")
        return True
    for r in waiting:
        _out(f"  #{r['id']}  {r['agent']}  $ {r['command']}")
        _out(f"       {r['rule']}: {r['why']}")
    _out("/approve <id> to allow once, /approve reject <id> to refuse")
    return True


def _handle_breaker_builtin(argument: str, db) -> bool:
    """`/breaker` shows open trips; `/breaker reset` clears them. Always True."""
    from sable.policy import breaker

    if db is None:
        _out("the breaker needs the session database, which is unavailable")
        return True
    if argument == "reset":
        n = breaker.reset(db._conn)
        _out(f"breaker reset ({n} trip{'s' if n != 1 else ''} cleared)")
        return True
    if argument:
        _out("usage: /breaker [reset]")
        return True
    trips = breaker.tripped(db._conn)
    if not trips:
        _out("breaker: not tripped")
        return True
    for t in trips:
        _out(f"  #{t['id']}  {t['job']}: {t['reason']}")
    _out("/breaker reset to resume autonomous jobs")
    return True


def _handle_dash_builtin() -> bool:
    """`/dash`: the full-screen command center, as a child process. Always True.

    The child owns the terminal until it quits; the shell never imports
    textual (tests/unit/test_ui_state.py holds it to that).
    """
    import importlib.util
    import subprocess

    if importlib.util.find_spec("textual") is None:
        _out("/dash needs textual: pip install 'textual>=8.2,<8.3'")
        return True
    try:
        subprocess.run([sys.executable, "-m", "sable.ui.dash"], check=False)
    except OSError as e:
        _out(f"/dash could not start: {e}")
    return True


def _handle_corrections_builtin(argument: str, db) -> bool:
    """Handle `/corrections [delete <id>]`. Always returns True.

    Withheld rows are reported alongside the kept ones rather than silently
    omitted: a user who corrected five commands and sees three listed should
    be able to learn that the other two carried a secret.
    """
    from sable.skills.corrections import (
        delete_correction, list_corrections, withheld_count,
    )

    if argument.startswith("delete"):
        target = argument[len("delete"):].strip()
        if not target.isdigit():
            _out("usage: /corrections delete <id>")
            return True
        if delete_correction(db, int(target)):
            _out(f"deleted correction {target}")
        else:
            _out(f"no correction with id {target}")
        return True

    if argument:
        _out("usage: /corrections [delete <id>]")
        return True

    rows = list_corrections(db)
    withheld = withheld_count(db)

    if not rows:
        _out("no corrections recorded yet")
    else:
        for row in rows:
            _out(f"  {row['id']:>4}  {row['kind']:<6}  {row['proposed']}")
            _out(f"        {'':<6}  -> {row['corrected']}")

    if withheld:
        _out(f"  {withheld} withheld this week (contained a secret)")
    return True


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
        # Detect repeated command patterns and draft them as skills.
        #
        # Phase 2 (Task 7) changed what happens next. This used to write the
        # skill enabled, so a pattern crossing the 3x threshold started
        # reaching a model's context with nobody having approved it, and the
        # user was told as their shell closed, with no way to act on the
        # message. Drafting still happens here; enabling is a human's job.
        try:
            from sable.skills.watcher import PatternWatcher
            from sable.skills.crystalliser import SkillCrystalliser
            watcher = PatternWatcher()
            patterns = watcher.observe()
            if patterns:
                crystalliser = SkillCrystalliser(config=config)
                for p in patterns:
                    path = crystalliser.crystallise(p, status="pending")
                    _out(f"[skill] draft saved: {path.name} "
                         f"(/skill list to review, /skill approve to enable)")
        except (ImportError, OSError, sqlite3.Error, ValueError):
            # Crystallisation runs on the way out. A failure here must not stop
            # the user exiting their shell.
            pass
        raise SystemExit(0)

    if cmd == "/model":
        _out(f"backend: {config.backend}  model: {config.model}")
        return True

    if cmd == "/theme" or cmd.startswith("/theme "):
        from sable.ui.theme import THEMES, persist, set_theme
        name = cmd[len("/theme"):].strip()
        if not set_theme(name):
            _out(f"theme: {config.theme}   usage: /theme {'|'.join(THEMES)}")
            return True
        config.theme = name
        saved = "" if persist(name) else " (not saved: config.json unwritable)"
        _out(f"theme: {name}{saved}")
        return True

    if cmd == "/layout" or cmd.startswith("/layout "):
        from sable.ui.tmux.layout import apply_preset
        _out(apply_preset(cmd[len("/layout"):].strip()))
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
        except (ImportError, OSError, ValueError, AttributeError) as exc:
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

    if cmd == "/why" or cmd.startswith("/why "):
        from sable.app.builtins.why import handle_why
        return handle_why(cmd[len("/why"):])

    if cmd == "/route" or cmd.startswith("/route "):
        return _handle_route_builtin(cmd[len("/route"):], config)

    if cmd == "/task" or cmd.startswith("/task "):
        parts = cmd[len("/task"):].strip().split()
        return _handle_task_builtin(parts, config, db, turns=turns)

    if cmd == "/block" or cmd.startswith("/block "):
        from sable.app.builtins.block import handle_block
        return handle_block(cmd[len("/block"):].strip(), db, session_id)

    if cmd == "/alias" or cmd.startswith("/alias "):
        return _handle_alias_builtin(cmd[len("/alias"):].strip(), db)

    if cmd == "/audit" or cmd.startswith("/audit "):
        from sable.app.builtins.audit import handle_audit
        return handle_audit(cmd[len("/audit"):])

    if cmd == "/tools":
        from sable.tools import registry

        for role in ("orchestrator", "worker"):
            _out(f"{role}:")
            for t in registry.for_role(role):
                _out(f"  {t.signature():<40} {t.tier.value:<8} {t.description}")
        _out("policy files can raise a tool's tier: match `tool:<name>` in a [[rule]]")
        return True

    if cmd == "/inbox" or cmd.startswith("/inbox "):
        from sable.app.builtins.inbox import handle_inbox
        return handle_inbox(cmd[len("/inbox"):], db)

    if cmd == "/approve" or cmd.startswith("/approve "):
        return _handle_approve_builtin(cmd[len("/approve"):].strip(), db)

    if cmd == "/schedule" or cmd.startswith("/schedule "):
        from sable.app.builtins.schedule import handle_schedule
        return handle_schedule(cmd[len("/schedule"):].strip(), getattr(db, "_conn", None), config)

    if cmd == "/secret" or cmd.startswith("/secret "):
        from sable.app.builtins.secret import _handle_secret_builtin
        return _handle_secret_builtin(cmd[len("/secret"):].strip())

    if cmd == "/dash":
        return _handle_dash_builtin()

    if cmd == "/notify" or cmd.startswith("/notify "):
        from sable.app.builtins.notify import _handle_notify_builtin
        return _handle_notify_builtin(cmd[len("/notify"):])

    if cmd == "/breaker" or cmd.startswith("/breaker "):
        return _handle_breaker_builtin(cmd[len("/breaker"):].strip(), db)

    if cmd == "/corrections" or cmd.startswith("/corrections "):
        return _handle_corrections_builtin(cmd[len("/corrections"):].strip(), db)

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
