"""TaskAgent autonomous goal-directed REPL for background task execution.

Entry point: python3 -m sable.agents.worker --task <name> --goal "<text>"

Per-turn sequence:
1. Build context (pinned goal + compressed history + matched skills)
2. backend.complete(), via agents/runtime.call_llm
3. safety.py blocklist check
4. agents/runtime.run_command, wrapped by sandbox.wrap_command
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
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx

from sable.agents import runtime
from sable.core.events.types import EventKind

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_TEMPLATE = """You are an autonomous task agent running inside a sandboxed workspace: {workspace}
All commands run with that as CWD. You have full R/W access inside it; read-only outside.

Principles:
1. NON-INTERACTIVE every command must run without user input. Use -y/--yes/--no-interaction flags. Pipe `yes |` if needed. Never run anything that waits for a keypress.
2. DETACHED LONG-RUNNING PROCESSES background services (docker, dev servers) must be started detached (e.g. `docker compose up -d --build`). Check their output separately with logs commands.
3. USE CURRENT VERSIONS pick tool versions that match the runtime. Check Node/Python version first if unsure; use compatible package versions.
4. ADAPT ON FAILURE read the error, understand the root cause, try a different approach. Never repeat a failed command unchanged.
5. CHECK BEFORE CREATE verify files/dirs exist before creating them (ls, cat). Don't overwrite work.
6. RELATIVE PATHS ONLY never cd outside the workspace.
7. SANDBOX LIMITS `docker compose` (v2) only, no apt/dpkg, no global npm/yarn installs.

For each turn respond with JSON only no markdown, no extra text:
{{
  "command": "<bash command, or empty string if done>",
  "explanation": "<one sentence: what and why>",
  "done": false
}}
When the goal is fully achieved, set "done": true and leave "command" empty.
"""


class TaskAgent:
    def __init__(self, task_name: str, goal: str, config, db_path: str,
                 shared_read_dir: str | None = None) -> None:
        from sable.memory.task import TaskMemory
        from sable.agents.sandbox import Sandbox
        from sable.skills.loader import TaskSkillLoader

        self._name = task_name
        self._goal = goal
        self._config = config
        self._db_path = db_path

        tasks_base = str(Path(config.tasks_base_dir).expanduser())
        task_dir = os.path.join(tasks_base, task_name)
        # workspace: agent's private R/W sandbox all commands run from here
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
            except (OSError, UnicodeDecodeError):
                # Starting without the orchestrator's context is worse than
                # starting with it, but far better than not starting.
                pass

        # Extract orchestrator's original CWD from goal prefix "Working directory: <path>"
        extra_write_dirs: list[str] = []
        if goal.startswith("Working directory:"):
            first_line = goal.split("\n", 1)[0]
            cwd_path = first_line.removeprefix("Working directory:").strip()
            if cwd_path and os.path.isdir(cwd_path):
                extra_write_dirs.append(cwd_path)

        self._sandbox = Sandbox(task_dir=workspace, shared_read_dir=shared_read_dir,
                                extra_write_dirs=extra_write_dirs or None)
        self._skill_loader = TaskSkillLoader(task_name, tasks_base)
        self._guidance_q: queue.Queue = queue.Queue()
        self._running = True

        # The bus is how the orchestrator learns what this worker is doing.
        # status.md and result.md are still written below, but as
        # human-readable mirrors: nothing reads them back any more.
        from sable.core.events.bus import EventBus

        self._bus = EventBus(db_path=self._db_path)

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

    def _publish(self, kind: str, **payload) -> None:
        """Announce something on the bus. Never raises, by the bus's contract."""
        self._bus.publish(self._name, kind, payload)

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
        from sable.llm.registry import build_backend

        backend = build_backend(self._config, role="worker", mock_mode="worker")
        print("[agent] calling LLM...", flush=True)
        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(workspace=self._workspace)
        return runtime.call_llm(backend, messages, system_prompt)

    def _record_turn(self, step: int, messages: list[dict], parsed: dict, response) -> None:
        """Store what the model saw and answered, for `/task <name> replay` (I9).

        Redaction happens inside the replay log. Never raises, by that module's
        contract: a worker must not die because its own audit trail failed.
        """
        from sable.core.events.replay import record_turn

        record_turn(
            self._db_path,
            agent=self._name,
            role="worker",
            turn=step,
            system_prompt=_SYSTEM_PROMPT_TEMPLATE.format(workspace=self._workspace),
            messages=messages,
            response=json.dumps(parsed),
            model=getattr(response, "model", None) or self._config.model_for("worker"),
            prompt_tokens=getattr(response, "prompt_tokens", 0),
            completion_tokens=getattr(response, "completion_tokens", 0),
            cost_usd=getattr(response, "cost_usd", 0.0),
        )

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

    def _run_command(self, command: str, timeout: int = runtime.COMMAND_TIMEOUT) -> str:
        """Run one command inside the sandbox and return its output.

        The pty loop is `runtime.run_command`; what is specific to the worker
        is the `Sandbox` wrapper and the line it prints when time runs out.
        """
        def announce_timeout(limit: int) -> None:
            print(f"[agent] command timed out after {limit}s killing", flush=True)

        return runtime.run_command(
            command,
            cwd=self._workspace,
            timeout=timeout,
            wrap=self._sandbox.wrap_command,
            on_timeout=announce_timeout,
            prefix="agent_",
        )

    def run(self) -> None:
        t = threading.Thread(target=self._stdin_reader, daemon=True)
        t.start()

        print(f"[agent] starting task '{self._name}'", flush=True)
        print(f"[agent] goal: {self._goal}", flush=True)
        print(f"[agent] workspace: {self._workspace}", flush=True)
        print(f"[agent] sandbox: {'bwrap (kernel namespace)' if self._sandbox.use_bwrap else 'bash-wrapper (writes blocked outside workspace)'}", flush=True)
        self._update_task_status("running")
        self._publish(
            EventKind.STARTED,
            goal=self._goal,
            workspace=self._workspace,
            sandbox="bwrap" if self._sandbox.use_bwrap else "bash-wrapper",
        )
        _MAX_STEPS = 25
        _step = 0

        while self._running:
            _step += 1
            if _step > _MAX_STEPS:
                print(f"[agent] step limit ({_MAX_STEPS}) reached stopping", flush=True)
                self._update_task_status("lost")
                self._publish(
                    EventKind.LOST,
                    reason=f"step limit ({_MAX_STEPS}) reached",
                    steps=_step - 1,
                )
                break

            guidance = self._drain_guidance()
            skills = self._skill_loader.load_relevant(self._goal)
            messages = self._memory.build_context()

            # Remind the agent of its goal every 5 steps to prevent drift
            if _step % 5 == 0:
                messages.append({
                    "role": "user",
                    "content": f"[reminder] Your goal is: {self._goal}. Focus on completing it. Do not deviate."
                })

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

            response = None
            for _attempt in range(3):
                try:
                    response = self._call_llm(messages)
                    break
                except (runtime.AgentError, httpx.HTTPError, ValueError, OSError) as exc:
                    logger.warning("LLM call failed (attempt %d/3): %s", _attempt + 1, exc)
                    if _attempt == 2:
                        logger.error("LLM call failed after 3 attempts giving up")
                        self._update_task_status("lost")
                        self._publish(
                            EventKind.FAILED,
                            reason=f"LLM unreachable after 3 attempts: {exc}",
                            steps=_step,
                        )
            if response is None:
                break

            parsed = self._parse_response(response)
            self._record_turn(_step, messages, parsed, response)
            command = parsed.get("command", "")

            if command:
                from sable.policy.engine import is_destructive
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
                # Image and package pulls get the longer ceiling; see runtime.
                output = self._run_command(
                    command, timeout=runtime.escalated_timeout(command)
                )
                print(f"[agent] output: {output[:200]}", flush=True)

            self._update_task_status("running", output)
            self._publish(
                EventKind.STATUS,
                step=_step,
                command=command,
                explanation=parsed.get("explanation", ""),
                # Bounded: the bus is read on every orchestrator turn, and a
                # command that prints a megabyte must not bloat every read.
                output=(output or "")[:2000],
            )
            self._write_live_status(command, parsed.get("explanation", ""), step=_step)
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
                summary = self._write_result_summary()
                self._publish(
                    EventKind.COMPLETED,
                    steps=_step,
                    explanation=parsed.get("explanation", ""),
                    result=summary or "",
                )
                print(f"\n[task '{self._name}'] Goal achieved. Window kept open for review.")
                break

        self._running = False

    def _write_live_status(self, command: str, explanation: str, step: int = 0) -> None:
        """Write a live status file so orchestrator knows what agent is doing right now."""
        try:
            tree = subprocess.run(
                ["find", ".", "-maxdepth", "3", "-not", "-path", "*/.agentic/*", "-not", "-name", ".*"],
                capture_output=True, text=True, cwd=self._workspace, timeout=3,
            ).stdout.strip()
            status_path = Path(self._workspace).parent / ".agentic" / "status.md"
            status_path.parent.mkdir(parents=True, exist_ok=True)
            status_path.write_text(
                f"# Agent: {self._name} (running step {step})\n"
                f"**Goal**: {self._goal}\n"
                f"**Workspace**: {self._workspace}\n"
                f"**Last action**: `{command}` {explanation}\n\n"
                f"## Current workspace files\n```\n{tree or '(empty)'}\n```\n"
            )
        except (OSError, subprocess.SubprocessError):
            # Includes the `find` above timing out. status.md is a mirror;
            # the bus already carries this step.
            pass

    def _write_result_summary(self) -> str:
        """Write result.md and return the summary text.

        Returns the summary so the caller can put it on the bus as the
        `completed` payload. The file is still written, but as a
        human-readable mirror: since Phase 1 the orchestrator reads the event,
        not the file.

        Returns an empty string if the summary could not be built, which the
        caller publishes as an empty result rather than failing the task. A
        finished piece of work must not be reported as failed because its
        write-up could not be produced.
        """
        try:
            # Collect all assistant turns as summary
            turns = self._memory._turns
            steps = [
                t["content"] for t in turns
                if t.get("role") == "assistant"
            ]
            # Run ls in workspace to capture final file tree
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
                    summary += f"{i}. `{p.get('command','')}`  {p.get('explanation','')}\n"
                except (json.JSONDecodeError, TypeError, AttributeError):
                    # A turn that is not action JSON: show it verbatim.
                    summary += f"{i}. {s[:120]}\n"

            result_path = Path(self._workspace).parent / ".agentic" / "result.md"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(summary)
            print(f"[agent] result written to {result_path}", flush=True)
            return summary
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"[agent] could not write result: {exc}", flush=True)
            return ""


if __name__ == "__main__":
    import argparse
    import json as _json
    from pathlib import Path as _Path

    parser = argparse.ArgumentParser(description="TaskAgent runner")
    parser.add_argument("--task", required=True, help="Task name")
    parser.add_argument("--goal", default=None, help="Goal text (deprecated, use --goal-file)")
    parser.add_argument("--goal-file", default=None, help="Path to file containing goal text")
    parser.add_argument("--shared-read-dir", default=None,
                        help="Parent task dir to mount read-only for sub-agent")
    args = parser.parse_args()

    if args.goal_file:
        goal = _Path(args.goal_file).read_text(encoding="utf-8").strip()
    elif args.goal:
        goal = args.goal
    else:
        raise SystemExit("Either --goal or --goal-file must be provided")

    from sable.core.db import DB_PATH
    config_path = _Path.home() / ".config" / "agentic-shell" / "config.json"

    try:
        raw = _json.loads(config_path.read_text())
        from sable.core.config.schema import ShellConfig
        config = ShellConfig.from_dict(raw)
    except (OSError, _json.JSONDecodeError, ValueError, KeyError):
        # No config, unreadable config, or one this version rejects. A worker
        # is spawned without a terminal to ask on, so it runs on defaults.
        from sable.core.config.schema import ShellConfig
        config = ShellConfig.defaults()

    agent = TaskAgent(
        task_name=args.task,
        goal=goal,
        config=config,
        db_path=str(DB_PATH),
        shared_read_dir=args.shared_read_dir,
    )
    agent.run()
