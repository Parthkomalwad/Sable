"""A4: a second model checks a goal's work before it is reported done.

One call, no tools, runs nothing. Step output is redacted and framed as
untrusted data, since it came from commands the reviewer never saw run.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from sable.agents import runtime
from sable.llm import prompts
from sable.policy.engine import redact_text

#: Total characters of step text sent to the reviewer.
INPUT_CAP = 6000
#: Characters of each step's output kept, from the end.
TAIL = 800
VERDICTS = ("pass", "concerns", "fail")


@dataclass
class Verdict:
    verdict: str
    why: str


def build_input(goal: str, steps: list[dict]) -> str:
    """The goal plus each step's command and output tail, capped at INPUT_CAP."""
    body = ""
    for i, step in enumerate(steps, 1):
        tail = str(step.get("output", ""))[-TAIL:]
        body += f"step {i}: $ {step.get('command', '')}\n{tail}\n\n"
    body = redact_text(body)
    if len(body) > INPUT_CAP:
        # The latest steps matter most for "is it done", so keep the end.
        body = "[earlier steps cut]\n" + body[-INPUT_CAP:]
    return f"Goal: {goal}\n\n<untrusted-steps>\n{body}</untrusted-steps>"


def _raw(response) -> str:
    return getattr(response, "raw", "") or getattr(response, "explanation", "") or ""


def _verdict(raw: str) -> Verdict | None:
    parsed = runtime.parse_json_action(raw, {})
    verdict = str(parsed.get("verdict", "")).strip().lower()
    if verdict not in VERDICTS:
        return None
    return Verdict(verdict, str(parsed.get("why", "")).strip())


def review(backend, goal: str, steps: list[dict]) -> Verdict:
    """Return the reviewer's verdict. Never raises on a model or network problem."""
    system = prompts.load("reviewer")
    messages = [{"role": "user", "content": build_input(goal, steps)}]
    try:
        raw = _raw(runtime.call_llm(backend, messages, system))
        verdict = _verdict(raw)
        if verdict is None:
            # Step 3 of the fallback chain: ask once more for the JSON alone.
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": 'Reply with only the JSON object {"verdict": ..., "why": ...}.'}]
            verdict = _verdict(_raw(runtime.call_llm(backend, messages, system)))
    except (runtime.AgentError, httpx.HTTPError, ValueError, OSError) as exc:
        return Verdict("concerns", f"reviewer unavailable: {exc}")
    return verdict or Verdict("concerns", "reviewer reply unreadable")
