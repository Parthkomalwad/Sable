"""Upgrading a machine that already had the old `shell/` layout.

This is the scenario the compat shim in `shell/__init__.py` exists for, and
the one with real consequences: Sable is the user's **login shell**. If an
upgrade breaks `python -m shell.main`, the next SSH login lands on a
traceback instead of a shell, and `scp`/`rsync` stop working too.

The danger is not the Python import, which is easy to get right. It is that
an upgrade leaves **stale command strings** behind that nobody rewrites:

  - `install.sh` bakes `python -m shell.telemetry.watch` and
    `python -m shell.tasks.panel` into tmux `send-keys` strings. A tmux
    session started before the upgrade keeps re-running those forever,
    because each pane loops `while true; do <old command>; done`.
  - the `sable` wrapper in /usr/local/bin wraps `python -m shell.main`, and
    `/etc/shells` plus the chsh'd login shell both point at that wrapper.
    Neither is rewritten by pulling new source.

So this test does not check "can I import shell". It reconstructs the old
invocations verbatim and runs them against the new tree, which is what an
upgraded-but-not-restarted machine actually does.

Requires the playground image (tmux, sshd, a POSIX host).
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from tests.integration.conftest import ensure_config as _ensure_config

pytestmark = pytest.mark.integration

# Exactly the strings install.sh and the Dockerfiles bake in. Written out
# literally rather than imported, because the whole point is to catch the
# case where the source moved and these did not.
LEGACY_MODULES = [
    "shell.main",
    "shell.telemetry.watch",
    "shell.tasks.panel",
]

# Old import paths that user config, docs, or a half-updated checkout might
# still use. Each maps to where it lives now.
LEGACY_IMPORTS = {
    "shell.loop": "sable.app.repl",
    "shell.router": "sable.agents.router",
    "shell.safety": "sable.policy.engine",
    "shell.executor": "sable.core.executor",
    "shell.config.schema": "sable.core.config.schema",
    "shell.telemetry.db": "sable.core.db",
    "shell.tasks.orchestrator": "sable.agents.orchestrator",
    "shell.tasks.agent": "sable.agents.worker",
    "shell.tasks.memory": "sable.memory.task",
    "shell.skills.pattern_watcher": "sable.skills.watcher",
    "shell.memory.store": "sable.memory.session",
    "shell.tui.layout": "sable.ui.tmux.layout",
    "shell.clipboard.manager": "sable.ui.clipboard.manager",
}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

requires_posix = pytest.mark.skipif(
    os.name != "posix", reason="the login-shell paths are POSIX only",
)


def _run(args: list[str], timeout: int = 30, **kwargs) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": REPO_ROOT}
    env.update(kwargs.pop("env_extra", {}))
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout,
        cwd=REPO_ROOT, env=env, **kwargs,
    )


@requires_posix
class TestLegacyEntryPointsStillRun:
    """`python -m shell.*`, the way an unrestarted machine invokes it."""

    def test_login_shell_module_still_starts(self):
        """`python -m shell.main --version` is the installed wrapper's core.

        If this fails, every new SSH login on an upgraded box gets a
        traceback instead of a shell.
        """
        result = _run(["python3", "-m", "shell.main", "--version"])
        assert result.returncode == 0, (
            f"the legacy login-shell entry point is broken, an upgraded "
            f"machine would be locked out:\n{result.stderr}"
        )
        assert "sable" in result.stdout.lower()

    def test_bypass_still_works_through_the_legacy_module(self):
        """scp and rsync go through this path on an upgraded machine."""
        result = _run(["python3", "-m", "shell.main", "-c", "echo upgrade-ok"])
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "upgrade-ok"

    def test_env_var_bypass_still_works_through_the_legacy_module(self):
        result = _run(
            ["python3", "-m", "shell.main"],
            env_extra={"SSH_ORIGINAL_COMMAND": "echo env-upgrade-ok"},
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "env-upgrade-ok"

    @pytest.mark.parametrize("module", ["shell.telemetry.watch", "shell.tasks.panel"])
    def test_legacy_pane_modules_start_and_keep_running(self, module):
        """The two tmux panes an already-running session re-executes forever.

        Both are render loops with no natural exit, so "still running when
        the timeout fires" is success and any exit is a failure. Without
        this, an upgrade leaves the sidebar and tasks bar as a scrolling
        traceback in a session the user never restarted.
        """
        result = _run(["timeout", "5", "python3", "-m", module])
        assert result.returncode == 124, (
            f"{module} exited with {result.returncode} instead of running "
            f"until the timeout. An upgraded session's pane would show this "
            f"on repeat:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
        )

    def test_no_module_prints_a_traceback_on_startup(self):
        """A pane can keep running and still be broken, if it loops on an
        error it catches. Check the output too.
        """
        for module in ("shell.telemetry.watch", "shell.tasks.panel"):
            result = _run(["timeout", "5", "python3", "-m", module])
            combined = result.stdout + result.stderr
            assert "Traceback (most recent call last)" not in combined, (
                f"{module} printed a traceback:\n{combined[-2000:]}"
            )
            assert "ModuleNotFoundError" not in combined
            # A DeprecationWarning here is fine and expected: these ARE the
            # legacy names. What must not appear is a failure.


@requires_posix
class TestLegacyImportPaths:
    """Old dotted paths keep resolving, to the same object."""

    @pytest.mark.parametrize("old,new", sorted(LEGACY_IMPORTS.items()))
    def test_old_path_resolves_to_the_new_module(self, old, new):
        """Same object, not a second copy.

        A shim that re-executes the module under the old name would give two
        module objects, so module-level singletons (the DB connection, a
        patched attribute) would silently diverge.
        """
        script = (
            "import warnings; warnings.simplefilter('ignore')\n"
            f"import {old} as legacy\n"
            f"import {new} as current\n"
            "import sys\n"
            "assert legacy is current, 'shim returned a different module object'\n"
            "print('ok')\n"
        )
        result = _run(["python3", "-c", script])
        assert result.returncode == 0, f"{old} -> {new} failed:\n{result.stderr}"

    def test_old_paths_warn_so_the_deprecation_is_visible(self):
        """Silent compatibility is how a shim becomes permanent."""
        result = _run([
            "python3", "-W", "error::DeprecationWarning",
            "-c", "import shell.loop",
        ])
        assert result.returncode != 0, "no DeprecationWarning was raised"
        assert "DeprecationWarning" in result.stderr
        assert "sable.app.repl" in result.stderr, (
            "the warning should name the new path so the fix is obvious"
        )

    def test_from_import_form_works(self):
        """`from shell import loop` goes through __getattr__, not the finder."""
        result = _run([
            "python3", "-c",
            "import warnings; warnings.simplefilter('ignore')\n"
            "from shell import loop\n"
            "import sable.app.repl as r\n"
            "assert loop is r\n"
            "print('ok')\n",
        ])
        assert result.returncode == 0, result.stderr

    def test_unknown_shell_submodule_still_raises_importerror(self):
        """The shim must not swallow a genuine typo into something odd."""
        result = _run([
            "python3", "-c",
            "import warnings; warnings.simplefilter('ignore')\n"
            "import shell.does_not_exist\n",
        ])
        assert result.returncode != 0
        assert "ModuleNotFoundError" in result.stderr or "ImportError" in result.stderr


@requires_posix
@pytest.mark.skipif(not shutil.which("tmux"), reason="needs tmux")
class TestUpgradedSessionPanes:
    """A tmux session holding pre-upgrade command strings.

    Reconstructs what install.sh leaves running and checks the panes render
    against the new tree, rather than filling with ModuleNotFoundError.
    """

    def test_panes_started_with_legacy_commands_render(self):
        import time
        import uuid

        # An upgraded machine has a config file already; a bare pytest run
        # against the image does not, and without one the pane renders the
        # first-run wizard instead of panels. Writing it keeps the test
        # about the upgrade rather than about onboarding.
        _ensure_config()

        session = f"sable-upgrade-{uuid.uuid4().hex[:8]}"

        def tmux(*args, check=True):
            result = subprocess.run(
                ["tmux", *args], capture_output=True, text=True, timeout=30,
            )
            if check and result.returncode != 0:
                raise AssertionError(f"tmux {' '.join(args)}: {result.stderr}")
            return result.stdout.strip()

        # The pane command install.sh writes, verbatim apart from the venv
        # path: a restart loop around the OLD module name.
        legacy_sidebar = (
            f"clear; while true; do PYTHONPATH={REPO_ROOT} "
            f"python3 -m shell.telemetry.watch; sleep 2; done"
        )

        tmux("new-session", "-d", "-s", session, "-x", "160", "-y", "45")
        try:
            pane = tmux("display-message", "-t", f"{session}:0.0", "-p", "#{pane_id}")
            tmux("send-keys", "-t", pane, legacy_sidebar, "Enter")

            deadline = time.monotonic() + 30
            captured = ""
            while time.monotonic() < deadline:
                captured = tmux("capture-pane", "-t", pane, "-p")
                if "session" in captured:
                    break
                time.sleep(0.5)

            assert "session" in captured, (
                "a pane started with the pre-upgrade command never rendered. "
                f"An upgraded session would look like this:\n{captured}"
            )
            assert "ModuleNotFoundError" not in captured
            assert "Traceback" not in captured
        finally:
            tmux("kill-session", "-t", session, check=False)
