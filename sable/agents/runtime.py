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
import re
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


_EXIT_MARKER = re.compile(r"(?:\(no output; exit (\d+)\)|\[exit (\d+)\])\s*\Z")


def exit_code_of(output: str) -> int | None:
    """The exit status `run_command` encoded in its return text, if known.

    `run_command` returns text, not a status: a failure ends in `[exit N]`, a
    silent command is `(no output; exit N)`, and output with neither marker
    exited 0. Refusals, errors, timeouts and an unavailable status are None.
    """
    m = _EXIT_MARKER.search(output)
    if m:
        return int(m.group(1) or m.group(2))
    if output.startswith(("[blocked:", "[error:")) or "[timeout after" in output \
            or "exit status unavailable" in output[-60:]:
        return None
    return 0


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

    from sable.policy import secrets

    # F6: resolve `$SECRET:name` here, after gate/preview/audit saw only the
    # placeholder. The value goes in the child's env, never the script.
    try:
        command, secret_env, reveal = secrets.resolve(command)
    except secrets.SecretError as exc:
        return f"[blocked: {exc}]"
    env = {**os.environ, **secret_env} if secret_env else None

    body = wrap(command) if wrap is not None else command

    try:
        handle, script_path = tempfile.mkstemp(suffix=".sh", prefix=prefix)
        try:
            with os.fdopen(handle, "w") as script:
                script.write(body)

            proc = PtyProcessUnicode.spawn(["/bin/bash", script_path], cwd=cwd, env=env)
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

            status = _reap(proc)
            output = secrets.redact("".join(chunks), reveal)

            # Two separate problems, both solved by reporting the status the
            # pty already collected and this function used to discard.
            #
            # Silence read as "still running". A command that printed nothing
            # came back empty, the caller substituted "(no output)", and in
            # the Phase 2 gate the model answered that with a `done` that
            # abandoned the goal after one of three steps, 4 runs out of 4.
            # The same goal with a command that echoed a line completed every
            # time, so the silence was the trigger rather than the model.
            #
            # Failure read as success. A deploy that wrote its error to
            # stderr and exited 1 returned only its prose, so the *model* was
            # told it had failed and the runtime was not. Grading looks for a
            # marker, found none, and nudged the skill's confidence up on the
            # run that was meant to push it down. Output is not an outcome:
            # a failure has to be legible to the code as well as the model.
            failed = status is None or status != 0
            if not output.strip():
                if status is None:
                    return "(no output; exit status unavailable)"
                return f"(no output; exit {status})"
            if failed:
                suffix = (
                    "exit status unavailable" if status is None else f"exit {status}"
                )
                return f"{output.rstrip()}\n[{suffix}]"
            return output
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


def _reap(proc) -> int | None:
    """Collect the exit status, ignoring a process that has already gone.

    Returns None when the status cannot be had, which is not the same as 0
    and must not be reported as success.
    """
    try:
        return proc.wait()
    except (OSError, ProcessLookupError, ChildProcessError):
        return None


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


# ---------------------------------------------------------------------------
# J4, J5: verify-after-act, the repeat guard and the retry ladder
# ---------------------------------------------------------------------------

#: How many times the same command or tool call may run in one goal. The
#: next one is refused by the runtime, not left to the prompt.
MAX_IDENTICAL = 2

#: Rungs of the retry ladder, indexed by consecutive failures (1-based).
#: Past the last rung the step is marked failed and the counter resets.
LADDER = ("retry", "alternative", "ask")


def action_key(action: dict) -> str:
    """What makes two actions "the same": whitespace-normalised command text,
    or the tool call rendered as `tool:<name> <sorted args>`."""
    if action.get("tool") or action.get("action") == "tool":
        call = action.get("tool") or action
        return "tool:" + str(call.get("name", "")) + " " + json.dumps(
            call.get("args", {}), sort_keys=True, default=str)
    return " ".join(str(action.get("command", "")).split())


class RepeatGuard:
    """Counts actions that ran in a goal; refuses the third identical one.

    `refuse` only checks; `ran` counts. A proposal that was blocked or
    cancelled never ran, so it does not use up a try.
    """

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    def refuse(self, key: str) -> str | None:
        """None to go ahead, or the message for the model."""
        if key.strip() and self._seen.get(key, 0) >= MAX_IDENTICAL:
            return (f"[refused: `{key}` already ran {MAX_IDENTICAL} times in this goal. "
                    "Running it again will not change the result. Try a different "
                    "command, or finish with what you have.]")
        return None

    def ran(self, key: str) -> None:
        self._seen[key] = self._seen.get(key, 0) + 1


def failed_output(output: str) -> bool:
    """Did this action's output report a failure? Non-zero or unknown status."""
    return exit_code_of(output or "") != 0


def _local_url(url: str) -> bool:
    # Localhost only, deliberately: verify exists to check the user's own
    # local services, and this keeps a model-chosen URL off the network and
    # off metadata addresses. web.py's URL policy refuses localhost, so it is
    # the wrong check to reuse here.
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and parts.hostname in ("localhost", "127.0.0.1")


_VERIFY_EXIT = re.compile(r"^\s*exit\s*(?:==|=|:)\s*(\d+)\s*$", re.IGNORECASE)
_VERIFY_KEYED = re.compile(r"^\s*(stdout_contains|file_exists)\s*:\s*(.+?)\s*$")


def _structured_verify(verify):
    """A string that spells a structured check, as the structured check.

    The contract says `{"exit": 0}`, but a live gate run showed gpt-4o-mini
    sending `"verify": "exit: 0"`. Run as a shell command that can never
    pass (`exit:: command not found`), so a successful `fs.read` was marked
    failed twice and the goal abandoned. `exit: N`, `exit == N`,
    `stdout_contains: text` and `file_exists: path` now mean what they say;
    any other string is still a shell command.
    """
    if not isinstance(verify, str):
        return verify
    if m := _VERIFY_EXIT.match(verify):
        return {"exit": int(m.group(1))}
    if m := _VERIFY_KEYED.match(verify):
        return {m.group(1): m.group(2).strip("\"'")}
    return verify


def run_verify(verify, *, output: str, cwd: str, run: Callable[[str], str]) -> dict | None:
    """Check an action with its `verify`. None when it passed.

    `output` is the action's own output. `run` executes a verify command and
    must gate it like any other command (the caller's runner already does).
    A failure is the structured record fed back to the model.
    """
    def fail(check, got) -> dict:
        return {"verify": "failed", "check": check, "got": got}

    verify = _structured_verify(verify)
    if isinstance(verify, str):
        if not verify.strip():
            return None
        got = run(verify)
        return None if exit_code_of(got) == 0 else fail(verify, got[-500:])
    if not isinstance(verify, dict) or len(verify) != 1:
        return fail(verify, "unknown verify form")
    (kind, want), = verify.items()
    if kind == "exit":
        code = exit_code_of(output or "")
        return None if code == want else fail(verify, code)
    if kind == "stdout_contains":
        return None if str(want) in (output or "") else fail(verify, (output or "")[-500:])
    if kind == "file_exists":
        path = os.path.join(cwd, os.path.expanduser(str(want)))
        return None if os.path.exists(path) else fail(verify, "missing")
    if kind == "http_status" and isinstance(want, dict):
        url, status = str(want.get("url", "")), want.get("status", 200)
        if not _local_url(url):
            return fail(verify, "http_status is limited to localhost/127.0.0.1 URLs for now")
        import httpx

        try:
            got = httpx.get(url, timeout=httpx.Timeout(30.0), follow_redirects=False).status_code
        except httpx.HTTPError as exc:
            return fail(verify, str(exc))
        return None if got == status else fail(verify, got)
    return fail(verify, "unknown verify form")


def failure_of(output: str) -> tuple[str, str]:
    """(check, got) for a failed step, short enough for one terminal line."""
    for line in reversed((output or "").splitlines()):
        if line.startswith('{"verify": "failed"'):
            try:
                v = json.loads(line)
            except json.JSONDecodeError:
                break
            check = v.get("check")
            return (check if isinstance(check, str) else json.dumps(check)), str(v.get("got"))[-80:]
    code = exit_code_of(output or "")
    lines = [l for l in (output or "").splitlines() if l.strip() and not _EXIT_MARKER.search(l)]
    return (f"exit {code}" if code is not None else "no exit status"), (lines[-1] if lines else "")[-80:]


def rung_label(failures: int, ask: str = "asking you") -> str:
    """What happens next, for the visible block. 0 means marked failed."""
    return {1: "retry 1 of 2", 2: "try an alternative", 3: ask}.get(failures, "marked failed, moving on")


def failure_block(check: str, got: str, next_: str) -> str:
    return f"  ↻ step failed: {check} (got: {got})\n    next: {next_}"


def reflection(failures: int, what: str) -> str:
    """The reflection turn injected after a failed step, by ladder rung."""
    rung = LADDER[min(failures, len(LADDER)) - 1]
    then = {
        "retry": "Then retry, fixing the cause.",
        "alternative": "The same approach failed twice: try a different one.",
        "ask": "This step has failed three times; guidance has been requested.",
    }[rung]
    return (f"[reflect] The last step failed: {what[:300]}\n"
            "Before your next action, say briefly in its explanation: what failed, "
            f"why, and what to try instead. {then}")


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
        # Two objects back to back is valid JSON followed by more valid JSON,
        # which json.loads rejects as "Extra data". One action per turn is the
        # contract, so the first complete object is the answer.
        try:
            parsed, _ = json.JSONDecoder().raw_decode(cleaned)
        except (json.JSONDecodeError, TypeError, AttributeError):
            return dict(default) if default is not None else {}
    if not isinstance(parsed, dict):
        return dict(default) if default is not None else {}
    return parsed
