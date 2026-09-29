"""The sabled loop (Phase 5, E1).

One process per user. Every tick calls the registered handlers (schedules,
watchers, notifications); one handler failing is logged and the rest still
run. Runs as a systemd user unit, or in the foreground with `sable daemon run`.
"""
from __future__ import annotations

import logging
import os
import signal
import sqlite3
import sys
import time
from pathlib import Path
from typing import Callable

import httpx

from sable.core.paths import SABLE_HOME

TICK_S = 5.0
LOCK = SABLE_HOME / "sabled.lock"
UNIT = Path.home() / ".config" / "systemd" / "user" / "sabled.service"
log = logging.getLogger("sabled")

Handler = Callable[[sqlite3.Connection], None]
HANDLERS: list[Handler] = []


def register(fn: Handler) -> Handler:
    HANDLERS.append(fn)
    return fn


def tick(conn: sqlite3.Connection, handlers: list[Handler] | None = None) -> None:
    for fn in HANDLERS if handlers is None else handlers:
        try:
            fn(conn)
        # ponytail: the failure types handlers are known to hit; a new kind
        # of failure should surface as a crash (systemd restarts us) and be
        # added here once understood.
        except (OSError, sqlite3.Error, ValueError, KeyError, httpx.HTTPError) as exc:
            log.error("handler %s failed: %s", getattr(fn, "__name__", fn), exc)


def acquire_lock(path: Path = LOCK):
    """An open, flock-held file, or None when another daemon holds it."""
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    fh.seek(0); fh.truncate(); fh.write(str(os.getpid())); fh.flush()
    return fh


def _pid() -> int | None:
    try:
        return int(LOCK.read_text().strip())
    except (OSError, ValueError):
        return None


def _load_handlers() -> None:
    """Import the modules that register handlers. Each is optional so the
    foundation runs before later tasks land."""
    import importlib
    for name in ("schedule", "watchers", "approvals", "maintenance"):
        try:
            importlib.import_module(f"sable.daemon.{name}")
        except ModuleNotFoundError as exc:
            if exc.name != f"sable.daemon.{name}":
                raise


def run() -> int:
    from sable.core.db import DB_PATH
    from sable.daemon import jobs

    logging.basicConfig(level=logging.INFO, format="%(asctime)s sabled %(levelname)s %(message)s")
    lock = acquire_lock()
    if lock is None:
        sys.stderr.write(f"sabled already running (pid {_pid()})\n")
        return 1
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    lost = jobs.mark_lost(conn)
    if lost:
        log.warning("%d run(s) from a previous daemon marked lost", lost)
    register(lambda c: jobs.resume_waiting(c, background=True))
    _load_handlers()
    stop = []
    signal.signal(signal.SIGTERM, lambda *a: stop.append(1))
    log.info("started, pid %d, %d handler(s)", os.getpid(), len(HANDLERS))
    try:
        while not stop:
            tick(conn)
            # Sleep to the next boundary so ticks do not drift.
            time.sleep(TICK_S - time.time() % TICK_S)
    except KeyboardInterrupt:
        pass
    log.info("stopped")
    return 0


def unit_text() -> str:
    root = Path(__file__).resolve().parents[2]
    return (
        "[Unit]\nDescription=Sable background daemon\n\n"
        "[Service]\n"
        f"Environment=PYTHONPATH={root}\n"
        f"ExecStart={sys.executable} -m sable.app.main daemon run\n"
        "Restart=on-failure\nRestartSec=5\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


def install() -> str:
    UNIT.parent.mkdir(parents=True, exist_ok=True)
    UNIT.write_text(unit_text())
    return (f"wrote {UNIT}\nTo start it now and at every boot, run:\n"
            "  systemctl --user daemon-reload && systemctl --user enable --now sabled\n"
            f"  sudo loginctl enable-linger {os.environ.get('USER', '$USER')}   # keep it running after logout\n")


def status() -> str:
    pid = _pid()
    if pid is None:
        return "sabled is not running"
    try:
        os.kill(pid, 0)
    except OSError:
        return "sabled is not running (stale lock)"
    return f"sabled is running (pid {pid})"


def stop() -> str:
    pid = _pid()
    if pid is None:
        return "sabled is not running"
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return "sabled is not running (stale lock)"
    return f"sent SIGTERM to sabled (pid {pid})"


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "run":
        return run()
    actions = {"install": install, "status": status, "stop": stop}
    if cmd not in actions:
        sys.stderr.write("usage: sable daemon run|install|status|stop\n")
        return 2
    sys.stdout.write(actions[cmd]() + "\n")
    return 0
