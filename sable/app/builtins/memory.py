"""The `/memory` builtin: show, compress and clear session context.

Extracted from `app/repl.py` in Phase 0.5 step 3. Pure move, no logic change.
"""
from __future__ import annotations

from sable.ui.console import out as _out


def _handle_memory_builtin(subcmd: str, session_id: str, turns: list[dict]) -> None:
    """Handle /memory subcommands: versions, revert <id>, show, clear."""
    from sable.memory.session import (
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
