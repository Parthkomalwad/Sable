"""Session context persistence.

Loads and saves compressed context to/from the session_memory SQLite table.
Used on login (resume) and after each compression cycle.
"""
from __future__ import annotations

MAX_RESUME_TOKENS = 500
MAX_REVERT_TOKENS = 4000  # allow larger snapshots for revert


def load_session_context(username: str) -> str | None:
    """Load the most recent compressed context for this user.

    Only loads if the context is under 500 tokens to avoid bloating prompts.

    Args:
        username: OS username of the logged-in user.

    Returns:
        Compressed context string, or None if unavailable/too large.
    """
    try:
        from sable.core.db import Database

        db = Database()
        row = db.get_latest_session_memory(username)
        db.close()

        if row is None:
            return None

        if row["token_count"] > MAX_RESUME_TOKENS:
            return None

        return row["compressed"]
    except Exception:
        return None


def list_versions(username: str) -> list[dict]:
    """Return all saved context snapshots for this user, newest first.

    Returns list of dicts with keys: id, session_id, token_count, created_at, compressed (preview).
    """
    try:
        from sable.core.db import Database
        db = Database()
        rows = db._conn.execute(
            """SELECT id, session_id, token_count, created_at, compressed
               FROM session_memory
               WHERE username = ?
               ORDER BY id DESC LIMIT 20""",
            (username,),
        ).fetchall()
        db.close()
        return [
            {
                "id": r[0],
                "session_id": r[1],
                "token_count": r[2],
                "created_at": r[3],
                "preview": (r[4] or "")[:120].replace("\n", " "),
            }
            for r in rows
        ]
    except Exception:
        return []


def load_version(version_id: int, username: str) -> dict | None:
    """Load a specific snapshot by its DB id.

    Returns dict with compressed (str) and raw_turns (list).
    """
    try:
        from sable.core.db import Database
        import json
        db = Database()
        row = db._conn.execute(
            """SELECT compressed, raw_turns FROM session_memory
               WHERE id = ? AND username = ?""",
            (version_id, username),
        ).fetchone()
        db.close()
        if row is None:
            return None
        raw_turns = []
        if row[1]:
            try:
                raw_turns = json.loads(row[1])
            except Exception:
                pass
        return {"compressed": row[0] or "", "raw_turns": raw_turns}
    except Exception:
        return None


def save_session_context(
    session_id: str, compressed: str, raw_turns: list[dict], token_count: int
) -> None:
    """Persist a compressed context snapshot to session_memory table.

    Args:
        session_id: UUID for the current shell session.
        compressed: Compressed context string.
        raw_turns: Full raw turn list archived as JSON.
        token_count: Token count of the compressed block.
    """
    import os

    try:
        from sable.core.db import Database

        username = os.environ.get("USER", os.environ.get("USERNAME", "unknown"))
        db = Database()
        db.save_session_memory(
            session_id=session_id,
            username=username,
            compressed=compressed,
            raw_turns=raw_turns,
            token_count=token_count,
        )
        db.close()
    except Exception:
        pass  # Fail silently memory persistence is best-effort
