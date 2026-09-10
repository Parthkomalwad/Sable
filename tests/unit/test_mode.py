"""Unit tests for the mode switch (I13): on/off/status, re-attach, CLI."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from sable.app import mode


@pytest.fixture
def sable_home(tmp_path, monkeypatch):
    """Redirect ~/.sable/ into tmp_path."""
    import sable.core.paths as paths

    home = tmp_path / ".sable"
    monkeypatch.setattr(paths, "SABLE_HOME", home)
    monkeypatch.setattr(paths, "STATE_DIR", home / "state")
    monkeypatch.setattr(paths, "DISABLED_FLAG", home / "disabled")
    return home


class TestToggle:
    def test_disable_writes_the_flag(self, sable_home):
        message = mode.disable()

        assert (sable_home / "disabled").exists()
        assert "off" in message
        assert "sable on" in message

    def test_disable_creates_the_home_directory(self, sable_home):
        assert not sable_home.exists()

        mode.disable()

        assert sable_home.is_dir()

    def test_enable_removes_the_flag(self, sable_home):
        mode.disable()

        message = mode.enable()

        assert not (sable_home / "disabled").exists()
        assert "on" in message

    def test_enable_when_already_on_is_not_an_error(self, sable_home):
        assert "already on" in mode.enable()

    def test_status_reports_off_when_disabled(self, sable_home):
        mode.disable()

        assert "off" in mode.status()

    def test_status_reports_on_by_default(self, sable_home):
        assert "on" in mode.status()

    def test_round_trip(self, sable_home):
        from sable.core import paths

        assert paths.is_disabled() is False
        mode.disable()
        assert paths.is_disabled() is True
        mode.enable()
        assert paths.is_disabled() is False

    def test_flag_file_explains_itself(self, sable_home):
        """Someone finding this file should be able to work out what it does."""
        mode.disable()

        body = (sable_home / "disabled").read_text()
        assert "sable on" in body


class TestSessionName:
    def test_uses_the_username(self, monkeypatch):
        monkeypatch.setenv("USER", "parth")
        assert mode.session_name() == "sable-parth"

    def test_explicit_username_wins(self):
        assert mode.session_name("someone") == "sable-someone"

    def test_falls_back_when_no_user_is_set(self, monkeypatch):
        monkeypatch.delenv("USER", raising=False)
        monkeypatch.delenv("USERNAME", raising=False)
        assert mode.session_name() == "sable-user"


class TestAttachExisting:
    def test_returns_false_without_tmux(self, monkeypatch):
        monkeypatch.setattr(mode.shutil, "which", lambda _: None)

        assert mode.attach_existing() is False

    def test_returns_false_when_no_session_exists(self, monkeypatch):
        monkeypatch.setattr(mode.shutil, "which", lambda _: "/usr/bin/tmux")
        monkeypatch.delenv("TMUX", raising=False)
        with patch("subprocess.run", return_value=MagicMock(returncode=1)):
            assert mode.attach_existing() is False

    def test_does_not_attach_from_inside_tmux(self, monkeypatch):
        monkeypatch.setattr(mode.shutil, "which", lambda _: "/usr/bin/tmux")
        monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,123,0")
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            assert mode.attach_existing() is False

    def test_execs_tmux_attach_when_a_session_is_live(self, monkeypatch, capsys):
        monkeypatch.setattr(mode.shutil, "which", lambda _: "/usr/bin/tmux")
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.setenv("USER", "parth")
        calls = []

        with patch("subprocess.run", return_value=MagicMock(returncode=0)), \
             patch("os.execvp", side_effect=lambda f, a: calls.append((f, a))):
            mode.attach_existing()

        assert calls == [("tmux", ["tmux", "attach-session", "-t", "sable-parth"])]
        assert "re-attaching" in capsys.readouterr().out


class TestBanner:
    def test_prints_mode_and_how_to_leave(self, capsys):
        mode.banner("[plain] bash subshell", "type 'exit' to come back")

        out = capsys.readouterr().out
        assert "[plain] bash subshell" in out
        assert "exit" in out


class TestPlainSubshell:
    def test_runs_bash_and_restores_the_sidebar(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr(mode.shutil, "which", lambda _: "/bin/bash")
        toggles = []
        monkeypatch.setattr(mode, "_hide_sidebar", lambda: toggles.append("hide") or True)
        monkeypatch.setattr(mode, "_show_sidebar", lambda: toggles.append("show"))
        monkeypatch.setattr(mode, "_spawn_interactive", lambda bash, cwd: 0)

        assert mode.run_plain_subshell(str(tmp_path)) == 0
        assert toggles == ["hide", "show"]

    def test_announces_both_directions(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr(mode.shutil, "which", lambda _: "/bin/bash")
        monkeypatch.setattr(mode, "_hide_sidebar", lambda: False)
        monkeypatch.setattr(mode, "_spawn_interactive", lambda bash, cwd: 0)

        mode.run_plain_subshell(str(tmp_path))

        out = capsys.readouterr().out
        assert "[plain]" in out
        assert "exit" in out
        assert "back in sable" in out

    def test_sidebar_is_restored_even_if_bash_fails(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mode.shutil, "which", lambda _: "/bin/bash")
        restored = []
        monkeypatch.setattr(mode, "_hide_sidebar", lambda: True)
        monkeypatch.setattr(mode, "_show_sidebar", lambda: restored.append(True))

        def _boom(bash, cwd):
            raise OSError("no pty")

        monkeypatch.setattr(mode, "_spawn_interactive", _boom)

        with pytest.raises(OSError):
            mode.run_plain_subshell(str(tmp_path))
        assert restored == [True]


class TestCli:
    def _capture(self, argv, capsys):
        from sable.app.main import _handle_cli

        handled = _handle_cli(argv)
        return handled, capsys.readouterr().out

    def test_no_arguments_is_not_a_cli_command(self, capsys):
        handled, _ = self._capture([], capsys)
        assert handled is False

    def test_help_prints_usage(self, capsys):
        handled, out = self._capture(["--help"], capsys)
        assert handled is True
        assert "sable on" in out
        assert "sable off" in out
        assert "--wrap" in out

    def test_version_prints_the_package_version(self, capsys):
        from shell import __version__

        handled, out = self._capture(["--version"], capsys)
        assert handled is True
        assert __version__ in out

    @pytest.mark.parametrize("command", ["on", "off", "status"])
    def test_toggle_commands_are_handled(self, command, capsys, sable_home):
        handled, out = self._capture([command], capsys)
        assert handled is True
        assert "sable" in out.lower()

    def test_start_is_not_treated_as_a_cli_command(self, capsys):
        handled, _ = self._capture(["--wrap"], capsys)
        assert handled is False
