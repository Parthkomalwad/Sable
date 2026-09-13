"""K3: record what the user corrected, so the shell can learn from it.

Two sources feed this table:

- An `e`-edit at the orchestrator's confirm prompt, where the user rewrote a
  command the model proposed. The (proposed, corrected) pair is the signal.
- An answer to the `[b/a]` routing prompt, where the user told the router
  which way a line should have gone. That keeps writing its TSV corpus row
  as well; this is an addition, not a replacement.

**Redaction and the decision to withhold.** docs/contracts.md §3 fixes the
rule that model-adjacent text is redacted on the way in, so the table is safe
to read and to export. Applying it literally here would be worse than
useless, for a reason that only showed up against the real redactor:
`policy/engine.py:strip_secrets` ends in `" ".join(tokens)`, so it normalises
whitespace. For the prose in `agent_turns` that costs nothing. For a shell
command it is corrupting: `awk -F'\t'` and any alignment inside a quoted
string come back altered, and a correction corpus that silently rewrites its
own commands is worth much less than one that is honest about what it holds.

So a pair whose redaction fires anything is **withheld entirely** rather than
stored in a lossy form, and the withholding is counted so `/corrections` can
say why the number is lower than expected. Rows that are kept are byte-exact.

This does not make the table airtight, and the limitation is deliberate
rather than overlooked: every shipped secret pattern is assignment-shaped
(`api_key=`, `Bearer `), and the entropy fallback needs > 4.5, so a bare
low-entropy token such as `sk-ant-api03-ZZZyyy...` (4.40) survives. Widening
those patterns is out of scope here: they gate every command in the shell and
a hasty regex there is a far worse bug than a missing corpus row.
`tests/unit/test_corrections.py:test_bare_token_gap_is_known` pins the hole
so it stays visible.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone

#: Kinds of correction. Plain strings, stored as text, for the same reason
#: `EventKind` uses strings: they are read by other processes and an unknown
#: value must be carried rather than rejected.
KIND_EDIT = "edit"
KIND_ROUTE = "route"


def record_correction(db, proposed: str, corrected: str, kind: str = KIND_EDIT) -> bool:
    """Store one (proposed, corrected) pair. Returns True if a row was written.

    Returns False, having written nothing, when the correction is empty, when
    it changed nothing of substance, or when either side carries a secret. A
    withheld secret still increments the withheld counter so the user can see
    that something was dropped and why.

    Never raises. A correction is a side effect of the user getting on with
    their work, and failing to record one must not interrupt that.
    """
    if db is None:
        return False

    proposed = (proposed or "").strip()
    corrected = (corrected or "").strip()

    # Nothing to learn from: no edit, or only whitespace moved.
    if not corrected or proposed == corrected:
        return False

    if _contains_secret(proposed) or _contains_secret(corrected):
        _bump_withheld(db)
        return False

    try:
        db._conn.execute(
            """
            INSERT INTO skill_corrections (ts, kind, proposed, corrected, withheld)
            VALUES (?, ?, ?, ?, 0)
            """,
            (_now(), kind, proposed, corrected),
        )
        db._conn.commit()
        return True
    except (sqlite3.Error, AttributeError):
        # An unwritable or missing telemetry database costs a corpus row, not
        # the user's command. The same posture as the audit log and the bus.
        return False


def list_corrections(db, limit: int = 50) -> list[dict]:
    """Stored corrections, newest first. Withheld markers are not included."""
    if db is None:
        return []
    try:
        rows = db._conn.execute(
            """
            SELECT id, ts, kind, proposed, corrected
              FROM skill_corrections
             WHERE withheld = 0
             ORDER BY id DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except (sqlite3.Error, AttributeError):
        return []
    return [
        {"id": r[0], "ts": r[1], "kind": r[2], "proposed": r[3], "corrected": r[4]}
        for r in rows
    ]


def delete_correction(db, correction_id: int) -> bool:
    """Delete one correction by id. True if a row went away."""
    if db is None:
        return False
    try:
        cur = db._conn.execute(
            "DELETE FROM skill_corrections WHERE id = ? AND withheld = 0",
            (correction_id,),
        )
        db._conn.commit()
        return cur.rowcount > 0
    except (sqlite3.Error, AttributeError):
        return False


def weekly_count(db) -> int:
    """Corrections stored in the last 7 days. Backs the sidebar counter."""
    return _count_since(db, withheld=0)


def withheld_count(db) -> int:
    """Corrections dropped in the last 7 days for carrying a secret."""
    return _count_since(db, withheld=1)


def _count_since(db, withheld: int, days: int = 7) -> int:
    if db is None:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        row = db._conn.execute(
            "SELECT COUNT(*) FROM skill_corrections WHERE withheld = ? AND ts >= ?",
            (withheld, cutoff),
        ).fetchone()
    except (sqlite3.Error, AttributeError):
        # The sidebar renders this every few seconds; a broken database shows
        # a zero rather than taking the pane down.
        return 0
    return row[0] if row else 0


def _bump_withheld(db) -> None:
    """Record that a correction was dropped, without storing what it said.

    The text is nulled rather than redacted: the whole point of withholding
    is that the redacted form is not trustworthy enough to keep.
    """
    try:
        db._conn.execute(
            """
            INSERT INTO skill_corrections (ts, kind, proposed, corrected, withheld)
            VALUES (?, ?, NULL, NULL, 1)
            """,
            (_now(), "withheld"),
        )
        db._conn.commit()
    except (sqlite3.Error, AttributeError):
        # Losing the marker for a row we already refused to store is not worth
        # surfacing to the user.
        pass


def _contains_secret(text: str) -> bool:
    """True when redaction would have fired on this text.

    Imported here rather than at module scope only for symmetry with the rest
    of the module's lazy imports; `skills` sits above `policy` in the layering
    so a module-level import would also be legal.
    """
    from sable.policy.engine import strip_secrets

    try:
        _, count = strip_secrets(text)
    except (re.error, ValueError):
        # A malformed pattern is a policy-file problem. Treat the text as
        # suspect rather than storing something we could not check.
        return True
    return count > 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
