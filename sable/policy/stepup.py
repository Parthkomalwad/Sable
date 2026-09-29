"""Step-up approval for deny-tier commands (Phase 8, K7).

A deny-tier command can run once if the person at the keyboard proves a
second factor: a TOTP code (RFC 6238: SHA1, 6 digits, 30 s steps, stdlib
only) or a tap on a phone push (the Phase 5 ntfy path). Success makes a
grant for that exact command string, used once, within two minutes.

Only `gate()` offers it, only for role user/orchestrator, only with a tty on
both ends, and never under the daemon or `--mcp-serve` (they set
SABLE_NO_STEPUP). A worker never gets it: nobody reads its window.

The secret lives in the keyring only. The last accepted time step is kept in
the session database, so a code is never accepted twice, even after a
restart. Codes and the secret are never logged.
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sqlite3
import struct
import sys
import time
from pathlib import Path
from urllib.parse import quote

SERVICE = "stepup-totp"
STEP_S = 30
DIGITS = 6
GRANT_TTL_S = 120
PHONE_WAIT_S = 120
PHONE_POLL_S = 3.0
_CODE = re.compile(r"[0-9]{6}")

# command -> expiry. In memory on purpose: a grant must not outlive the
# process that asked for it.
_grants: dict[str, float] = {}


# ── TOTP ────────────────────────────────────────────────────────────────

def hotp(key: bytes, counter: int, digits: int = DIGITS, digest=hashlib.sha1) -> str:
    """RFC 4226 HOTP value for `counter`."""
    mac = hmac.new(key, struct.pack(">Q", counter), digest).digest()
    off = mac[-1] & 0x0F
    value = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 10 ** digits
    return f"{value:0{digits}d}"


def totp(key: bytes, t: float, digits: int = DIGITS, digest=hashlib.sha1) -> str:
    """RFC 6238 TOTP value at unix time `t`."""
    return hotp(key, int(t // STEP_S), digits, digest)


def _decode(secret_b32: str) -> bytes:
    s = secret_b32.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


# ── state ───────────────────────────────────────────────────────────────

def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    from sable.core.db import DB_PATH
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("CREATE TABLE IF NOT EXISTS stepup_state (key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
    return conn


def _secret() -> str | None:
    from sable.core.config import keyring
    try:
        return keyring.lookup(SERVICE)
    except (keyring.KeyringUnavailable, RuntimeError):
        return None


def is_set_up() -> bool:
    return bool(_secret())


def setup(db_path: str | Path | None = None) -> tuple[str, str]:
    """New secret into the keyring; returns (base32 secret, otpauth URI).

    Raises RuntimeError if the keyring cannot store it.
    """
    from sable.core.config import keyring
    secret_b32 = base64.b32encode(secrets.token_bytes(20)).decode()
    keyring.store_api_key(SERVICE, secret_b32)
    with _connect(db_path) as conn:  # a new secret starts with no used steps
        conn.execute("DELETE FROM stepup_state WHERE key = 'last_step'")
    try:
        who = f"{getpass.getuser()}@{socket.gethostname()}"
    except (KeyError, OSError):
        who = socket.gethostname()
    uri = (f"otpauth://totp/{quote('Sable:' + who)}?secret={secret_b32}"
           f"&issuer=Sable&algorithm=SHA1&digits={DIGITS}&period={STEP_S}")
    return secret_b32, uri


def off() -> bool:
    from sable.core.config import keyring
    try:
        return keyring.delete(SERVICE)
    except (keyring.KeyringUnavailable, RuntimeError):
        return False


def verify(code: str, now: float | None = None, db_path: str | Path | None = None) -> bool:
    """True once for a valid code: the current step or one either side.

    A step at or before the last accepted one is refused, so the same code
    (or an older one) never works twice. The burn is a conditional UPDATE,
    so two racing checks cannot both pass.
    """
    secret_b32 = _secret()
    code = (code or "").strip()
    if not secret_b32 or not _CODE.fullmatch(code):
        return False
    key = _decode(secret_b32)
    step = int((time.time() if now is None else now) // STEP_S)
    matched = None
    for s in (step - 1, step, step + 1):  # no early exit: same work either way
        if hmac.compare_digest(hotp(key, s), code) and matched is None:
            matched = s
    if matched is None:
        return False
    with _connect(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO stepup_state (key, value) VALUES ('last_step', -1)")
        cur = conn.execute("UPDATE stepup_state SET value = ? WHERE key = 'last_step' AND value < ?",
                           (matched, matched))
        return cur.rowcount == 1


# ── phone ───────────────────────────────────────────────────────────────

def phone_available() -> bool:
    from sable.daemon import notify
    s = notify.settings()
    return bool(s.get("topic") and s.get("reply_topic"))


def phone(command: str, *, wait: float = PHONE_WAIT_S, sleep=time.sleep, clock=time.time) -> bool:
    """Push "allow once?" with a fresh single-use token; True on the reply.

    The reply body is `stepup yes <token>`, which the daemon's approval
    regex does not match, so it never decides a queue item.
    """
    import httpx

    from sable.daemon import notify
    s = notify.settings()
    if not (s.get("topic") and s.get("reply_topic")):
        return False
    body = f"stepup yes {secrets.token_urlsafe(16)}"
    url = f"{s['server']}/{s['reply_topic']}"
    start = int(clock())
    if not notify.send(f"Step-up: allow `{command[:200]}` once?", f"$ {command}",
                       [{"action": "http", "label": "Allow once", "url": url,
                         "method": "POST", "body": body, "clear": True}]):
        return False
    while clock() - start <= wait:
        try:
            r = notify._client().get(f"{url}/json", params={"poll": "1", "since": str(start)},
                                     headers=notify.auth_headers(), timeout=notify.TIMEOUT)
            r.raise_for_status()
            for line in r.text.splitlines():
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if (isinstance(msg, dict) and msg.get("event") == "message"
                        and hmac.compare_digest(str(msg.get("message", "")).strip().encode(),
                                                body.encode())):
                    return True  # the token lived only in this call: single use
        except httpx.HTTPError as exc:
            notify.log.warning("step-up reply poll failed: %s", exc)
        sleep(PHONE_POLL_S)
    return False


# ── grants and the offer ────────────────────────────────────────────────

def grant(command: str, now: float | None = None) -> None:
    _grants[command] = (time.time() if now is None else now) + GRANT_TTL_S


def take(command: str, now: float | None = None) -> bool:
    """Consume the grant for exactly `command`. True at most once."""
    expiry = _grants.pop(command, None)
    return expiry is not None and (time.time() if now is None else now) <= expiry


def eligible(role: str) -> bool:
    """Whether gate() may offer step-up. Anything unattended: no."""
    if role not in ("user", "orchestrator") or os.environ.get("SABLE_NO_STEPUP"):
        return False
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
    except (AttributeError, ValueError):
        return False
    return is_set_up()


def _audit(factor: str, outcome: str, command: str) -> None:
    from sable.core.audit import write_action
    from sable.policy.engine import redact_text
    write_action("stepup", f"factor={factor} outcome={outcome} {redact_text(command)}")


def offer(command: str, ask=input, secret=getpass.getpass) -> bool:
    """Ask for a factor; on success grant `command` once. One code attempt."""
    has_phone = phone_available()
    try:
        choice = ask(f"  s step-up ({'code or phone' if has_phone else 'code'})  q cancel: ")
        if choice.strip().lower() != "s":
            _audit("none", "cancelled", command)
            return False
        prompt = "  6-digit code" + (", or p to push to your phone" if has_phone else "") + ": "
        answer = secret(prompt).strip()
        if has_phone and answer.lower() == "p":
            factor = "phone"
            ok = phone(command)
        else:
            factor = "totp"
            ok = verify(answer)
    except (EOFError, KeyboardInterrupt):
        _audit("none", "cancelled", command)
        return False
    _audit(factor, "granted" if ok else "refused", command)
    if ok:
        grant(command)
    return ok
