"""TaskAgent — autonomous goal-directed REPL for background task execution.

Entry point: python3 -m shell.tasks.agent --task <name> --goal "<text>"

Per-turn sequence:
1. Build context (pinned goal + compressed history + matched skills)
2. backend.complete()
3. safety.py blocklist check
4. sandbox.wrap_command() + PtyProcessUnicode
5. Update tasks row
6. Write task_events row
7. Compress if token threshold exceeded
8. Check for {"done": true} in LLM response
9. Drain non-blocking guidance queue (stdin daemon thread)
"""
from __future__ import annotations

import json
import logging
import os
import queue
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from ptyprocess import PtyProcessUnicode

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_TEMPLATE = """You are an autonomous task agent. Complete the goal step by step.
You are running inside a sandboxed workspace folder: {workspace}
All your commands run with this as the current directory (CWD).
You have full read/write access inside this folder. You can read (but NOT write) files outside it.

Rules:
- Always use relative paths. Never cd outside the workspace.
- Use `docker compose` (NOT `docker-compose` — v1 is not installed).
- npm/yarn local installs are allowed. Global installs (-g/--global) are blocked.
- apt/dpkg are blocked. Do not try to install system packages.
- If a command fails, read the error and adapt — do not repeat the same command.
- Before creating files, check if they exist first with ls or cat.
- NEVER run interactive commands that wait for user input. Always use non-interactive flags:
  - `npm create vite@latest myapp -- --template react-ts` then `echo y` pipe or use `yes | npm create ...`
  - prefer `npm init -y` over `npm init`
  - use `--yes` / `-y` / `--no-interaction` flags wherever available
  - for vite: `echo y | npm create vite@latest myapp -- --template react-ts`

For each turn, respond with JSON only — no markdown, no extra text:
{{
  "command": "<bash command to run, or empty string if done>",
  "explanation": "<one sentence: what this does and why>",
  "done": false
}}
When the goal is fully achieved, set "done": true and leave "command" empty.
"""


class TaskAgent:
    def __init__(self, task_name: str, goal: str, config, db_path: str) -> None:
        from shell.tasks.memory import TaskMemory
        from shell.tasks.sandbox import Sandbox
        from shell.tasks.skills import TaskSkillLoader

        self._name = task_name
        self._goal = goal
        self._config = config
        self._db_path = db_path

        tasks_base = str(Path(config.tasks_base_dir).expanduser())
        task_dir = os.path.join(tasks_base, task_name)
        # workspace: agent's private R/W sandbox — all commands run from here
        workspace = os.path.join(task_dir, "workspace")
        os.makedirs(workspace, exist_ok=True)
        self._workspace = workspace

        self._memory = TaskMemory(task_name, tasks_base, db=self._open_db())
        self._memory.set_goal(goal)

        # Load orchestrator context handoff if present
        handoff_path = Path(task_dir) / ".agentic" / "handoff.txt"
        if handoff_path.exists():
            try:
                handoff = handoff_path.read_text().strip()
                if handoff:
                    self._memory.add_turns([{
                        "role": "system",
                        "content": f"[orchestrator context]\n{handoff}",
                    }])
                    handoff_path.unlink()  # consume once
            except Exception:
                pass

        self._sandbox = Sandbox(task_dir=workspace)
        self._skill_loader = TaskSkillLoader(task_name, tasks_base)
        self._guidance_q: queue.Queue = queue.Queue()
        self._running = True

    def _open_db(self):
        class _DB:
            def __init__(self, path):
                self._conn = sqlite3.connect(path, check_same_thread=False)
                self._conn.execute("PRAGMA journal_mode=WAL")
        return _DB(self._db_path)

    def _stdin_reader(self) -> None:
        for line in sys.stdin:
            self._guidance_q.put(line.rstrip())

    def _drain_guidance(self) -> str:
        lines = []
        while True:
            try:
                lines.append(self._guidance_q.get_nowait())
            except queue.Empty:
                break
        return "\n".join(lines)

    def _db_conn(self):
        return sqlite3.connect(self._db_path, check_same_thread=False)

    def _update_task_status(self, status: str, last_output: str = "") -> None:
        conn = self._db_conn()
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "UPDATE tasks SET status=?, last_output=?, step_count=step_count+1 WHERE name=?",
            (status, last_output[:500], self._name),
        )
        conn.commit()
        conn.close()

    def _write_task_event(self, prompt_tokens: int, completion_tokens: int,
                          cost_usd: float, model: str) -> None:
        conn = self._db_conn()
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """INSERT INTO task_events
               (task_name, timestamp, prompt_tokens, completion_tokens, cost_usd, model)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (self._name, datetime.now(timezone.utc).isoformat(),
             prompt_tokens, completion_tokens, cost_usd, model),
        )
        conn.commit()
        conn.close()

    def _call_llm(self, messages: list[dict]):
        from shell.loop import _build_backend
        import asyncio
        backend = _build_backend(self._config)
        print("[agent] calling LLM...", flush=True)
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(workspace=self._workspace)
            result = loop.run_until_complete(
                asyncio.wait_for(backend.complete(messages, system_prompt), timeout=60.0)
            )
            # Drain pending tasks before closing to avoid "Task destroyed" warnings
            pending = asyncio.all_tasks(loop)
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            return result
        except asyncio.TimeoutError:
            raise RuntimeError("LLM call timed out after 60s")

    def _parse_response(self, response) -> dict:
        # LLMResponse now carries a done field populated by the backend from the parsed JSON.
        if hasattr(response, "command") and hasattr(response, "explanation"):
            return {
                "command": response.command or "",
                "explanation": response.explanation or "",
                "done": bool(getattr(response, "done", False)),
            }
        # Fallback: raw string response
        raw = str(response).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        try:
            parsed = json.loads(raw)
            return {
                "command": parsed.get("command", ""),
                "explanation": parsed.get("explanation", ""),
                "done": bool(parsed.get("done", False)),
            }
        except json.JSONDecodeError:
            return {"command": "", "explanation": raw, "done": False}

    def _run_command(self, command: str) -> str:
        import tempfile
        wrapped = self._sandbox.wrap_command(command)
        # Write to a temp script file so multi-line guard scripts work correctly
        try:
            fd, script_path = tempfile.mkstemp(suffix=".sh", prefix="agent_")
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(wrapped)
                proc = PtyProcessUnicode.spawn(
                    ["/bin/bash", script_path],
                    cwd=self._workspace,
                )
                output_parts = []
                while True:
                    try:
                        chunk = proc.read(1024)
                        output_parts.append(chunk)
                    except EOFError:
                        break
                proc.wait()
                return "".join(output_parts)
            finally:
                try:
                    os.unlink(script_path)
                except OSError:
                    pass
        except Exception as exc:
            return f"[error: {exc}]"

    def run(self) -> None:
        t = threading.Thread(target=self._stdin_reader, daemon=True)
        t.start()

        print(f"[agent] starting task '{self._name}'", flush=True)
        print(f"[agent] goal: {self._goal}", flush=True)
        print(f"[agent] workspace: {self._workspace}", flush=True)
        print(f"[agent] sandbox: {'bwrap (kernel namespace)' if self._sandbox.use_bwrap else 'bash-wrapper (writes blocked outside workspace)'}", flush=True)
        self._update_task_status("running")

        while self._running:
            guidance = self._drain_guidance()
            skills = self._skill_loader.load_relevant(self._goal)
            messages = self._memory.build_context()

            if guidance:
                messages.append({"role": "user", "content": f"[guidance] {guidance}"})

            if skills:
                skill_text = "\n\n".join(
                    f"# skill: {s['name']}\n{s['content']}" for s in skills
                    if not self._memory.is_skill_seen(s["hash"])
                )
                if skill_text:
                    messages.insert(1, {"role": "user", "content": skill_text})
                for s in skills:
                    self._memory.register_skill_hash(s["name"], s["content"])

            try:
                response = self._call_llm(messages)
            except Exception as exc:
                logger.error("LLM call failed: %s", exc)
                self._update_task_status("lost")
                break

            parsed = self._parse_response(response)
            command = parsed.get("command", "")

            if command:
                from shell.safety import is_destructive
                if is_destructive(command):
                    logger.warning("Blocked destructive command: %s", command)
                    self._memory.add_turns([
                        {"role": "assistant", "content": f"[blocked] {command}"},
                        {"role": "user", "content": "That command was blocked as destructive. Try a safer approach."},
                    ])
                    continue

            output = ""
            if command:
                print(f"[agent] running: {command}", flush=True)
                output = self._run_command(command)
                print(f"[agent] output: {output[:200]}", flush=True)

            self._update_task_status("running", output)
            self._write_live_status(command, parsed.get("explanation", ""))
            self._write_task_event(
                prompt_tokens=getattr(response, "prompt_tokens", 0),
                completion_tokens=getattr(response, "completion_tokens", 0),
                cost_usd=getattr(response, "cost_usd", 0.0),
                model=getattr(response, "model", self._config.model),
            )

            self._memory.add_turns([
                {"role": "assistant", "content": json.dumps(parsed)},
                {"role": "user", "content": output or "(no output)"},
            ])
            self._memory.save_snapshot()

            if parsed.get("done"):
                self._update_task_status("completed")
                self._write_result_summary()
                print(f"\n[task '{self._name}'] Goal achieved. Window kept open for review.")
                break

        self._running = False

    def _write_live_status(self, command: str, explanation: str) -> None:
        """Write a live status file so orchestrator knows what agent is doing right now."""
        try:
            import subprocess
            tree = subprocess.run(
                ["find", ".", "-maxdepth", "3", "-not", "-path", "*/.agentic/*", "-not", "-name", ".*"],
                capture_output=True, text=True, cwd=self._workspace, timeout=3,
            ).stdout.strip()
            status_path = Path(self._workspace).parent / ".agentic" / "status.md"
            status_path.parent.mkdir(parents=True, exist_ok=True)
            step = getattr(self, "_step_count", 0)
            status_path.write_text(
                f"# Agent: {self._name} (running — step {step})\n"
                f"**Goal**: {self._goal}\n"
                f"**Workspace**: {self._workspace}\n"
                f"**Last action**: `{command}` — {explanation}\n\n"
                f"## Current workspace files\n```\n{tree or '(empty)'}\n```\n"
            )
        except Exception:
            pass

    def _write_result_summary(self) -> None:
        """Write a result.md summary so the orchestrator knows what was done."""
        try:
            # Collect all assistant turns as summary
            turns = self._memory._turns
            steps = [
                t["content"] for t in turns
                if t.get("role") == "assistant"
            ]
            # Run ls in workspace to capture final file tree
            import subprocess
            tree = subprocess.run(
                ["find", ".", "-not", "-path", "*/.agentic/*", "-not", "-name", ".*"],
                capture_output=True, text=True, cwd=self._workspace, timeout=5,
            ).stdout.strip()

            summary = (
                f"# Task: {self._name}\n"
                f"**Goal**: {self._goal}\n"
                f"**Workspace**: {self._workspace}\n\n"
                f"## Files created\n```\n{tree or '(none)'}\n```\n\n"
                f"## Steps taken ({len(steps)})\n"
            )
            for i, s in enumerate(steps[-10:], 1):
                try:
                    p = json.loads(s)
                    summary += f"{i}. `{p.get('command','')}`  — {p.get('explanation','')}\n"
                except Exception:
                    summary += f"{i}. {s[:120]}\n"

            result_path = Path(self._workspace).parent / ".agentic" / "result.md"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(summary)
            print(f"[agent] result written to {result_path}", flush=True)
        except Exception as exc:
            print(f"[agent] could not write result: {exc}", flush=True)


if __name__ == "__main__":
    import argparse
    import json as _json
    from pathlib import Path as _Path

    parser = argparse.ArgumentParser(description="TaskAgent runner")
    parser.add_argument("--task", required=True, help="Task name")
    parser.add_argument("--goal", required=True, help="Goal text")
    args = parser.parse_args()

    from shell.telemetry.db import DB_PATH
    config_path = _Path.home() / ".config" / "agentic-shell" / "config.json"

    try:
        raw = _json.loads(config_path.read_text())
        from shell.config.schema import ShellConfig
        config = ShellConfig.from_dict(raw)
    except Exception:
        from shell.config.schema import ShellConfig
        config = ShellConfig.defaults()

    agent = TaskAgent(
        task_name=args.task,
        goal=args.goal,
        config=config,
        db_path=str(DB_PATH),
    )
    agent.run()
