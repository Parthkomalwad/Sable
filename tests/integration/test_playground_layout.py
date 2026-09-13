"""The three-pane tmux layout, built and inspected the way a user sees it.

Phase 0 shipped a playground that was a single bare pane while `install.sh`
built three on a real machine, so the gate item "sidebar visible" could not be
met and nothing noticed. The sidebar and the tasks bar are the parts of Sable
a user looks at continuously; if they are missing, mis-sized or blank, the
product is broken even though every unit test passes.

So this builds the same layout `docker/playground-entry.sh` builds, at the
same size, and asserts on geometry AND on rendered content.

Panes are addressed by **pane id** (`%3`), never by index. tmux renumbers
indices as panes are created and destroyed, so `playground:0.1` is not
reliably the sidebar. The entry script and `install.sh` both capture ids from
`split-window -P -F '#{pane_id}'` for the same reason.

Requires: tmux and a POSIX host, i.e. the playground image.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid

import pytest

pytestmark = pytest.mark.integration

# The window the playground sizes its session to. install.sh reads the real
# terminal instead, but a fixed size is what makes the geometry assertions
# below meaningful.
WIDTH = 160
HEIGHT = 45

SIDEBAR_COLS = 48   # entry script: split-window -h -l 48
TASKS_ROWS = 12     # entry script: split-window -v -l 12

requires_tmux = pytest.mark.skipif(
    not shutil.which("tmux") or os.name != "posix",
    reason="needs tmux on a POSIX host (the playground image)",
)


def _tmux(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["tmux", *args], capture_output=True, text=True, timeout=30,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"tmux {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _pane_dims(pane_id: str) -> tuple[int, int]:
    out = _tmux("display-message", "-t", pane_id, "-p", "#{pane_width}x#{pane_height}")
    width, height = out.split("x")
    return int(width), int(height)


def _capture(pane_id: str) -> str:
    """Rendered text of a pane, escape sequences stripped by tmux itself."""
    return _tmux("capture-pane", "-t", pane_id, "-p")


def _wait_for_content(pane_id: str, needle: str, timeout: float = 30.0) -> str:
    """Poll a pane until `needle` renders, then return the whole capture.

    The sidebar and tasks panes are Rich `Live` loops started via send-keys,
    so there is a real startup delay: the shell has to run, Python has to
    import, and the first frame has to paint. Polling beats a fixed sleep,
    which would either be flaky or slow.
    """
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        last = _capture(pane_id)
        if needle in last:
            return last
        time.sleep(0.5)
    raise AssertionError(
        f"{needle!r} never rendered in pane {pane_id} within {timeout}s. "
        f"Last capture was:\n{last}"
    )


from tests.integration.conftest import TEST_MODEL, ensure_config as _ensure_config


@pytest.fixture(scope="module")
def layout():
    """Build the playground's three-pane session and yield the pane ids.

    Mirrors docker/playground-entry.sh. The main pane runs a plain bash
    rather than `exec sable`, because this test is about the layout the
    shell sits in, and a REPL waiting on stdin adds nothing to assert on.
    """
    if not shutil.which("tmux") or os.name != "posix":
        pytest.skip("needs tmux on a POSIX host")

    _ensure_config()
    session = f"sable-layout-test-{uuid.uuid4().hex[:8]}"
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = f"PYTHONPATH={repo_root}"

    _tmux("new-session", "-d", "-s", session, "-x", str(WIDTH), "-y", str(HEIGHT))
    try:
        main = _tmux("display-message", "-t", f"{session}:0.0", "-p", "#{pane_id}")

        sidebar = _tmux(
            "split-window", "-h", "-t", main, "-l", str(SIDEBAR_COLS),
            "-P", "-F", "#{pane_id}",
        )
        _tmux(
            "send-keys", "-t", sidebar,
            f"clear; {env} python3 -m shell.telemetry.watch", "Enter",
        )

        tasks = _tmux(
            "split-window", "-v", "-t", main, "-l", str(TASKS_ROWS),
            "-P", "-F", "#{pane_id}",
        )
        _tmux(
            "send-keys", "-t", tasks,
            f"clear; {env} python3 -m shell.tasks.panel", "Enter",
        )

        yield {"session": session, "main": main, "sidebar": sidebar, "tasks": tasks}
    finally:
        _tmux("kill-session", "-t", session, check=False)


@requires_tmux
class TestPlaygroundLayout:
    def test_session_has_exactly_three_panes(self, layout):
        """A regression guard on the bug itself: the playground had one."""
        ids = _tmux(
            "list-panes", "-t", layout["session"], "-F", "#{pane_id}"
        ).splitlines()
        assert len(ids) == 3, f"expected 3 panes, found {len(ids)}: {ids}"
        for role in ("main", "sidebar", "tasks"):
            assert layout[role] in ids, f"{role} pane {layout[role]} is missing"

    def test_pane_ids_are_distinct(self, layout):
        """Three roles, three ids. Cheap, and it catches a split that failed
        silently and handed back an id we already had.
        """
        ids = {layout["main"], layout["sidebar"], layout["tasks"]}
        assert len(ids) == 3

    def test_sidebar_is_48_columns_and_full_height(self, layout):
        """The sidebar is the fixed-width column on the right.

        It spans the full window height because it is split off the whole
        window before the tasks bar is carved out of the left-hand side.
        """
        width, height = _pane_dims(layout["sidebar"])
        assert width == SIDEBAR_COLS, f"sidebar is {width} columns, expected {SIDEBAR_COLS}"
        assert height == HEIGHT, f"sidebar is {height} rows, expected the full {HEIGHT}"

    def test_tasks_bar_is_12_rows_and_sits_under_the_shell(self, layout):
        """The tasks bar takes the bottom of the left-hand column only, so it
        is as wide as the shell pane, not as wide as the window.
        """
        width, height = _pane_dims(layout["tasks"])
        assert height == TASKS_ROWS, f"tasks bar is {height} rows, expected {TASKS_ROWS}"
        expected_width = WIDTH - SIDEBAR_COLS - 1  # 1 column of pane border
        assert width == expected_width, (
            f"tasks bar is {width} columns, expected {expected_width} "
            f"({WIDTH} window - {SIDEBAR_COLS} sidebar - 1 border)"
        )

    def test_shell_pane_gets_the_remaining_space(self, layout):
        """Whatever is left after the sidebar and the tasks bar. If this
        drifts, one of the two splits silently took more than it should.
        """
        width, height = _pane_dims(layout["main"])
        assert width == WIDTH - SIDEBAR_COLS - 1
        assert height == HEIGHT - TASKS_ROWS - 1  # 1 row of pane border

    def test_panes_tile_the_window_without_gaps(self, layout):
        """Widths and heights add up to the window, borders included.

        Geometry asserted per pane can all be individually right while the
        layout as a whole is wrong, so check the sum too.
        """
        main_w, main_h = _pane_dims(layout["main"])
        side_w, side_h = _pane_dims(layout["sidebar"])
        tasks_w, tasks_h = _pane_dims(layout["tasks"])

        assert main_w + 1 + side_w == WIDTH
        assert tasks_w + 1 + side_w == WIDTH
        assert main_h + 1 + tasks_h == HEIGHT
        assert side_h == HEIGHT

    def test_sidebar_renders_the_session_panel_with_the_model(self, layout):
        """Geometry is not enough: a correctly sized blank pane is still a
        broken sidebar. Assert the first Rich panel actually paints.

        `_panel_session` in shell/telemetry/watch.py draws a "session" panel
        whose first row is the configured model, so both strings appearing
        means the watcher started, read config and rendered.
        """
        rendered = _wait_for_content(layout["sidebar"], "session")
        assert "Model" in rendered, (
            f"sidebar rendered without the model row:\n{rendered}"
        )
        # The pane read the real config rather than printing a placeholder.
        # Only asserted when this fixture wrote the config; inside a live
        # playground the model is whatever the user configured.
        import json
        import pathlib

        configured = json.loads(
            (pathlib.Path.home() / ".config" / "agentic-shell" / "config.json").read_text()
        )["model"]
        if configured == TEST_MODEL:
            assert TEST_MODEL in rendered, (
                f"sidebar shows a model other than the configured one:\n{rendered}"
            )

    def test_sidebar_renders_the_system_panel(self, layout):
        """A second panel, so a partial render that stops after the first
        one does not pass.
        """
        rendered = _wait_for_content(layout["sidebar"], "system")
        assert "CPU" in rendered, f"system panel has no CPU row:\n{rendered}"

    def test_tasks_bar_renders_its_header(self, layout):
        """`shell/tasks/panel.py` draws a "TASKS" panel with a TASK/GOAL
        table header. With no tasks running the body reads "no tasks yet",
        which is itself proof the panel rendered rather than crashed.
        """
        rendered = _wait_for_content(layout["tasks"], "TASKS")
        assert "TASK" in rendered
        assert "GOAL" in rendered, f"tasks table header is missing:\n{rendered}"

    def test_no_pane_shows_a_python_traceback(self, layout):
        """The failure mode a content check can otherwise sail past: the
        watcher dies on import and the pane shows a traceback instead.
        """
        for role in ("sidebar", "tasks"):
            rendered = _capture(layout[role])
            assert "Traceback (most recent call last)" not in rendered, (
                f"{role} pane crashed:\n{rendered}"
            )
