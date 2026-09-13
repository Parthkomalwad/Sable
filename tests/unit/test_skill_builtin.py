"""The `/skill` builtin: list, new, edit.

`/skill edit` raised NameError on every invocation: the handler read
`os.environ` and the module never imported `os`. Nothing caught it because
`/skill` had no tests at all, which is the more interesting bug of the two.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from sable.app.builtins.skill import _handle_skill_builtin


@pytest.fixture(autouse=True)
def skills_dir(tmp_path, monkeypatch):
    """Point the builtin's skills directory at tmp_path.

    It resolves `Path.home()` inside the function, so redirecting HOME is what
    moves it.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    return tmp_path / "skills" / "instructions"


class TestList:
    def test_no_arguments_lists(self, skills_dir, capsys):
        assert _handle_skill_builtin([]) is True
        assert "no skills found" in capsys.readouterr().out

    def test_explicit_list_is_handled(self, skills_dir, capsys):
        assert _handle_skill_builtin(["list"]) is True

    def test_lists_skill_names(self, skills_dir, capsys):
        skills_dir.mkdir(parents=True)
        (skills_dir / "deploy-api.md").write_text("# deploy")

        _handle_skill_builtin(["list"])

        assert "deploy-api" in capsys.readouterr().out

    def test_creates_the_directory_if_missing(self, skills_dir):
        _handle_skill_builtin(["list"])
        assert skills_dir.is_dir()


class TestNew:
    def test_writes_a_stub_file(self, skills_dir, capsys):
        assert _handle_skill_builtin(["new", "my-skill"]) is True

        path = skills_dir / "my-skill.md"
        assert path.exists()
        assert "my-skill" in path.read_text()

    def test_reports_where_it_was_written(self, skills_dir, capsys):
        _handle_skill_builtin(["new", "my-skill"])
        assert "my-skill.md" in capsys.readouterr().out

    def test_new_without_a_name_shows_usage(self, skills_dir, capsys):
        assert _handle_skill_builtin(["new"]) is True
        assert "usage:" in capsys.readouterr().out


class TestEdit:
    """The regression these exist for: every one of these raised NameError."""

    def test_edit_opens_an_editor(self, skills_dir):
        skills_dir.mkdir(parents=True)
        (skills_dir / "thing.md").write_text("# thing")

        with patch("subprocess.run") as run:
            assert _handle_skill_builtin(["edit", "thing"]) is True

        run.assert_called_once()
        assert "thing.md" in run.call_args[0][0][1]

    def test_editor_comes_from_the_environment(self, skills_dir, monkeypatch):
        monkeypatch.setenv("EDITOR", "vim")

        with patch("subprocess.run") as run:
            _handle_skill_builtin(["edit", "thing"])

        assert run.call_args[0][0][0] == "vim"

    def test_editor_falls_back_to_nano(self, skills_dir, monkeypatch):
        monkeypatch.delenv("EDITOR", raising=False)

        with patch("subprocess.run") as run:
            _handle_skill_builtin(["edit", "thing"])

        assert run.call_args[0][0][0] == "nano"

    def test_edit_without_a_name_shows_usage(self, skills_dir, capsys):
        assert _handle_skill_builtin(["edit"]) is True
        assert "usage:" in capsys.readouterr().out


class TestUnknown:
    def test_an_unknown_subcommand_shows_usage(self, skills_dir, capsys):
        assert _handle_skill_builtin(["frobnicate"]) is True
        assert "usage:" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Phase 2 (B2, Task 8): the surface that makes the approval gate usable.
#
# Until these exist a pending draft cannot be approved from the shell at all:
# the crystalliser writes one and the only way to enable it is by editing
# JSON by hand. `/skill list` also has to say which skills are drafts, or the
# gate is invisible and a user never learns there is something to approve.
# ---------------------------------------------------------------------------


def _folder_skill(home, name, *, status="enabled", description="A skill",
                  body="Steps.", validate=""):
    """Write a B2 folder skill under the redirected home."""
    folder = home / "skills" / name
    folder.mkdir(parents=True, exist_ok=True)
    v = f'validate = "{validate}"\n' if validate else ""
    (folder / "SKILL.md").write_text(
        f'+++\nname = "{name}"\ndescription = "{description}"\n{v}'
        f'status = "{status}"\nsource = "crystallised"\n+++\n\n{body}\n',
        encoding="utf-8",
    )
    return folder / "SKILL.md"


def _index(home, name, *, status="enabled", auto=True):
    from sable.skills.index import SkillIndex

    index = SkillIndex()
    index.add(name=name, file=str(home / "skills" / name / "SKILL.md"),
              keywords=[name], auto_generated=auto, status=status)
    return index


class TestListShowsState:
    """A draft the user cannot see is a draft they will never approve."""

    def test_a_pending_skill_is_marked_as_a_draft(self, skills_dir, tmp_path, capsys):
        _folder_skill(tmp_path, "deploy-api", status="pending")
        _index(tmp_path, "deploy-api", status="pending")

        _handle_skill_builtin(["list"])

        out = capsys.readouterr().out
        assert "deploy-api" in out
        assert "draft" in out.lower()

    def test_an_enabled_skill_shows_its_confidence(self, skills_dir, tmp_path, capsys):
        _folder_skill(tmp_path, "deploy-api")
        _index(tmp_path, "deploy-api", auto=True)

        _handle_skill_builtin(["list"])

        assert "0.5" in capsys.readouterr().out

    def test_a_disabled_skill_is_marked(self, skills_dir, tmp_path, capsys):
        _folder_skill(tmp_path, "off-skill", status="disabled")
        _index(tmp_path, "off-skill", status="disabled")

        _handle_skill_builtin(["list"])

        assert "disabled" in capsys.readouterr().out.lower()

    def test_folder_skills_are_listed(self, skills_dir, tmp_path, capsys):
        """B2 moved skills into folders; listing only the flat dir hides them."""
        _folder_skill(tmp_path, "folder-skill")

        _handle_skill_builtin(["list"])

        assert "folder-skill" in capsys.readouterr().out


class TestShow:
    def test_show_renders_the_body(self, skills_dir, tmp_path, capsys):
        _folder_skill(tmp_path, "deploy-api", body="Run the deploy.")

        assert _handle_skill_builtin(["show", "deploy-api"]) is True

        assert "Run the deploy." in capsys.readouterr().out

    def test_show_renders_the_contract_fields(self, skills_dir, tmp_path, capsys):
        _folder_skill(tmp_path, "deploy-api", description="Deploy the API",
                      validate="docker compose ps")

        _handle_skill_builtin(["show", "deploy-api"])

        out = capsys.readouterr().out
        assert "Deploy the API" in out
        assert "docker compose ps" in out

    def test_show_of_an_unknown_skill_says_so(self, skills_dir, capsys):
        assert _handle_skill_builtin(["show", "nope"]) is True
        out = capsys.readouterr().out.lower()
        assert "nope" in out
        assert "no skill" in out or "not found" in out

    def test_show_without_a_name_shows_usage(self, skills_dir, capsys):
        assert _handle_skill_builtin(["show"]) is True
        assert "usage:" in capsys.readouterr().out


class TestApprove:
    """The command the whole approval gate depends on existing."""

    def test_approve_enables_a_pending_skill(self, skills_dir, tmp_path, capsys):
        from sable.skills.index import SkillIndex

        _folder_skill(tmp_path, "deploy-api", status="pending")
        _index(tmp_path, "deploy-api", status="pending")

        assert _handle_skill_builtin(["approve", "deploy-api"]) is True

        assert SkillIndex().list_all()[0]["status"] == "enabled"

    def test_approve_writes_through_to_the_file(self, skills_dir, tmp_path):
        path = _folder_skill(tmp_path, "deploy-api", status="pending")
        _index(tmp_path, "deploy-api", status="pending")

        _handle_skill_builtin(["approve", "deploy-api"])

        assert 'status = "enabled"' in path.read_text(encoding="utf-8")

    def test_approve_reports_the_confidence_it_starts_at(self, skills_dir, tmp_path, capsys):
        """Approval is not a vouch: the draft starts where it was drafted."""
        _folder_skill(tmp_path, "deploy-api", status="pending")
        _index(tmp_path, "deploy-api", status="pending")

        _handle_skill_builtin(["approve", "deploy-api"])

        assert "0.5" in capsys.readouterr().out

    def test_approving_an_unknown_skill_says_so(self, skills_dir, capsys):
        assert _handle_skill_builtin(["approve", "nope"]) is True
        assert "nope" in capsys.readouterr().out.lower()

    def test_approve_without_a_name_shows_usage(self, skills_dir, capsys):
        assert _handle_skill_builtin(["approve"]) is True
        assert "usage:" in capsys.readouterr().out


class TestRejectAndDisable:
    def test_reject_removes_the_index_entry(self, skills_dir, tmp_path, capsys):
        from sable.skills.index import SkillIndex

        _folder_skill(tmp_path, "deploy-api", status="pending")
        _index(tmp_path, "deploy-api", status="pending")

        assert _handle_skill_builtin(["reject", "deploy-api"]) is True

        assert SkillIndex().list_all() == []

    def test_reject_keeps_the_file_and_says_so(self, skills_dir, tmp_path, capsys):
        """Rejecting is not deleting: the draft stays readable."""
        path = _folder_skill(tmp_path, "deploy-api", status="pending")
        _index(tmp_path, "deploy-api", status="pending")

        _handle_skill_builtin(["reject", "deploy-api"])

        assert path.exists()
        assert str(path.parent.name) in capsys.readouterr().out

    def test_disable_is_reversible(self, skills_dir, tmp_path):
        from sable.skills.index import SkillIndex

        _folder_skill(tmp_path, "deploy-api")
        _index(tmp_path, "deploy-api")

        _handle_skill_builtin(["disable", "deploy-api"])
        assert SkillIndex().list_all()[0]["status"] == "disabled"

        _handle_skill_builtin(["approve", "deploy-api"])
        assert SkillIndex().list_all()[0]["status"] == "enabled"

    def test_disable_without_a_name_shows_usage(self, skills_dir, capsys):
        assert _handle_skill_builtin(["disable"]) is True
        assert "usage:" in capsys.readouterr().out


class TestStats:
    def test_stats_shows_use_count_and_confidence(self, skills_dir, tmp_path, capsys):
        from sable.skills.index import SkillIndex

        _folder_skill(tmp_path, "deploy-api")
        index = _index(tmp_path, "deploy-api")
        index.nudge("deploy-api", success=True)

        assert _handle_skill_builtin(["stats"]) is True

        out = capsys.readouterr().out
        assert "deploy-api" in out
        assert "0.55" in out
        assert "1" in out

    def test_stats_with_no_skills_says_so(self, skills_dir, capsys):
        assert _handle_skill_builtin(["stats"]) is True
        assert capsys.readouterr().out.strip()
