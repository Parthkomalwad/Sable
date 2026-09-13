"""K4: natural-language aliases, resolved before the router ever runs.

An alias is a phrase the user has already told us the answer to. Matching it
is deterministic string work: normalise, then compare with `difflib` from the
stdlib. No model is consulted, so a matched alias costs nothing and returns
instantly. That is the entire value of the feature, and it is why the match
sits in `app/repl.py` ahead of `classify()` rather than inside the agent
loop: anywhere further in and it would already have cost a turn.

`difflib` rather than a fuzzy-matching dependency because CLAUDE.md fixes the
approved dependency list and this does not need more than the stdlib offers.

**An alias is not a safety bypass.** Resolving one produces a command string
and nothing else; the caller runs it down the ordinary bash path, where
`policy/engine.py:is_destructive` gates it exactly as it would a typed
command. `rm -rf` hidden behind a friendly phrase still asks.

**Promotion is offered, never taken.** An alias that has earned its keep (3
uses) is worth turning into a skill, but a skill is something a model reads
and acts on, so a human says yes. `should_offer_promotion` reports when to
ask; `mark_promotion_offered` records that we asked, so an ignored offer does
not nag on every later use.
"""
from __future__ import annotations

import difflib
import re
import sqlite3
from datetime import datetime, timezone

#: Below this similarity a phrase is not an alias and the line goes to the
#: router untouched. High on purpose: silently running the wrong command
#: because a sentence looked a bit like a stored phrase is far worse than
#: making the user type the phrase again.
SIMILARITY_THRESHOLD = 0.9

#: Uses before promotion to a skill is offered.
PROMOTION_THRESHOLD = 3

_PUNCTUATION = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")


def normalise(phrase: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    Matching on the normalised form is what lets "Restart the API!" find
    "restart the api" without any model involvement.
    """
    text = (phrase or "").lower()
    text = _PUNCTUATION.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def add_alias(db, phrase: str, command: str) -> bool:
    """Store a phrase and the command it stands for. True if stored.

    Re-adding a phrase replaces its command: an alias is a single mapping and
    two rows for one phrase would make matching ambiguous.
    """
    if db is None:
        return False

    phrase = (phrase or "").strip()
    command = (command or "").strip()
    if not phrase or not command:
        return False

    try:
        db._conn.execute(
            """
            INSERT INTO skill_aliases
                (phrase, normalised, command, use_count, promoted_offered, created_at)
            VALUES (?, ?, ?, 0, 0, ?)
            ON CONFLICT(normalised) DO UPDATE SET
                phrase = excluded.phrase,
                command = excluded.command
            """,
            (phrase, normalise(phrase), command, _now()),
        )
        db._conn.commit()
        return True
    except (sqlite3.Error, AttributeError):
        return False


def list_aliases(db) -> list[dict]:
    """Every stored alias, most used first."""
    if db is None:
        return []
    try:
        rows = db._conn.execute(
            """
            SELECT id, phrase, command, use_count, promoted_offered
              FROM skill_aliases
             ORDER BY use_count DESC, id ASC
            """
        ).fetchall()
    except (sqlite3.Error, AttributeError):
        return []
    return [
        {"id": r[0], "phrase": r[1], "command": r[2],
         "use_count": r[3], "promoted_offered": bool(r[4])}
        for r in rows
    ]


def delete_alias(db, phrase: str) -> bool:
    """Remove an alias by phrase. True if one went away."""
    if db is None:
        return False
    try:
        cur = db._conn.execute(
            "DELETE FROM skill_aliases WHERE normalised = ?", (normalise(phrase),)
        )
        db._conn.commit()
        return cur.rowcount > 0
    except (sqlite3.Error, AttributeError):
        return False


def match_alias(db, line: str) -> dict | None:
    """The alias this line resolves to, or None to fall through to the router.

    Exact normalised equality first, then the best `difflib` ratio at or above
    `SIMILARITY_THRESHOLD`. Makes no LLM call and touches no network.
    """
    if db is None:
        return None

    target = normalise(line)
    if not target:
        return None

    aliases = list_aliases(db)
    if not aliases:
        return None

    best: dict | None = None
    best_score = 0.0
    for entry in aliases:
        candidate = normalise(entry["phrase"])
        if candidate == target:
            return entry
        score = difflib.SequenceMatcher(None, target, candidate).ratio()
        if score > best_score:
            best, best_score = entry, score

    if best is not None and best_score >= SIMILARITY_THRESHOLD:
        return best
    return None


def record_use(db, phrase: str) -> None:
    """Count one use of an alias. Never raises."""
    if db is None:
        return
    try:
        db._conn.execute(
            "UPDATE skill_aliases SET use_count = use_count + 1 WHERE normalised = ?",
            (normalise(phrase),),
        )
        db._conn.commit()
    except (sqlite3.Error, AttributeError):
        # Losing a use count costs a promotion offer, not the user's command.
        pass


def should_offer_promotion(db, phrase: str) -> bool:
    """True when this alias has earned an offer to become a skill.

    False once the offer has been made, whatever the user answered: an offer
    the user ignored must not reappear on every subsequent use.
    """
    entry = _get(db, phrase)
    if entry is None:
        return False
    return entry["use_count"] >= PROMOTION_THRESHOLD and not entry["promoted_offered"]


def mark_promotion_offered(db, phrase: str) -> None:
    """Record that promotion was offered, so it is not offered again."""
    if db is None:
        return
    try:
        db._conn.execute(
            "UPDATE skill_aliases SET promoted_offered = 1 WHERE normalised = ?",
            (normalise(phrase),),
        )
        db._conn.commit()
    except (sqlite3.Error, AttributeError):
        pass


def _get(db, phrase: str) -> dict | None:
    if db is None:
        return None
    normalised = normalise(phrase)
    for entry in list_aliases(db):
        if normalise(entry["phrase"]) == normalised:
            return entry
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
