"""K3: corrections recorded from `e`-edits and routing answers.

The redaction rule here is the one from docs/contracts.md §3: text is
redacted on the way in, not on the way out, because the table must be safe
to read and to export.

Two behaviours are deliberately pinned that the plan did not anticipate,
both found by running the real redactor rather than a fixture:

1. `strip_secrets` normalises whitespace (it ends in `" ".join(...)`). For
   prose that is harmless; for a stored shell command it is not. So a row
   carrying a secret is withheld entirely rather than stored lossily.
2. `policy/engine.py` does not catch a bare, low-entropy `sk-ant-` token.
   `test_bare_token_gap_is_known` pins that hole so it stays documented
   rather than being assumed closed.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def _make_db():
    """A real Database on a temp file, the test_db_v3.py convention."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    with patch("sable.core.db.DB_PATH", Path(tmp)):
        from sable.core.db import Database
        return Database(), tmp


@pytest.fixture
def db():
    database, path = _make_db()
    try:
        yield database, path
    finally:
        database.close()
        os.unlink(path)


class TestSchema:
    def test_table_exists(self, db):
        _, path = db
        conn = sqlite3.connect(path)
        try:
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "skill_corrections" in tables
        finally:
            conn.close()

    def test_wal_mode(self, db):
        database, _ = db
        mode = database._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"


class TestRecording:
    def test_an_edit_stores_the_pair(self, db):
        from sable.skills.corrections import record_correction, list_corrections

        database, _ = db
        record_correction(database, "docker ps -a", "docker ps", kind="edit")

        rows = list_corrections(database)
        assert len(rows) == 1
        assert rows[0]["proposed"] == "docker ps -a"
        assert rows[0]["corrected"] == "docker ps"
        assert rows[0]["kind"] == "edit"

    def test_an_edit_that_changes_nothing_stores_nothing(self, db):
        from sable.skills.corrections import record_correction, list_corrections

        database, _ = db
        record_correction(database, "docker ps", "docker ps", kind="edit")

        assert list_corrections(database) == []

    def test_whitespace_only_change_stores_nothing(self, db):
        """A stray space is not a correction worth learning from."""
        from sable.skills.corrections import record_correction, list_corrections

        database, _ = db
        record_correction(database, "docker ps", "  docker ps  ", kind="edit")

        assert list_corrections(database) == []

    def test_empty_correction_stores_nothing(self, db):
        from sable.skills.corrections import record_correction, list_corrections

        database, _ = db
        record_correction(database, "docker ps", "", kind="edit")

        assert list_corrections(database) == []

    def test_a_missing_database_is_not_fatal(self):
        """Recording a correction must never take the shell down."""
        from sable.skills.corrections import record_correction

        record_correction(None, "a", "b", kind="edit")


class TestRedaction:
    def test_an_assignment_secret_is_withheld_not_stored(self, db):
        """Decision (b): a row carrying a secret is withheld entirely.

        Storing the redacted form would mean storing a whitespace-normalised
        command, and a corpus that rewrites its own commands is worth less
        than one that is honest about the gap.
        """
        from sable.skills.corrections import record_correction, list_corrections

        database, _ = db
        secret = "deploy --api_key=AbCdEf1234567890XyZwVuTsRq"
        record_correction(database, "deploy", secret, kind="edit")

        rows = list_corrections(database)
        assert rows == [] or all("AbCdEf1234567890" not in str(r) for r in rows)

    def test_a_withheld_correction_is_counted(self, db):
        """Skipping a write must not be silent: /corrections explains the gap."""
        from sable.skills.corrections import (
            record_correction, list_corrections, withheld_count,
        )

        database, _ = db
        record_correction(database, "deploy", "deploy --api_key=AbCdEf1234567890XyZ", kind="edit")

        assert withheld_count(database) >= 1
        assert list_corrections(database) == []

    def test_a_secret_in_the_proposed_side_also_withholds(self, db):
        from sable.skills.corrections import record_correction, list_corrections

        database, _ = db
        record_correction(
            database,
            "curl -H 'Authorization: Bearer AbCdEf1234567890XyZwVuTsRqPo'",
            "curl localhost",
            kind="edit",
        )

        assert list_corrections(database) == []

    def test_a_clean_command_keeps_its_exact_whitespace(self, db):
        """The reason for withholding rather than redacting, asserted.

        `awk -F'\\t'` must come back byte-identical or the row is misleading.
        """
        from sable.skills.corrections import record_correction, list_corrections

        database, _ = db
        exact = "awk -F'\\t' '{print $2}'  file.tsv"
        record_correction(database, "awk '{print $2}' file.tsv", exact, kind="edit")

        rows = list_corrections(database)
        assert len(rows) == 1
        assert rows[0]["corrected"] == exact

    def test_bare_token_gap_is_known(self):
        """A known hole in policy/engine.py, pinned so it stays visible.

        Every shipped secret pattern is assignment-shaped, and this token's
        entropy (4.40) sits under the 4.5 fallback threshold, so it survives
        redaction. Widening those patterns is out of scope here: they gate
        every command in the shell. If this test ever fails, the hole was
        closed and this test should become an assertion that it stays closed.
        """
        from sable.policy.engine import strip_secrets

        bare = "deploy --token sk-ant-api03-ZZZyyyXXXwwwVVVuuuTTTsssRRRqqqPPPooo111222333"
        _, count = strip_secrets(bare)
        assert count == 0, "the bare-token gap closed; update this test"


class TestRouterCorrections:
    def test_the_corpus_row_is_still_written(self, tmp_path, monkeypatch):
        """The [b/a] recorder must keep feeding the router corpus (I3)."""
        corrections = tmp_path / "router_corrections.tsv"
        monkeypatch.setattr("sable.core.paths.STATE_DIR", tmp_path)
        monkeypatch.setattr("sable.core.paths.ROUTER_CORRECTIONS", corrections)

        from sable.app.builtins.route import _record_router_correction
        _record_router_correction("restart the api", "agentic")

        assert corrections.read_text(encoding="utf-8") == "restart the api\tagentic\n"

    def test_it_also_writes_a_correction(self, tmp_path, monkeypatch, db):
        """Same call now feeds both the corpus and the corrections table."""
        database, _ = db
        monkeypatch.setattr("sable.core.paths.STATE_DIR", tmp_path)
        monkeypatch.setattr(
            "sable.core.paths.ROUTER_CORRECTIONS", tmp_path / "router_corrections.tsv"
        )

        from sable.app.builtins.route import _record_router_correction
        from sable.skills.corrections import list_corrections

        _record_router_correction("restart the api", "agentic", db=database)

        rows = list_corrections(database)
        assert len(rows) == 1
        assert rows[0]["kind"] == "route"
        assert rows[0]["corrected"] == "agentic"

    def test_no_database_still_writes_the_corpus(self, tmp_path, monkeypatch):
        """The corpus must not depend on telemetry being available."""
        corrections = tmp_path / "router_corrections.tsv"
        monkeypatch.setattr("sable.core.paths.STATE_DIR", tmp_path)
        monkeypatch.setattr("sable.core.paths.ROUTER_CORRECTIONS", corrections)

        from sable.app.builtins.route import _record_router_correction
        _record_router_correction("restart the api", "agentic", db=None)

        assert "restart the api" in corrections.read_text(encoding="utf-8")


class TestWeeklyCount:
    def test_counts_this_week(self, db):
        from sable.skills.corrections import record_correction, weekly_count

        database, _ = db
        record_correction(database, "a", "b", kind="edit")
        record_correction(database, "c", "d", kind="edit")

        assert weekly_count(database) == 2

    def test_ignores_older_rows(self, db):
        from sable.skills.corrections import record_correction, weekly_count

        database, _ = db
        record_correction(database, "a", "b", kind="edit")
        database._conn.execute(
            "UPDATE skill_corrections SET ts = '2020-01-01T00:00:00+00:00'"
        )
        database._conn.commit()

        assert weekly_count(database) == 0

    def test_a_broken_database_reads_zero(self):
        """The sidebar must render even when telemetry is unavailable."""
        from sable.skills.corrections import weekly_count

        assert weekly_count(None) == 0


class TestDeletion:
    def test_delete_removes_one_row(self, db):
        from sable.skills.corrections import (
            record_correction, list_corrections, delete_correction,
        )

        database, _ = db
        record_correction(database, "a", "b", kind="edit")
        row_id = list_corrections(database)[0]["id"]

        assert delete_correction(database, row_id) is True
        assert list_corrections(database) == []

    def test_deleting_an_unknown_id_is_false(self, db):
        from sable.skills.corrections import delete_correction

        database, _ = db
        assert delete_correction(database, 999) is False


class TestBuiltin:
    def test_lists_corrections(self, db, capsys):
        from sable.app.builtins.dispatch import handle_builtin
        from sable.skills.corrections import record_correction

        database, _ = db
        record_correction(database, "docker ps -a", "docker ps", kind="edit")

        assert handle_builtin("/corrections", database, "s", _config()) is True
        assert "docker ps" in capsys.readouterr().out

    def test_reports_when_empty(self, db, capsys):
        from sable.app.builtins.dispatch import handle_builtin

        database, _ = db
        assert handle_builtin("/corrections", database, "s", _config()) is True
        assert "no corrections" in capsys.readouterr().out.lower()

    def test_mentions_withheld_rows(self, db, capsys):
        from sable.app.builtins.dispatch import handle_builtin
        from sable.skills.corrections import record_correction

        database, _ = db
        record_correction(database, "deploy", "deploy --api_key=AbCdEf1234567890XyZ", kind="edit")

        handle_builtin("/corrections", database, "s", _config())
        assert "withheld" in capsys.readouterr().out.lower()

    def test_delete_subcommand(self, db, capsys):
        from sable.app.builtins.dispatch import handle_builtin
        from sable.skills.corrections import record_correction, list_corrections

        database, _ = db
        record_correction(database, "a", "b", kind="edit")
        row_id = list_corrections(database)[0]["id"]

        assert handle_builtin(f"/corrections delete {row_id}", database, "s", _config()) is True
        assert list_corrections(database) == []

    def test_delete_with_a_bad_id_is_a_message(self, db, capsys):
        from sable.app.builtins.dispatch import handle_builtin

        database, _ = db
        assert handle_builtin("/corrections delete abc", database, "s", _config()) is True
        assert "usage" in capsys.readouterr().out.lower()


class TestSidebar:
    def test_panel_renders_the_count(self, db):
        from sable.skills.corrections import record_correction
        from sable.ui.sidebar.watch import _panel_corrections

        database, _ = db
        record_correction(database, "docker ps -a", "docker ps", kind="edit")

        panel = _panel_corrections(database)
        assert panel is not None

    def test_panel_survives_a_broken_database(self):
        from sable.ui.sidebar.watch import _panel_corrections

        assert _panel_corrections(None) is not None


def _config():
    from sable.core.config.schema import ShellConfig
    return ShellConfig.from_dict({"backend": "ollama", "model": "llama3.1"})
