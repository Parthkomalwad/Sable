"""The command catalog behind /help, /help <cmd>, Tab completion and Ctrl+P."""
from __future__ import annotations

from prompt_toolkit.document import Document
from rich.console import Console

from sable.ui import commands
from sable.ui.prompt.session import _ShellCompleter

#: Every builtin the old flat /help listed. None may drop out of the catalog.
OLD_HELP = {
    "/new", "/clear", "/exit", "/config", "/mode", "/model", "/stats", "/selfcheck",
    "/history", "/clip", "/task", "/skill", "/corrections", "/alias", "/route", "/why",
    "/inbox", "/approve", "/dash", "/mcp", "/plugin", "/tools", "/audit", "/schedule",
    "/watch", "/host", "/secret", "/bash", "/tour", "/theme", "/layout", "/budget",
    "/palace", "/remember", "/forget", "/undo", "/memory",
}


def _render(text, width=120) -> str:
    console = Console(width=width, record=True, force_terminal=False)
    console.print(text)
    return console.export_text()


def _complete(line: str) -> list[tuple[str, str]]:
    doc = Document(line, len(line))
    return [(c.text, c.display_meta_text) for c in _ShellCompleter().get_completions(doc, None)]


class TestCatalog:
    def test_nothing_from_the_old_help_is_missing(self):
        assert OLD_HELP <= {c.name for c in commands.COMMANDS}

    def test_every_command_has_a_known_group_and_usage(self):
        for c in commands.COMMANDS:
            assert c.group in commands.GROUPS, c.name
            assert c.usage and c.summary, c.name

    def test_names_are_unique(self):
        names = [c.name for c in commands.COMMANDS]
        assert len(names) == len(set(names))

    def test_find_takes_slashless_names_and_aliases(self):
        assert commands.find("task").name == "/task"
        assert commands.find("/plain").name == "/bash"
        assert commands.find("/nope") is None

    def test_subcommands_come_from_the_usage_forms(self):
        subs = dict(commands.subcommands(commands.find("/task")))
        assert {"new", "list", "attach", "pause", "resume", "kill", "diff", "undo"} <= set(subs)
        assert "<name>" not in subs
        assert subs["new"] == "start one in its own tmux window"

    def test_placeholders_and_flags_are_not_subcommands(self):
        assert commands.subcommands(commands.find("/audit")) == []
        assert commands.subcommands(commands.find("/remember")) == []


class TestHelpViews:
    def test_overview_groups_fit_the_width(self):
        text = _render(commands.overview(120), 120)
        for group in commands.GROUPS:
            assert group.upper() in text
        assert all(len(line) <= 120 for line in text.splitlines())
        assert "/palace" in text and "KEYS" in text

    def test_two_columns_when_wide_one_when_narrow(self):
        wide = _render(commands.overview(120), 120)
        narrow = _render(commands.overview(80), 80)
        row = next(l for l in wide.splitlines() if "EVERYDAY" in l)
        assert "AGENTS & TASKS" in row
        assert not any("EVERYDAY" in l and "AGENTS" in l for l in narrow.splitlines())

    def test_three_columns_on_a_wide_pane_and_it_fits_a_screen(self):
        text = _render(commands.overview(148), 148)
        row = next(l for l in text.splitlines() if "EVERYDAY" in l)
        assert "AGENTS & TASKS" in row and "MEMORY & SKILLS" in row
        assert len(text.strip().splitlines()) <= 33      # the playground's main pane
        assert "…" not in text                           # no summary cut at this width

    def test_detail_lists_every_form_and_the_example(self):
        text = _render(commands.detail(commands.find("/task")))
        for form, _ in commands.find("/task").usage:
            assert form in text
        assert 'example   /task new deploy' in text

    def test_unknown_suggests_the_closest(self):
        text = _render(commands.unknown("tsk"))
        assert "no command /tsk" in text and "/task" in text

    def test_help_builtin_prints_overview_and_detail(self, capsys):
        from unittest.mock import MagicMock
        from sable.app.builtins.dispatch import handle_builtin
        assert handle_builtin("/help", MagicMock(), "s", MagicMock())
        assert "EVERYDAY" in capsys.readouterr().out
        assert handle_builtin("/help skill", MagicMock(), "s", MagicMock())
        assert "/skill doctor" in capsys.readouterr().out

    def test_palette_still_gets_builtins(self):
        from sable.app.builtins.dispatch import _HELP_TEXT
        from sable.ui import palette
        labels = [i.label for i in palette.builtin_items(_HELP_TEXT)]
        assert any(l.startswith("/stats") for l in labels)
        assert any(l.startswith("/task new") for l in labels)


class TestFormsFoundByTheLiveSmokeRun:
    """Forms /help advertises that were broken when typed at a real prompt."""

    def test_memory_does_not_crash_the_shell(self, capsys, monkeypatch):
        """memory.py used `os` without importing it; the NameError ended Sable."""
        from unittest.mock import MagicMock, patch
        from sable.app.builtins.dispatch import handle_builtin
        with patch("sable.memory.session.load_session_context", return_value=""), \
             patch("sable.memory.session.list_versions", return_value=[]):
            assert handle_builtin("/memory", MagicMock(), "s", MagicMock())
            assert handle_builtin("/memory versions", MagicMock(), "s", MagicMock())
        assert "No saved memory versions." in capsys.readouterr().out

    def test_stats_csv_is_a_builtin(self):
        """Only `shell stats --csv` was handled; `/stats --csv` went to bash."""
        from unittest.mock import MagicMock, patch
        from sable.app.builtins.dispatch import handle_builtin
        with patch("sable.app.builtins.dispatch._show_stats_csv") as csv:
            assert handle_builtin("/stats --csv", MagicMock(), "s", MagicMock())
        csv.assert_called_once()


class TestABrokenCommandKeepsTheShell:
    def _safe(self, tmp_path, monkeypatch, raises):
        from unittest.mock import MagicMock, patch
        from sable.app.builtins import dispatch
        monkeypatch.setattr(dispatch, "ERROR_LOG", str(tmp_path / "errors.log"))
        with patch.object(dispatch, "handle_builtin", side_effect=raises):
            return dispatch.handle_builtin_safely("/memory", MagicMock(), "s", MagicMock())

    def test_a_bug_is_one_line_and_a_log_entry(self, tmp_path, monkeypatch, capsys):
        assert self._safe(tmp_path, monkeypatch, NameError("name 'os' is not defined")) is True
        out = capsys.readouterr().out
        assert "/memory failed" in out and "NameError" in out and "still running" in out
        log = (tmp_path / "errors.log").read_text()
        assert "/memory" in log and "NameError" in log and "Traceback" in log

    def test_exit_and_ctrl_c_are_not_swallowed(self, tmp_path, monkeypatch):
        import pytest
        for exc in (SystemExit(0), KeyboardInterrupt()):
            with pytest.raises(type(exc)):
                self._safe(tmp_path, monkeypatch, exc)

    def test_the_repl_uses_the_safe_dispatcher(self):
        from sable.app import repl
        from sable.app.builtins.dispatch import handle_builtin_safely
        assert repl._handle_builtin is handle_builtin_safely


class TestUnknownCommands:
    def test_a_mistyped_builtin_is_answered_not_sent_to_bash(self, capsys):
        from unittest.mock import MagicMock
        from sable.app.builtins.dispatch import handle_builtin
        assert handle_builtin("/tsk list", MagicMock(), "s", MagicMock())
        out = capsys.readouterr().out
        assert "no command /tsk" in out and "/task" in out

    def test_real_paths_still_go_to_bash(self, tmp_path):
        from unittest.mock import MagicMock
        from sable.app.builtins.dispatch import handle_builtin
        for line in ("/bin/ls -la", "/usr/bin/env python3", "/tmp", "/Upper"):
            assert handle_builtin(line, MagicMock(), "s", MagicMock()) is False, line


class TestCompletion:
    def test_slash_lists_commands_with_summaries(self):
        got = dict(_complete("/ta"))
        assert got == {"/task": "start and manage sub-agents"}

    def test_bare_slash_offers_everything(self):
        assert len(_complete("/")) == len(commands.COMMANDS)

    def test_subcommands_after_a_space(self):
        got = dict(_complete("/task "))
        assert "new" in got and "attach" in got
        assert dict(_complete("/task at")) == {"attach": "jump into its window"}

    def test_no_completion_past_the_subcommand(self):
        assert _complete("/task new myname ") == []

    def test_unknown_command_offers_nothing(self):
        assert _complete("/nope ") == []
