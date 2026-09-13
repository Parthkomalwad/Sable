"""Degraded-mode detection (Phase 1, I7).

The failure these exist to prevent is the quiet one: a capability missing, the
behaviour silently reduced, and nothing anywhere admitting it. So the tests
care about two things — that a check fires when the capability is gone, and
that what it reports is actionable rather than just alarming.
"""
from __future__ import annotations

import sqlite3

import pytest

from sable.core import health


class TestDegradation:
    def test_line_names_the_problem_and_the_cost(self):
        degradation = health.Degradation(
            name="x", summary="thing missing", reduced="feature is off", hint="do this"
        )
        line = degradation.line()
        assert "thing missing" in line
        assert "feature is off" in line

    def test_every_shipped_check_carries_a_hint(self, monkeypatch, tmp_path):
        """A banner that reports a problem without saying what to do about it
        is noise. This pins that every degradation we can produce has an action."""
        monkeypatch.setattr(health.shutil, "which", lambda _: None)
        produced = [
            health.check_tmux(),
            health.check_terminal_width(width=40),
            health.check_database(tmp_path / "nope" / "x.db"),
            health.llm_unreachable("connection refused"),
        ]
        for degradation in produced:
            if degradation is not None:
                assert degradation.hint, f"{degradation.name} has no hint"
                assert degradation.reduced, f"{degradation.name} says nothing reduced"


class TestTmux:
    def test_absent_tmux_is_reported(self, monkeypatch):
        monkeypatch.setattr(health.shutil, "which", lambda _: None)
        degradation = health.check_tmux()
        assert degradation is not None
        assert "tmux" in degradation.summary

    def test_present_tmux_is_silent(self, monkeypatch):
        monkeypatch.setattr(health.shutil, "which", lambda _: "/usr/bin/tmux")
        assert health.check_tmux() is None

    def test_says_what_stops_working(self, monkeypatch):
        monkeypatch.setattr(health.shutil, "which", lambda _: None)
        reduced = health.check_tmux().reduced
        assert "sidebar" in reduced
        assert "sub-agent" in reduced


class TestTerminalWidth:
    def test_narrow_terminal_is_reported(self):
        degradation = health.check_terminal_width(width=60)
        assert degradation is not None
        assert "60" in degradation.summary

    def test_wide_terminal_is_silent(self):
        assert health.check_terminal_width(width=200) is None

    def test_the_threshold_is_inclusive(self):
        assert health.check_terminal_width(width=health.MIN_TERMINAL_WIDTH) is None
        assert health.check_terminal_width(width=health.MIN_TERMINAL_WIDTH - 1) is not None

    def test_not_a_terminal_is_not_a_degradation(self, monkeypatch):
        """Under a pipe or in a test there is no width to complain about."""
        def _no_size():
            raise OSError("not a terminal")

        monkeypatch.setattr(health.os, "get_terminal_size", _no_size)
        assert health.check_terminal_width() is None


class TestDatabase:
    def test_a_writable_database_is_silent(self, tmp_path):
        assert health.check_database(tmp_path / "sessions.db") is None

    def test_an_unwritable_path_is_reported(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        degradation = health.check_database(blocker / "sessions.db")
        assert degradation is not None
        assert "database" in degradation.name

    def test_reports_what_is_lost(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        reduced = health.check_database(blocker / "db.sqlite").reduced
        assert "replay" in reduced or "events" in reduced

    def test_it_does_not_leave_its_probe_table_behind_as_a_surprise(self, tmp_path):
        """The probe writes, because a readable-but-unwritable database is the
        case that matters. The table it creates is named so it is obviously
        ours if anyone looks."""
        path = tmp_path / "sessions.db"
        health.check_database(path)
        names = {
            row[0]
            for row in sqlite3.connect(str(path)).execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert names == {"_sable_health"}


class TestKeyring:
    def test_missing_secretstorage_is_reported(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def _fail(name, *args, **kwargs):
            if name == "secretstorage":
                raise ImportError("no secretstorage")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fail)
        degradation = health.check_keyring()
        assert degradation is not None
        assert "config.json" in degradation.reduced


class TestLlmUnreachable:
    def test_carries_the_reason(self):
        assert "connection refused" in health.llm_unreachable("connection refused").summary

    def test_works_without_a_reason(self):
        assert health.llm_unreachable().summary == "LLM unreachable"

    def test_says_bash_still_works(self):
        """The whole point of the banner: what you can still do."""
        degradation = health.llm_unreachable()
        assert "bash" in degradation.reduced
        assert "bash" in degradation.hint


class TestStartupDegradations:
    def test_a_healthy_system_reports_nothing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(health, "STARTUP_CHECKS", (lambda: None, lambda: None))
        assert health.startup_degradations() == []

    def test_collects_every_degradation(self, monkeypatch):
        one = health.Degradation("a", "s1", "r1", "h1")
        two = health.Degradation("b", "s2", "r2", "h2")
        monkeypatch.setattr(health, "STARTUP_CHECKS", (lambda: one, lambda: None, lambda: two))
        assert health.startup_degradations() == [one, two]

    def test_a_check_that_raises_does_not_break_startup(self, monkeypatch):
        """These run on the path to the user's login shell."""
        def _boom():
            raise OSError("probe exploded")

        good = health.Degradation("b", "s", "r", "h")
        monkeypatch.setattr(health, "STARTUP_CHECKS", (_boom, lambda: good))
        assert health.startup_degradations() == [good]

    def test_the_real_checks_run_without_raising(self):
        """Whatever this machine looks like, the checks must survive it."""
        assert isinstance(health.startup_degradations(), list)
