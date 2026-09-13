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
