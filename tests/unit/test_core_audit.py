"""The audit ledger, extracted from the REPL in Phase 0.5 step 3.

`write_command`'s tab-separated format is a contract, not an implementation
detail: `skills/watcher.py` parses these lines to find repeated command
clusters, and a change to the field order or separator would stop skill
crystallisation finding anything, silently. These tests pin the format.
"""
from __future__ import annotations

import datetime

import pytest

from sable.core import audit


@pytest.fixture
def log_path(tmp_path, monkeypatch):
    """Point AUDIT_LOG_PATH at a temp file.

    write_command imports it inside the function, so patching the attribute
    on sable.core.db is what takes effect.
    """
    import sable.core.db as db

    path = tmp_path / "nested" / "audit.log"
    monkeypatch.setattr(db, "AUDIT_LOG_PATH", path)
    return path


class TestWriteCommand:
    def test_creates_the_parent_directory(self, log_path):
        """First run on a fresh machine has no ~/.local/share tree yet."""
        assert not log_path.parent.exists()
        audit.write_command("sess", "/tmp", "ls")
        assert log_path.exists()

    def test_line_is_tab_separated_in_the_documented_order(self, log_path):
        audit.write_command("sess-1", "/home/me", "git status")
        fields = log_path.read_text(encoding="utf-8").rstrip("\n").split("\t")
        assert len(fields) == 4, f"expected 4 tab-separated fields, got {fields}"
        stamp, session, cwd, command = fields
        assert session == "sess-1"
        assert cwd == "/home/me"
        assert command == "git status"
        # Parseable ISO 8601, which is what any consumer will assume.
        datetime.datetime.fromisoformat(stamp)

    def test_timestamp_is_timezone_aware_utc(self, log_path):
        audit.write_command("s", "/tmp", "ls")
        stamp = log_path.read_text(encoding="utf-8").split("\t")[0]
        parsed = datetime.datetime.fromisoformat(stamp)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == datetime.timedelta(0)

    def test_appends_rather_than_truncates(self, log_path):
        """The watcher needs history; a truncating write would erase it."""
        audit.write_command("s", "/tmp", "first")
        audit.write_command("s", "/tmp", "second")
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert lines[0].endswith("first")
        assert lines[1].endswith("second")

    def test_unwritable_log_does_not_raise(self, monkeypatch, tmp_path):
        """An audit failure must never take a command down with it."""
        import sable.core.db as db

        monkeypatch.setattr(db, "AUDIT_LOG_PATH", tmp_path / "a.log")

        def _boom(*args, **kwargs):
            raise PermissionError("read-only filesystem")

        monkeypatch.setattr("builtins.open", _boom)
        audit.write_command("s", "/tmp", "ls")  # must not raise

    def test_unicode_command_survives_the_round_trip(self, log_path):
        audit.write_command("s", "/tmp", "echo 'héllo ✦'")
        assert "héllo ✦" in log_path.read_text(encoding="utf-8")


class TestWriteAction:
    def test_writes_key_value_fields(self, tmp_path, monkeypatch):
        real_open = open

        def _capture(path, mode="r", *args, **kwargs):
            if str(path).endswith("audit.log") and "a" in mode:
                target = tmp_path / "action.log"
                return real_open(target, mode, *args, **kwargs)
            return real_open(path, mode, *args, **kwargs)

        monkeypatch.setattr("builtins.open", _capture)
        audit.write_action("bash", "ls -la", exit_code=0)

        line = (tmp_path / "action.log").read_text(encoding="utf-8")
        assert "action=bash" in line
        assert "cmd='ls -la'" in line
        assert "exit=0" in line
        assert "user=" in line

    def test_unwritable_log_does_not_raise(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise PermissionError("no")

        monkeypatch.setattr("builtins.open", _boom)
        audit.write_action("bash", "ls")  # must not raise

    def test_missing_passwd_entry_falls_back_to_unknown(self, tmp_path, monkeypatch):
        """Containers run with --user have a uid with no passwd entry."""
        def _boom():
            raise OSError("no username set in the environment")

        monkeypatch.setattr(audit.getpass, "getuser", _boom)

        real_open = open

        def _capture(path, mode="r", *args, **kwargs):
            if str(path).endswith("audit.log") and "a" in mode:
                return real_open(tmp_path / "u.log", mode, *args, **kwargs)
            return real_open(path, mode, *args, **kwargs)

        monkeypatch.setattr("builtins.open", _capture)
        audit.write_action("bash", "ls")
        assert "user=unknown" in (tmp_path / "u.log").read_text(encoding="utf-8")
