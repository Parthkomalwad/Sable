"""Migrating pre-B2 flat skill files into folders.

Today a skill is `~/skills/instructions/<slug>.md`: bare markdown, no
frontmatter. B2 makes it `~/skills/<slug>/SKILL.md` with a contract. Every
skill a user already has is in the old shape, and Sable is their login
shell, so a migration that loses one is not a cosmetic bug.

Two rules these tests exist to hold.

**The original is kept.** For one release the flat file stays where it is,
the same precedent the `shell/` compat shim set. Frontmatter inference is a
guess (the description comes from the first heading); if the guess is wrong
the user still has the file they wrote. Task 7 of the plan is where the
originals are removed, a release later.

**Nothing is silently disabled.** A migrated skill lands `enabled`, because
a skill the user already had working must keep working. Sending them all to
`pending` would be indistinguishable, from the user's side, from the
migration having lost them.

The index is migrated with the files: a `SkillIndex` entry points at a
`file` path, and leaving those aimed at the flat copies would mean the index
and disk disagree about where a skill lives the moment Task 7 deletes them.
"""
from __future__ import annotations

import json

import pytest

from sable.skills import migrate as migrate_module
from sable.skills.index import SkillIndex
from sable.skills.migrate import SKILL_FILENAME, migrate_flat_skills
from sable.skills.model import parse_skill


@pytest.fixture
def skills_root(tmp_path, monkeypatch):
    """Redirect every skills path into tmp_path, so ~/skills is untouched."""
    root = tmp_path / "skills"
    flat = root / "instructions"
    flat.mkdir(parents=True)
    monkeypatch.setattr(migrate_module, "SKILLS_ROOT", root)
    monkeypatch.setattr(migrate_module, "FLAT_SKILLS_DIR", flat)
    return root


@pytest.fixture
def flat_dir(skills_root):
    return skills_root / "instructions"


def _write_flat(flat_dir, name: str, text: str):
    path = flat_dir / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    return path


class TestMigratingOneFile:
    def test_creates_a_folder_with_a_skill_file(self, skills_root, flat_dir):
        _write_flat(flat_dir, "restart-nginx", "# Restart nginx\n\nSteps.\n")
        migrate_flat_skills()
        assert (skills_root / "restart-nginx" / "SKILL.md").exists()

    def test_the_original_flat_file_is_kept(self, flat_dir):
        """Reversibility. The inferred frontmatter is a guess, not a fact."""
        original = _write_flat(flat_dir, "restart-nginx", "# Restart nginx\n\nSteps.\n")
        migrate_flat_skills()
        assert original.exists()
        assert original.read_text(encoding="utf-8") == "# Restart nginx\n\nSteps.\n"

    def test_the_body_survives_verbatim(self, skills_root, flat_dir):
        """The body is what reaches a model. Losing a step is losing the skill."""
        body = "# Restart nginx\n\n## Steps\n1. Check config.\n2. Reload.\n"
        _write_flat(flat_dir, "restart-nginx", body)
        migrate_flat_skills()
        migrated = parse_skill(
            (skills_root / "restart-nginx" / "SKILL.md").read_text(encoding="utf-8")
        )
        assert "1. Check config." in migrated.body
        assert "2. Reload." in migrated.body

    def test_frontmatter_is_inferred_from_the_file(self, skills_root, flat_dir):
        _write_flat(flat_dir, "restart-nginx", "# Restart nginx\n\nSteps.\n")
        migrate_flat_skills()
        migrated = parse_skill(
            (skills_root / "restart-nginx" / "SKILL.md").read_text(encoding="utf-8")
        )
        assert migrated.name == "restart-nginx"
        assert migrated.description == "Restart nginx"
        assert migrated.is_legacy is False

    def test_a_migrated_skill_is_enabled(self, skills_root, flat_dir):
        """A skill that already worked keeps working. See the module docstring."""
        _write_flat(flat_dir, "restart-nginx", "# Restart nginx\n")
        migrate_flat_skills()
        migrated = parse_skill(
            (skills_root / "restart-nginx" / "SKILL.md").read_text(encoding="utf-8")
        )
        assert migrated.status == "enabled"
        assert migrated.is_enabled is True

    def test_a_migrated_skill_is_sourced_as_user_written(self, skills_root, flat_dir):
        """Phase 8's K8 sets a policy floor by source, so this is not cosmetic.

        A flat file predates crystallisation recording provenance, so the
        honest answer is that a human is responsible for it.
        """
        _write_flat(flat_dir, "restart-nginx", "# Restart nginx\n")
        migrate_flat_skills()
        migrated = parse_skill(
            (skills_root / "restart-nginx" / "SKILL.md").read_text(encoding="utf-8")
        )
        assert migrated.source == "user"


class TestWhatIsWrittenCanBeReadBack:
    """Migration must never write a `SKILL.md` the parser then refuses.

    Found by running the migration against real flat files rather than by a
    unit test: a file with no heading infers an empty description, which
    `render_skill` happily emits and `parse_skill` then rejects as missing.
    The skill survived migration and vanished on the next startup, which is
    the exact failure mode this whole task exists to avoid.
    """

    @pytest.mark.parametrize("text", [
        "# Restart nginx\n\nSteps.\n",
        "no heading at all, just prose\n",          # infers no description
        "\n\n# Leading blank lines\n\nBody.\n",
        "# Heading only\n",
        "   \n# Indented blank first line\n\nBody.\n",
    ])
    def test_every_migrated_file_parses_back(self, skills_root, flat_dir, text):
        _write_flat(flat_dir, "a-skill", text)
        migrate_flat_skills()
        written = (skills_root / "a-skill" / SKILL_FILENAME).read_text(encoding="utf-8")
        reparsed = parse_skill(written)      # must not raise
        assert reparsed.name == "a-skill"
        assert reparsed.is_enabled is True

    def test_a_file_without_a_heading_gets_a_usable_description(self, skills_root, flat_dir):
        """The slug is a poor description and an honest one.

        `/skill list` and ranking both show it, so an empty string would be
        a worse outcome than the name the user already refers to it by.
        """
        _write_flat(flat_dir, "odd-one", "no heading at all, just prose\n")
        migrate_flat_skills()
        migrated = parse_skill(
            (skills_root / "odd-one" / SKILL_FILENAME).read_text(encoding="utf-8")
        )
        assert migrated.description == "odd-one"


class TestReportingWhatHappened:
    def test_returns_one_result_per_migrated_skill(self, flat_dir):
        _write_flat(flat_dir, "a", "# A\n")
        _write_flat(flat_dir, "b", "# B\n")
        results = migrate_flat_skills()
        assert {r.name for r in results if r.migrated} == {"a", "b"}

    def test_returns_nothing_when_there_is_nothing_to_do(self, flat_dir):
        assert migrate_flat_skills() == []

    def test_a_missing_flat_directory_is_not_an_error(self, skills_root, monkeypatch):
        """A fresh install has never had one, and must still start."""
        monkeypatch.setattr(migrate_module, "FLAT_SKILLS_DIR", skills_root / "nope")
        assert migrate_flat_skills() == []


class TestIdempotence:
    """Migration runs on first use, which means it runs on every startup."""

    def test_running_twice_changes_nothing(self, skills_root, flat_dir):
        _write_flat(flat_dir, "restart-nginx", "# Restart nginx\n\nSteps.\n")
        migrate_flat_skills()
        first = (skills_root / "restart-nginx" / "SKILL.md").read_text(encoding="utf-8")
        migrate_flat_skills()
        second = (skills_root / "restart-nginx" / "SKILL.md").read_text(encoding="utf-8")
        assert first == second

    def test_the_second_run_reports_nothing_migrated(self, flat_dir):
        _write_flat(flat_dir, "restart-nginx", "# Restart nginx\n")
        migrate_flat_skills()
        assert [r for r in migrate_flat_skills() if r.migrated] == []

    def test_an_existing_folder_is_never_overwritten(self, skills_root, flat_dir):
        """The folder is the live copy. A hand-edited skill outranks a flat original."""
        _write_flat(flat_dir, "restart-nginx", "# Old flat version\n")
        folder = skills_root / "restart-nginx"
        folder.mkdir(parents=True)
        edited = (
            '+++\nname = "restart-nginx"\ndescription = "Hand edited"\n'
            'status = "enabled"\nsource = "user"\n+++\n\n# Hand edited\n'
        )
        (folder / "SKILL.md").write_text(edited, encoding="utf-8")

        migrate_flat_skills()

        assert (folder / "SKILL.md").read_text(encoding="utf-8") == edited

    def test_an_existing_folder_is_reported_as_skipped(self, skills_root, flat_dir):
        _write_flat(flat_dir, "restart-nginx", "# Old\n")
        folder = skills_root / "restart-nginx"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            '+++\nname = "restart-nginx"\ndescription = "d"\n+++\n\nbody\n',
            encoding="utf-8",
        )
        results = migrate_flat_skills()
        skipped = [r for r in results if r.name == "restart-nginx"]
        assert skipped and skipped[0].migrated is False
        assert "exists" in skipped[0].reason


class TestOneBadFileDoesNotStopTheRest:
    """A skill is not safety-critical: one unreadable file must not take the
    shell down, and must not stop the other skills migrating."""

    def test_an_unreadable_file_is_reported_not_raised(self, flat_dir, monkeypatch):
        _write_flat(flat_dir, "good", "# Good\n")
        bad = _write_flat(flat_dir, "bad", "# Bad\n")

        real_read = migrate_module.Path.read_text

        def explode(self, *args, **kwargs):
            if self == bad:
                raise OSError("disk is on fire")
            return real_read(self, *args, **kwargs)

        monkeypatch.setattr(migrate_module.Path, "read_text", explode)

        results = migrate_flat_skills()

        assert any(r.name == "good" and r.migrated for r in results)
        failed = [r for r in results if r.name == "bad"]
        assert failed and failed[0].migrated is False
        assert "disk is on fire" in failed[0].reason

    def test_a_file_that_cannot_be_written_is_reported(self, skills_root, flat_dir, monkeypatch):
        _write_flat(flat_dir, "restart-nginx", "# Restart nginx\n")

        def explode(self, *args, **kwargs):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(migrate_module.Path, "write_text", explode)

        results = migrate_flat_skills()

        assert results and results[0].migrated is False
        assert "read-only filesystem" in results[0].reason

    def test_non_markdown_files_are_ignored(self, skills_root, flat_dir):
        (flat_dir / "notes.txt").write_text("not a skill", encoding="utf-8")
        assert migrate_flat_skills() == []
        assert not (skills_root / "notes").exists()


class TestTheIndexMovesWithTheFiles:
    """A `SkillIndex` entry carries the path a skill lives at.

    Leaving those pointing at the flat copies would mean index and disk
    disagree the moment the originals are removed a release from now, and
    `/skill show` would read a file that is no longer there.
    """

    def test_the_index_entry_points_at_the_new_file(self, skills_root, flat_dir):
        flat = _write_flat(flat_dir, "restart-nginx", "# Restart nginx\n")
        index_path = skills_root / "skills_index.json"
        index = SkillIndex(index_path=str(index_path))
        index.add(name="restart-nginx", file=str(flat), keywords=["nginx"],
                  auto_generated=False)

        migrate_flat_skills(index_path=str(index_path))

        entry = json.loads(index_path.read_text(encoding="utf-8"))[0]
        assert entry["file"] == str(skills_root / "restart-nginx" / "SKILL.md")

    def test_confidence_and_use_count_are_preserved(self, skills_root, flat_dir):
        """Migration is a move. Resetting a skill's earned score is a loss."""
        flat = _write_flat(flat_dir, "restart-nginx", "# Restart nginx\n")
        index_path = skills_root / "skills_index.json"
        index = SkillIndex(index_path=str(index_path))
        index.add(name="restart-nginx", file=str(flat), keywords=["nginx"],
                  auto_generated=True)
        index.record_use("restart-nginx", success=True)

        migrate_flat_skills(index_path=str(index_path))

        entry = json.loads(index_path.read_text(encoding="utf-8"))[0]
        assert entry["confidence"] == pytest.approx(0.55)
        assert entry["use_count"] == 1

    def test_a_skill_with_no_index_entry_still_migrates(self, skills_root, flat_dir):
        """Files and index drift apart; the file is the thing that matters."""
        _write_flat(flat_dir, "orphan", "# Orphan\n")
        index_path = skills_root / "skills_index.json"
        SkillIndex(index_path=str(index_path))

        results = migrate_flat_skills(index_path=str(index_path))

        assert any(r.name == "orphan" and r.migrated for r in results)
        assert (skills_root / "orphan" / "SKILL.md").exists()

    def test_a_missing_index_is_not_an_error(self, skills_root, flat_dir):
        _write_flat(flat_dir, "restart-nginx", "# Restart nginx\n")
        results = migrate_flat_skills(index_path=str(skills_root / "absent.json"))
        assert any(r.migrated for r in results)
