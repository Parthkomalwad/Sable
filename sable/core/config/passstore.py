"""`pass` (password-store) fallback for broker secrets on headless servers.

Used only when the Secret Service is unavailable (no D-Bus over SSH). The
stdlib has no symmetric encryption, so this shells out to the `pass` CLI,
which encrypts with gpg. Argv lists, no shell. The value goes in on stdin
only: never argv, never a file Sable writes, never a log line.

Entries live under `sable/<name>` in the store.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

PREFIX = "sable/"
# `pass show` may wait on gpg-agent's pinentry for a passphrase.
READ_TIMEOUT = 60.0
WRITE_TIMEOUT = 30.0


class PassError(RuntimeError):
    """`pass` failed, timed out, or is not set up."""


def store_dir() -> Path:
    env = os.environ.get("PASSWORD_STORE_DIR")
    return Path(env) if env else Path.home() / ".password-store"


def available() -> bool:
    """`pass` on PATH and the store initialised with `pass init`."""
    return shutil.which("pass") is not None and (store_dir() / ".gpg-id").is_file()


def _run(args: list[str], timeout: float, stdin: str | None = None) -> str:
    try:
        proc = subprocess.run(
            ["pass", *args], input=stdin, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PassError(f"pass {args[0]} timed out after {timeout:.0f}s") from exc
    except OSError as exc:
        raise PassError(f"pass could not run: {exc}") from exc
    if proc.returncode != 0:
        # stderr never holds the value: pass writes it on stdout only.
        raise PassError(f"pass {args[0]} failed: {proc.stderr.strip()[:200]}")
    return proc.stdout


def show(name: str) -> str | None:
    """First line of `sable/<name>`, None if the entry does not exist."""
    if not (store_dir() / f"{PREFIX}{name}.gpg").is_file():
        return None
    out = _run(["show", PREFIX + name], READ_TIMEOUT)
    return out.split("\n", 1)[0]


def insert(name: str, value: str) -> None:
    _run(["insert", "-m", "-f", PREFIX + name], WRITE_TIMEOUT, stdin=value + "\n")


def remove(name: str) -> bool:
    if not (store_dir() / f"{PREFIX}{name}.gpg").is_file():
        return False
    _run(["rm", "-f", PREFIX + name], WRITE_TIMEOUT)
    return True


def names() -> list[str]:
    """Entry names from the store dir; no need to parse `pass ls` tree output."""
    return sorted(p.stem for p in (store_dir() / PREFIX.rstrip("/")).glob("*.gpg"))
