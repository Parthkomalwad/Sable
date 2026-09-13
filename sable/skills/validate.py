"""Grading a skill's use (B5).

A skill may declare `validate` in its frontmatter: a command that answers
"did this actually work?" more honestly than the agent's own say-so. An
agent reporting `done` is the agent grading its own homework; a validator
asks the system instead.

`agents/runtime.run_command` returns everything the command printed and
never an exit code, so this appends a marker and parses it back out. That
is a decision made here rather than one the plan settled: the alternative
was a second pty runner that returns a code, and one runner with one
timeout policy and one set of failure modes is worth more than a tidier
return type.

**The validator runs sandboxed.** It is text that came off disk, and once
B7 lands it is text a stranger wrote. Running it unwrapped would make
`validate = "rm -rf ~"` a working exploit against anyone who approved a
skill without reading its frontmatter. The caller passes the same
`Sandbox.wrap_command` its own commands go through.

**A missing validator is not a failure.** Most skills will declare none.
Those fall back to the run's own outcome, so the absence of a check never
costs a skill confidence it earned.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sable.agents.runtime import run_command

#: Appended to a validator so its exit code survives a string-only runner.
#: Deliberately unlikely to appear in real output.
_MARKER = "__SABLE_VALIDATE__"

_MARKER_RE = re.compile(rf"{_MARKER}(\d+)")

#: A validator inspects state that already exists; it is not doing the work.
#: A check that hangs must not hold a worker open for the full command
#: timeout.
VALIDATE_TIMEOUT = 30

#: Exit codes that say the validator could not run, rather than that the
#: thing it checks is broken. These grade as no signal at all: the run's own
#: outcome stands, as it does for a skill declaring no validator.
#:
#: Found by the Phase 2 gate run. A crystallised deploy skill carried
#: `curl -sf localhost:8080/health`, a reasonable inference from the
#: commands the model was shown. Run anywhere the API is not up, curl exits
#: 7 and the skill was graded a failure: confidence fell across runs that
#: had succeeded. Each use costs 0.10, so a good skill used a few times on a
#: machine where the service is not running drops out of retrieval and the
#: loop degrades itself.
#:
#: Deliberately short, and curl-flavoured because that is what models
#: reach for. This is a heuristic, not a taxonomy: a validator that fails
#: for an environmental reason outside this set is still graded a failure,
#: and knowing what a validator *is* belongs to B4's skill doctor. A health
#: check that genuinely fails returns 1 or 22, which must keep failing or
#: the distinction becomes a way to never fail at all.
ENVIRONMENT_EXIT_CODES = frozenset({
    7,    # curl: failed to connect to host
    127,  # shell: command not found
})


@dataclass(frozen=True)
class Grade:
    """Whether a skill's use worked, and how that was decided.

    `reason` is shown by `/skill stats`, so it has to read as an
    explanation rather than as a status code.
    """

    success: bool
    reason: str
    output: str = ""


def grade_skill_use(
    validate_command: str,
    cwd: str,
    wrap=None,
    ran_ok: bool = True,
) -> Grade:
    """Decide whether a skill's use succeeded.

    With a validator, its exit code decides. Without one, `ran_ok` does:
    the outcome of the run that used the skill.
    """
    command = (validate_command or "").strip()

    if not command:
        return Grade(
            success=ran_ok,
            reason="no validator declared; graded on the run's own outcome",
        )

    output = run_command(
        f"{command}\necho {_MARKER}$?",
        cwd=cwd,
        timeout=VALIDATE_TIMEOUT,
        wrap=wrap,
        prefix="validate_",
    )

    if "[timeout after" in output:
        return Grade(
            success=False,
            reason=f"validator timed out after {VALIDATE_TIMEOUT}s",
            output=_strip_marker(output),
        )

    match = _MARKER_RE.search(output)
    if match is None:
        # The echo never ran, so the validator was killed or the shell died.
        # A check that did not finish has not demonstrated success, and
        # guessing in its favour is how a broken skill keeps its confidence.
        return Grade(
            success=False,
            reason="validator produced no exit code; treated as a failure",
            output=_strip_marker(output),
        )

    code = int(match.group(1))

    if code in ENVIRONMENT_EXIT_CODES:
        # The validator could not run here, which is evidence about the
        # machine and none at all about the skill. The run's own outcome
        # stands, exactly as it does for a skill declaring no validator.
        return Grade(
            success=ran_ok,
            reason=(
                f"validator could not run (exited {code}); "
                f"graded on the run's own outcome"
            ),
            output=_strip_marker(output),
        )

    return Grade(
        success=code == 0,
        reason=f"validator exited {code}",
        output=_strip_marker(output),
    )


def _strip_marker(output: str) -> str:
    """Remove the bookkeeping marker from output a human will read."""
    return _MARKER_RE.sub("", output).rstrip()
