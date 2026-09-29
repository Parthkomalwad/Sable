"""`sable eval`: run the task suite and record how the agent did (Phase 9, H3).

A task is a folder under evals/tasks/: `task.toml` (goal, timeout_s, tags),
`setup.sh`, `check.sh` (exit 0 = pass) and, for the mock backend, `mock.json`
(the scripted solution, read by tests/fixtures/mock_llm.py).

Each task runs in a child process with a fresh temp HOME and a fresh work dir
exported as $EVAL_ROOT. A child, not a thread, because Sable resolves its
paths (config, DB, audit log) from HOME at import time: only a new process
gets a truly empty home. The child runs the goal through the real
orchestrator in headless mode: previews are accepted, a typed YES is refused
(stdin is /dev/null), spawn and graph are refused, the reviewer is off.

Cost: the default backend is the mock. A paid backend runs only when it is
named on the command line, never from config.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

from sable.agents.orchestrator import OrchestratorAgent

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO_ROOT / "evals" / "tasks"
BACKENDS = ("mock", "ollama", "openai", "anthropic")
PAID = {"openai", "anthropic"}

_console = Console(highlight=False)


def _out(text: str) -> None:
    _console.print(text, markup=False)

_CREATE_EVAL_RUNS = """
CREATE TABLE IF NOT EXISTS eval_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    backend TEXT NOT NULL,
    task TEXT NOT NULL,
    passed INTEGER NOT NULL,
    steps INTEGER NOT NULL,
    tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    seconds REAL NOT NULL
)
"""


@dataclass(frozen=True)
class Task:
    name: str
    goal: str
    timeout_s: int
    tags: tuple[str, ...]
    path: Path


@dataclass(frozen=True)
class Result:
    task: str
    passed: bool
    steps: int
    tokens: int
    cost_usd: float
    seconds: float


def load_task(path: Path) -> Task:
    """One task folder. Raises ValueError saying what is wrong with it."""
    try:
        meta = tomllib.loads((path / "task.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{path.name}: unreadable task.toml ({exc})") from exc
    goal, timeout, tags = meta.get("goal"), meta.get("timeout_s", 120), meta.get("tags", [])
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError(f"{path.name}: goal must be a non-empty string")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError(f"{path.name}: timeout_s must be a positive integer")
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise ValueError(f"{path.name}: tags must be a list of strings")
    for script in ("setup.sh", "check.sh"):
        if not (path / script).is_file():
            raise ValueError(f"{path.name}: missing {script}")
    return Task(path.name, goal.strip(), timeout, tuple(tags), path)


def load_tasks(root: Path = TASKS_DIR, only: str | None = None) -> list[Task]:
    tasks = [load_task(p) for p in sorted(root.iterdir()) if p.is_dir()
             and (only is None or p.name == only)]
    if only is not None and not tasks:
        raise ValueError(f"no task named {only!r} in {root}")
    return tasks


def check_backend(backend: str, explicit: bool) -> None:
    """Refuse an unknown backend, and a paid one nobody named on the command line."""
    if backend not in BACKENDS:
        raise ValueError(f"unknown backend {backend!r}; one of {', '.join(BACKENDS)}")
    if backend in PAID and not explicit:
        raise ValueError(f"{backend} costs money; pass --backend {backend} to run it")


class HeadlessOrchestrator(OrchestratorAgent):
    """The orchestrator with nobody at the keyboard.

    Previews are accepted, since a person would press enter on a correct
    step; policy's typed YES is not overridden here, so with stdin at
    /dev/null a confirm-tier step is refused and fails the task honestly.
    """

    tokens = 0
    cost_usd = 0.0

    def _confirm_command(self, command: str, explanation: str) -> str | None:
        _out(f"  [eval] $ {command}")
        return command

    def _confirm_tool(self, name, args, explanation, ctx=None) -> bool:
        _out(f"  [eval] tool {name}")
        return True

    def _refuse(self, action: dict, kind: str) -> None:
        msg = f"[{kind} refused: sable eval runs headless; do the work with run actions]"
        _out(f"  [eval] {msg}")
        self._history.append({"role": "assistant", "content": json.dumps(action)})
        self._history.append({"role": "user", "content": msg})

    def _handle_spawn(self, action: dict) -> None:
        self._refuse(action, "spawn")

    def _handle_graph(self, action: dict) -> None:
        self._refuse(action, "graph")

    def _review(self, action: dict) -> str:
        return "done"

    def _ask_user(self) -> str:
        return ""

    def _undo_point(self, command, touches):
        return True, None

    def _record_cost(self, model, prompt_tokens, completion_tokens, cost_usd) -> None:
        self.tokens += prompt_tokens + completion_tokens
        self.cost_usd += cost_usd
        super()._record_cost(model, prompt_tokens, completion_tokens, cost_usd)


def _headless(goal: str, cwd: str, result_path: str) -> int:
    """Child side: run one goal and write {steps, tokens, cost_usd} as JSON."""
    from sable.core.config.schema import ShellConfig
    from sable.core.config.wizard import CONFIG_PATH
    from sable.core.db import DB_PATH, Database

    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config = ShellConfig.from_dict(raw)
    if raw.get("api_key"):
        config.api_key = raw["api_key"]  # type: ignore[attr-defined]
    Database().close()  # create the schema in the fresh home
    os.chdir(cwd)
    agent = HeadlessOrchestrator(goal=goal, cwd=cwd, config=config,
                                 db_path=str(DB_PATH), task_manager=None)
    agent.run()
    Path(result_path).write_text(json.dumps({
        "steps": len(agent._steps), "tokens": agent.tokens, "cost_usd": agent.cost_usd}))
    return 0


def _config_for(backend: str) -> dict:
    """The child's config.json: the user's own settings for that backend if
    they use it, else defaults. Reviewer and rehearsal off: one model, no
    second copy of the work."""
    cfg = {"backend": "ollama", "model": "llama3.1", "api_base": "http://localhost:11434"}
    if backend != "mock":
        from sable.core.config.wizard import CONFIG_PATH
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            user = {}
        if user.get("backend") == backend:
            cfg = {k: user[k] for k in ("backend", "model", "api_base", "api_key", "models")
                   if k in user}
        else:
            cfg = {"backend": backend, "api_base": None,
                   "model": {"openai": "gpt-4o-mini", "anthropic": "claude-haiku-4-5"}.get(backend, "llama3.1")}
    return {**cfg, "routing_mode": "auto", "setup_complete": True,
            "review": "off", "rehearse": "off"}


def _bash(script: Path, env: dict, cwd: Path, timeout: int) -> int:
    try:
        return subprocess.run(["bash", str(script)], cwd=cwd, env=env, timeout=timeout,
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode
    except subprocess.TimeoutExpired:
        return 124


def run_task(task: Task, backend: str, log_dir: Path | None = None) -> Result:
    tmp = Path(tempfile.mkdtemp(prefix=f"sable-eval-{task.name}-"))
    home, root = tmp / "home", tmp / "root"
    home.mkdir()
    root.mkdir()
    cfg_path = home / ".config" / "agentic-shell" / "config.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps(_config_for(backend)))
    env = {**os.environ, "HOME": str(home), "EVAL_ROOT": str(root), "SABLE_NO_STEPUP": "1",
           "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO_ROOT), os.environ.get("PYTHONPATH")]))}
    if backend == "mock":
        env["SABLE_MOCK_LLM"] = "1"
    else:
        env.pop("SABLE_MOCK_LLM", None)
    t0 = time.monotonic()
    data = {}
    try:
        if _bash(task.path / "setup.sh", env, root, task.timeout_s) != 0:
            _out(f"  [eval] {task.name}: setup failed")
        else:
            result = tmp / "result.json"
            log = (log_dir / f"{task.name}.log") if log_dir else tmp / "goal.log"
            with open(log, "w", encoding="utf-8") as fh:
                try:
                    subprocess.run([sys.executable, "-m", "sable.evals.runner", "--headless",
                                    task.goal, str(root), str(result)],
                                   cwd=root, env=env, timeout=task.timeout_s,
                                   stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT)
                except subprocess.TimeoutExpired:
                    _out(f"  [eval] {task.name}: timed out after {task.timeout_s}s")
            try:
                data = json.loads(result.read_text())
            except (OSError, ValueError):
                data = {}
        seconds = time.monotonic() - t0
        passed = bool(data) and _bash(task.path / "check.sh", env, root, 60) == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return Result(task.name, passed, int(data.get("steps", 0)), int(data.get("tokens", 0)),
                  float(data.get("cost_usd", 0.0)), round(seconds, 1))


def run(tasks: list[Task], backend: str = "mock", explicit: bool = False,
        log_dir: Path | None = None) -> list[Result]:
    check_backend(backend, explicit)
    results = []
    for task in tasks:
        r = run_task(task, backend, log_dir)
        _out(f"  {'PASS' if r.passed else 'FAIL'}  {task.name}  ({r.steps} steps, {r.seconds}s)")
        results.append(r)
    return results


def record(results: list[Result], backend: str, db_path: str | Path | None = None) -> str:
    """Append one row per task to `eval_runs`; returns the run id."""
    if db_path is None:
        from sable.core.db import DB_PATH as db_path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run_id = now
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(_CREATE_EVAL_RUNS)
        conn.executemany(
            "INSERT INTO eval_runs (run_id, created_at, backend, task, passed, steps, tokens, "
            "cost_usd, seconds) VALUES (?,?,?,?,?,?,?,?,?)",
            [(run_id, now, backend, r.task, int(r.passed), r.steps, r.tokens, r.cost_usd, r.seconds)
             for r in results])
        conn.commit()
    finally:
        conn.close()
    return run_id


def markdown(results: list[Result], backend: str) -> str:
    passed = sum(r.passed for r in results)
    lines = [f"sable eval, backend {backend}: {passed}/{len(results)} passed", "",
             "| task | pass | steps | tokens | cost | seconds |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {r.task} | {'yes' if r.passed else 'no'} | {r.steps} | {r.tokens} | "
              f"${r.cost_usd:.4f} | {r.seconds} |" for r in results]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if argv[:1] == ["--headless"]:
        return _headless(*argv[1:4])
    from sable.core.paths import ensure_state_dir

    out = _out
    parser = argparse.ArgumentParser(prog="sable eval")
    parser.add_argument("--backend", choices=BACKENDS)
    parser.add_argument("--only")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    backend = args.backend or "mock"
    try:
        check_backend(backend, explicit=args.backend is not None)
        tasks = load_tasks(only=args.only)
    except ValueError as exc:
        out(f"sable eval: {exc}")
        return 2
    results = run(tasks, backend, explicit=args.backend is not None)
    record(results, backend)
    table = markdown(results, backend)
    dest = Path(args.out) if args.out else ensure_state_dir() / "eval-latest.md"
    dest.write_text(table, encoding="utf-8")
    out(table)
    out(f"written to {dest}")
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
