"""Unit tests for shell/executor.py.

Tests:
- cd interception calls os.chdir (not subprocess)
- Non-cd commands are routed to ptyprocess
- cd with ~ expands correctly
- cd with missing directory returns error

No LLM calls, no real subprocess, no file I/O beyond os.chdir.
"""
import os
import sys

import pytest
from unittest.mock import patch, MagicMock
from sable.core.executor import execute_bash


class TestCdInterception:
    def test_cd_calls_os_chdir(self, tmp_path):
        """cd must update the Python process cwd via os.chdir, not subprocess."""
        exit_code, output = execute_bash(f"cd {tmp_path}", cwd=str(tmp_path))
        assert exit_code == 0
        assert os.getcwd() == str(tmp_path)

    def test_cd_missing_dir_returns_error(self, tmp_path, capsys):
        """cd reports the failure on stdout and returns exit 1. The message is
        printed, not returned, so the caller does not echo it a second time."""
        exit_code, output = execute_bash("cd /nonexistent_path_xyz", cwd=str(tmp_path))

        assert exit_code == 1
        assert output == ""
        assert "No such file or directory" in capsys.readouterr().out

    def test_cd_no_arg_goes_home(self):
        """'cd' with no argument should go to home directory."""
        with patch("os.chdir") as mock_chdir:
            execute_bash("cd", cwd="/tmp")
            mock_chdir.assert_called_once_with(os.path.expanduser("~"))

    def test_cd_tilde_expands(self):
        """'cd ~' should expand to home directory."""
        with patch("os.chdir") as mock_chdir:
            execute_bash("cd ~", cwd="/tmp")
            mock_chdir.assert_called_once_with(os.path.expanduser("~"))


@pytest.fixture
def real_stdin():
    """Give _pty_exec a stdin with a real fileno().

    It calls sys.stdin.fileno() unguarded, and pytest replaces stdin with a
    pseudofile that raises UnsupportedOperation. Point stdin at /dev/null for
    the duration so the routing assertions can be reached.
    """
    with open(os.devnull) as devnull:
        original = sys.stdin
        sys.stdin = devnull
        try:
            yield
        finally:
            sys.stdin = original


def _fake_pty(mock_pty_class):
    """Wire a PtyProcessUnicode mock that survives _pty_exec's select() loop.

    isalive() False short-circuits the loop before select() is reached, so the
    routing assertion runs without needing a real pty.
    """
    mock_proc = MagicMock()
    mock_proc.fd = 0
    mock_proc.read.side_effect = EOFError
    mock_proc.isalive.return_value = False
    mock_proc.exitstatus = 0
    mock_pty_class.spawn.return_value = mock_proc
    return mock_proc


class TestPtyProcessRouting:
    def test_non_cd_uses_ptyprocess(self, real_stdin):
        """Non-cd commands must go through PtyProcessUnicode, not subprocess."""
        with patch("sable.core.executor.PtyProcessUnicode") as mock_pty_class:
            _fake_pty(mock_pty_class)

            execute_bash("grep -r TODO src/", cwd="/tmp")

            mock_pty_class.spawn.assert_called_once()
            args = mock_pty_class.spawn.call_args[0][0]
            assert args == ["/bin/bash", "-c", "grep -r TODO src/"]

    def test_ls_is_rendered_by_rich_not_ptyprocess(self, tmp_path, real_stdin):
        """`ls` is intercepted and rendered with Rich, so it never spawns a pty.
        Anything with shell syntax falls through to the pty instead."""
        with patch("sable.core.executor.PtyProcessUnicode") as mock_pty_class:
            _fake_pty(mock_pty_class)

            execute_bash("ls -la", cwd=str(tmp_path))
            mock_pty_class.spawn.assert_not_called()

            execute_bash("ls -la | head", cwd=str(tmp_path))
            mock_pty_class.spawn.assert_called_once()

    def test_echo_command_not_intercepted_as_cd(self, real_stdin):
        """'echo cd' must not trigger the cd interceptor."""
        with patch("sable.core.executor.PtyProcessUnicode") as mock_pty_class:
            _fake_pty(mock_pty_class)

            execute_bash("echo cd /tmp", cwd="/tmp")

            mock_pty_class.spawn.assert_called_once()
            assert mock_pty_class.spawn.call_args[0][0] == [
                "/bin/bash", "-c", "echo cd /tmp",
            ]
