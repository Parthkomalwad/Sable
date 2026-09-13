"""The `/task` builtin: spawn, list, attach, pause and inspect sub-agents.

Extracted from `app/repl.py` in Phase 0.5 step 3. Pure move, no logic change.
"""
from __future__ import annotations

from sable.ui.console import out as _out


def _build_spawn_context(turns: list[dict], goal: str) -> str:
    """Build a compressed context summary to hand off to a spawned agent.

    Takes last 8 turns from the orchestrator session, compresses them,
    and prepends a header explaining why this agent was spawned.
    """
    if not turns:
        return ""
    recent = turns[-8:]
    try:
        from sable.memory.compressor import compress
        summary = compress(recent)
    except Exception:
        summary = "\n".join(
            f"{t.get('role','user')}: {str(t.get('content',''))[:200]}"
            for t in recent
        )
    return (
        f"You were spawned by the orchestrator to: {goal}\n\n"
        f"Recent orchestrator session history (compressed):\n{summary}"
    )


def _handle_task_builtin(parts: list[str], config, db, turns: list[dict] | None = None) -> bool:
    """Handle /task subcommands. Return True if handled."""
    from sable.agents.manager import TaskManager
    manager = TaskManager(config=config, db=db)

    if not parts:
        _out("usage: /task <new|list|attach|back|clean|pause|resume|kill|inspect|stats|history|checkpoint|revert>")
        return True

    sub = parts[0]

    if sub == "back":
        manager.back()
        return True

    if sub == "clean":
        import shutil as _shutil
        from pathlib import Path as _P
        tasks_base = _P(getattr(config, "tasks_base_dir", "~/tasks")).expanduser()
        if not tasks_base.exists():
            _out("no tasks folder found")
            return True
        folders = [f for f in tasks_base.iterdir() if f.is_dir()]
        if not folders:
            _out("no task folders to delete")
            return True
        _out(f"  will delete {len(folders)} task folder(s):")
        for f in folders:
            _out(f"    {f.name}/")
        try:
            answer = input("  type YES to confirm: ").strip()
        except (EOFError, KeyboardInterrupt):
            return True
        if answer != "YES":
            _out("cancelled")
            return True
        for f in folders:
            try:
                _shutil.rmtree(f)
                _out(f"  deleted {f.name}/")
            except Exception as exc:
                _out(f"  failed to delete {f.name}/: {exc}")
        # Also clear tasks from DB
        try:
            db._conn.execute("DELETE FROM tasks")
            db._conn.commit()
        except Exception:
            pass
        _out("done all task folders deleted")
        return True

    if sub == "list":
        tasks = manager.list_tasks()
        if not tasks:
            _out("no tasks")
        for t in tasks:
            _out(f"  [{t['status']}] {t['name']} {t['goal'][:60]}")
        return True

    if sub == "new" and len(parts) >= 3:
        name = parts[1]
        goal = " ".join(parts[2:])
        try:
            # Build context handoff from recent orchestrator turns
            context = _build_spawn_context(turns=turns or [], goal=goal)
            manager.spawn(name, goal, context=context)
            _out(f"task '{name}' spawned")
            if context:
                _out(f"  context: {len(context)} chars of session history handed off")
        except Exception as exc:
            _out(f"[error] {exc}")
        return True

    if len(parts) >= 2:
        name = parts[1]
        if sub == "attach":
            manager.attach(name)
        elif sub == "pause":
            manager.pause(name)
            _out(f"task '{name}' paused")
        elif sub == "resume":
            manager.resume(name)
            _out(f"task '{name}' resumed")
        elif sub in ("kill", "done"):
            manager.kill(name)
            _out(f"task '{name}' killed")
        elif sub == "inspect":
            manager.inspect(name)
        elif sub == "stats":
            s = manager.stats(name)
            _out(f"  tokens: {s['prompt_tokens']}p / {s['completion_tokens']}c  cost: ${s['cost_usd']:.4f}")
        elif sub == "history":
            for row in manager.history(name):
                _out(f"  {row['timestamp']}  {row['model']}  ${row['cost_usd']:.4f}")
        elif sub == "checkpoint":
            v = manager.checkpoint(name)
            _out(f"checkpoint v{v} saved")
        elif sub == "revert" and len(parts) >= 3:
            version = int(parts[2].lstrip("v"))
            manager.revert(name, version)
            _out(f"context reverted to v{version}")
        else:
            _out(f"unknown /task subcommand: {sub}")
        return True

    _out(f"usage: /task {sub} <name>")
    return True
