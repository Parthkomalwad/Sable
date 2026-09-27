"""`/secret add <name>`, `/secret list`, `/secret rm <name>` (F6).

The value is read with no echo and goes straight to the keyring. It is never
printed, logged, or put in the conversation; `list` shows names only.
"""
from __future__ import annotations

import getpass

from sable.core.config import keyring
from sable.policy.secrets import NAME, SERVICE_PREFIX
from sable.ui.console import out as _out

_USAGE = "usage: /secret add <name> | /secret list | /secret rm <name>"


def _handle_secret_builtin(argument: str, prompt=getpass.getpass) -> bool:
    parts = argument.split()
    sub, rest = (parts[0], parts[1:]) if parts else ("list", [])
    try:
        if sub == "list" and not rest:
            names = [s[len(SERVICE_PREFIX):] for s in keyring.services(SERVICE_PREFIX)]
            _out("\n".join(f"  {n}" for n in names) if names else "no secrets stored")
        elif sub in ("add", "rm") and len(rest) == 1:
            name = rest[0]
            if not NAME.match(name):
                _out("secret names are letters, digits and _, not starting with a digit")
            elif sub == "rm":
                removed = keyring.delete(SERVICE_PREFIX + name)
                _out(f"removed {name}" if removed else f"no secret named {name!r}")
            else:
                try:
                    value = prompt(f"value for {name} (not echoed): ")
                except (EOFError, KeyboardInterrupt):
                    value = ""
                if not value:
                    _out("nothing stored")
                else:
                    keyring.store_api_key(SERVICE_PREFIX + name, value)
                    _out(f"stored {name}; use it as $SECRET:{name}")
        else:
            _out(_USAGE)
    except (keyring.KeyringUnavailable, RuntimeError) as exc:
        _out(f"keyring unavailable, nothing changed: {exc}")
    return True
