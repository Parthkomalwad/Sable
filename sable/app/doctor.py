"""`sable doctor [--fix]`: check an install, and repair what is safe to (I6).

Lives in `app/` rather than `core/` because it looks at every layer (tasks in
`agents`, the palace in `memory`), and only `app` may import them all.

Reports first; `--fix` then migrates the config (after a timestamped backup),
sets its mode to 600, marks stale tasks lost and reindexes the palace. It
never deletes user data.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sable.core import health
from sable.core.config import migrate
from sable.core.config.wizard import CONFIG_PATH
from sable.core.db import DB_PATH
from sable.core.paths import SABLE_HOME


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str
    fix_hint: str = ""


def _stale_probe(db_path: Path) -> Callable[[bool], list[str]]:
    def probe(fix: bool) -> list[str]:
        if not db_path.exists():
            return []
        from sable.agents.reconcile import reconcile

        return reconcile(str(db_path), dry_run=not fix)
    return probe


def _palace_probe(fix: bool) -> tuple[str, str]:
    """Compare the palace's fact files with its index; reindex on --fix."""
    try:
        from sable.memory import palace
    except ImportError:
        return "ok", "not installed yet"
    files = sum(1 for _ in (SABLE_HOME / "palace").rglob("*.md"))
    if fix:
        palace.reindex()
        return "ok", f"reindexed {files} fact files"
    rooms = palace.rooms()
    if isinstance(rooms, dict):
        indexed = sum(rooms.values())
        if indexed != files:
            return "warn", f"{files} fact files, {indexed} indexed"
    return "ok", f"{files} fact files"


def _check_config(path: Path, fix: bool) -> list[Check]:
    if not path.exists():
        return [Check("config version", "warn", f"{path} missing", "run sable to start the setup wizard")]
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return [Check("config version", "fail", f"unreadable: {exc}", f"fix or remove {path}")]
    checks = []
    version = migrate.version_of(data)
    if version < migrate.CURRENT and fix:
        backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
        new, notes = migrate.migrate(data)
        path.write_text(json.dumps(new, indent=2))
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        checks.append(Check("config version", "ok",
                            f"migrated {version} -> {migrate.CURRENT} ({'; '.join(notes)}); backup {backup.name}"))
    elif version < migrate.CURRENT:
        checks.append(Check("config version", "warn", f"schema {version}, current {migrate.CURRENT}",
                            "sable doctor --fix (backs up first)"))
    elif version > migrate.CURRENT:
        checks.append(Check("config version", "warn", f"schema {version} is newer than this Sable",
                            "upgrade Sable"))
    else:
        checks.append(Check("config version", "ok", f"schema {version}"))

    if sys.platform != "win32":
        mode = path.stat().st_mode & 0o777
        if mode != 0o600 and fix:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            checks.append(Check("config permissions", "ok", f"was {mode:o}, now 600"))
        elif mode != 0o600:
            checks.append(Check("config permissions", "warn", f"mode {mode:o}; it may hold an API key",
                                "sable doctor --fix, or chmod 600"))
        else:
            checks.append(Check("config permissions", "ok", "600"))
    return checks


def _check_db(path: Path) -> Check:
    if not path.exists():
        return Check("database", "ok", "not created yet")
    try:
        conn = sqlite3.connect(str(path), timeout=1.0)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return Check("database", "fail", str(exc), f"restore {path} from a backup")
    if result != "ok":
        return Check("database", "fail", result, f"restore {path} from a backup")
    return Check("database", "ok", "integrity ok")


def run(
    fix: bool = False,
    *,
    config_path: Path = CONFIG_PATH,
    db_path: Path = DB_PATH,
    which: Callable[[str], str | None] = shutil.which,
    bwrap: Callable[[], bool] = health.bwrap_available,
    stale: Callable[[bool], list[str]] | None = None,
    palace: Callable[[bool], tuple[str, str]] = _palace_probe,
) -> list[Check]:
    """Every check, in order. Probes are injectable so tests touch no real tools."""
    db_path = Path(db_path)
    stale = stale or _stale_probe(db_path)
    py = sys.version_info
    version = f"{py.major}.{py.minor}.{py.micro}"
    checks = [Check("python", "ok", version) if py >= (3, 11) else
              Check("python", "fail", version, "Sable needs Python 3.11 or newer")]

    tmux = which("tmux")
    checks.append(Check("tmux", "ok", tmux) if tmux else
                  Check("tmux", "warn", "not found: no sidebar, no sub-agents", "install tmux"))
    if bwrap():
        checks.append(Check("sandbox", "ok", "bwrap with user namespaces"))
    else:
        checks.append(Check("sandbox", "warn", "bwrap missing or user namespaces blocked",
                            "install bubblewrap and enable user namespaces"))

    checks.append(_check_db(db_path))
    checks.extend(_check_config(Path(config_path), fix))

    try:
        lost = stale(fix)
    except (OSError, sqlite3.Error, ImportError) as exc:
        checks.append(Check("stale tasks", "warn", f"could not check: {exc}"))
    else:
        if not lost:
            checks.append(Check("stale tasks", "ok", "none"))
        elif fix:
            checks.append(Check("stale tasks", "ok", "marked lost: " + ", ".join(lost)))
        else:
            checks.append(Check("stale tasks", "warn", "no tmux window: " + ", ".join(lost),
                                "sable doctor --fix marks them lost"))

    try:
        status, detail = palace(fix)
    except (OSError, sqlite3.Error) as exc:
        status, detail = "warn", f"could not check: {exc}"
    checks.append(Check("palace", status, detail, "" if status == "ok" else "sable doctor --fix reindexes"))
    return checks


def main(argv: list[str]) -> int:
    """CLI entry: print the table, return 1 if any check failed."""
    from rich.console import Console
    from rich.table import Table

    checks = run(fix="--fix" in argv)
    colour = {"ok": "green", "warn": "yellow", "fail": "red"}
    table = Table(title="sable doctor")
    for col in ("check", "status", "detail", "fix"):
        table.add_column(col)
    for c in checks:
        table.add_row(c.name, f"[{colour[c.status]}]{c.status}[/]", c.detail, c.fix_hint)
    Console().print(table)
    return 1 if any(c.status == "fail" for c in checks) else 0
