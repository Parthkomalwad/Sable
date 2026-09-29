"""`/inbox`: one list of everything waiting on you (Phase 5, E6).

Reads `sable.ui.state.inbox_all`: pending approvals (a daemon's queued step
included), open breaker trips, crystallised-skill drafts. Decisions go
through the same functions `/approve`, `/breaker` and `/skill` use:
`queue.decide_request`, `breaker.reset`, `SkillIndex.approve/reject`. A
waiting daemon run needs nothing more: its next tick sees the decided row.
"""
from __future__ import annotations

import time

from rich.console import Console
from rich.table import Table

from sable.ui import state
from sable.ui.console import out as _out

_USAGE = "usage: /inbox [show|approve|reject KEY]   (KEY as listed: a7, b2, or a skill name)"


def key(item) -> str:
    """A stable handle: deciding one item never changes another's key, so a
    number typed from an older listing cannot land on a different item."""
    return {"approval": f"a{item.id}", "breaker": f"b{item.id}", "runbook": f"r{item.id}"}.get(item.kind, item.text)


def _age(created_at: float) -> str:
    s = max(0, int(time.time() - created_at))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{s // size}{unit}"
    return f"{s}s"


def _list(items) -> None:
    if not items:
        _out("inbox empty: nothing is waiting on you")
        return
    table = Table(title="inbox")
    for col in ("key", "kind", "age", "source", "what"):
        table.add_column(col)
    for i in items:
        table.add_row(key(i), i.kind, _age(i.created_at), i.agent, i.text)
    Console(markup=False).print(table)   # a command's [brackets] are not style tags
    _out("/inbox show KEY, /inbox approve KEY, /inbox reject KEY")


def _decide(item, approve: bool, db) -> str:
    verb = "approved" if approve else "rejected"
    if item.kind == "approval":
        from sable.policy import queue

        if not queue.decide_request(db._conn, item.id, approve=approve):
            return f"#{item.id} was already decided"
        return f"{verb}: {item.text}"
    if item.kind == "breaker":
        if not approve:
            return "a breaker trip can only be reset: /inbox approve KEY"
        from sable.policy import breaker

        n = breaker.reset(db._conn)
        return f"breaker reset ({n} trip{'s' if n != 1 else ''} cleared)"
    if item.kind == "runbook":
        import os

        from sable.memory import runbooks

        return runbooks.decide(db._conn, item.id, approve, os.getcwd())
    from sable.skills.index import SkillIndex

    index = SkillIndex()
    ok = index.approve(item.text) if approve else index.reject(item.text)
    return f"skill {item.text} {verb}" if ok else f"skill {item.text} is gone"


def handle_inbox(argument: str, db) -> bool:
    """`/inbox`, `/inbox show N`, `/inbox approve N`, `/inbox reject N`. Always True."""
    parts = argument.split()
    items = state.inbox_all()
    if not parts:
        _list(items)
        return True
    if len(parts) != 2 or parts[0] not in ("show", "approve", "reject"):
        _out(_USAGE)
        return True
    item = next((i for i in items if key(i) == parts[1]), None)
    if item is None:
        _out(f"no item {parts[1]} in the inbox ({len(items)} waiting); /inbox to list")
        return True
    if parts[0] == "show":
        _out(f"{key(item)}  {item.kind}  from {item.agent}, {_age(item.created_at)} ago")
        _out(f"  {item.text}")
        _out(f"  {item.why}")
        return True
    if db is None and item.kind != "skill":
        _out("the inbox needs the session database, which is unavailable")
        return True
    _out(_decide(item, parts[0] == "approve", db))
    return True
