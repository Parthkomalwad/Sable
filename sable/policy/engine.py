"""Safety guard runs every command through blocklist and entropy checks.

Applied on both the bash path and the agentic path before any execution.

Key responsibilities:
- DESTRUCTIVE_PATTERNS regex blocklist
- Shannon entropy check for secrets in privacy mode
- Confirmation flow (requires literal 'YES')
- Dry-run option for file-touching commands
- strip_secrets() for privacy mode
"""
from __future__ import annotations
import math
import re
import sys
from collections import Counter

from sable.policy import rules

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


def is_destructive(command: str) -> bool:
    """Return True if command matches any destructive pattern."""
    for pattern in DESTRUCTIVE_PATTERNS:
        if re.search(pattern, command):
            return True
    return False


def confirm_destructive(command: str, reason: str = "") -> bool:
    """Display warning and require 'YES' to proceed. Returns True if confirmed.

    Args:
        command: The command to confirm.
        reason: Optional reason string (e.g. 'AI flagged as unsafe').
    """
    label = reason if reason else "pattern matched as destructive"
    sys.stdout.write(
        f"\n\033[38;5;203m  ⚠ {label}\033[0m\n"
        f"  \033[2;37mcommand:\033[0m {command}\n"
        f"  \033[2;37mThis operation may be irreversible.\033[0m\n"
    )
    sys.stdout.flush()
    try:
        answer = input("  type YES to confirm: ").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer == "YES"


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
