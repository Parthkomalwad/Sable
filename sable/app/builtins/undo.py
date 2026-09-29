"""`/undo`, `/undo <id>`, `/undo list`, `/task diff|undo <name>` (A8 K6).

Undo always shows the diff first and asks; nothing is restored unconfirmed.
"""
from __future__ import annotations

import os
import time

from sable.core import snapshots
from sable.ui.console import out as _out


def handle_undo(arg: str) -> bool:
    arg = arg.strip()
    if arg == "list":
        rows = snapshots.list()
        if not rows:
            _out("no snapshots yet")
        for m in rows:
            age = int(time.time() - m.get("time", 0))
            who = m.get("agent") or "?"
            _out(f"  s{m['id']}  {age // 60}m ago  {who}  {str(m.get('label', ''))}")
        return True
    if arg:
        try:
            sid = int(arg.lstrip("s"))
        except ValueError:
            _out("usage: /undo [list | <id>]")
            return True
    else:
        m = snapshots.latest(agent="orchestrator", session=str(os.getpid()))
        if m is None:
            _out("nothing to undo in this session (/undo list shows every snapshot)")
            return True
        sid = m["id"]
    _undo(sid)
    return True


def _latest_for(name: str) -> dict | None:
    m = snapshots.latest(agent=name)
    if m is None:
        _out(f"no snapshot for task '{name}'")
    return m


def task_diff(name: str) -> None:
    m = _latest_for(name)
    if m is not None:
        _show_diff(m["id"])


def task_undo(name: str) -> None:
    m = _latest_for(name)
    if m is not None:
        _undo(m["id"])


def _show_diff(sid: int) -> bool:
    try:
        text = snapshots.diff(sid)
    except (snapshots.SnapshotError, OSError) as exc:
        _out(f"[undo] {str(exc)}")
        return False
    _out(f"s{sid}: {snapshots.manifest(sid).get('label', '')}")
    _out(text if text else "  (no changes since the snapshot)")
    return True


def _undo(sid: int) -> None:
    if not _show_diff(sid):
        return
    try:
        answer = input(f"  restore s{sid}? [y/N] ").strip().lower()
    except EOFError:
        answer = ""
    if answer != "y":
        _out("  not restored")
        return
    try:
        for line in snapshots.restore(sid):
            _out(f"  {line}")
    except (snapshots.SnapshotError, OSError) as exc:
        _out(f"[undo] {str(exc)}")
