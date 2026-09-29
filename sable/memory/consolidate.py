"""Nightly palace consolidation (Phase 7 Task 3, C3). Rules only, no model.

1. Expire facts past `valid_to`.
2. Drop episodic facts older than 30 days (never promoted).
3. Merge near-duplicates within a room (token Jaccard >= 0.8); the older
   fact keeps its text and id and gains the other's sources.
4. Promote episodic facts seen in two or more sessions to semantic.

Running it twice in a row changes nothing the second time.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sable.memory import palace

log = logging.getLogger("sabled.consolidate")
STALE = timedelta(days=30)
THRESHOLD = 0.8
_TOKEN = re.compile(r"[a-z0-9]{2,}")
_STOP = frozenset("the an and or of in on at to is are was were be it its for with by as from this that".split())


@dataclass
class Summary:
    expired: int = 0
    dropped: int = 0
    merged: int = 0
    promoted: int = 0
    changes: list = field(default_factory=list)

    def __str__(self) -> str:
        """What `/palace consolidate` prints."""
        head = (f"consolidated: {self.merged} merged, {self.promoted} promoted, "
                f"{self.expired} expired, {self.dropped} dropped")
        return "\n".join([head, *(f"  {c}" for c in self.changes)])


def _publish(payload: dict) -> None:
    from sable.core.events.bus import EventBus
    try:
        with EventBus() as bus:
            bus.publish("daemon", "palace.consolidated", payload)
    except sqlite3.Error:
        pass


def _tokens(text: str) -> frozenset:
    return frozenset(_TOKEN.findall(text.lower())) - _STOP


def _similar(a: frozenset, b: frozenset) -> bool:
    return bool(a | b) and len(a & b) / len(a | b) >= THRESHOLD


def _created(f: palace.Fact) -> datetime | None:
    try:
        dt = datetime.fromisoformat(f.created)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def run(now: datetime | None = None) -> Summary:
    now = (now or datetime.now(timezone.utc)).astimezone()
    today = now.date().isoformat()
    s = Summary()

    def change(rule: str, f: palace.Fact, what: str, **extra) -> None:
        s.changes.append(what)
        _publish({"rule": rule, "id": f.id, "room": f.room, **extra})

    live = []
    for f in palace.all_facts():
        if f.valid_to and f.valid_to < today:
            palace.forget(f.id)
            s.expired += 1
            change("expire", f, f"expired {f.id} ({f.room}): valid until {f.valid_to}")
            continue
        created = _created(f)
        if f.tier == "episodic" and created and now - created > STALE:
            palace.forget(f.id)
            s.dropped += 1
            change("drop", f, f"dropped {f.id} ({f.room}): episodic since {f.created[:10]}")
            continue
        live.append(f)

    # all_facts sorts by (room, created), so the first of a pair is the older.
    # ponytail: O(n^2) per room, fine for hundreds of facts; bucket by token if rooms grow large.
    kept: list[tuple[palace.Fact, frozenset]] = []
    for f in live:
        toks = _tokens(f.text)
        into = next((k for k, kt in kept if k.room == f.room and _similar(kt, toks)), None)
        if into is None:
            kept.append((f, toks))
            continue
        into.sources += [src for src in f.sources if src not in into.sources]
        if f.tier == "semantic":
            into.tier = "semantic"
        into.untrusted = into.untrusted and f.untrusted
        palace.save(into)
        palace.forget(f.id)
        s.merged += 1
        change("merge", into, f"merged {f.id} into {into.id} ({into.room})", merged=f.id)

    for f, _ in kept:
        sessions = {src.get("session") for src in f.sources if isinstance(src, dict) and src.get("session")}
        if f.tier == "episodic" and len(sessions) >= 2:
            f.tier = "semantic"
            palace.save(f)
            s.promoted += 1
            change("promote", f, f"promoted {f.id} ({f.room}): seen in {len(sessions)} sessions")

    log.info("consolidated: %d expired, %d dropped, %d merged, %d promoted",
             s.expired, s.dropped, s.merged, s.promoted)
    for line in s.changes:
        log.info("  %s", line)
    return s
