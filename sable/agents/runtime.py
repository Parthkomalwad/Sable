"""Shared agent machinery: the parts the orchestrator and the worker really do share.

Phase 1 (A2). `agents/orchestrator.py` and `agents/worker.py` each grew their
own copy of three things: a pty command runner with a deadline, an asyncio
loop wrapper around `backend.complete()`, and a fence-stripping JSON parse.
Those copies had drifted in ways nobody intended. This module holds one of
each.

**Why this is helpers and not one `Agent(role=...)` class.** The roadmap (§3.1)
calls the two classes "70% duplicated" and asks for a single class with a role
enum. Read side by side they are not. The orchestrator is interactive: it
confirms every command through `input()`, drives a spinner, and writes to the
user's terminal. The worker is headless: it takes guidance off a stdin queue,
keeps a `TaskMemory`, injects skills, wraps every command in a `Sandbox`, and
escalates its timeout to 600s for commands that pull images or packages. Their
action schemas differ too (`{action, command, goal, name}` against
`{command, explanation, done}`), as do their limits (20 turns against 25
steps). A single class would be two disjoint halves behind a role flag, with
every method branching on it. What is genuinely common is the three helpers
below, and those are what this module extracts.

**Why the command runner is not in `core/executor.py`.** `executor._pty_exec`
looks similar and is not interchangeable. It forwards the user's stdin into the
child, puts the terminal in raw mode, streams output to stdout as it arrives,
and has no timeout. An agent needs the opposite: nothing on stdin, nothing on
stdout, a hard deadline enforced with SIGKILL, and the output returned as a
string for the model to read. Merging them would mean a function whose every
behaviour is conditional on who called it.
"""
from __future__ import annotations

import asyncio
import json
import os
import select
import signal
import tempfile
import time
from typing import Any, Callable

#: Longest a single `backend.complete()` may take. Distinct from the per-command
#: timeout below: this bounds thinking, that bounds doing.
LLM_TIMEOUT = 120.0

#: Default ceiling on one command. The worker raises it for commands that pull
#: images or packages; see `escalated_timeout`.
COMMAND_TIMEOUT = 120

#: Commands that routinely take minutes on a cold cache. A worker gives these
#: `LONG_COMMAND_TIMEOUT` instead of the default, because killing a half-finished
#: `docker pull` at 120s leaves the agent with no way to make progress.
LONG_RUNNING_MARKERS = ("docker", "npm", "pip", "yarn", "git clone")
LONG_COMMAND_TIMEOUT = 600

#: How long to block in `select` before re-checking the deadline. Bounded so a
#: command that produces no output is still killed close to its timeout rather
#: than whenever it next writes.
_SELECT_SLICE = 5.0


class AgentError(Exception):
    """Base for runtime failures that are the agent's problem, not the user's."""


class LLMUnavailable(AgentError):
    """The backend could not be reached, or did not answer inside LLM_TIMEOUT."""


def escalated_timeout(command: str, default: int = COMMAND_TIMEOUT) -> int:
    """Seconds to allow `command`, raised for image and package pulls.

    Pre-existing worker behaviour, lifted here so the rule is stated once and
    can be tested without running an agent.
    """
    if any(marker in command for marker in LONG_RUNNING_MARKERS):
        return LONG_COMMAND_TIMEOUT
    return default


def run_command(
    command: str,
    cwd: str,
    timeout: float = COMMAND_TIMEOUT,
    wrap: Callable[[str], str] | None = None,
    on_timeout: Callable[[int], None] | None = None,
    prefix: str = "agent_",
) -> str:
    """Run one command in a pty and return everything it printed.

    The two callers differ only in `wrap` (the worker passes
    `Sandbox.wrap_command`, the orchestrator passes nothing) and in what they
    want said when time runs out, which is `on_timeout`.

    The command goes through a temp script file rather than `bash -c` because
    the sandbox wrapper is a multi-line script with function definitions, and
    passing that as a single `-c` argument does not survive quoting.

    Never raises: the return value is what the model will read, so a failure
    has to come back as text it can reason about. A timeout appends a marker
    the caller greps for (the orchestrator delegates the goal to a sub-agent
    when it sees one).
    """
    if not command.strip():
        return "(empty command)"

    from ptyprocess import PtyProcessUnicode

    body = wrap(command) if wrap is not None else command

    try:
        handle, script_path = tempfile.mkstemp(suffix=".sh", prefix=prefix)
        try:
            with os.fdopen(handle, "w") as script:
                script.write(body)

            proc = PtyProcessUnicode.spawn(["/bin/bash", script_path], cwd=cwd)
            chunks: list[str] = []
            deadline = time.monotonic() + timeout

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if on_timeout is not None:
                        on_timeout(timeout)
                    _kill(proc)
                    chunks.append(f"\n[timeout after {timeout}s]")
                    break
                try:
                    readable, _, _ = select.select(
                        [proc.fd], [], [], min(remaining, _SELECT_SLICE)
                    )
                    if readable:
                        chunks.append(proc.read(1024))
                    elif not proc.isalive():
                        break
                except EOFError:
                    # The child closed the pty: normal end of output.
                    break
                except (OSError, ValueError):
                    # fd went away underneath us; whatever was read still counts.
                    break

            _reap(proc)
            return "".join(chunks)
        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass
    except (OSError, ValueError) as exc:
        return f"[error: {exc}]"


def _kill(proc) -> None:
    """SIGKILL, ignoring a process that has already gone."""
    try:
        proc.kill(signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


def _reap(proc) -> None:
    """Collect the exit status, ignoring a process that has already gone."""
    try:
        proc.wait()
    except (OSError, ProcessLookupError, ChildProcessError):
        pass


def call_llm(backend, messages: list[dict], system: str, timeout: float = LLM_TIMEOUT):
    """Run one `backend.complete()` to completion on a private event loop.

    Both agents are synchronous loops calling an async backend, so each one
    needs its own loop per call. Pending tasks are drained before closing;
    without that, a backend that left a request in flight produces a "Task was
    destroyed but it is pending" warning into the middle of the user's terminal.

    Raises LLMUnavailable on timeout so a caller can retry or give up
    deliberately, rather than reading it out of a generic exception.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            asyncio.wait_for(backend.complete(messages, system), timeout=timeout)
        )
    except asyncio.TimeoutError as exc:
        raise LLMUnavailable(f"LLM call timed out after {timeout:g}s") from exc
    finally:
        pending = asyncio.all_tasks(loop)
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


def strip_fences(raw: str) -> str:
    """Remove markdown code fences from a model response.

    Step 1 of the JSON fallback chain in CLAUDE.md. Models wrap JSON in
    ```json blocks despite being told not to, often enough that this is the
    common case rather than the exception.
    """
    return raw.replace("```json", "").replace("```", "").strip()


def parse_json_action(raw: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    """Parse a model response into an action dict, or return `default`.

    Steps 1 and 2 of the fallback chain: strip fences, then parse. Never
    raises, per CLAUDE.md: an unparseable response is a thing the agent has to
    handle and report, not an exception that ends the run. A response that
    parses to something other than an object (a bare list, a string) counts as
    unparseable, since every caller goes on to read keys off it.
    """
    cleaned = strip_fences(raw)
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return dict(default) if default is not None else {}
    if not isinstance(parsed, dict):
        return dict(default) if default is not None else {}
    return parsed
