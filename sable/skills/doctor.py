"""Skill doctor (Phase 9 Task 1, B4). Rules only, read only.

`diagnose()` looks at the index and the files and proposes; it never changes
anything. The index keeps no per-run history, only a confidence that moves
+0.05 / -0.1 per run, so "failing" is confidence below 0.3 after 3+ runs.
The nightly self-check can call `diagnose()` directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sable.memory.consolidate import _similar, _tokens
from sable.skills import signing

FAIL_CONFIDENCE = 0.3
FAIL_MIN_RUNS = 3
STALE = timedelta(days=60)


@dataclass(frozen=True)
class Finding:
    skill: str
    kind: str       # failing | stale | duplicate | unsigned | tampered
    detail: str
    proposal: str   # repair | merge | retire | sign


def _when(value) -> datetime | None:
    try:
        when = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _body(file: str) -> str:
    try:
        return Path(file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def diagnose(index, now: datetime | None = None) -> list[Finding]:
    now = now or datetime.now(timezone.utc)
    out: list[Finding] = []
    entries = index.list_all()
    for e in entries:
        name, file = e["name"], e.get("file", "")
        conf, runs = float(e.get("confidence", 0.0)), int(e.get("use_count", 0))
        if conf < FAIL_CONFIDENCE and runs >= FAIL_MIN_RUNS:
            out.append(Finding(name, "failing",
                               f"confidence {conf:.2f} after {runs} runs", "repair"))
        # A never-used skill counts from its creation, so a fresh one is not stale.
        last = _when(e.get("last_used")) or _when(e.get("created_at"))
        if last is not None and now - last > STALE:
            out.append(Finding(name, "stale",
                               f"not used in {(now - last).days} days", "retire"))
        # Flat files cannot be signed, so only folder skills are checked.
        if file and Path(file).name == "SKILL.md":
            trust = signing.trust_of(file)
            if trust != "signed":
                detail = ("changed since it was signed" if trust == "tampered"
                          else "no signature")
                out.append(Finding(name, trust, detail, "sign"))

    # ponytail: O(n^2) pair scan, fine for hundreds of skills.
    toks = [_tokens(" ".join([e["name"], *e.get("keywords", []), _body(e.get("file", ""))]))
            for e in entries]
    for i, a in enumerate(entries):
        for j in range(i + 1, len(entries)):
            if _similar(toks[i], toks[j]):
                b = entries[j]["name"]
                out.append(Finding(b, "duplicate", f"overlaps {a['name']}", "merge"))
    return out
