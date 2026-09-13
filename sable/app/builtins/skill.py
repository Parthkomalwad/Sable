"""The `/skill` builtin: list, show, new, edit, approve, reject, disable, stats.

Phase 2 (B2, Task 8) added everything past `new` and `edit`. Before that a
pending draft could not be approved from the shell at all: the crystalliser
wrote one and the only way to enable it was editing `skills_index.json` by
hand. `list` also has to say which skills are drafts, or the approval gate is
invisible and a user never learns there is something waiting for them.

`list` reads both layouts. B2 moved skills into `~/skills/<slug>/SKILL.md`,
and the pre-B2 flat files in `~/skills/instructions/` stay for a release, so
listing only one of them would hide half a user's library.

State changes go through `SkillIndex`, never by writing the file here. The
index writes the file and the index entry in the right order (file first, so
a crash leaves a skill withheld rather than wrongly enabled), and putting a
second copy of that rule in a builtin is how the two drift apart.
"""
from __future__ import annotations

from sable.ui.console import out as _out

_USAGE = (
    "usage: /skill <list|show <name>|new <name>|edit <name>|"
    "approve <name>|reject <name>|disable <name>|stats>"
)


def _skills_root():
    """Resolved per call, not at import: tests redirect `Path.home()`."""
    from pathlib import Path

    return Path.home() / "skills"


def _flat_dir():
    return _skills_root() / "instructions"


def _skill_path(name: str):
    """Where a skill lives, folder layout first, then the flat original."""
    folder = _skills_root() / name / "SKILL.md"
    if folder.is_file():
        return folder
    flat = _flat_dir() / f"{name}.md"
    if flat.is_file():
        return flat
    return None


def _known_skills() -> list[str]:
    """Every skill name on disk, in either layout, without duplicates."""
    names: set[str] = set()
    root = _skills_root()
    if root.is_dir():
        for entry in root.iterdir():
            if entry.is_dir() and entry.name != "instructions":
                if (entry / "SKILL.md").is_file():
                    names.add(entry.name)
    flat = _flat_dir()
    if flat.is_dir():
        names.update(p.stem for p in flat.glob("*.md"))
    return sorted(names)


def _index():
    from sable.skills.index import SkillIndex

    return SkillIndex()


def _entries_by_name() -> dict[str, dict]:
    return {e["name"]: e for e in _index().list_all()}


def _handle_skill_builtin(parts: list[str]) -> bool:
    """Handle /skill subcommands. Return True if handled."""
    import os
    import subprocess

    _flat_dir().mkdir(parents=True, exist_ok=True)

    sub = parts[0] if parts else "list"
    name = parts[1] if len(parts) >= 2 else ""

    if sub == "list":
        return _list()

    if sub == "stats":
        return _stats()

    if sub == "show":
        if not name:
            _out(_USAGE)
            return True
        return _show(name)

    if sub in ("approve", "reject", "disable"):
        if not name:
            _out(_USAGE)
            return True
        return _set_state(sub, name)

    if sub == "new" and name:
        path = _flat_dir() / f"{name}.md"
        path.write_text(f"# {name}\n\n<!-- describe when to use this skill -->\n")
        _out(f"created {path}")
        return True

    if sub == "edit" and name:
        path = _skill_path(name) or (_flat_dir() / f"{name}.md")
        editor = os.environ.get("EDITOR", "nano")
        subprocess.run([editor, str(path)])
        return True

    _out(_USAGE)
    return True


def _list() -> bool:
    names = _known_skills()
    if not names:
        _out("no skills found")
        return True

    entries = _entries_by_name()
    for skill_name in names:
        entry = entries.get(skill_name)
        if entry is None:
            # On disk but not indexed. Shown rather than hidden: the file is
            # what can actually be injected, and a silent omission here looks
            # exactly like the skill having been lost.
            _out(f"  {skill_name}  (not indexed)")
            continue

        status = entry.get("status", "enabled")
        confidence = entry.get("confidence", 0.0)
        if status == "pending":
            _out(f"  {skill_name}  draft, pending approval "
                 f"(/skill approve {skill_name})")
        elif status == "disabled":
            _out(f"  {skill_name}  {confidence:.2f}  disabled")
        else:
            _out(f"  {skill_name}  {confidence:.2f}")
    return True


def _show(name: str) -> bool:
    from sable.skills.model import SkillFormatError, parse_skill

    path = _skill_path(name)
    if path is None:
        _out(f"no skill named '{name}'")
        return True

    try:
        skill = parse_skill(path.read_text(encoding="utf-8"), name=name)
    except (OSError, UnicodeDecodeError, SkillFormatError) as exc:
        # Shown rather than swallowed: a malformed skill is something the
        # user can fix, and `/skill edit` is the next thing they will run.
        _out(f"could not read {path}: {exc}")
        return True

    _out(f"{skill.name}  ({path})")
    if skill.description:
        _out(f"  {skill.description}")
    if skill.triggers:
        _out(f"  triggers: {', '.join(skill.triggers)}")
    if skill.preconditions:
        _out(f"  preconditions: {', '.join(skill.preconditions)}")
    if skill.validate:
        _out(f"  validate: {skill.validate}")
    _out(f"  status: {skill.status}   source: {skill.source}")
    _out("")
    _out(skill.body)
    return True


def _set_state(action: str, name: str) -> bool:
    index = _index()

    if action == "reject":
        if not index.reject(name):
            _out(f"no skill named '{name}'")
            return True
        path = _skill_path(name)
        where = f" ({path.parent})" if path is not None else ""
        _out(f"rejected {name}; the file was kept{where}")
        return True

    changed = index.approve(name) if action == "approve" else index.disable(name)
    if not changed:
        _out(f"no skill named '{name}'")
        return True

    if action == "approve":
        entry = _entries_by_name().get(name, {})
        _out(f"enabled {name} (confidence {entry.get('confidence', 0.0):.2f})")
    else:
        _out(f"disabled {name}; /skill approve {name} re-enables it")
    return True


def _stats() -> bool:
    entries = _index().list_all()
    if not entries:
        _out("no skills indexed yet")
        return True

    _out("  skill                 conf   uses  status    last used")
    for entry in sorted(entries, key=lambda e: -e.get("confidence", 0.0)):
        last_used = entry.get("last_used") or "never"
        _out(
            f"  {entry['name']:<20}  {entry.get('confidence', 0.0):.2f}"
            f"  {entry.get('use_count', 0):>5}"
            f"  {entry.get('status', 'enabled'):<9}"
            f" {last_used[:19]}"
        )
    return True
