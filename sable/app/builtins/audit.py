"""`/audit [--since 1h|30m|2d] [--agent NAME] [--export jsonl]` (F4).

Renders the `audit_ledger` table (see `core/audit.py`): who ran what, why, and
what came of it. `--export jsonl` writes the filtered rows to a timestamped
file under ~/.sable/audit/ and prints its path, so the export is not mixed
into a terminal a user may be copying from.
"""
from __future__ import annotations

import datetime
import json
import re
import sqlite3
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from sable.core import audit
from sable.ui.console import out as _out

EXPORT_DIR = Path.home() / ".sable" / "audit"
USAGE = "usage: /audit [--since 1h|30m|2d] [--agent NAME] [--export jsonl]"
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_since(text: str) -> int | None:
    """'30m' -> 1800. None for anything that is not <number><s|m|h|d>."""
    match = re.fullmatch(r"(\d+)([smhd])", text.strip())
    return int(match.group(1)) * _UNITS[match.group(2)] if match else None


def handle_audit(argument: str) -> bool:
    """Parse the flags, then print a table or write an export. Always True."""
    args = argument.split()
    since = agent = export = None
    try:
        while args:
            flag, value = args.pop(0), args.pop(0)
            if flag == "--since":
                since = parse_since(value)
                if since is None:
                    raise ValueError(value)
            elif flag == "--agent":
                agent = value
            elif flag == "--export" and value == "jsonl":
                export = value
            else:
                raise ValueError(flag)
    except (IndexError, ValueError):
        _out(USAGE)
        return True

    try:
        rows = audit.query(since_seconds=since, agent=agent)
    except (sqlite3.Error, OSError) as exc:
        _out(f"audit ledger unavailable: {exc}")
        return True

    if export:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = EXPORT_DIR / f"audit-{stamp}.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        _out(f"{len(rows)} rows -> {path}")
        return True

    if not rows:
        _out("no audit rows match")
        return True
    table = Table(title="audit ledger")
    for column in ("time", "agent", "model", "command", "tier / rule", "outcome", "exit", "ms"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            row["ts"][11:19], Text(row["agent"]), row["model"] or "-", Text(row["command"]),
            f"{row['tier'] or '-'} {row['rule'] or ''}".strip(), row["outcome"],
            "-" if row["exit_code"] is None else str(row["exit_code"]),
            "-" if row["duration_ms"] is None else str(row["duration_ms"]),
        )
    # Text cells: a command with [brackets] must print as typed, not as markup.
    Console().print(table)
    return True
