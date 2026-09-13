"""The resize hook: sidebar collapses on a narrow window and comes back.

`docker/playground-entry.sh` writes `/usr/local/bin/sable-fit-panes` and
binds it to tmux's `client-attached` and `client-resized` hooks. It exists
because tmux resizes a session to whatever client attaches, which squeezes
splits made beforehand: the entry script builds a 48-column sidebar in a
detached 200x50 session, and a 90-column terminal attaching would otherwise
leave the shell pane a few columns wide and unusable.

`test_playground_layout.py` builds the three panes but never attaches a
client, so none of that logic runs there. This covers the hook itself.

The script is exercised directly rather than by attaching a real terminal:
its four branches are decided by `#{window_width}` and `#{window_height}`,
and `tmux resize-window` sets those deterministically. Driving a real client
would add a terminal emulator to the test for no extra coverage.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
import uuid

import pytest

pytestmark = pytest.mark.integration

SIDEBAR_COLS = 48
TASKS_ROWS = 10        # the hook's target, not the 12 the entry script splits at
COLLAPSED = 2

WIDE, NARROW = 170, 90         # either side of the hook's 120-column threshold
TALL, SHORT = 45, 24           # either side of its 30-row threshold

requires_tmux = pytest.mark.skipif(
    not shutil.which("tmux") or os.name != "posix",
    reason="needs tmux on a POSIX host (the playground image)",
)


def _tmux(*args: str, check: bool = True) -> str:
    result = subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=30)
    if check and result.returncode != 0:
        raise AssertionError(f"tmux {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _width(pane_id: str) -> int:
    return int(_tmux("display-message", "-t", pane_id, "-p", "#{pane_width}"))


def _height(pane_id: str) -> int:
    return int(_tmux("display-message", "-t", pane_id, "-p", "#{pane_height}"))


@pytest.fixture
def session(tmp_path):
    """A three-pane session plus the fit-panes script bound to its ids.

    The script is regenerated here with this session's real pane ids, the
    same way the entry script generates it with the ids it just created.
    Its body is kept byte-identical to the shipped one so the branches under
    test are the ones that ship.
    """
    if not shutil.which("tmux") or os.name != "posix":
        pytest.skip("needs tmux on a POSIX host")

    name = f"sable-resize-{uuid.uuid4().hex[:8]}"
    _tmux("new-session", "-d", "-s", name, "-x", str(WIDE), "-y", str(TALL))
    try:
        main = _tmux("display-message", "-t", f"{name}:0.0", "-p", "#{pane_id}")
        side = _tmux("split-window", "-h", "-t", main, "-l", str(SIDEBAR_COLS),
                     "-P", "-F", "#{pane_id}")
        tasks = _tmux("split-window", "-v", "-t", main, "-l", "12",
                      "-P", "-F", "#{pane_id}")

        script = tmp_path / "sable-fit-panes"
        script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env bash
            SIDE_ID="{side}"
            TASKS_ID="{tasks}"
            W=$(tmux display-message -t {name} -p '#{{window_width}}')
            H=$(tmux display-message -t {name} -p '#{{window_height}}')
            if [ "$W" -ge 120 ]; then
                tmux resize-pane -t "$SIDE_ID" -x 48
            else
                tmux resize-pane -t "$SIDE_ID" -x 2 2>/dev/null || true
            fi
            if [ "$H" -ge 30 ]; then
                tmux resize-pane -t "$TASKS_ID" -y 10
            else
                tmux resize-pane -t "$TASKS_ID" -y 2 2>/dev/null || true
            fi
            """), encoding="utf-8")
        script.chmod(0o755)

        def fit(width: int, height: int) -> None:
            """Resize the window, then run the hook as tmux would."""
            _tmux("resize-window", "-t", name, "-x", str(width), "-y", str(height))
            subprocess.run([str(script)], check=True, timeout=30)

        yield {"name": name, "main": main, "sidebar": side,
               "tasks": tasks, "fit": fit}
    finally:
        _tmux("kill-session", "-t", name, check=False)


@requires_tmux
class TestSidebarWidth:
    def test_stays_48_columns_on_a_wide_window(self, session):
        session["fit"](WIDE, TALL)
        assert _width(session["sidebar"]) == SIDEBAR_COLS

    def test_collapses_on_a_narrow_window(self, session):
        """Below 120 columns the sidebar gets out of the way.

        Without this the shell pane is squeezed to a handful of columns on a
        small terminal, which is what the hook was written to prevent.
        """
        session["fit"](NARROW, TALL)
        assert _width(session["sidebar"]) == COLLAPSED

    def test_shell_pane_takes_the_reclaimed_width(self, session):
        """Collapsing is only useful if the shell actually gets the space."""
        session["fit"](NARROW, TALL)
        assert _width(session["main"]) == NARROW - COLLAPSED - 1

    def test_returns_when_the_window_widens_again(self, session):
        """The bug this guards against is a one-way collapse."""
        session["fit"](NARROW, TALL)
        assert _width(session["sidebar"]) == COLLAPSED
        session["fit"](WIDE, TALL)
        assert _width(session["sidebar"]) == SIDEBAR_COLS

    def test_survives_repeated_resizes(self, session):
        """client-resized fires on every drag, so the hook runs constantly."""
        for _ in range(3):
            session["fit"](NARROW, TALL)
            session["fit"](WIDE, TALL)
        assert _width(session["sidebar"]) == SIDEBAR_COLS
        assert _width(session["main"]) == WIDE - SIDEBAR_COLS - 1

    @pytest.mark.parametrize("width,expected", [
        (119, COLLAPSED),      # just under
        (120, SIDEBAR_COLS),   # exactly at the threshold, -ge so it expands
        (121, SIDEBAR_COLS),   # just over
    ])
    def test_threshold_is_exactly_120(self, session, width, expected):
        """Pinning the boundary, since an off-by-one here is invisible."""
        session["fit"](width, TALL)
        assert _width(session["sidebar"]) == expected


@requires_tmux
class TestTasksBarHeight:
    def test_is_10_rows_on_a_tall_window(self, session):
        session["fit"](WIDE, TALL)
        assert _height(session["tasks"]) == TASKS_ROWS

    def test_collapses_on_a_short_window(self, session):
        session["fit"](WIDE, SHORT)
        assert _height(session["tasks"]) == COLLAPSED

    def test_returns_when_the_window_grows(self, session):
        session["fit"](WIDE, SHORT)
        assert _height(session["tasks"]) == COLLAPSED
        session["fit"](WIDE, TALL)
        assert _height(session["tasks"]) == TASKS_ROWS

    @pytest.mark.parametrize("height,expected", [
        (29, COLLAPSED),
        (30, TASKS_ROWS),
        (31, TASKS_ROWS),
    ])
    def test_threshold_is_exactly_30(self, session, height, expected):
        session["fit"](WIDE, height)
        assert _height(session["tasks"]) == expected


@requires_tmux
class TestBothAtOnce:
    def test_small_window_collapses_both(self, session):
        """A phone-sized terminal should leave almost all of it to the shell."""
        session["fit"](NARROW, SHORT)
        assert _width(session["sidebar"]) == COLLAPSED
        assert _height(session["tasks"]) == COLLAPSED
        assert _width(session["main"]) == NARROW - COLLAPSED - 1

    def test_large_window_restores_both(self, session):
        session["fit"](NARROW, SHORT)
        session["fit"](WIDE, TALL)
        assert _width(session["sidebar"]) == SIDEBAR_COLS
        assert _height(session["tasks"]) == TASKS_ROWS

    def test_panes_still_tile_after_a_collapse(self, session):
        """Widths must still add up, or a gap or overlap has appeared."""
        session["fit"](NARROW, SHORT)
        assert _width(session["main"]) + 1 + _width(session["sidebar"]) == NARROW
        assert _height(session["main"]) + 1 + _height(session["tasks"]) == SHORT
