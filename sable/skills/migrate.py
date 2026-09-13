"""Migrating pre-B2 flat skill files into folders.

Before B2 a skill was `~/skills/instructions/<slug>.md`: bare markdown, no
frontmatter, described only by its first heading. B2 makes it
`~/skills/<slug>/SKILL.md` carrying the contract in `skills/model.py`.
Every skill a user already has is in the old shape, and Sable is their login
shell, so this runs on the way in and must never be the reason a shell fails
to start.

Three rules this module holds.

**The original is kept.** The flat file stays where it is for one release,
the same precedent the `shell/` compat shim set. Inferring frontmatter is a
guess: the description comes from the first heading, and there is no honest
source for triggers or a validator. If the guess is wrong, the user still
has the file they wrote. Removing the originals is a later, separate change.

**Nothing is silently disabled.** A migrated skill lands `enabled`, not
`pending`. Phase 2's approval gate is about skills the *machine* drafted;
applying it retroactively to work a human already had running would be
indistinguishable, from the user's side, from this migration having lost
them.

**One bad file does not stop the rest.** A skill is not safety-critical, so
unlike `policy/rules.py` this reports failures and carries on rather than
raising. The caller decides whether to show them; nothing here prints.

The index moves with the files. A `SkillIndex` entry carries the path its
skill lives at, and leaving those aimed at the flat copies would mean index
and disk disagree the moment the originals are removed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sable.skills.model import Skill, parse_skill, render_skill

#: `~/skills/<slug>/SKILL.md`, the B2 layout.
SKILLS_ROOT = Path.home() / "skills"

#: `~/skills/instructions/<slug>.md`, the pre-B2 layout this reads from.
FLAT_SKILLS_DIR = SKILLS_ROOT / "instructions"

#: The filename inside each skill folder.
SKILL_FILENAME = "SKILL.md"


@dataclass(frozen=True)
class MigrationResult:
    """What happened to one flat skill file.

    Carries `reason` whether or not it succeeded, so a caller rendering a
    report never has to reconstruct why something was skipped.
    """

    name: str
    migrated: bool
    reason: str = ""
    path: Path | None = None


def _index_entries(index_path: Path) -> list[dict]:
    """Read the index, or an empty list if there is not a usable one.

    A missing or corrupt index is not a reason to refuse to migrate: the
    files are what matter, and the index is derived from them.
    """
    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return raw if isinstance(raw, list) else []


def _repoint_index(index_path: Path, moved: dict[str, Path]) -> None:
    """Aim each migrated skill's index entry at its new `SKILL.md`.

    Only the `file` field changes. Confidence, use_count and last_used are
    earned scores and a migration is a move, not a reset.
    """
    if not moved:
        return
    entries = _index_entries(index_path)
    if not entries:
        return

    changed = False
    for entry in entries:
        new_path = moved.get(entry.get("name", ""))
        if new_path is not None and entry.get("file") != str(new_path):
            entry["file"] = str(new_path)
            changed = True

    if not changed:
        return
    try:
        index_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    except OSError:
        # The files migrated; only the index pointer is stale. Losing the
        # shell over a bookkeeping write would be a worse outcome than an
        # entry that has to be repointed on the next run.
        pass


def migrate_flat_skills(index_path: str | None = None) -> list[MigrationResult]:
    """Migrate every flat skill file into a folder. Returns what happened.

    Idempotent: this runs on first use, which in practice means on every
    startup. A skill folder that already exists is left alone and reported
    as skipped, because the folder is the live copy and may have been edited
    since.
    """
    flat_dir = FLAT_SKILLS_DIR
    if not flat_dir.is_dir():
        # A fresh install has never had one.
        return []

    results: list[MigrationResult] = []
    moved: dict[str, Path] = {}

    for flat_file in sorted(flat_dir.glob("*.md")):
        name = flat_file.stem
        folder = SKILLS_ROOT / name
        target = folder / SKILL_FILENAME

        if target.exists():
            results.append(MigrationResult(
                name=name,
                migrated=False,
                reason=f"{target} already exists; the folder is the live copy",
                path=target,
            ))
            continue

        try:
            text = flat_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            results.append(MigrationResult(
                name=name, migrated=False, reason=f"could not read {flat_file}: {exc}"
            ))
            continue

        # `parse_skill` owns the inference from a flat file, so the loader
        # and this migration cannot disagree about what an old file means.
        # A legacy parse yields status "enabled", which is the rule above.
        parsed = parse_skill(text, name=name)
        skill = Skill(
            name=parsed.name,
            # A flat file with no heading infers no description, but the
            # contract requires one: without this the migration writes a
            # SKILL.md that `parse_skill` then refuses to read back, so the
            # skill survives migration and disappears on the next startup.
            # The slug is a poor description and an honest one, and the user
            # can improve it with `/skill edit`.
            description=parsed.description or name,
            body=parsed.body,
            status=parsed.status,
            # A flat file predates any record of provenance, so the honest
            # answer is that a human is responsible for it. Phase 8's K8
            # sets a policy floor by source, which makes this load-bearing.
            source="user",
        )

        try:
            folder.mkdir(parents=True, exist_ok=True)
            target.write_text(render_skill(skill), encoding="utf-8")
        except OSError as exc:
            results.append(MigrationResult(
                name=name, migrated=False, reason=f"could not write {target}: {exc}"
            ))
            continue

        moved[name] = target
        results.append(MigrationResult(
            name=name, migrated=True, reason="migrated to a folder", path=target
        ))

    resolved_index = Path(index_path) if index_path else SKILLS_ROOT / "skills_index.json"
    _repoint_index(resolved_index, moved)

    return results
