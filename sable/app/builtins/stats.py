"""The `/stats` builtin, plain and CSV.

Extracted from `app/repl.py` in Phase 0.5 step 3. Pure move, no logic change.
"""
from __future__ import annotations

from sable.ui.console import out as _out


def _show_stats(db) -> None:
    if db is None:
        _out("Telemetry not available.")
        return
    try:
        rows = db.get_stats(days=7)
        _out("\nToken Usage Last 7 Days")
        _out(f"{'Day':<12} {'Calls':>6} {'Tokens':>8} {'Cost':>10}")
        _out("-" * 40)
        for r in rows:
            _out(f"{r['day']:<12} {r['calls']:>6} {r['tokens'] or 0:>8} ${r['cost']:.4f}" if r["cost"] else f"{r['day']:<12} {r['calls']:>6} {r['tokens'] or 0:>8} $0.0000")
        _out("")
    except Exception as exc:
        _out(f"Stats error: {exc}")


def _show_stats_csv(db) -> None:
    if db is None:
        _out("day,calls,tokens,cost")
        return
    try:
        rows = db.get_stats(days=7)
        _out("day,calls,tokens,cost")
        for r in rows:
            _out(f"{r['day']},{r['calls']},{r['tokens'] or 0},{r['cost'] or 0:.6f}")
    except Exception as exc:
        _out(f"Stats error: {exc}")
