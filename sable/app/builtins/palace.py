"""`/palace`, `/remember`, `/forget`: the memory palace from the shell (Phase 7 Task 2, C5 C2).

Fact text and sources may have come from a model reading a machine, so they
are untrusted: printed with markup off and control characters stripped.
"""
from __future__ import annotations

import datetime
import importlib
import re

from rich.console import Console
from rich.table import Table

from sable.memory import palace
from sable.ui.console import out as _out

_PALACE_USAGE = "usage: /palace [<room> | find <text> | why <id> | consolidate]"
_REMEMBER_USAGE = "usage: /remember <text> [--room user|server|incidents|repos/<name>]"
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]+")


def _clean(text, cap: int = 0) -> str:
    s = _CTRL.sub(" ", str(text or "")).strip()
    return s[:cap - 3] + "..." if cap and len(s) > cap else s


def _age(created: str) -> str:
    try:
        then = datetime.datetime.fromisoformat(created)
    except (TypeError, ValueError):
        return "?"
    if then.tzinfo is None:
        then = then.replace(tzinfo=datetime.timezone.utc)
    s = max(0, int((datetime.datetime.now(datetime.timezone.utc) - then).total_seconds()))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{s // size}{unit}"
    return f"{s}s"


def _table(facts, title: str) -> None:
    table = Table(title=_clean(title))
    for col in ("id", "room", "tier", "", "age", "text"):
        table.add_column(col)
    for f in facts:
        table.add_row(f.id, _clean(f.room), f.tier, "untrusted" if f.untrusted else "",
                      _age(f.created), _clean(f.text, 80))
    Console(markup=False, highlight=False).print(table)


def _source(src) -> str:
    if not isinstance(src, dict):
        return _clean(src, 300)
    if src.get("by") == "you":
        return "you (/remember)"
    parts = [f"{k}: {_clean(src[k], 200)}" for k in ("session", "goal") if src.get(k)]
    cmds = src.get("commands") or []
    if isinstance(cmds, str):
        cmds = [cmds]
    parts += [f"$ {_clean(c, 200)}" for c in cmds]
    rest = {k: v for k, v in src.items() if k not in ("session", "goal", "commands")}
    parts += [f"{_clean(k, 40)}: {_clean(v, 200)}" for k, v in rest.items()]
    return "; ".join(parts) or "unknown"


def _why(fact_id: str) -> None:
    f = palace.why(fact_id)
    if f is None:
        _out(f"no fact {_clean(fact_id, 40)!r}")
        return
    _out(f"{f.id}  ({_clean(f.room)}, {f.tier}{', untrusted' if f.untrusted else ''})")
    _out(f"  {_clean(f.text)}")
    _out(f"  created: {_clean(f.created) or '?'}   valid until: {_clean(f.valid_to) or 'no expiry'}")
    _out("  sources:")
    for s in f.sources or ["none recorded"]:
        _out(f"    - {_source(s)}")


def _consolidate() -> None:
    try:
        mod = importlib.import_module("sable.memory.consolidate")
    except ImportError:
        _out("consolidation arrives with Task 3")
        return
    _out(_clean(mod.run()))


def handle_palace(argument: str) -> bool:
    arg = argument.strip()
    sub, _, rest = arg.partition(" ")
    rest = rest.strip()
    if not arg:
        counts = palace.rooms()
        if not counts:
            _out("the palace is empty: /remember <text> adds a fact, and agents "
                 "save what they learn while finishing a goal")
            return True
        for room, n in counts.items():
            _out(f"  {_clean(room):<24} {n} fact{'s' if n != 1 else ''}")
        _out("/palace <room> to list, /palace find <text>, /palace why <id>")
    elif sub == "find":
        if not rest:
            _out(_PALACE_USAGE)
            return True
        hits = palace.recall(rest)
        if hits:
            _table(hits, f"palace: {rest}")
        else:
            _out("nothing in the palace matches that")
    elif sub == "why":
        if rest:
            _why(rest)
        else:
            _out(_PALACE_USAGE)
    elif sub == "consolidate" and not rest:
        _consolidate()
    else:
        try:
            palace.check_room(arg)
        except ValueError as e:
            _out(str(e))
            _out(_PALACE_USAGE)
            return True
        facts = palace.all_facts(arg)
        if facts:
            _table(facts, f"palace: {arg}")
        else:
            _out(f"no facts in {arg}")
    return True


def handle_remember(argument: str) -> bool:
    text, room = argument.strip(), "user"
    m = re.search(r"(?:^|\s)--room(?:=|\s+)(\S+)\s*$", text)
    if m:
        room, text = m.group(1), text[:m.start()].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    if not text or "--room" in text.split():
        _out(_REMEMBER_USAGE)
        return True
    try:
        fid = palace.remember(text, room, {"by": "you"}, tier="semantic")
    except ValueError as e:
        _out(str(e))
        return True
    _out(f"remembered {fid} in {room}")
    return True


def handle_forget(argument: str) -> bool:
    fid = argument.strip()
    if not fid:
        _out("usage: /forget <id>")
        return True
    f = palace.why(fid)
    if f is None or not palace.forget(fid):
        _out(f"no fact {_clean(fid, 40)!r}")
        return True
    _out(f"forgot {f.id} ({_clean(f.room)}): {_clean(f.text, 120)}")
    return True
