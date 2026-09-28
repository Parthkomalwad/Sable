"""A 5-field cron matcher, stdlib only (Phase 5, E2; plan section 0: no croniter).

`minute hour day-of-month month day-of-week`, with `*`, lists, ranges, steps
and month and weekday names. Weekday 0 and 7 are both Sunday. When both day
fields are restricted a time matches if either does, as in Vixie cron.
"""
from __future__ import annotations

from datetime import datetime

_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_DAYS = "sun mon tue wed thu fri sat".split()
_FIELDS = (  # name, low, high, names, offset (name index + offset = value)
    ("minute", 0, 59, None, 0),
    ("hour", 0, 23, None, 0),
    ("day of month", 1, 31, None, 0),
    ("month", 1, 12, _MONTHS, 1),
    ("day of week", 0, 7, _DAYS, 0),
)


def _value(text: str, names, offset: int) -> int:
    if names and text.lower() in names:
        return names.index(text.lower()) + offset
    return int(text)


def _field(text: str, name: str, lo: int, hi: int, names, offset: int) -> set[int]:
    out: set[int] = set()
    try:
        for part in text.split(","):
            rng, _, step = part.partition("/")
            step_n = int(step) if step else 1
            if rng == "*":
                a, b = lo, hi
            elif "-" in rng:
                a, b = (_value(x, names, offset) for x in rng.split("-", 1))
            else:
                a = b = _value(rng, names, offset)
                if step:
                    b = hi
            if step_n < 1 or not lo <= a <= b <= hi:
                raise ValueError
            out.update(range(a, b + 1, step_n))
    except ValueError:
        raise ValueError(f"bad cron {name} field: {text!r}") from None
    return out


def parse(expr: str) -> list[set[int]]:
    """Each field as its set of values. Raises ValueError naming the bad field."""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron needs 5 fields, got {len(parts)}: {expr!r}")
    sets = [_field(p, *f) for p, f in zip(parts, _FIELDS)]
    if 7 in sets[4]:
        sets[4].add(0)
    return sets


def matches(expr: str, dt: datetime) -> bool:
    minute, hour, dom, month, dow = parse(expr)
    if dt.minute not in minute or dt.hour not in hour or dt.month not in month:
        return False
    day_ok, wday_ok = dt.day in dom, (dt.isoweekday() % 7) in dow
    parts = expr.split()
    if parts[2] != "*" and parts[4] != "*":
        return day_ok or wday_ok
    return day_ok and wday_ok


def describe(expr: str) -> str:
    """Short English for the common shapes; the expression itself otherwise."""
    parse(expr)
    m, h, dom, mon, dow = expr.split()
    if (dom, mon, dow) == ("*", "*", "*"):
        if (m, h) == ("*", "*"):
            return "every minute"
        if m.startswith("*/") and h == "*":
            return f"every {m[2:]} minutes"
        if m.isdigit() and h == "*":
            return f"every hour at :{int(m):02d}"
        if m.isdigit() and h.isdigit():
            return f"every day at {int(h):02d}:{int(m):02d}"
    if m.isdigit() and h.isdigit() and dom == "*" and mon == "*":
        return f"at {int(h):02d}:{int(m):02d} on {dow}"
    return f"cron {expr}"
