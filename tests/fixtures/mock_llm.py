"""Mock LLM backend for unit and integration tests, and for SABLE_MOCK_LLM=1.

Returns canned LLMResponse objects. Never makes real HTTP calls, never needs
an API key. Three modes:

- single-shot   the original {command, explanation, safe, plan} responses,
                keyed by a substring of the last user message.
- orchestrator  a scripted run / run / spawn / done sequence, so the whole
                orchestrator loop can be exercised end to end.
- worker        a scripted {command, explanation, done} sequence for TaskAgent.

The orchestrator and worker sequences advance per instance: each call to
complete() returns the next step. A backend built for a goal that matches a
named script uses that script; anything else falls back to a short generic
run-then-done sequence, so an unscripted goal still terminates.
"""
from __future__ import annotations

import os

from shell.llm.base import LLMBackend, LLMResponse

# Canned responses keyed by nl_input substring
CANNED_RESPONSES: dict[str, LLMResponse] = {
    "list files": LLMResponse(
        command="ls -la",
        explanation="List all files in the current directory with details.",
        safe=True,
        plan=None,
        prompt_tokens=120,
        completion_tokens=30,
        cost_usd=0.0,
    ),
    "disk usage": LLMResponse(
        command="df -h",
        explanation="Show disk usage in human-readable format.",
        safe=True,
        plan=None,
        prompt_tokens=115,
        completion_tokens=25,
        cost_usd=0.0,
    ),
    "set up nginx": LLMResponse(
        command="",
        explanation="Multi-step nginx setup.",
        safe=True,
        plan=[
            "apt install nginx -y",
            "systemctl enable nginx",
            "systemctl start nginx",
        ],
        prompt_tokens=200,
        completion_tokens=60,
        cost_usd=0.0,
    ),
    "delete everything": LLMResponse(
        command="rm -rf /",
        explanation="Delete all files on the system.",
        safe=False,
        plan=None,
        prompt_tokens=100,
        completion_tokens=20,
        cost_usd=0.0,
    ),
}


def _run(command: str, explanation: str) -> LLMResponse:
    return LLMResponse(
        command=command,
        explanation=explanation,
        safe=True,
        plan=None,
        prompt_tokens=100,
        completion_tokens=20,
        cost_usd=0.0,
        model="mock",
        action="run",
    )


def _spawn(name: str, goal: str, explanation: str) -> LLMResponse:
    return LLMResponse(
        command="",
        explanation=explanation,
        safe=True,
        plan=None,
        prompt_tokens=120,
        completion_tokens=30,
        cost_usd=0.0,
        model="mock",
        action="spawn",
        spawn={"name": name, "goal": goal},
    )


def _done(explanation: str) -> LLMResponse:
    return LLMResponse(
        command="",
        explanation=explanation,
        safe=True,
        plan=None,
        prompt_tokens=90,
        completion_tokens=15,
        cost_usd=0.0,
        model="mock",
        action="done",
        done=True,
    )


def _worker_step(command: str, explanation: str) -> LLMResponse:
    return LLMResponse(
        command=command,
        explanation=explanation,
        safe=True,
        plan=None,
        prompt_tokens=80,
        completion_tokens=20,
        cost_usd=0.0,
        model="mock",
        done=False,
    )


# Orchestrator scripts: run, run, spawn, done.
ORCHESTRATOR_SCRIPTS: dict[str, list[LLMResponse]] = {
    # run, run, spawn, run, done. The run after the spawn matters: the
    # orchestrator only folds a sub-agent's result.md into its context while
    # building the next turn's messages, so a script that finished straight
    # after spawning would never observe the worker's result.
    "hello file": [
        _run("echo hello > hello.txt", "Write the hello file."),
        _run("cat hello.txt", "Check what the file contains."),
        _spawn(
            "verify-hello",
            "Verify hello.txt exists and report its contents.",
            "Delegating verification to a sub-agent.",
        ),
        _run("ls -l hello.txt", "Confirm the file while the sub-agent reports."),
        _done("Created hello.txt and verified it with a sub-agent."),
    ],
}

# The sequence used when no named script matches the goal.
DEFAULT_ORCHESTRATOR_SCRIPT: list[LLMResponse] = [
    _run("echo mock", "Run a placeholder command."),
    _done("Goal completed by the mock backend."),
]

# Worker scripts: {command, explanation, done}.
WORKER_SCRIPTS: dict[str, list[LLMResponse]] = {
    "verify-hello": [
        _worker_step("ls hello.txt", "Check the file exists."),
        _worker_step("cat hello.txt", "Read its contents."),
        _done("hello.txt exists and contains 'hello'."),
    ],
}

DEFAULT_WORKER_SCRIPT: list[LLMResponse] = [
    _worker_step("echo working", "Do the work."),
    _done("Worker finished."),
]


def _last_user_message(messages: list[dict]) -> str:
    return next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    ).lower()


def _full_text(messages: list[dict]) -> str:
    return " ".join(str(m.get("content", "")) for m in messages).lower()


def _turns_taken(messages: list[dict]) -> int:
    """How many actions the agent has already emitted in this conversation.

    The script position has to be recovered from the conversation, because
    OrchestratorAgent builds a fresh backend on every turn; instance state
    would reset each time and replay step one forever.

    Two kinds of message mark a completed turn:

    - an assistant message holding the action JSON, for a command that ran.
      The orchestrator's priming line ("Understood...") and its per-status
      acknowledgements ("Noted.") are not JSON, so they do not count.
    - a "[user cancelled command: ...]" line. A cancelled command appends only
      a user message, so without this the script would stall whenever a
      confirm is declined, including on EOF when stdin is a pipe.
    """
    return sum(
        1
        for m in messages
        if (
            m.get("role") == "assistant"
            and str(m.get("content", "")).lstrip().startswith("{")
        )
        or str(m.get("content", "")).startswith("[user cancelled command:")
    )


class MockLLMBackend(LLMBackend):
    """Mock LLM backend that returns canned responses for known inputs.

    Args:
        mode: "single" (default), "orchestrator" or "worker".
        script: explicit list of responses, overriding script selection.
    """

    def __init__(self, mode: str = "single", script: list[LLMResponse] | None = None) -> None:
        self.mode = mode
        self._script = script
        self._step = 0

    def _select_script(self, messages: list[dict]) -> list[LLMResponse]:
        if self._script is not None:
            return self._script
        text = _full_text(messages)
        table = ORCHESTRATOR_SCRIPTS if self.mode == "orchestrator" else WORKER_SCRIPTS
        for key, script in table.items():
            if key in text:
                return script
        return (
            DEFAULT_ORCHESTRATOR_SCRIPT
            if self.mode == "orchestrator"
            else DEFAULT_WORKER_SCRIPT
        )

    async def complete(self, messages: list[dict], system: str) -> LLMResponse:
        """Return the next canned response.

        In single mode this is keyed by the last user message. In orchestrator
        and worker mode the script position is derived from the conversation
        itself, not from a counter on this object: OrchestratorAgent builds a
        fresh backend every turn, so instance state would reset each time and
        the same first action would repeat forever.
        """
        if self.mode in ("orchestrator", "worker"):
            script = self._select_script(messages)
            step = max(self._step, _turns_taken(messages))
            response = script[min(step, len(script) - 1)]
            self._step = step + 1
            return response

        last_user = _last_user_message(messages)
        for key, response in CANNED_RESPONSES.items():
            if key in last_user:
                return response

        return LLMResponse(
            command="echo 'unknown input'",
            explanation="Default mock response.",
            safe=True,
            plan=None,
            prompt_tokens=100,
            completion_tokens=20,
            cost_usd=0.0,
        )


def mock_llm_enabled() -> bool:
    """True when SABLE_MOCK_LLM is set to a truthy value."""
    return os.environ.get("SABLE_MOCK_LLM", "").strip().lower() in {"1", "true", "yes", "on"}
