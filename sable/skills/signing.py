"""Skill signatures (Phase 8 Task 6, K8).

A skill folder is signed with HMAC-SHA256 under a local key kept in the
keyring (service `skill-signing`, 32 random bytes, made on first use). The
MAC covers every regular file in the folder, by sorted relative path and
content, except the signature file itself, so an edited or an added file
both show up.

`verify()` answers one of three things:

- `signed`: the signature matches.
- `unsigned`: no signature, or no key to check it with. Commands run while
  following it are one tier stricter.
- `tampered`: a signature that does not match. The skill is not injected.

No keyring is never a crash: signing does nothing and every skill reads as
unsigned, which is the stricter direction to fail. A missing key is not
recreated at verify time, since that would turn every signed skill into
`tampered` on a machine that merely lost its D-Bus session.

Public-key signatures are later (see the Phase 8 plan, section 0.1).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

from sable.core.config import keyring

SERVICE = "skill-signing"
SIGNATURE_FILE = ".sable-signature"


def _key_file() -> Path:
    from sable.core.paths import SABLE_HOME
    return SABLE_HOME / "skill-signing.key"


def _file_key(create: bool) -> bytes | None:
    """The key in a 0600 file, for servers with no keyring (the Phase 8 gate
    found signing impossible on a headless host). Same trust boundary: only
    this user can read it, as only this user can write their skills."""
    path = _key_file()
    try:
        return bytes.fromhex(path.read_text().strip())
    except (OSError, ValueError):
        pass
    if not create:
        return None
    key = secrets.token_bytes(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(key.hex())
    except OSError:
        return None
    return key


def _key(create: bool) -> bytes | None:
    try:
        stored = keyring.lookup(SERVICE)
    except keyring.KeyringUnavailable:
        return _file_key(create)
    if stored:
        try:
            return bytes.fromhex(stored)
        except ValueError:
            return None
    if _key_file().exists():
        return _file_key(False)
    if not create:
        return None
    key = secrets.token_bytes(32)
    try:
        keyring.store_api_key(SERVICE, key.hex())
    except RuntimeError:
        return _file_key(True)
    return key


def _digest(skill_dir: Path, key: bytes) -> str:
    mac = hmac.new(key, digestmod=hashlib.sha256)
    for path in sorted(p for p in skill_dir.rglob("*") if p.is_file() and not p.is_symlink()):
        rel = path.relative_to(skill_dir).as_posix()
        if rel == SIGNATURE_FILE:
            continue
        data = path.read_bytes()
        # Length-prefixed, so moving bytes between a name and a body cannot
        # produce the same stream.
        for part in (rel.encode(), data):
            mac.update(len(part).to_bytes(8, "big"))
            mac.update(part)
    return mac.hexdigest()


def sign(skill_dir: str | Path) -> str | None:
    """Sign a skill folder and write the signature. None when no key or no folder."""
    skill_dir = Path(skill_dir)
    if not skill_dir.is_dir():
        return None
    key = _key(create=True)
    if key is None:
        return None
    try:
        sig = _digest(skill_dir, key)
        (skill_dir / SIGNATURE_FILE).write_text(sig + "\n", encoding="utf-8")
    except OSError:
        return None
    return sig


def verify(skill_dir: str | Path) -> str:
    """'signed', 'unsigned' or 'tampered'."""
    skill_dir = Path(skill_dir)
    try:
        stored = (skill_dir / SIGNATURE_FILE).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return "unsigned"
    key = _key(create=False)
    if key is None:
        return "unsigned"
    try:
        actual = _digest(skill_dir, key)
    except OSError:
        return "tampered"
    return "signed" if hmac.compare_digest(stored, actual) else "tampered"


def trust_of(skill_file: str | Path) -> str:
    """Trust for a skill file: its folder's verdict, or 'unsigned' for a flat file."""
    path = Path(skill_file)
    if path.name != "SKILL.md":
        return "unsigned"
    return verify(path.parent)
