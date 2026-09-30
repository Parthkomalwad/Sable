"""The first screen: prompt, welcome banner, tasks pane, tmux theme.

Styling itself is not asserted (a colour code is not a design); these hold
what each piece says and the shapes that were broken or easy to break.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

from sable.ui.prompt.session import _render_prompt
from sable.ui.sidebar import agents_panel

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(s: str) -> str:
    return ANSI.sub("", s)


def _git(monkeypatch, branch: str):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: SimpleNamespace(stdout=branch, returncode=0))


class TestPrompt:
    def test_reads_like_the_readme(self, monkeypatch):
        _git(monkeypatch, "main")
        assert _plain(_render_prompt("/srv/api", 0)) == "/srv/api main ✓ ❯ "

    def test_a_failure_shows_the_exit_code(self, monkeypatch):
        _git(monkeypatch, "main")
        assert _plain(_render_prompt("/srv/api", 127)) == "/srv/api main ✗ 127 ❯ "

    def test_no_branch_outside_a_repo(self, monkeypatch):
        _git(monkeypatch, "")
        assert _plain(_render_prompt("/tmp", 0)) == "/tmp ✓ ❯ "

    def test_home_is_shortened(self, monkeypatch):
        _git(monkeypatch, "")
        home = str(Path.home())
        assert _plain(_render_prompt(home + "/proj", 0)).startswith("~/proj ")

    def test_no_clock_in_the_prompt(self, monkeypatch):
        """The tmux status bar carries the time now."""
        _git(monkeypatch, "")
        assert not re.search(r"\d\d:\d\d", _plain(_render_prompt("/tmp", 0)))


class TestWelcome:
    def test_names_version_backend_and_hints(self, monkeypatch):
        from sable import __version__
        from sable.app.main import _welcome
        monkeypatch.delenv("SABLE_MOCK_LLM", raising=False)
        out = _plain(_welcome(SimpleNamespace(backend="openai", model="gpt-4o-mini")))
        assert f"sable {__version__}" in out
        assert "openai · gpt-4o-mini" in out
        assert "/tour" in out and "/help" in out

    def test_says_when_responses_are_canned(self, monkeypatch):
        from sable.app.main import _welcome
        monkeypatch.setenv("SABLE_MOCK_LLM", "1")
        out = _plain(_welcome(SimpleNamespace(backend="openai", model="gpt-4o-mini")))
        assert "mock responses" in out


class TestTasksPane:
    def test_empty_explains_what_goes_here(self):
        lines = agents_panel.render([], 100).plain.splitlines()
        assert lines[0] == ""                                   # breathing room under the border
        assert lines[1] == "  TASKS   none running"
        assert lines[3].startswith("  /task new <name> <goal>")
        assert agents_panel.pane_height([]) == agents_panel.MIN_HEIGHT
        assert len(lines) < agents_panel.MIN_HEIGHT

    def test_rows_show_state_step_goal_and_cost(self):
        rows = [("smoke-suite", "running", 2, "run the smoke tests", 0.0142),
                ("migrate-db", "completed", 4, "migrate", 0.0)]
        lines = agents_panel.render(rows, 100).plain.splitlines()[1:]
        assert lines[0].startswith("  TASKS") and "1 running" in lines[0]
        assert "smoke-suite" in lines[1] and "running" in lines[1] and "step 2" in lines[1]
        assert lines[1].rstrip().endswith("$0.0142")
        assert "done" in lines[2]

    def test_long_goals_are_cut_to_the_width(self):
        rows = [("t", "running", 1, "x" * 500, 0.0)]
        line = agents_panel.render(rows, 90).plain.splitlines()[2]
        assert len(line) <= 90 and "…" in line

    def test_height_grows_with_tasks_and_is_capped(self):
        many = [(f"t{i}", "running", 1, "g", 0.0) for i in range(20)]
        assert agents_panel.pane_height(many[:1]) == agents_panel.MIN_HEIGHT
        assert agents_panel.pane_height(many[:5]) == 8
        assert agents_panel.pane_height(many) == agents_panel.MAX_ROWS + 3


def test_tmux_theme_is_shared_by_install_and_playground():
    root = Path(__file__).resolve().parents[2]
    theme = root / "sable" / "data" / "tmux-theme.conf"
    assert theme.exists()
    assert "sable/data/tmux-theme.conf" in (root / "install.sh").read_text(encoding="utf-8")
    assert "sable/data/tmux-theme.conf" in (root / "docker" / "playground-entry.sh").read_text(encoding="utf-8")
