"""`/exit` drafts a skill; it no longer enables one (Task 7).

This is the behaviour change Phase 2 turns on, and the only task in the
phase that alters something a user already relies on.

Before: `PatternWatcher` found a command cluster that had crossed the 3x
threshold, `SkillCrystalliser.crystallise()` wrote
`~/skills/instructions/<slug>.md`, and `SkillIndex` recorded it at
confidence 0.5. From that moment `TaskSkillLoader` could inject it into a
worker's context. Nobody approved it. The user was told
"[skill] auto-generated: x.md" as their shell closed, with no way to act
on the message and nothing telling them it was now live.

After: the same detection and the same file, written `pending`. It is inert
until a human runs `/skill approve`. The gate's words are "never
auto-enabled without approval", and drafting at `/exit` was the one path
that broke them.

Two things these tests hold that are easy to lose.

**Exiting still always works.** Crystallisation runs on the way out, and a
failure in it must never keep someone in a shell they asked to leave. The
existing guard around the whole block stays.

**The user finds out.** A draft nobody is told about is the same as no
draft. The count is reported at the next login, when there is a shell to
act in, rather than shouted at a closing one.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from sable.app.builtins.dispatch import handle_builtin
from sable.skills.index import SkillIndex


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Redirect every skills path, so the real ~/skills is untouched."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    (tmp_path / ".local" / "share" / "agentic-shell").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def config():
    return MagicMock()


_PATTERN = {
    "pattern_hash": "abc123",
    "repo_path": "/srv/api",
    "command_sequence": "docker|docker|make",
    "intent_keywords": ["docker", "deploy", "api"],
    "occurrence_count": 3,
}


def _exit_with(patterns, crystalliser=None, config=None):
    """Drive the /exit branch with a canned watcher and crystalliser."""
    watcher = MagicMock()
    watcher.observe.return_value = patterns
    crystalliser = crystalliser or MagicMock()

    with patch("sable.skills.watcher.PatternWatcher", return_value=watcher), \
         patch("sable.skills.crystalliser.SkillCrystalliser", return_value=crystalliser):
        with pytest.raises(SystemExit):
            handle_builtin("/exit", db=MagicMock(), session_id="s",
                           config=config or MagicMock())
    return crystalliser


class TestTheDraftIsNotEnabled:
    """The load-bearing change. Everything else here is presentation."""

    def test_a_crossed_pattern_is_drafted_pending(self, home, config):
        crystalliser = _exit_with([_PATTERN], config=config)

        crystalliser.crystallise.assert_called_once()
        kwargs = crystalliser.crystallise.call_args.kwargs
        assert kwargs.get("status") == "pending"

    def test_nothing_is_drafted_when_no_pattern_crossed(self, home, config):
        crystalliser = _exit_with([], config=config)
        crystalliser.crystallise.assert_not_called()


class TestWhatTheUserIsTold:
    def test_the_message_says_draft_not_auto_generated(self, home, config, capsys):
        """"auto-generated" told the user a thing had happened to them.

        "draft" tells them a thing is waiting for them, which is now true.
        """
        crystalliser = MagicMock()
        crystalliser.crystallise.return_value = home / "skills" / "deploy" / "SKILL.md"

        _exit_with([_PATTERN], crystalliser=crystalliser, config=config)

        out = capsys.readouterr().out.lower()
        assert "draft" in out
        assert "auto-generated" not in out

    def test_the_message_names_the_approval_command(self, home, config, capsys):
        """A notification with no next step is noise."""
        crystalliser = MagicMock()
        crystalliser.crystallise.return_value = home / "skills" / "deploy" / "SKILL.md"

        _exit_with([_PATTERN], crystalliser=crystalliser, config=config)

        assert "/skill" in capsys.readouterr().out


class TestExitingAlwaysWorks:
    """A failure on the way out must not trap someone in their shell."""

    def test_a_crystalliser_failure_still_exits(self, home, config):
        crystalliser = MagicMock()
        crystalliser.crystallise.side_effect = OSError("disk full")

        with patch("sable.skills.watcher.PatternWatcher") as watcher_cls, \
             patch("sable.skills.crystalliser.SkillCrystalliser",
                   return_value=crystalliser):
            watcher_cls.return_value.observe.return_value = [_PATTERN]
            with pytest.raises(SystemExit):
                handle_builtin("/exit", db=MagicMock(), session_id="s", config=config)

    def test_a_watcher_failure_still_exits(self, home, config):
        with patch("sable.skills.watcher.PatternWatcher") as watcher_cls:
            watcher_cls.return_value.observe.side_effect = sqlite3.Error("locked")
            with pytest.raises(SystemExit):
                handle_builtin("/exit", db=MagicMock(), session_id="s", config=config)

    def test_the_exit_marker_is_written_before_crystallisation(self, home, config):
        """The marker is what the login wrapper reads to stay out.

        Writing it after the skills work would mean a crash in that work
        drops the user back into Sable when they asked to leave.
        """
        marker = home / ".local" / "share" / "agentic-shell" / "exit_requested"

        with patch("sable.skills.watcher.PatternWatcher") as watcher_cls:
            watcher_cls.return_value.observe.side_effect = sqlite3.Error("locked")
            with pytest.raises(SystemExit):
                handle_builtin("/exit", db=MagicMock(), session_id="s", config=config)

        assert marker.exists()


class TestThePendingCountAtStartup:
    """Reported at the next login, when there is a shell to act in."""

    def test_it_reports_how_many_drafts_are_waiting(self, home):
        from sable.app.repl import pending_skills_banner

        index = SkillIndex()
        index.add(name="a", file="/a", keywords=["k"], auto_generated=True,
                  status="pending")
        index.add(name="b", file="/b", keywords=["k"], auto_generated=True,
                  status="pending")

        line = pending_skills_banner()

        assert "2" in line
        assert "/skill" in line

    def test_it_says_nothing_when_there_are_no_drafts(self, home):
        from sable.app.repl import pending_skills_banner

        SkillIndex().add(name="live", file="/a", keywords=["k"],
                         auto_generated=False)

        assert pending_skills_banner() == ""

    def test_it_says_nothing_when_there_is_no_index(self, home):
        """A fresh install must not be greeted by an error."""
        from sable.app.repl import pending_skills_banner

        assert pending_skills_banner() == ""

    def test_the_singular_reads_naturally(self, home):
        from sable.app.repl import pending_skills_banner

        SkillIndex().add(name="a", file="/a", keywords=["k"],
                         auto_generated=True, status="pending")

        line = pending_skills_banner()

        assert "1 draft skill" in line
        assert "skills pending" not in line
