"""`/watch add|list|rm`: watchers the daemon checks (Phase 5, E3).

A `run` or `approve` watcher carries a plan; it is shown with each step's
policy tier and confirmed before it is stored, as `/schedule` does.
"""
from __future__ import annotations

import os
import shlex
import sys

from sable.ui.console import out as _out

DIM = "\033[2;37m"
RESET = "\033[0m"
USAGE = ("usage: /watch add disk <path> <pct> | file <path> | log <path> <regex> | http <url> <status>\n"
         "         [--tier notify|run|approve] [--do \"<cmd>; <cmd>\"]\n"
         "       /watch list | /watch rm N")


def _confirm(steps: list[str]) -> bool:
    from sable.policy.engine import decide
    for i, step in enumerate(steps, 1):
        _out(f"  {i}. [{decide(step).tier.value}] {step}")
    sys.stdout.write(f"  {DIM}↵ approve   q cancel  ›{RESET} ")
    sys.stdout.flush()
    try:
        return input("").strip().lower() != "q"
    except (EOFError, KeyboardInterrupt):
        return False


def _add(args: list[str], conn) -> None:
    from sable.daemon import watchers
    tier, steps = "notify", []
    try:
        if "--tier" in args:
            i = args.index("--tier")
            tier = args[i + 1]
            del args[i:i + 2]
        if "--do" in args:
            i = args.index("--do")
            steps = [s.strip() for s in args[i + 1].split(";") if s.strip()]
            del args[i:i + 2]
    except IndexError:
        _out(USAGE)
        return
    if not args or len(args) < (2 if args[0] == "file" else 3):
        _out(USAGE)
        return
    kind, target, arg = args[0], args[1], " ".join(args[2:])
    if kind != "http":
        target = os.path.abspath(os.path.expanduser(target))
    try:
        watchers.validate(kind, target, arg)
        if tier != "notify" and steps and not _confirm(steps):
            _out("cancelled")
            return
        wid = watchers.add(conn, kind, target, arg, tier=tier, steps=steps)
    except ValueError as exc:
        _out(f"watch: {exc}")
        return
    _out(f"watcher #{wid} added ({tier}); the daemon checks it every 30 s")


def handle_watch(argument: str, db) -> bool:
    """Always True."""
    from sable.daemon import watchers
    if db is None:
        _out("watchers need the session database, which is unavailable")
        return True
    try:
        args = shlex.split(argument)
    except ValueError:
        _out(USAGE)
        return True
    conn = db._conn
    if args and args[0] == "add":
        _add(args[1:], conn)
    elif args in ([], ["list"]):
        rows = watchers.list_all(conn)
        if not rows:
            _out("no watchers yet")
        for w in rows:
            plan = f"  do: {'; '.join(w['steps'])}" if w["steps"] else ""
            _out(f"  #{w['id']}  {w['kind']} {w['target']} {w['arg']}  [{w['tier']}]"
                 f"  state: {w['last_state'] or 'unchecked'}{plan}")
    elif len(args) == 2 and args[0] == "rm" and args[1].isdigit():
        _out(f"removed #{args[1]}" if watchers.remove(conn, int(args[1])) else f"no watcher #{args[1]}")
    else:
        _out(USAGE)
    return True
