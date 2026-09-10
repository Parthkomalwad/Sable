"""The non-interactive bypass in sable/app/main.py.

If this breaks, `ssh host cmd`, `scp`, `rsync` and `git push` all hang or drop
the user into an interactive REPL, so it is worth testing at the source level:
importing main.py runs the guard, and the guard calls os.execvp, which would
replace the test process.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

MAIN = Path(__file__).resolve().parents[2] / "sable" / "app" / "main.py"


class TestGuardIsFirst:
    def test_bypass_is_the_first_executable_statement(self):
        """CLAUDE.md makes this non-negotiable, and CI asserts it too."""
        src = MAIN.read_text(encoding="utf-8")
        tree = ast.parse(src)

        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue  # module docstring
            segment = ast.get_source_segment(src, node)
            assert "SSH_ORIGINAL_COMMAND" in segment, (
                "the first executable statement is no longer the bypass:\n" + segment[:200]
            )
            return
        pytest.fail("no executable statement found in main.py")

    def test_guard_handles_both_argv_and_env(self):
        """sshd passes the command as argv for `ssh host cmd`, scp and rsync,
        and only moves it into SSH_ORIGINAL_COMMAND behind ForceCommand or an
        authorized_keys command=. Both have to be covered."""
        src = MAIN.read_text(encoding="utf-8")
        guard = src[: src.index("def ")]

        assert "sys.argv" in guard
        assert '"-c"' in guard
        assert "SSH_ORIGINAL_COMMAND" in guard
        assert "execvp" in guard


def _run_main(args: list[str], env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Run sable.app.main in a subprocess, since the guard execs over the process."""
    import os

    # MAIN is sable/app/main.py, so the repo root is three levels up.
    env = {**os.environ, "PYTHONPATH": str(MAIN.parents[2])}
    if env_extra:
        env.update(env_extra)
    env.pop("SSH_ORIGINAL_COMMAND", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "sable.app.main", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="execvp to /bin/bash needs a POSIX host")
class TestBypassBehaviour:
    def test_dash_c_runs_the_command_in_bash(self):
        """The path sshd actually uses for `ssh host cmd`, scp and rsync."""
        result = _run_main(["-c", "echo bypass-ok"])

        assert result.returncode == 0
        assert "bypass-ok" in result.stdout
        assert "Sable" not in result.stdout, "the REPL banner means the bypass did not fire"

    def test_dash_c_does_not_start_the_repl(self):
        result = _run_main(["-c", "true"])

        assert "type naturally" not in result.stdout

    def test_ssh_original_command_still_works(self):
        """ForceCommand and authorized_keys command= route through the env."""
        result = _run_main([], {"SSH_ORIGINAL_COMMAND": "echo env-path-ok"})

        assert result.returncode == 0
        assert "env-path-ok" in result.stdout

    def test_exit_status_propagates(self):
        """scp and git read the exit code, so it has to survive the exec."""
        assert _run_main(["-c", "exit 7"]).returncode == 7

    def test_command_with_pipes_and_quotes_survives(self):
        result = _run_main(["-c", "echo 'a b' | tr ' ' '-'"])

        assert "a-b" in result.stdout

    def test_argv_takes_precedence_over_the_env(self):
        result = _run_main(["-c", "echo from-argv"], {"SSH_ORIGINAL_COMMAND": "echo from-env"})

        assert "from-argv" in result.stdout
        assert "from-env" not in result.stdout
