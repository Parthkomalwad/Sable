"""The `/history` builtin.

Extracted from `app/repl.py` in Phase 0.5 step 3. Pure move, no logic change.
"""
from __future__ import annotations

from sable.ui.console import out as _out


def _show_history(db, limit: int = 20) -> None:
    """Show recent command history with token costs from telemetry DB."""
    if db is None:
        _out("Telemetry not available.")
        return

    PURPLE = '\033[38;5;141m'
    DIM    = '\033[2;37m'
    GREEN  = '\033[38;5;114m'
    RESET  = '\033[0m'
    SEP    = '\033[38;5;238m' + '─' * 60 + RESET

    try:
        rows = db._conn.execute(
            """
            SELECT timestamp, action_type, nl_input, command, total_tokens, cost_usd
            FROM token_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
    except Exception as exc:
        _out(f"History error: {exc}")
        return

    if not rows:
        _out("No history yet.")
        return

    sys.stdout.write(f"\n{PURPLE}  Recent Commands{RESET}\n")
    sys.stdout.write(f"  {SEP}\n")

    for i, (ts, action_type, nl_input, command, total_tokens, cost_usd) in enumerate(reversed(rows), 1):
        try:
            date_part = ts[:10]
            time_part = ts[11:16]
            ts_display = f"{date_part} {time_part}"
        except Exception:
            ts_display = ts[:16] if ts else "?"

        if action_type == "nl_route" and nl_input:
            display = nl_input[:50]
            if len(nl_input) > 50:
                display += "…"
            if cost_usd and cost_usd > 0:
                cost_str = f"{DIM}${cost_usd:.4f} · {total_tokens} tok{RESET}"
            else:
                cost_str = f"{DIM}{total_tokens} tok{RESET}" if total_tokens else f"{GREEN}AI{RESET}"
        else:
            display = (command or nl_input or "?")[:50]
            cost_str = f"{DIM}bash{RESET}"

        sys.stdout.write(
            f"  {DIM}{i:>2}{RESET}  {DIM}{ts_display}{RESET}  "
            f"{display:<52}  {cost_str}\n"
        )

    sys.stdout.write(f"  {SEP}\n")

    try:
        today = db.get_today_stats()
        if today["calls"] > 0:
            sys.stdout.write(
                f"  {DIM}{today['calls']} AI calls today · ${today['cost']:.4f} total{RESET}\n"
            )
    except Exception:
        pass

    sys.stdout.write("\n")
    sys.stdout.flush()
