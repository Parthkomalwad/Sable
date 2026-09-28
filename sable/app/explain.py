"""K2 explain-last-error: `?` explains a failed command, `!` asks for a fix.

After a typed command exits non-zero the REPL prints `HINT`. The very next
line decides: exactly `?` or `!` is taken here, anything else proceeds as
normal and the offer lapses.

What leaves the machine is the command, its exit code and the last
`TAIL_LINES` lines of its output, each passed through `redact_text` first.
A fix is not run from here: it becomes a goal for the orchestrator, so the
proposed command gets the usual preview and `gate()` like any other.
"""
from __future__ import annotations

from dataclasses import dataclass

TAIL_LINES = 40

_SYSTEM = (
    "You explain why a shell command failed, in at most four short sentences. "
    'Reply with JSON only: {"command": "", "explanation": "<your explanation>", '
    '"safe": true, "plan": null}'
)


@dataclass
class Failure:
    command: str
    exit_code: int
    output: str
    cwd: str = ""

    @property
    def tainting(self) -> bool:
        from sable.policy.taint import is_tainting
        return is_tainting(self.command, self.cwd or None)


def hint() -> str:
    from sable.ui.theme import sgr
    return f"{sgr('dim')}? explain   ! fix\033[0m"


def payload(failure: Failure) -> str:
    """The only text sent about a failure: command, exit, redacted tail."""
    from sable.policy.engine import redact_text
    from sable.policy.taint import wrap_untrusted

    # Line by line: redact_text folds whitespace, newlines included.
    tail = "\n".join(redact_text(line) for line in failure.output.splitlines()[-TAIL_LINES:])
    return (f"Command: {redact_text(failure.command)}\n"
            f"Exit code: {failure.exit_code}\n"
            f"Last {TAIL_LINES} lines of output:\n{wrap_untrusted(tail)}")


def _http_error() -> type:
    try:
        import httpx
        return httpx.HTTPError
    except ImportError:
        return OSError


def explain(failure: Failure, config) -> str:
    """Ask the model why it failed and print the answer. Returns the text."""
    from rich.console import Console
    from rich.panel import Panel

    from sable.agents import runtime
    from sable.llm.registry import build_backend

    try:
        response = runtime.call_llm(
            build_backend(config, role="orchestrator"),
            [{"role": "user", "content": payload(failure)}], _SYSTEM,
        )
        text = (response.explanation or response.command or "").strip() or "(no explanation)"
    except (runtime.AgentError, OSError, ValueError, _http_error()) as exc:
        text = f"could not reach the model: {exc}"
    Console().print(Panel(text, title="why it failed", title_align="left", border_style="dim"))
    return text


def fix_goal(failure: Failure) -> str:
    """The goal handed to the orchestrator for `!`."""
    return ("This command failed. Propose one command that fixes the cause.\n"
            + payload(failure))


def handle(line: str, failure: Failure | None, config, run_goal) -> bool:
    """Consume `?` or `!` after a failure. True if the line was taken."""
    if failure is None or line not in ("?", "!"):
        return False
    if line == "?":
        explain(failure, config)
    else:
        # Output from a tainting command is untrusted: the fixer starts
        # tainted, as a worker spawned from a tainted turn does.
        run_goal(fix_goal(failure), failure.tainting)
    return True
