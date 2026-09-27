"""The secret broker (F6): `$SECRET:name` resolved at exec time, never before.

The model writes `$SECRET:db_pass`. The preview, `gate()`, the audit row, the
event bus and `agent_turns` all see that placeholder, because resolution
happens in the runner, after every one of them has already run.

**How the value reaches the command.** Not spliced into the command string:
that would put it in `ps`, in the temp script on disk and in bash's argv. Each
placeholder is rewritten to a reference to `SABLE_SECRET_<NAME>` and the value
travels in the child's environment only. Outside double quotes the reference
is emitted quoted (`"${SABLE_SECRET_DB_PASS}"`) so a value with spaces stays
one word; inside double quotes it is emitted bare so the quoting is not
broken. Inside single quotes it would never expand, so that is refused.

**Refusal, not fallback.** An unknown name or an unavailable keyring raises
`SecretError` and the command does not run. There is no plaintext or
environment-variable fallback, and the literal placeholder never reaches bash.

**Output.** `redact` swaps every resolved value in the command's output back
to its placeholder, so a command that echoes the secret does not hand it to
the model or to memory.
"""
from __future__ import annotations

import re

from sable.core.config import keyring

PLACEHOLDER = re.compile(r"\$SECRET:([A-Za-z_][A-Za-z0-9_]*)")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
SERVICE_PREFIX = "secret:"


class SecretError(Exception):
    """A placeholder that cannot be resolved. The command must not run."""


def env_name(name: str) -> str:
    return "SABLE_SECRET_" + name.upper()


def _quote_state(command: str, end: int) -> str | None:
    """'"' or "'" if position `end` is inside that quote, else None."""
    state = None
    i = 0
    while i < end:
        c = command[i]
        if c == "\\" and state != "'":
            i += 2
            continue
        if state is None and c in "'\"":
            state = c
        elif c == state:
            state = None
        i += 1
    return state


def resolve(command: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """Return (rewritten command, env to add, {value: placeholder}).

    A command with no placeholder comes back unchanged with empty dicts and
    never touches the keyring. Raises SecretError.
    """
    matches = list(PLACEHOLDER.finditer(command))
    if not matches:
        return command, {}, {}

    env: dict[str, str] = {}
    reveal: dict[str, str] = {}
    parts: list[str] = []
    last = 0
    for m in matches:
        name = m.group(1)
        quote = _quote_state(command, m.start())
        if quote == "'":
            raise SecretError(f"$SECRET:{name} is inside single quotes and would not expand")
        var = env_name(name)
        if var not in env:
            try:
                value = keyring.lookup(SERVICE_PREFIX + name)
            except keyring.KeyringUnavailable as exc:
                raise SecretError(f"keyring unavailable, refusing to run ({exc})") from exc
            if value is None:
                raise SecretError(f"no secret named {name!r} (add it with /secret add {name})")
            env[var] = value
            if value:
                reveal[value] = m.group(0)
        ref = "${" + var + "}"
        parts.append(command[last:m.start()])
        parts.append(ref if quote == '"' else f'"{ref}"')
        last = m.end()
    parts.append(command[last:])
    return "".join(parts), env, reveal


def redact(text: str, reveal: dict[str, str]) -> str:
    """Replace each resolved value in `text` with its placeholder. Longest first."""
    for value in sorted(reveal, key=len, reverse=True):
        text = text.replace(value, reveal[value])
    return text
