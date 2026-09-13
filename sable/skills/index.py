"""SkillIndex manages skills_index.json.

Created automatically on first use (no separate init step needed).
Tracks: name, file, keywords, auto_generated, confidence, use_count,
        last_used, needs_update, status, created_at.

Confidence nudges: +0.05 on success (max 1.0), -0.1 on failure (min 0.0).
Initial: 0.5 auto-generated, 1.0 manual.

Two things Phase 2 added, both load-bearing.

**`status` gates injection.** A skill is `pending`, `enabled` or `disabled`,
and only an enabled one is ever returned by `get_ranked()`. That is the
whole of Phase 2's approval gate: the crystalliser may draft a skill
unattended, but nothing reaches a model's context until a human approves it.
`add()` still defaults to `enabled`, because every caller that exists today
writes a skill the user asked for; the crystalliser passes `pending`
deliberately rather than the default flipping underneath its callers.

An entry written before Phase 2 has no `status` key at all. A missing status
reads as enabled, not pending: treating it as pending would silently stop
injecting every skill a user already has, which is the same failure the flat
file migration was careful to avoid.

**Ranking is a product, not a sort.** `confidence x recency x use_count x
match` replaces the old two-key sort on confidence then use_count. Every
factor is bounded above zero so that a brand new approved skill, which has
no uses and no last_used, can still be retrieved; if any factor multiplied
to zero, an approved draft could never be used and so could never earn its
first success.

The weights here are a heuristic and will be tuned. The tests pin orderings
rather than scores, so tuning does not mean rewriting the suite.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

#: The states a skill can be in. Mirrors `skills/model.py:STATUSES`, which is
#: the same contract seen from the file rather than from the index.
STATUSES = frozenset({"pending", "enabled", "disabled"})

#: An entry with no `status` key predates Phase 2. See the module docstring.
_DEFAULT_STATUS = "enabled"

#: Recency half-life. A skill unused for this long ranks at half the recency
#: weight of one used today, and never below `_MIN_RECENCY`.
_RECENCY_HALF_LIFE_DAYS = 30.0

#: Floors, so no factor can zero out a product. A never-used skill has to
#: stay retrievable or it can never earn its first success.
_MIN_RECENCY = 0.25
_MIN_USE = 1.0


class SkillIndex:
    def __init__(self, index_path: str | None = None) -> None:
        if index_path is None:
            index_path = str(Path.home() / "skills" / "skills_index.json")
        self._path = Path(index_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("[]")
        self._data: list[dict] = json.loads(self._path.read_text())

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._data, indent=2))

    def _find(self, name: str) -> dict | None:
        for entry in self._data:
            if entry["name"] == name:
                return entry
        return None

    def add(self, name: str, file: str, keywords: list[str],
            auto_generated: bool, status: str = _DEFAULT_STATUS) -> None:
        if status not in STATUSES:
            raise ValueError(
                f"unknown status {status!r}; expected one of {sorted(STATUSES)}"
            )
        now = datetime.now(timezone.utc).isoformat()
        confidence = 0.5 if auto_generated else 1.0
        entry = {
            "name": name,
            "file": file,
            "keywords": keywords,
            "auto_generated": auto_generated,
            "confidence": confidence,
            "use_count": 0,
            "last_used": None,
            "needs_update": False,
            "status": status,
            "created_at": now,
        }
        for i, e in enumerate(self._data):
            if e["name"] == name:
                self._data[i] = entry
                self._save()
                return
        self._data.append(entry)
        self._save()

    def list_all(self) -> list[dict]:
        return list(self._data)

    def pending(self) -> list[dict]:
        """Drafts waiting on a human. Backs the startup line and `/skill list`."""
        return [e for e in self._data if self._status(e) == "pending"]

    # ------------------------------------------------------------------
    # Approval
    # ------------------------------------------------------------------

    def approve(self, name: str) -> bool:
        """Enable a skill. Returns False if there is no such skill.

        Deliberately does not touch confidence. Approval says "you may run
        this", not "I vouch for it": an approved draft starts where it was
        drafted and earns its way up from there.
        """
        return self._set_status(name, "enabled")

    def disable(self, name: str) -> bool:
        """Stop a skill being injected, reversibly. `approve()` undoes it."""
        return self._set_status(name, "disabled")

    def reject(self, name: str) -> bool:
        """Drop a skill from the index. Returns False if it was not there.

        The file on disk is left alone. Rejecting is not deleting: the draft
        stays readable so a user can see what was proposed and why it was
        not worth keeping.
        """
        entry = self._find(name)
        if entry is None:
            return False
        self._data.remove(entry)
        self._save()
        return True

    def _set_status(self, name: str, status: str) -> bool:
        entry = self._find(name)
        if entry is None:
            return False
        # The file first, then the index. Status lives in both: the index is
        # what `/skill list` and the startup pending-count read cheaply, and
        # the file is what keeps the gate honest when the index is deleted.
        # A crash between the two writes then leaves the skill withheld,
        # which is the safe direction to fail. The reverse order would leave
        # a skill enabled in the index that the loader still refuses,
        # unreachable with no way to clear it.
        self._write_status_to_file(entry.get("file", ""), status)
        entry["status"] = status
        self._save()
        return True

    @staticmethod
    def _write_status_to_file(file_path: str, status: str) -> None:
        """Rewrite a skill file's frontmatter status. Best effort.

        A file that is missing, unreadable, malformed or has no frontmatter
        at all is left alone and the index update still goes ahead. The skill
        is unusable either way, but a stale index entry that cannot be
        approved is worse: there would be no way to clear it.

        A pre-B2 flat file has no frontmatter to rewrite, and gains none
        here. Adding some would quietly convert it to the new format behind
        the user's back, which is the migration's job and only with the
        original kept.
        """
        if not file_path:
            return

        from sable.skills.model import SkillFormatError, parse_skill, render_skill

        path = Path(file_path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return

        try:
            skill = parse_skill(text, name=path.parent.name)
        except SkillFormatError:
            return

        if skill.is_legacy:
            return

        skill.status = status
        try:
            path.write_text(render_skill(skill), encoding="utf-8")
        except OSError:
            # The index update below still runs, so the skill can be managed
            # even on a read-only or full filesystem.
            return

    @staticmethod
    def _status(entry: dict) -> str:
        return entry.get("status", _DEFAULT_STATUS)

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    def record_use(self, name: str, success: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for entry in self._data:
            if entry["name"] == name:
                delta = 0.05 if success else -0.1
                entry["confidence"] = max(0.0, min(1.0, entry["confidence"] + delta))
                entry["use_count"] += 1
                entry["last_used"] = now
                self._save()
                return

    def nudge(self, name: str, success: bool) -> None:
        """Move a skill's confidence after a run that used it (B1).

        A named alias for `record_use`, not a second scoring path: there is
        one place confidence moves, so the bus event and the index can never
        disagree about what a run was worth. A name that is no longer in the
        index is a no-op, because grading happens after a run finishes and
        `/skill reject` may have removed it in between.
        """
        self.record_use(name, success)

    def mark_needs_update(self, name: str) -> None:
        for entry in self._data:
            if entry["name"] == name:
                entry["needs_update"] = True
                self._save()
                return

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------

    def _recency(self, entry: dict) -> float:
        """Exponential decay on last_used, floored so it never zeroes out."""
        last_used = entry.get("last_used")
        if not last_used:
            return _MIN_RECENCY
        try:
            when = datetime.fromisoformat(last_used)
        except ValueError:
            # A hand-edited or corrupt timestamp should cost the skill its
            # recency bonus, not make it unretrievable.
            return _MIN_RECENCY
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        days = max(0.0, (datetime.now(timezone.utc) - when).total_seconds() / 86400.0)
        decayed = 0.5 ** (days / _RECENCY_HALF_LIFE_DAYS)
        return max(_MIN_RECENCY, decayed)

    @staticmethod
    def _use_weight(entry: dict) -> float:
        """Diminishing returns on use_count, floored at 1.0 for a fresh skill.

        Linear would let a much-used skill drown out a better match; this
        keeps the factor meaningful without letting it dominate the product.
        """
        return _MIN_USE + float(entry.get("use_count", 0)) ** 0.5

    @staticmethod
    def _match(entry: dict, kw_set: set[str]) -> float:
        """Fraction of the goal's keywords this skill claims.

        Matching two of the caller's words beats matching one, which is what
        separates a skill written for this job from one that shares a word
        with it.
        """
        if not kw_set:
            return 0.0
        skill_kws = {k.lower() for k in entry.get("keywords", [])}
        return len(kw_set & skill_kws) / len(kw_set)

    def score(self, entry: dict, keywords: list[str]) -> float:
        """The ranking score for one entry. Exposed so `/skill why` can show it."""
        kw_set = {k.lower() for k in keywords}
        return (
            float(entry.get("confidence", 0.0))
            * self._recency(entry)
            * self._use_weight(entry)
            * self._match(entry, kw_set)
        )

    def get_ranked(self, keywords: list[str]) -> list[dict]:
        """Enabled skills matching any keyword, best first.

        Pending and disabled skills are never returned. That single filter is
        what makes the approval gate real rather than advisory.
        """
        kw_set = {k.lower() for k in keywords}
        scored: list[tuple[float, int, dict]] = []
        for position, entry in enumerate(self._data):
            if self._status(entry) != "enabled":
                continue
            if self._match(entry, kw_set) == 0.0:
                continue
            scored.append((self.score(entry, keywords), position, entry))

        # Sorting on (-score, position) keeps equally-scored skills in index
        # order across calls. An unstable order would make the Task 11
        # announcement name a different skill each run for no visible reason.
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [entry for _, _, entry in scored]
