"""The memory palace: what Sable has learned about this server (Phase 7, C6).

One fact per markdown file under `~/.sable/palace/<room>/<id>.md`, with a
small `key: value` header above a `---` line. The files are the truth; an
FTS5 index in the session database makes them searchable and can be rebuilt
from the files at any time (`reindex`).

Rooms: `server`, `user`, `incidents`, `repos/<name>`. Tiers: `episodic`
(seen once, from a goal) and `semantic` (confirmed, or written by you).
A recalled fact is untrusted input: agents get it framed as data.
"""
from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from sable.core.paths import SABLE_HOME

ROOT = SABLE_HOME / "palace"
DB: Path | None = None  # None: the session database
TOP_ROOMS = ("server", "user", "incidents")
TIERS = ("episodic", "semantic")
_REPO = re.compile(r"repos/[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}\Z")
_WORD = re.compile(r"[A-Za-z0-9_]{2,}")


@dataclass
class Fact:
    id: str
    room: str
    text: str
    tier: str = "episodic"
    untrusted: bool = False
    created: str = ""
    valid_to: str = ""
    sources: list = field(default_factory=list)


def check_room(room: str) -> str:
    if room in TOP_ROOMS or _REPO.match(room or ""):
        return room
    raise ValueError(f"room must be one of {', '.join(TOP_ROOMS)} or repos/<name>, not {room!r}")


@contextlib.contextmanager
def _db():
    """A connection that commits on success and is always closed."""
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _connect() -> sqlite3.Connection:
    if DB is None:
        from sable.core.db import DB_PATH
        path = DB_PATH
    else:
        path = DB
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS palace_fts USING fts5(id UNINDEXED, room UNINDEXED, text)")
    return conn


def _path(fact_id: str, room: str) -> Path:
    return ROOT / room / f"{fact_id}.md"


def _write(f: Fact) -> None:
    p = _path(f.id, f.room)
    p.parent.mkdir(parents=True, exist_ok=True)
    header = (f"id: {f.id}\nroom: {f.room}\ntier: {f.tier}\nuntrusted: {'yes' if f.untrusted else 'no'}\n"
              f"created: {f.created}\nvalid_to: {f.valid_to}\nsources: {json.dumps(f.sources)}\n---\n")
    p.write_text(header + f.text + "\n", encoding="utf-8")


def _read(p: Path) -> Fact | None:
    try:
        head, _, body = p.read_text(encoding="utf-8").partition("\n---\n")
    except OSError:
        return None
    meta = dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)
    try:
        sources = json.loads(meta.get("sources") or "[]")
    except ValueError:
        sources = []
    if "id" not in meta or "room" not in meta:
        return None
    return Fact(id=meta["id"], room=meta["room"], text=body.strip(), tier=meta.get("tier", "episodic"),
                untrusted=meta.get("untrusted") == "yes", created=meta.get("created", ""),
                valid_to=meta.get("valid_to", ""), sources=sources)


def _index(conn, f: Fact) -> None:
    conn.execute("DELETE FROM palace_fts WHERE id = ?", (f.id,))
    conn.execute("INSERT INTO palace_fts (id, room, text) VALUES (?, ?, ?)", (f.id, f.room, f.text))


def _all_files():
    if not ROOT.exists():
        return []
    return [p for p in ROOT.rglob("*.md") if p.is_file()]


def _find(fact_id: str) -> Path | None:
    if not re.fullmatch(r"f[0-9a-f]{12}", fact_id or ""):
        return None
    return next((p for p in _all_files() if p.stem == fact_id), None)


def remember(text: str, room: str, source: dict, *, tier: str = "episodic",
             untrusted: bool = False, valid_to: str = "") -> str:
    """Store a fact and return its id. The same text in the same room is one
    fact: a second call adds its source instead of a duplicate."""
    from sable.policy.engine import redact_text

    check_room(room)
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}")
    text = redact_text(" ".join(str(text).split()))[:2000]
    if not text:
        raise ValueError("a fact needs some text")
    fact_id = "f" + hashlib.sha1(f"{room}\n{text.lower()}".encode()).hexdigest()[:12]
    existing = _read(_path(fact_id, room)) if _path(fact_id, room).exists() else None
    if existing:
        if source not in existing.sources:
            existing.sources.append(source)
        existing.untrusted = existing.untrusted and untrusted  # one clean source clears it
        if tier == "semantic":
            existing.tier = "semantic"
        f = existing
    else:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        f = Fact(id=fact_id, room=room, text=text, tier=tier, untrusted=untrusted,
                 created=now, valid_to=valid_to, sources=[source])
    _write(f)
    with _db() as conn:
        _index(conn, f)
    return fact_id


def _expired(f: Fact) -> bool:
    return bool(f.valid_to) and f.valid_to < datetime.date.today().isoformat()


def recall(query: str, room: str | None = None, k: int = 8) -> list[Fact]:
    """The facts most relevant to `query`, best first; expired ones skipped."""
    words = _WORD.findall(query or "")
    if not words:
        return []
    match = " OR ".join(f'"{w}"' for w in words)
    sql = "SELECT id, room FROM palace_fts WHERE palace_fts MATCH ?"
    args: list = [match]
    if room:
        check_room(room)
        sql += " AND room = ?"
        args.append(room)
    sql += " ORDER BY bm25(palace_fts) LIMIT ?"
    args.append(k * 3)
    with _db() as conn:
        rows = conn.execute(sql, args).fetchall()
    out = []
    for fact_id, fact_room in rows:
        f = _read(_path(fact_id, fact_room))
        if f and not _expired(f):
            out.append(f)
        if len(out) == k:
            break
    return out


def why(fact_id: str) -> Fact | None:
    p = _find(fact_id)
    return _read(p) if p else None


def forget(fact_id: str) -> bool:
    p = _find(fact_id)
    if p is None:
        return False
    p.unlink()
    with _db() as conn:
        conn.execute("DELETE FROM palace_fts WHERE id = ?", (fact_id,))
    return True


def all_facts(room: str | None = None) -> list[Fact]:
    facts = [f for f in (_read(p) for p in _all_files()) if f]
    return sorted((f for f in facts if room is None or f.room == room), key=lambda f: (f.room, f.created))


def rooms() -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in all_facts():
        counts[f.room] = counts.get(f.room, 0) + 1
    return dict(sorted(counts.items()))


def reindex() -> int:
    """Rebuild the index from the files; returns how many facts it holds."""
    facts = all_facts()
    with _db() as conn:
        conn.execute("DELETE FROM palace_fts")
        for f in facts:
            _index(conn, f)
    return len(facts)


def save(f: Fact) -> None:
    """Rewrite a fact after an in-place change (consolidation)."""
    check_room(f.room)
    _write(f)
    with _db() as conn:
        _index(conn, f)
