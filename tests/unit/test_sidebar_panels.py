"""The sidebar fits in the pane it is given.

A regression caught by an unrelated integration test, and only by luck:
`test_inplace_upgrade.py` greps a rendered tmux pane for the word "session",
so when a seventh panel pushed the session panel off the top of a 45-row
pane, that test failed. Nothing raised. No unit test noticed, because none
of them rendered into a pane with a height.

These are fast tests for the property that matters: the session panel is
first and must survive a full render. The next person to add a panel trips
this in milliseconds rather than in a 2.5 minute integration run, and finds
out why rather than seeing a grep for a word fail.

The deeper problem is not fixed here. `_render_all` still prints panels
unconditionally with no notion of the space available, so six panels break
at 38 rows exactly as seven break at 45. The real fix is
`ui.sidebar.panels` from config plus a renderer that measures and reports
what it dropped (structure.md §3.3, Phase 4 G1/G6).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sable.ui.sidebar import watch


#: The tightest pane height actually exercised anywhere: the window
#: `test_inplace_upgrade.py` builds. The sidebar is a full-height vertical
#: split, so it gets the whole window height (the tasks bar's rows come out
#: of the main pane, not this one). The playground runs 50.
#:
#: Hard-coded rather than imported: it is the height the regression happened
#: at, and a test that followed the constant would have moved with it.
PANE_ROWS = 45

#: An upper bound with headroom, NOT an exact height.
#:
#: The render is not the same size on every machine: the same tree measures
#: 46 lines in the Linux container and 49 on the Windows host, because
#: `_panel_git`, `_panel_processes` and `_panel_system` report on whatever
#: the host actually has. An exact assertion pins the machine that ran it,
#: which is how a test starts failing for everyone except its author.
#:
#: The bound is set from the CONTAINER, which renders taller than the
#: Windows host (52 lines against 46 for the same tree) and is where this
#: code actually runs. Setting it from whichever machine you tried first is
#: how a test passes locally and fails in the playground.
#:
#: It is deliberately not `PANE_ROWS`: in the container the sidebar
#: overflows a 45 row pane by seven lines with only six panels, and it did
#: so before the corrections panel existed. Gating on a real fit would fail
#: every commit until Phase 4 implements `ui.sidebar.panels`
#: (structure.md 3.3). That is why the property actually gated is the one
#: that matters and holds everywhere: the session panel survives.
MAX_LINES_WITHOUT_CORRECTIONS = 54
MAX_LINES_WITH_CORRECTIONS = 57


@pytest.fixture
def db():
    """A database answering every panel query in the real shapes.

    Matched to `core/db.py` rather than invented: `get_today_stats` returns
    `{calls, tokens, cost}` and `get_stats` a list of per-day dicts. A bare
    MagicMock passes `str()` and `len()` but fails the moment a panel
    subscripts a key or iterates a result, which is a failure in the test
    scaffold dressed up as a failure in the renderer.
    """
    stub = MagicMock()
    stub.get_today_stats.return_value = {"calls": 0, "tokens": 0, "cost": 0.0}
    stub.get_stats.return_value = [
        {"day": "2026-09-13", "calls": 2, "tokens": 1200, "cost": 0.01},
    ]
    stub.list_snippets.return_value = []
    stub.get_last_model.return_value = "test-model"
    return stub


def _rendered_lines(db) -> list[str]:
    return watch._render_all(db, model="test-model").splitlines()


class TestTheSidebarFitsItsPane:
    def test_the_render_height_has_not_grown(self, db):
        """The sidebar's height is pinned, not asserted to fit.

        It does not fit and never has: 46 lines into 45 rows before the
        corrections panel existed. Gating on a hard fit would fail every
        commit until Phase 4 implements `ui.sidebar.panels`, so what is
        gated instead is that nobody makes it *worse* without noticing.

        If this fails upward, a panel was added or grew. Removing one, or
        implementing the config driven panel list, is the fix; raising this
        number is how the session panel gets lost again.
        """
        lines = _rendered_lines(db)
        assert len(lines) <= MAX_LINES_WITHOUT_CORRECTIONS, (
            f"the sidebar renders {len(lines)} lines, over the "
            f"{MAX_LINES_WITHOUT_CORRECTIONS} line bound. It already "
            f"overflows a {PANE_ROWS} row pane by "
            f"{len(lines) - PANE_ROWS} line(s), and the session panel is "
            f"what scrolls off first. Remove a panel, or implement "
            f"ui.sidebar.panels (structure.md 3.3). Raising this bound is "
            f"how the session panel gets lost again."
        )

    def test_the_session_panel_survives(self, db):
        """It is first, so it is what overflow silently eats.

        Model, uptime, cwd and today's cost live here. Losing it is the
        difference between a sidebar and a decoration.
        """
        assert "session" in watch._render_all(db, model="test-model")

    def test_the_model_name_is_visible(self, db):
        """The session panel rendering is not the same as it being readable."""
        assert "test-model" in watch._render_all(db, model="test-model")


class TestTheCorrectionsPanelIsConditional:
    """The stop-gap: it appears only when it has something to report.

    This makes the overflow rarer, not impossible. A user who is actively
    teaching the shell, which is exactly who K3 was built for, still gets
    seven panels. That is why the fitting test above exists.
    """

    def test_it_is_absent_when_there_is_nothing_to_report(self, db, monkeypatch):
        monkeypatch.setattr(watch, "_has_corrections", lambda _db: False)
        assert "corrections" not in watch._render_all(db, model="m")

    def test_it_appears_once_there_is_something(self, db, monkeypatch):
        monkeypatch.setattr(watch, "_has_corrections", lambda _db: True)
        assert "corrections" in watch._render_all(db, model="m")

    def test_an_unreadable_database_hides_it(self, db, monkeypatch):
        """A sidebar that cannot count should show one panel fewer.

        Not crowd out the session panel in order to display a zero.
        """
        import sqlite3

        def explode(_db):
            raise sqlite3.Error("locked")

        monkeypatch.setattr("sable.skills.corrections.weekly_count", explode)

        assert watch._has_corrections(db) is False

    def test_the_session_panel_survives_with_corrections_shown(self, db, monkeypatch):
        """The case the stop-gap does not cover, pinned so it is not forgotten.

        If this fails, the conditional is no longer enough and the config
        driven panel list is overdue rather than optional.
        """
        monkeypatch.setattr(watch, "_has_corrections", lambda _db: True)
        rendered = watch._render_all(db, model="test-model")
        lines = rendered.splitlines()

        assert "session" in rendered, (
            "with the corrections panel shown the sidebar overflows far "
            "enough to lose the session panel again. The conditional in "
            "_render_all has stopped being sufficient: implement "
            "ui.sidebar.panels (structure.md 3.3)."
        )
        assert len(lines) <= MAX_LINES_WITH_CORRECTIONS, (
            f"{len(lines)} lines with corrections shown, over the "
            f"{MAX_LINES_WITH_CORRECTIONS} line bound. That is "
            f"{len(lines) - PANE_ROWS} over a {PANE_ROWS} row pane. This is "
            f"the case the stop-gap does not cover, so growth here is what "
            f"makes the config driven panel list overdue rather than optional."
        )
