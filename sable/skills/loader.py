"""TaskSkillLoader: choosing which skills a worker sees.

Resolution order, highest first:

1. **Local** task skills, `~/tasks/<name>/.agentic/skills/`, in either the
   folder or the flat layout. These override a global skill of the same name.
2. **Global** folder skills, `~/skills/<slug>/SKILL.md`, ranked by
   `SkillIndex.get_ranked()`.
3. **Global** flat skills, `~/skills/instructions/<slug>.md`, the pre-B2
   layout that Task 2's migration leaves in place for one release.

Before B1 this matched goal words against a filename and a first line and
returned everything that hit, in arbitrary order. The confidence the index
had tracked since Phase 3 was never consulted: the scoring loop was
write-only. Global skills now come back best-first, and an unapproved draft
does not come back at all.

Two things this module is deliberate about.

**The gate does not depend on the index.** A skill file carries its own
`status`, and that is checked whether or not the index has an entry for it.
If the filter lived only in `get_ranked()`, deleting `skills_index.json`
would enable every pending draft at once, which is precisely the failure the
approval gate exists to prevent.

**A missing index is not an error.** A fresh install, or a worker spawned
before anything was crystallised, falls back to keyword matching over the
files on disk. Returning nothing instead would look exactly like "this shell
has no skills", which is a lie a user cannot debug.

The returned dicts keep the shape `agents/worker.py` reads: `name`,
`content`, `hash`, `source`. `content` is the body only. Frontmatter is
bookkeeping for the shell, not instruction for the model, and this is the
most expensive context there is.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from sable.skills.model import SkillFormatError, parse_skill

#: `~/skills/<slug>/SKILL.md` (B2) and `~/skills/instructions/<slug>.md` (pre-B2).
SKILLS_ROOT = Path.home() / "skills"

#: The filename inside a skill folder.
SKILL_FILENAME = "SKILL.md"

#: The pre-B2 flat layout, still read because Task 2 keeps the originals.
FLAT_SKILLS_DIR = SKILLS_ROOT / "instructions"


class TaskSkillLoader:
    def __init__(self, task_name: str, tasks_base_dir: str,
                 index_path: str | None = None) -> None:
        self._local_dir = (
            Path(tasks_base_dir).expanduser() / task_name / ".agentic" / "skills"
        )
        self._index_path = index_path

    # ------------------------------------------------------------------
    # Reading one skill
    # ------------------------------------------------------------------

    @staticmethod
    def _read(path: Path, name: str, source: str) -> dict | None:
        """Parse one skill file into the dict the worker consumes.

        Returns None for anything unreadable, unparseable or not enabled.
        One bad skill must not cost a worker the rest of them.
        """
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        try:
            skill = parse_skill(text, name=name)
        except SkillFormatError:
            # Malformed frontmatter. Skipped rather than raised: the worker
            # is headless and a broken skill file is not its problem to fix.
            return None

        # Checked here, not only in the index, so the gate survives a missing
        # or deleted index. See the module docstring.
        if not skill.is_enabled:
            return None

        return {
            "name": skill.name or name,
            "content": skill.body,
            "hash": hashlib.sha256(skill.body.encode()).hexdigest(),
            "source": source,
            # Carried separately rather than left in `content`: the body is
            # what reaches the model, and frontmatter there would be tokens
            # spent on bookkeeping. Keyword matching needs the description
            # though, because after B2 it lives in frontmatter and a body
            # has no reason to repeat it.
            "description": skill.description,
            # B5: the command that decides whether using this skill worked.
            # Carried here because the worker grades at its terminal state,
            # by which point the file is no longer open.
            "validate": skill.validate,
        }

    def _candidate_paths(self, directory: Path) -> list[tuple[str, Path]]:
        """Every skill file in a directory, as (name, path), folders first.

        A folder skill shadows a flat file of the same name: after migration
        both exist, and the folder is the live copy.
        """
        if not directory.is_dir():
            return []

        found: dict[str, Path] = {}
        for flat in sorted(directory.glob("*.md")):
            found[flat.stem] = flat
        for folder in sorted(p for p in directory.iterdir() if p.is_dir()):
            skill_file = folder / SKILL_FILENAME
            if skill_file.is_file():
                found[folder.name] = skill_file
        return sorted(found.items())

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    @staticmethod
    def _keywords(goal: str) -> set[str]:
        return {w.lower().strip(".,?!") for w in goal.split() if len(w) > 2}

    def _matches(self, skill: dict, keywords: set[str]) -> bool:
        """The keyword test, used for local skills and the no-index fallback.

        Local skills have no index entry to rank by, and the fallback path
        runs when there is no index at all.

        Matched against the name and the description. Before B2 the
        description *was* the file's first line, so matching that line was
        the same thing; B2 moved it into frontmatter, which `_read` strips,
        so reading the first body line now tests against text that has no
        reason to contain the goal words. The first body line is still
        consulted for a legacy flat file, where it remains the description.
        """
        name = skill["name"].lower()
        description = (skill.get("description") or "").lower()

        first_line = ""
        for line in skill["content"].splitlines():
            if line.strip():
                first_line = line.lstrip("#").strip().lower()
                break

        return any(
            kw in name or kw in description or kw in first_line
            for kw in keywords
        )

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def load_relevant(self, goal: str) -> list[dict]:
        """Skills relevant to this goal, best first, local overriding global.

        Global skills are ranked by the index when there is one. Pending and
        disabled skills are never returned.
        """
        keywords = self._keywords(goal)
        ordered: list[dict] = []
        seen: set[str] = set()

        # Local first: a task-local skill outranks anything global, and has
        # no index entry to be ranked by.
        for name, path in self._candidate_paths(self._local_dir):
            skill = self._read(path, name, "local")
            if skill is not None and self._matches(skill, keywords):
                ordered.append(skill)
                seen.add(skill["name"])

        # Flat first, then folders on top: after migration both exist and the
        # folder is the live copy. Applying `instructions/` second would let
        # the stale flat original win, which is the opposite of the rule.
        global_files = dict(self._candidate_paths(SKILLS_ROOT / "instructions"))
        global_files.update(dict(self._candidate_paths(SKILLS_ROOT)))

        for name, skill in self._ranked_global(goal, keywords, global_files):
            if name not in seen:
                ordered.append(skill)
                seen.add(name)

        return ordered

    def _ranked_global(self, goal: str, keywords: set[str],
                       files: dict[str, Path]) -> list[tuple[str, dict]]:
        """Global skills in the order the index chose, or keyword order."""
        index = self._load_index()

        if index is None:
            # No usable index. Keyword match over the files on disk, in name
            # order so the result is at least deterministic.
            out = []
            for name, path in sorted(files.items()):
                skill = self._read(path, name, "global")
                if skill is not None and self._matches(skill, keywords):
                    out.append((name, skill))
            return out

        out = []
        for entry in index.get_ranked(sorted(keywords)):
            name = entry.get("name", "")
            path = files.get(name)
            if path is None:
                # Indexed but not on disk. Index and files drift apart, and
                # the file is what can actually be injected.
                continue
            skill = self._read(path, name, "global")
            if skill is not None:
                out.append((name, skill))
        return out

    def _load_index(self):
        """The SkillIndex, or None when there is not a usable one.

        Imported lazily so that constructing a loader does not read the
        index file, and so a broken index degrades to keyword matching
        rather than failing the worker's turn.
        """
        from sable.skills.index import SkillIndex

        path = self._index_path or str(SKILLS_ROOT / "skills_index.json")
        if not Path(path).is_file():
            return None
        try:
            return SkillIndex(index_path=path)
        except (OSError, ValueError):
            # ValueError covers a corrupt JSON payload, which json.loads
            # raises as JSONDecodeError, a ValueError subclass.
            return None
