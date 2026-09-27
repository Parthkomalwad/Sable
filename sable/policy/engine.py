"""Safety guard runs every command through blocklist and entropy checks.

Applied on both the bash path and the agentic path before any execution.

Key responsibilities:
- DESTRUCTIVE_PATTERNS regex blocklist
- Shannon entropy check for secrets in privacy mode
- decide() tiers a command; gate() turns the tier into run, YES, or refuse
- Dry-run option for file-touching commands
- strip_secrets() for privacy mode
"""
from __future__ import annotations
import math
import os
import re
import sys
from collections import Counter

from sable.core import audit
from sable.policy import hooks, privilege, rules, taint
from sable.policy.tiers import Decision, Tier

# The rules themselves live in defaults/policy.toml (Phase 0.5 step 4), so a
# pattern can be read and audited without reading code, and so each one
# carries the `why` that Phase 3's `/policy explain` will show the user.
#
# These two names stay: they are the public surface the rest of the codebase
# and the tests use, and they are still plain lists of regex strings in file
# order. A broken or missing policy file raises at import rather than
# yielding an empty list, because an empty blocklist is a shell that runs
# destructive commands without asking.
DESTRUCTIVE_PATTERNS: list[str] = [rule.pattern for rule in rules.destructive_rules()]

SECRET_PATTERNS: list[str] = [rule.pattern for rule in rules.secret_rules()]


def shannon_entropy(s: str) -> float:
    """Calculate Shannon entropy of a string."""
    counts = Counter(s)
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def looks_like_secret(token: str) -> bool:
    """Return True if the token looks like a secret.

    Two independent signals, either is enough:
    1. The token matches a known secret format (SECRET_PATTERNS). Structured
       credentials such as AWS access keys are not high-entropy enough to trip
       the threshold below, so the format check has to come first.
    2. The token is long and high-entropy, which catches formats we do not
       have a pattern for.
    """
    for pattern in SECRET_PATTERNS:
        if re.search(pattern, token):
            return True
    return len(token) >= 20 and shannon_entropy(token) > 4.5


def decide(command: str, *, tainted: bool = False) -> Decision:
    """The tier policy gives this command, and the rule that gave it.

    An unmatched command is `allow`. That is not the last line of defence it
    looks like: a model's command is still previewed before it runs, and a
    worker's runs inside its sandbox. The rules are for what a preview or a
    sandbox would not stop a tired human from approving.

    `tainted` means the agent has read untrusted output (see `taint.py`):
    the tier goes one step stricter. An `allow` gets a stand-in rule named
    `tainted-context`, so every caller that prints `d.rule.name` still can.
    """
    rule = rules.match(command)
    sudo = privilege.sudo_rule(command)
    if sudo and (rule is None or sudo.tier.severity > rule.tier.severity):
        rule = sudo
    if rule is None:
        d = Decision(tier=Tier.ALLOW, rule=None, why="", source="default")
    else:
        d = Decision(tier=rule.tier, rule=rule, why=rule.why, source=rule.source)
    if not tainted:
        return d
    why = "tainted context: the agent has read untrusted output this goal"
    rule = d.rule or rules.Rule(name="tainted-context", pattern="", why=why,
                                tier=Tier.CONFIRM, source="taint")
    return Decision(tier=taint.bump(d.tier), rule=rule,
                    why=f"{why} ({d.why})" if d.why else why, source=d.source)


def gate(
    command: str,
    *,
    role: str,
    approved: bool = False,
    tainted: bool = False,
    agent: str | None = None,
    model: str | None = None,
    goal: str | None = None,
) -> bool:
    """Decide, then act on it. True means the command may run.

    `role` is "user" (a typed line), "orchestrator" (a model's command in the
    user's session) or "worker" (a sub-agent nobody is watching). A worker is
    never prompted: its tmux window has no reader, so a prompt there blocks
    forever. It runs `allow` and refuses everything else.

    `approved` is a `/approve` from the queue: it stands in for the YES a
    `confirm` would ask for, and never lifts a `deny`.

    The `pre_command` hook runs last, only for a command policy would run,
    so a hook can block and never unblock.

    `tainted` is passed to `decide()`: one tier stricter.

    Every decision writes one `audit_ledger` row (F4); `agent`, `model` and
    `goal` are provenance for it, and `agent` defaults to `role`. The caller
    that runs the command then calls `core.audit.finish(exit_code)`.
    """
    d = decide(command, tainted=tainted)

    def _record(outcome: str) -> None:
        audit.record(
            command, agent=agent or role, role=role, model=model, goal=goal,
            tier=d.tier.value, rule=d.rule.name if d.rule else None,
            why=d.why if d.rule else None, outcome=outcome, redact=redact_text,
        )

    if d.tier is Tier.DENY:
        if role != "worker":
            _warn(f"refused by policy: {d.rule.name}", command, d.why)
        _record("refused")
        return False
    if d.tier is Tier.CONFIRM and not approved and (role == "worker" or not _confirm(command, d)):
        _record("unconfirmed")
        return False

    result = hooks.run("pre_command", {
        "command": command,
        "role": role,
        "tier": d.tier.value,
        "rule": d.rule.name if d.rule else None,
        "tainted": tainted,
        "cwd": os.getcwd(),
    })
    if result.blocked:
        if role != "worker":
            _warn("blocked by pre_command hook", command, result.message)
        _record("hook_blocked")
        return False
    if role != "worker":
        hooks.show("pre_command", result)
    if d.tier is Tier.CONFIRM:
        _record("approved" if approved else "confirmed")
    else:
        _record("allowed")
    return True


def _warn(label: str, command: str, why: str) -> None:
    sys.stdout.write(
        f"\n\033[38;5;203m  ⚠ {label}\033[0m\n"
        f"  \033[2;37mcommand:\033[0m {command}\n"
        f"  \033[2;37m{why}\033[0m\n"
    )
    sys.stdout.flush()


def _confirm(command: str, d: Decision) -> bool:
    """Show which rule fired and why, and require the literal word YES."""
    _warn(f"DESTRUCTIVE  {d.rule.name}", command, d.why)
    from sable.policy import blast
    sys.stdout.write(f"  {blast.tag(command)}  \033[2;37mtier {d.tier.value}\033[0m\n")
    try:
        answer = input("  type YES to confirm: ").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer == "YES"


def redact_text(text: str) -> str:
    """`strip_secrets` without the count, as a plain text-in/text-out callable.

    The shape `core/events/replay.py` wants: that module sits below `policy` in
    the layering rule, so it cannot import this one and takes a redactor as an
    argument instead. Agents pass this function.
    """
    return strip_secrets(text)[0]


def strip_secrets(text: str) -> tuple[str, int]:
    """Redact secrets from text before sending to LLM.

    Returns:
        Tuple of (redacted_text, redaction_count).
    """
    redacted = 0
    for pattern in SECRET_PATTERNS:
        matches = re.findall(pattern, text)
        redacted += len(matches)
        text = re.sub(pattern, "[REDACTED]", text)
    # entropy-based catch for unknown secret formats
    tokens = text.split()
    result_tokens = []
    for token in tokens:
        if looks_like_secret(token):
            result_tokens.append("[REDACTED]")
            redacted += 1
        else:
            result_tokens.append(token)
    return " ".join(result_tokens), redacted
