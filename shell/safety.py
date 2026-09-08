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

DESTRUCTIVE_PATTERNS: list[str] = [
    # Disk/filesystem destruction
    r"\bdd\s+if=.*of=/dev/",          # dd writing to a device
    r"\bmkfs\b",                       # format filesystem
    r"\bfdisk\b.*(/dev/)",             # partition a device
    r">\s*/dev/sd[a-z]\b",            # redirect into raw disk
    r">\s*/dev/nvme\d",               # redirect into nvme disk
    # Recursive deletion any path
    r"\brm\s+-[^\s]*r[^\s]*\s+\S",   # rm -rf <anything>
    # Pipe-to-shell (arbitrary code execution from network)
    r"\bcurl\b[^|]*\|\s*(sudo\s+)?(bash|sh)\b",
    r"\bwget\b[^|]*\|\s*(sudo\s+)?(bash|sh)\b",
    # System state changes
    r"\bshutdown\b",
    r"\breboot\b",
    r"\biptables\s+-F\b",             # flush all firewall rules
]

SECRET_PATTERNS: list[str] = [
    r"AKIA[A-Z0-9]{16}",
    r"(?i)secret[_\s]?key[\s:=]+\S{20,}",
    r"eyJ[A-Za-z0-9\-_]{20,}\.[A-Za-z0-9\-_]+",
    r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----",
    r"(?i)bearer\s+[A-Za-z0-9\-_\.]{20,}",
    r"(?i)api[_\-]?key[\s:=]+[A-Za-z0-9\-_\.]{20,}",
    r'"type"\s*:\s*"service_account"',
]


def shannon_entropy(s: str) -> float:
    """Calculate Shannon entropy of a string."""
    counts = Counter(s)
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def looks_like_secret(token: str) -> bool:
    """Return True if the token looks like a secret based on length and entropy."""
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
