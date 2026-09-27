"""Privilege rules no policy file can relax (Phase 3, I4).

Two of them. A command that escalates with `sudo` is always at least
`confirm`, whatever a user rule says: it is the one escalation where a
preview alone is not enough. And Sable does not run its agents as root: as
uid 0 it hands the session to plain bash instead, so root is never locked
out of a login shell.
"""
from __future__ import annotations

import os

from sable.policy.rules import Rule
from sable.policy.tiers import Tier

# `sudo` as a command word: at the start, or after ; & | ( or $( .
# `man sudo` and `grep sudoers` are not escalation and do not match.
SUDO_RULE = Rule(
    name="sudo",
    pattern=r"(^|[;&|(]\s*|\$\(\s*)sudo\b",
    category="privilege",
    why="runs with root privileges; always confirmed, whatever other rules say",
    tier=Tier.CONFIRM,
    source="built-in",
)


def sudo_rule(command: str) -> Rule | None:
    return SUDO_RULE if SUDO_RULE.compiled.search(command) else None


# Credential files. Anything that names one, a `cat` or a `tool:fs.read`
# alike, is at least `confirm`: a model that reads a private key has put it in
# a prompt sent to a provider, which is the leak. A worker is never prompted,
# so for it this is a refusal. Public keys (`id_*.pub`) and `known_hosts` are
# not secrets and do not match.
CREDENTIALS_RULE = Rule(
    name="credential-file",
    pattern=(
        r"\.ssh/(id_[\w-]+(?![\w.-])|[\w-]*_key(?![\w.-])|authorized_keys)"
        r"|\.aws/credentials|\.gnupg/|\.netrc\b|\.pgpass\b|\.git-credentials\b"
        r"|\.docker/config\.json|\.kube/config\b|\.config/agentic-shell/config\.json"
        r"|/etc/(g?shadow|sudoers)\b"
    ),
    category="credentials",
    why="reads a credential file; whatever a model reads is sent to its provider",
    tier=Tier.CONFIRM,
    source="built-in",
)


def credentials_rule(command: str) -> Rule | None:
    return CREDENTIALS_RULE if CREDENTIALS_RULE.compiled.search(command) else None


def _euid() -> int:
    return os.geteuid() if hasattr(os, "geteuid") else -1


def root_refusal() -> str | None:
    """The message to show instead of starting as root, or None.

    `SABLE_ALLOW_ROOT=1` opts out, for throwaway containers such as the
    playground where root is the only user. It is not a security boundary:
    root can do anything anyway. The check exists so nobody runs a model's
    commands as root by accident.
    """
    if _euid() != 0 or os.environ.get("SABLE_ALLOW_ROOT") == "1":
        return None
    return ("sable: not starting the agent layer as root. A model's commands "
            "would run with full privileges. Continuing in plain bash; log in "
            "as a normal user and use sudo, which sable always confirms.")
