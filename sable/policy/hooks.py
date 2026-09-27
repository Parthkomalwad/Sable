"""Lifecycle hooks: user scripts in ~/.sable/hooks/ (Phase 3 Task 4, F1).

The same contract as Claude Code's hooks, deliberately, so a user who has
written one already knows this one:

- The hook is an executable named after the event: `pre_command`,
  `post_command`, `pre_spawn` or `on_skill_use`.
- It receives one JSON object on stdin, with `hook` set to its own name.
- Exit 0 allows. Stdout that parses as a JSON object may carry `context`
  (added to what the model sees) and `message` (shown to the user); any
  other stdout is shown to the user as text rather than discarded.
- **Exit 2 blocks**, and stdout says why.
- Any other failure, including a hang past the timeout, is reported and does
  **not** block. A broken hook must not wedge the shell.

Hooks run after the policy decision (`engine.gate`), so they can make a
command stricter and never looser, for the same reason the admin floor
exists.
"""
from __future__ import annotations

import json
import subprocess  # hooks are the user's own scripts, not user commands: no pty
import sys
from dataclasses import dataclass
from pathlib import Path

from sable.core.paths import SABLE_HOME

HOOKS_DIR = SABLE_HOME / "hooks"
HOOK_TIMEOUT = 10.0   # seconds; a hook runs on the path to every command
BLOCK = 2


@dataclass(frozen=True)
class HookResult:
    blocked: bool = False
    message: str = ""   # for the user
    context: str = ""   # for the model


def _argv(path: Path) -> list[str]:
    """How a hook is invoked. A seam, so tests can run scripts on Windows."""
    return [str(path)]


def _error(name: str, what: str) -> HookResult:
    sys.stderr.write(f"sable: {name} hook {what}; not blocking\n")
    return HookResult()


def run(name: str, payload: dict) -> HookResult:
    """Run the hook called `name`, if there is one."""
    path = HOOKS_DIR / name
    if not path.is_file():
        return HookResult()
    try:
        proc = subprocess.run(
            _argv(path),
            input=json.dumps({"hook": name, **payload}),
            capture_output=True,
            text=True,
            timeout=HOOK_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return _error(name, f"timed out after {HOOK_TIMEOUT:g}s")
    except OSError as exc:
        return _error(name, f"could not run ({exc})")

    out = proc.stdout.strip()
    if proc.returncode == BLOCK:
        return HookResult(blocked=True, message=out or proc.stderr.strip() or f"blocked by {name} hook")
    if proc.returncode != 0:
        return _error(name, f"exited {proc.returncode}")

    try:
        data = json.loads(out) if out else None
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        return HookResult(message=str(data.get("message", "")), context=str(data.get("context", "")))
    return HookResult(message=out)


def show(name: str, result: HookResult) -> None:
    """Print a hook's message for the user, if it left one."""
    if result.message:
        sys.stdout.write(f"  \033[2;37m[{name}] {result.message}\033[0m\n")
        sys.stdout.flush()
