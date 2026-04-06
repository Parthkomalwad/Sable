"""TaskManager — task lifecycle: spawn, pause, resume, kill, attach, inspect.

All operations go through SQLite and libtmux.
"""
from __future__ import annotations

import os
import shlex
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import libtmux


class TaskManager:
    def __init__(self, config, db) -> None:
        self._config = config
        self._db = db
        self._tasks_base = Path(config.tasks_base_dir).expanduser()
        self._server = libtmux.Server()

    def _session(self):
        import subprocess
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#{session_name}"],
            capture_output=True, text=True,
        )
        name = result.stdout.strip()
        try:
            for s in self._server.sessions:
                if s.session_name == name:
                    return s
        except Exception:
            pass
        return None

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _find_window(self, session, window_id: str):
        """Find a tmux window by ID — compatible with libtmux >=0.28."""
        try:
            for w in session.windows:
                if w.window_id == window_id:
                    return w
        except Exception:
            pass
        return None

    def spawn(self, name: str, goal: str, context: str = "",
              task_base_dir: str | None = None) -> None:
        """Spawn a new task agent in a dedicated tmux window.

        Args:
            name: Task name (used as tmux window name and directory).
            goal: Natural language goal for the agent.
            context: Optional orchestrator context summary to seed the agent's memory.
            task_base_dir: If set, use this as the parent dir instead of global tasks_base.
                           Sub-agent's workspace = task_base_dir/name/workspace/
                           shared_read_dir passed to Sandbox = task_base_dir/
        """
        if task_base_dir:
            task_dir = Path(task_base_dir) / name
        else:
            task_dir = self._tasks_base / name
        task_dir.mkdir(parents=True, exist_ok=True)

        # Write context to a handoff file so agent can load it on startup
        if context:
            handoff_path = task_dir / ".agentic" / "handoff.txt"
            handoff_path.parent.mkdir(parents=True, exist_ok=True)
            handoff_path.write_text(context)

        self._db._conn.execute(
            """INSERT OR REPLACE INTO tasks
               (name, goal, status, created_at)
               VALUES (?, ?, 'starting', ?)""",
            (name, goal, self._now()),
        )
        self._db._conn.commit()

        session = self._session()
        if session is None:
            raise RuntimeError("Not inside a tmux session")

        window = session.new_window(window_name=f"task:{name}", attach=True)
        window_id = window.window_id

        self._db._conn.execute(
            "UPDATE tasks SET tmux_window_id=? WHERE name=?",
            (window_id, name),
        )
        self._db._conn.commit()

        python_bin = sys.executable
        import os as _os
        project_root = str(Path(__file__).resolve().parents[2])
        existing_pp = _os.environ.get("PYTHONPATH", "")
        pythonpath = f"{project_root}:{existing_pp}" if existing_pp else project_root

        # Write goal to a file to avoid shell injection via send_keys
        goal_file = task_dir / ".agentic" / "goal.txt"
        goal_file.parent.mkdir(parents=True, exist_ok=True)
        goal_file.write_text(goal, encoding="utf-8")

        # Pass shared_read_dir if spawned under a task_base_dir
        shared_arg = ""
        if task_base_dir:
            shared_arg = f" --shared-read-dir {shlex.quote(str(task_base_dir))}"

        window.active_pane.send_keys(
            f"PYTHONPATH={pythonpath} {python_bin} -m shell.tasks.agent"
            f" --task {shlex.quote(name)} --goal-file {shlex.quote(str(goal_file))}{shared_arg}",
            enter=True,
        )

    def pause(self, name: str) -> None:
        row = self._db._conn.execute(
            "SELECT pid FROM tasks WHERE name=?", (name,)
        ).fetchone()
        if row and row[0]:
            try:
                os.killpg(os.getpgid(row[0]), signal.SIGTSTP)
            except (ProcessLookupError, PermissionError):
                pass
        self._db._conn.execute(
            "UPDATE tasks SET status='paused' WHERE name=?", (name,)
        )
        self._db._conn.commit()

    def resume(self, name: str) -> None:
        row = self._db._conn.execute(
            "SELECT pid FROM tasks WHERE name=?", (name,)
        ).fetchone()
        if row and row[0]:
            try:
                os.killpg(os.getpgid(row[0]), signal.SIGCONT)
            except (ProcessLookupError, PermissionError):
                pass
        self._db._conn.execute(
            "UPDATE tasks SET status='running' WHERE name=?", (name,)
        )
        self._db._conn.commit()

    def kill(self, name: str) -> None:
        session = self._session()
        if session:
            row = self._db._conn.execute(
                "SELECT tmux_window_id FROM tasks WHERE name=?", (name,)
            ).fetchone()
            if row and row[0]:
                window = self._find_window(session, row[0])
                if window:
                    window.kill()
        self._db._conn.execute(
            "UPDATE tasks SET status='completed', ended_at=? WHERE name=?",
            (self._now(), name),
        )
        self._db._conn.commit()

    def attach(self, name: str) -> None:
        session = self._session()
        if not session:
            return
        row = self._db._conn.execute(
            "SELECT tmux_window_id FROM tasks WHERE name=?", (name,)
        ).fetchone()
        if row and row[0]:
            window = self._find_window(session, row[0])
            if window:
                window.select()

    def back(self) -> None:
        """Switch back to the main agentic-shell window (window index 0)."""
        session = self._session()
        if not session:
            return
        try:
            session.windows[0].select()
        except (IndexError, Exception):
            pass

    def inspect(self, name: str) -> None:
        task_dir = self._tasks_base / name
        session = self._session()
        if not session:
            return
        window = session.new_window(
            window_name=f"inspect:{name}",
            start_directory=str(task_dir),
            attach=True,
        )
        window.active_pane.send_keys("bash", enter=True)

    def list_tasks(self) -> list[dict]:
        rows = self._db._conn.execute(
            "SELECT name, goal, status, step_count, created_at, ended_at FROM tasks ORDER BY id DESC"
        ).fetchall()
        return [
            {"name": r[0], "goal": r[1], "status": r[2],
             "step_count": r[3], "created_at": r[4], "ended_at": r[5]}
            for r in rows
        ]

    def stats(self, name: str) -> dict:
        row = self._db._conn.execute(
            """SELECT SUM(prompt_tokens), SUM(completion_tokens), SUM(cost_usd), COUNT(*)
               FROM task_events WHERE task_name=?""",
            (name,),
        ).fetchone()
        return {
            "task": name,
            "prompt_tokens": row[0] or 0,
            "completion_tokens": row[1] or 0,
            "cost_usd": row[2] or 0.0,
            "turns": row[3] or 0,
        }

    def history(self, name: str) -> list[dict]:
        rows = self._db._conn.execute(
            """SELECT timestamp, prompt_tokens, completion_tokens, cost_usd, model
               FROM task_events WHERE task_name=? ORDER BY id""",
            (name,),
        ).fetchall()
        return [
            {"timestamp": r[0], "prompt_tokens": r[1],
             "completion_tokens": r[2], "cost_usd": r[3], "model": r[4]}
            for r in rows
        ]

    def checkpoint(self, name: str) -> int:
        from shell.tasks.memory import TaskMemory
        mem = TaskMemory(name, str(self._tasks_base), db=self._db)
        return mem.save_snapshot()

    def revert(self, name: str, version: int) -> dict:
        from shell.tasks.memory import TaskMemory
        mem = TaskMemory(name, str(self._tasks_base), db=self._db)
        return mem.load_snapshot(version)
