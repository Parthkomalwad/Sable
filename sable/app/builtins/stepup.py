"""`/stepup setup|status|off` (Phase 8, K7).

Setup prints the secret and the otpauth URI once, for an authenticator app
(no QR library), then asks for a first code; a wrong one removes the secret
again, so step-up is never on with a secret nobody holds.
"""
from __future__ import annotations

import getpass

from sable.policy import stepup
from sable.ui.console import out as _out

_USAGE = "usage: /stepup setup | /stepup status | /stepup off"


def _setup(secret) -> None:
    try:
        key, uri = stepup.setup()
    except RuntimeError as exc:
        _out(f"keyring unavailable, step-up not set up: {exc}")
        return
    _out("add this to your authenticator app; it is shown once:")
    _out(f"  secret {key}")
    _out(f"  {uri}")
    if stepup.verify(secret("first code (not echoed): ")):
        _out("step-up is on: deny-tier commands now offer a code"
             + (" or a phone push" if stepup.phone_available() else ""))
    else:
        stepup.off()
        _out("that code did not match; step-up stays off, run /stepup setup again")


def _handle_stepup_builtin(argument: str, secret=getpass.getpass) -> bool:
    sub = argument.strip()
    try:
        if sub == "setup":
            _setup(secret)
        elif sub in ("", "status"):
            if stepup.is_set_up():
                _out("step-up on: code" + (" or phone" if stepup.phone_available() else "")
                     + f", one command per grant, within {stepup.GRANT_TTL_S} s")
            else:
                _out("step-up off: deny-tier commands are refused (run /stepup setup)")
        elif sub == "off":
            _out("step-up off" if stepup.off() else "step-up was not set up")
        else:
            _out(_USAGE)
    except (EOFError, KeyboardInterrupt):
        if sub == "setup":
            stepup.off()  # a half-finished setup leaves step-up off
        _out("cancelled")
    return True
