"""TaskSkillLoader: choosing which skills a worker sees.

This is where Phase 2's ranking first affects a model's context. Before B1
the loader matched goal words against a filename and a first line, and
returned everything that hit in arbitrary order; the confidence the index
had been tracking since Phase 3 was never consulted. Now a global skill is
retrieved through `SkillIndex.get_ranked()`, which means an unapproved
draft cannot reach a model at all.

Three properties these tests exist to hold.

**The returned shape is unchanged.** `agents/worker.py` reads `name`,
`content`, `hash` and `source` off each dict and de-duplicates on `hash`.
Changing that shape here would break skill injection in a way no test of
this module would catch.

**Local skills still work, and still win.** A task-local skill lives in
`~/tasks/<name>/.agentic/skills/` and has no index entry; the index only
knows about `~/skills/`. So ranking applies to global skills only, and a
local skill must load regardless, and still override a global one by name.

**No index is not an error.** A fresh install, or a worker spawned before
anything was ever crystallised, falls back to keyword matching rather than
returning nothing. Returning nothing would look exactly like "this shell
has no skills", which is a lie a user cannot debug.
"""
from __future__ import annotations

import pytest

from sable.skills import loader as loader_module
from sable.skills.index import SkillIndex
from sable.skills.loader import TaskSkillLoader


@pytest.fixture
def tasks_base(tmp_path):
    base = tmp_path / "tasks"
    base.mkdir()
    return base


@pytest.fixture
def global_dir(tmp_path, monkeypatch):
    """Redirect the global skills root, so ~/skills is untouched."""
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr(loader_module, "SKILLS_ROOT", root)
    return root


@pytest.fixture
def index_path(global_dir):
    return global_dir / "skills_index.json"


def _write_global(global_dir, name: str, description: str, body: str = "Steps.",
                  status: str = "enabled") -> None:
    """Write a folder skill in the B2 layout."""
    folder = global_dir / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f'+++\nname = "{name}"\ndescription = "{description}"\n'
        f'status = "{status}"\nsource = "user"\n+++\n\n# {description}\n\n{body}\n',
        encoding="utf-8",
    )


def _write_local(tasks_base, task: str, name: str, text: str) -> None:
    local = tasks_base / task / ".agentic" / "skills"
    local.mkdir(parents=True, exist_ok=True)
    (local / f"{name}.md").write_text(text, encoding="utf-8")


def _indexed(index_path, name: str, keywords: list[str], *,
             status: str = "enabled", auto: bool = False) -> SkillIndex:
    index = SkillIndex(index_path=str(index_path))
    index.add(name=name, file=f"/s/{name}", keywords=keywords,
              auto_generated=auto, status=status)
    return index


class TestTheApprovalGateReachesTheModel:
    """The point of the phase: a draft never reaches a model's context."""

    def test_a_pending_skill_is_not_loaded(self, global_dir, index_path, tasks_base):
        _write_global(global_dir, "deploy-api", "Deploy the API", status="pending")
        _indexed(index_path, "deploy-api", ["deploy", "api"], status="pending", auto=True)

        loader = TaskSkillLoader("t", str(tasks_base), index_path=str(index_path))

        assert loader.load_relevant("deploy the api") == []

    def test_a_disabled_skill_is_not_loaded(self, global_dir, index_path, tasks_base):
        _write_global(global_dir, "deploy-api", "Deploy the API")
        _indexed(index_path, "deploy-api", ["deploy", "api"], status="disabled")

        loader = TaskSkillLoader("t", str(tasks_base), index_path=str(index_path))

        assert loader.load_relevant("deploy the api") == []

    def test_an_approved_skill_is_loaded(self, global_dir, index_path, tasks_base):
        _write_global(global_dir, "deploy-api", "Deploy the API")
        _indexed(index_path, "deploy-api", ["deploy", "api"])

        loader = TaskSkillLoader("t", str(tasks_base), index_path=str(index_path))

        assert [s["name"] for s in loader.load_relevant("deploy the api")] == ["deploy-api"]


class TestRankingOrder:
    def test_skills_arrive_best_first(self, global_dir, index_path, tasks_base):
        """The order the index chose is the order the model sees.

        A worker injects every returned skill, so order decides what leads
        the context when several match.
        """
        _write_global(global_dir, "weak", "Deploy something")
        _write_global(global_dir, "strong", "Deploy the API")
        index = SkillIndex(index_path=str(index_path))
        index.add(name="weak", file="/s/weak", keywords=["deploy"], auto_generated=True)
        index.add(name="strong", file="/s/strong", keywords=["deploy", "api"],
                  auto_generated=False)

        loader = TaskSkillLoader("t", str(tasks_base), index_path=str(index_path))

        assert [s["name"] for s in loader.load_relevant("deploy the api")][0] == "strong"

    def test_an_indexed_skill_with_no_file_is_skipped(self, global_dir, index_path, tasks_base):
        """Index and disk drift apart. The file is what can be injected."""
        _indexed(index_path, "ghost", ["deploy"])

        loader = TaskSkillLoader("t", str(tasks_base), index_path=str(index_path))

        assert loader.load_relevant("deploy the api") == []


class TestTheReturnedShape:
    """`agents/worker.py` reads these four keys and de-duplicates on hash."""

    def test_every_key_the_worker_reads_is_present(self, global_dir, index_path, tasks_base):
        _write_global(global_dir, "deploy-api", "Deploy the API")
        _indexed(index_path, "deploy-api", ["deploy", "api"])

        loader = TaskSkillLoader("t", str(tasks_base), index_path=str(index_path))
        skill = loader.load_relevant("deploy the api")[0]

        assert set(skill) >= {"name", "content", "hash", "source"}
        assert skill["name"] == "deploy-api"
        assert skill["source"] == "global"

    def test_the_content_is_the_body_not_the_frontmatter(self, global_dir, index_path, tasks_base):
        """Frontmatter in the context is tokens spent on nothing.

        It is bookkeeping for the shell, not instruction for the model, and
        this is the most expensive context there is.
        """
        _write_global(global_dir, "deploy-api", "Deploy the API", body="Run the thing.")
        _indexed(index_path, "deploy-api", ["deploy", "api"])

        loader = TaskSkillLoader("t", str(tasks_base), index_path=str(index_path))
        content = loader.load_relevant("deploy the api")[0]["content"]

        assert "Run the thing." in content
        assert "+++" not in content
        assert "status =" not in content

    def test_the_hash_is_stable_across_calls(self, global_dir, index_path, tasks_base):
        """The worker skips a skill it has already seen, keyed on this."""
        _write_global(global_dir, "deploy-api", "Deploy the API")
        _indexed(index_path, "deploy-api", ["deploy", "api"])

        loader = TaskSkillLoader("t", str(tasks_base), index_path=str(index_path))
        first = loader.load_relevant("deploy the api")[0]["hash"]
        second = loader.load_relevant("deploy the api")[0]["hash"]

        assert first == second

    def test_the_hash_changes_when_the_skill_changes(self, global_dir, index_path, tasks_base):
        _write_global(global_dir, "deploy-api", "Deploy the API", body="Old steps.")
        _indexed(index_path, "deploy-api", ["deploy", "api"])
        loader = TaskSkillLoader("t", str(tasks_base), index_path=str(index_path))
        before = loader.load_relevant("deploy the api")[0]["hash"]

        _write_global(global_dir, "deploy-api", "Deploy the API", body="New steps.")
        after = TaskSkillLoader(
            "t", str(tasks_base), index_path=str(index_path)
        ).load_relevant("deploy the api")[0]["hash"]

        assert before != after


class TestLocalSkills:
    """Task-local skills have no index entry: the index only knows ~/skills."""

    def test_a_local_skill_loads_without_an_index_entry(self, global_dir, index_path, tasks_base):
        _write_local(tasks_base, "t", "local-only", "# Local only\n\nSteps.\n")
        SkillIndex(index_path=str(index_path))

        loader = TaskSkillLoader("t", str(tasks_base), index_path=str(index_path))
        loaded = loader.load_relevant("local only")

        assert [s["name"] for s in loaded] == ["local-only"]
        assert loaded[0]["source"] == "local"

    def test_a_local_skill_overrides_a_global_one_of_the_same_name(
        self, global_dir, index_path, tasks_base
    ):
        _write_global(global_dir, "deploy-api", "Deploy the API", body="Global steps.")
        _indexed(index_path, "deploy-api", ["deploy", "api"])
        _write_local(tasks_base, "t", "deploy-api", "# Deploy the API\n\nLocal steps.\n")

        loader = TaskSkillLoader("t", str(tasks_base), index_path=str(index_path))
        loaded = loader.load_relevant("deploy the api")

        assert len(loaded) == 1
        assert loaded[0]["source"] == "local"
        assert "Local steps." in loaded[0]["content"]

    def test_a_local_folder_skill_loads_too(self, global_dir, index_path, tasks_base):
        """Local skills may be written in either layout."""
        local = tasks_base / "t" / ".agentic" / "skills" / "folder-skill"
        local.mkdir(parents=True)
        (local / "SKILL.md").write_text(
            '+++\nname = "folder-skill"\ndescription = "Folder skill"\n'
            'status = "enabled"\nsource = "user"\n+++\n\nSteps.\n',
            encoding="utf-8",
        )

        loader = TaskSkillLoader("t", str(tasks_base), index_path=str(index_path))

        assert [s["name"] for s in loader.load_relevant("folder skill")] == ["folder-skill"]


class TestMatchingUsesTheDescription:
    """After B2 the description is frontmatter, not the first body line.

    The pre-B2 loader matched goal words against the filename and the file's
    first line, which back then *was* the description. B2 moved it into
    frontmatter and `_read` strips frontmatter out, so a rule that still
    reads the first body line matches against text with no reason to contain
    the goal words.

    Found by running the no-index fallback against a real file: a skill
    described "deploy the api", whose body said "Steps for v2", was
    invisible to the goal "deploy the api".
    """

    def test_a_skill_matches_on_its_description(self, global_dir, tasks_base, tmp_path):
        _write_global(global_dir, "v2", "Deploy the API", body="Steps for v2.")

        loader = TaskSkillLoader(
            "t", str(tasks_base), index_path=str(tmp_path / "absent.json")
        )

        assert [s["name"] for s in loader.load_relevant("deploy the api")] == ["v2"]

    def test_an_approved_skill_survives_a_deleted_index(
        self, global_dir, index_path, tasks_base
    ):
        """The scenario that found this: approve, then lose the index.

        The file says enabled, so the skill must keep working. Otherwise
        deleting the index silently withdraws every approved skill, which
        is the opposite failure from the one the gate guards against.
        """
        _write_global(global_dir, "v2", "Deploy the API", body="Steps for v2.")
        _indexed(index_path, "v2", ["deploy", "api"])
        index_path.unlink()

        loader = TaskSkillLoader("t", str(tasks_base), index_path=str(index_path))

        assert [s["name"] for s in loader.load_relevant("deploy the api")] == ["v2"]


class TestDegradingWithoutAnIndex:
    """No index must not mean no skills. See the module docstring."""

    def test_folder_skills_load_by_keyword_when_there_is_no_index(
        self, global_dir, tasks_base, tmp_path
    ):
        _write_global(global_dir, "deploy-api", "Deploy the API")

        loader = TaskSkillLoader(
            "t", str(tasks_base), index_path=str(tmp_path / "absent.json")
        )

        assert [s["name"] for s in loader.load_relevant("deploy the api")] == ["deploy-api"]

    def test_a_pending_skill_is_still_withheld_without_an_index(
        self, global_dir, tasks_base, tmp_path
    ):
        """The gate cannot depend on the index being present.

        The file carries its own status, so a draft stays withheld even on
        the fallback path. Otherwise deleting the index would enable every
        pending draft at once.
        """
        _write_global(global_dir, "draft", "A draft", status="pending")

        loader = TaskSkillLoader(
            "t", str(tasks_base), index_path=str(tmp_path / "absent.json")
        )

        assert loader.load_relevant("a draft") == []


class TestMalformedSkillsDoNotStopTheRest:
    def test_an_unreadable_skill_is_skipped(self, global_dir, index_path, tasks_base):
        _write_global(global_dir, "good", "Deploy the API")
        bad = global_dir / "bad" / "SKILL.md"
        bad.parent.mkdir(parents=True)
        bad.write_text('+++\nname = "bad\n+++\n\nbody\n', encoding="utf-8")
        index = SkillIndex(index_path=str(index_path))
        index.add(name="good", file="/s/good", keywords=["deploy"], auto_generated=False)
        index.add(name="bad", file="/s/bad", keywords=["deploy"], auto_generated=False)

        loader = TaskSkillLoader("t", str(tasks_base), index_path=str(index_path))

        assert [s["name"] for s in loader.load_relevant("deploy the api")] == ["good"]

    def test_no_matching_skill_returns_empty(self, global_dir, index_path, tasks_base):
        _write_global(global_dir, "deploy-api", "Deploy the API")
        _indexed(index_path, "deploy-api", ["deploy", "api"])

        loader = TaskSkillLoader("t", str(tasks_base), index_path=str(index_path))

        assert loader.load_relevant("something entirely unrelated") == []

    def test_a_missing_global_directory_is_not_an_error(self, tasks_base, tmp_path, monkeypatch):
        monkeypatch.setattr(loader_module, "SKILLS_ROOT", tmp_path / "nope")

        loader = TaskSkillLoader(
            "t", str(tasks_base), index_path=str(tmp_path / "absent.json")
        )

        assert loader.load_relevant("anything") == []


class TestLegacyFlatSkillsStillLoad:
    """Task 2 keeps the originals for a release, so both layouts coexist."""

    def test_a_flat_instructions_file_still_loads(self, global_dir, index_path, tasks_base):
        flat = global_dir / "instructions"
        flat.mkdir()
        (flat / "old-skill.md").write_text("# Old skill\n\nSteps.\n", encoding="utf-8")

        loader = TaskSkillLoader("t", str(tasks_base), index_path=str(index_path))

        assert [s["name"] for s in loader.load_relevant("old skill")] == ["old-skill"]

    def test_a_folder_skill_wins_over_its_flat_original(self, global_dir, index_path, tasks_base):
        """After migration both exist. The folder is the live copy."""
        _write_global(global_dir, "deploy-api", "Deploy the API", body="Folder steps.")
        _indexed(index_path, "deploy-api", ["deploy", "api"])
        flat = global_dir / "instructions"
        flat.mkdir()
        (flat / "deploy-api.md").write_text(
            "# Deploy the API\n\nFlat steps.\n", encoding="utf-8"
        )

        loader = TaskSkillLoader("t", str(tasks_base), index_path=str(index_path))
        loaded = loader.load_relevant("deploy the api")

        assert len(loaded) == 1
        assert "Folder steps." in loaded[0]["content"]
