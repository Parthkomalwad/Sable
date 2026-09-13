"""K4: natural-language aliases, matched before the router.

The whole point of an alias is that it is instant and free: it resolves
before `classify()` and never reaches a model. These tests pin that, the
fuzzy threshold, and the fact that an alias is not a safety bypass.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def _make_db():
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
            assert "skill_aliases" in tables
        finally:
            conn.close()


class TestStoring:
    def test_add_stores_an_alias(self, db):
        from sable.skills.aliases import add_alias, list_aliases

        database, _ = db
        add_alias(database, "restart the api", "docker compose restart api")

        rows = list_aliases(database)
        assert len(rows) == 1
        assert rows[0]["phrase"] == "restart the api"
        assert rows[0]["command"] == "docker compose restart api"
        assert rows[0]["use_count"] == 0

    def test_adding_the_same_phrase_replaces_it(self, db):
        from sable.skills.aliases import add_alias, list_aliases

        database, _ = db
        add_alias(database, "restart the api", "systemctl restart api")
        add_alias(database, "restart the api", "docker compose restart api")

        rows = list_aliases(database)
        assert len(rows) == 1
        assert rows[0]["command"] == "docker compose restart api"

    def test_an_empty_phrase_is_rejected(self, db):
        from sable.skills.aliases import add_alias, list_aliases

        database, _ = db
        assert add_alias(database, "   ", "docker ps") is False
        assert list_aliases(database) == []

    def test_an_empty_command_is_rejected(self, db):
        from sable.skills.aliases import add_alias, list_aliases

        database, _ = db
        assert add_alias(database, "do the thing", "  ") is False
        assert list_aliases(database) == []

    def test_delete_removes_an_alias(self, db):
        from sable.skills.aliases import add_alias, delete_alias, list_aliases

        database, _ = db
        add_alias(database, "restart the api", "docker compose restart api")
        assert delete_alias(database, "restart the api") is True
        assert list_aliases(database) == []

    def test_deleting_an_unknown_phrase_is_false(self, db):
        from sable.skills.aliases import delete_alias

        database, _ = db
        assert delete_alias(database, "nope") is False

    def test_a_missing_database_is_not_fatal(self):
        from sable.skills.aliases import add_alias, list_aliases, match_alias

        assert add_alias(None, "a", "b") is False
        assert list_aliases(None) == []
        assert match_alias(None, "a") is None


class TestMatching:
    def test_an_exact_phrase_matches(self, db):
        from sable.skills.aliases import add_alias, match_alias

        database, _ = db
        add_alias(database, "restart the api", "docker compose restart api")

        hit = match_alias(database, "restart the api")
        assert hit is not None
        assert hit["command"] == "docker compose restart api"

    def test_matching_is_case_and_punctuation_insensitive(self, db):
        from sable.skills.aliases import add_alias, match_alias

        database, _ = db
        add_alias(database, "restart the api", "docker compose restart api")

        assert match_alias(database, "Restart the API!") is not None
        assert match_alias(database, "  restart   the api  ") is not None

    def test_a_near_miss_above_the_threshold_matches(self, db):
        """A small typo should still resolve: that is the point of fuzzy."""
        from sable.skills.aliases import add_alias, match_alias

        database, _ = db
        add_alias(database, "restart the api", "docker compose restart api")

        assert match_alias(database, "restart the apo") is not None

    def test_a_near_miss_below_the_threshold_falls_through(self, db):
        """Below 0.9 the router must see the line untouched."""
        from sable.skills.aliases import add_alias, match_alias

        database, _ = db
        add_alias(database, "restart the api", "docker compose restart api")

        assert match_alias(database, "deploy the frontend to staging") is None

    def test_similarity_threshold_is_enforced(self, db):
        from sable.skills.aliases import add_alias, match_alias, SIMILARITY_THRESHOLD

        assert SIMILARITY_THRESHOLD >= 0.9

        database, _ = db
        add_alias(database, "restart the api", "docker compose restart api")
        assert match_alias(database, "restart the database") is None

    def test_the_best_of_several_aliases_wins(self, db):
        from sable.skills.aliases import add_alias, match_alias

        database, _ = db
        add_alias(database, "restart the api", "docker compose restart api")
        add_alias(database, "restart the worker", "docker compose restart worker")

        hit = match_alias(database, "restart the worker")
        assert hit["command"] == "docker compose restart worker"

    def test_matching_makes_no_llm_call(self, db, monkeypatch):
        """An alias must be instant and free. Nothing may reach a backend."""
        from sable.skills.aliases import add_alias, match_alias

        def explode(*args, **kwargs):
            raise AssertionError("an alias match must not build an LLM backend")

        monkeypatch.setattr("sable.llm.registry.build_backend", explode)

        database, _ = db
        add_alias(database, "restart the api", "docker compose restart api")
        assert match_alias(database, "restart the api") is not None


class TestUseCounting:
    def test_recording_a_use_increments(self, db):
        from sable.skills.aliases import add_alias, record_use, list_aliases

        database, _ = db
        add_alias(database, "restart the api", "docker compose restart api")
        record_use(database, "restart the api")

        assert list_aliases(database)[0]["use_count"] == 1

    def test_three_uses_offers_promotion(self, db):
        from sable.skills.aliases import add_alias, record_use, should_offer_promotion

        database, _ = db
        add_alias(database, "restart the api", "docker compose restart api")
        for _ in range(2):
            record_use(database, "restart the api")
            assert should_offer_promotion(database, "restart the api") is False

        record_use(database, "restart the api")
        assert should_offer_promotion(database, "restart the api") is True

    def test_promotion_is_offered_once_not_repeatedly(self, db):
        """An offer the user ignored must not nag on every later use."""
        from sable.skills.aliases import (
            add_alias, record_use, should_offer_promotion, mark_promotion_offered,
        )

        database, _ = db
        add_alias(database, "restart the api", "docker compose restart api")
        for _ in range(3):
            record_use(database, "restart the api")

        assert should_offer_promotion(database, "restart the api") is True
        mark_promotion_offered(database, "restart the api")
        assert should_offer_promotion(database, "restart the api") is False

    def test_promotion_is_never_automatic(self, db):
        """Reaching the threshold must not create a skill by itself."""
        from sable.skills.aliases import add_alias, record_use, list_aliases

        database, _ = db
        add_alias(database, "restart the api", "docker compose restart api")
        for _ in range(5):
            record_use(database, "restart the api")

        # The alias is still just an alias. Nothing was crystallised.
        assert len(list_aliases(database)) == 1


class TestSafety:
    def test_a_destructive_resolved_command_is_still_checked(self, db):
        """An alias is not a safety bypass.

        The alias store does not run anything, so what this pins is that a
        destructive resolved command is still recognised as destructive by
        the same policy check the bash path uses.
        """
        from sable.policy.engine import is_destructive
        from sable.skills.aliases import add_alias, match_alias

        database, _ = db
        add_alias(database, "nuke the volumes", "rm -rf /var/lib/data")

        hit = match_alias(database, "nuke the volumes")
        assert is_destructive(hit["command"]) is True


class TestBuiltin:
    def test_alias_assignment_stores(self, db, capsys):
        from sable.app.builtins.dispatch import handle_builtin
        from sable.skills.aliases import list_aliases

        database, _ = db
        handled = handle_builtin(
            '/alias "restart the api" = docker compose restart api',
            database, "s", _config(),
        )
        assert handled is True

        rows = list_aliases(database)
        assert len(rows) == 1
        assert rows[0]["phrase"] == "restart the api"
        assert rows[0]["command"] == "docker compose restart api"

    def test_alias_accepts_single_quotes(self, db):
        from sable.app.builtins.dispatch import handle_builtin
        from sable.skills.aliases import list_aliases

        database, _ = db
        handle_builtin("/alias 'ship it' = git push", database, "s", _config())

        assert list_aliases(database)[0]["command"] == "git push"

    def test_bare_alias_lists(self, db, capsys):
        from sable.app.builtins.dispatch import handle_builtin
        from sable.skills.aliases import add_alias

        database, _ = db
        add_alias(database, "restart the api", "docker compose restart api")

        handle_builtin("/alias", database, "s", _config())
        assert "restart the api" in capsys.readouterr().out

    def test_reports_when_empty(self, db, capsys):
        from sable.app.builtins.dispatch import handle_builtin

        database, _ = db
        handle_builtin("/alias", database, "s", _config())
        assert "no aliases" in capsys.readouterr().out.lower()

    def test_delete_subcommand(self, db):
        from sable.app.builtins.dispatch import handle_builtin
        from sable.skills.aliases import add_alias, list_aliases

        database, _ = db
        add_alias(database, "restart the api", "docker compose restart api")

        handle_builtin('/alias delete "restart the api"', database, "s", _config())
        assert list_aliases(database) == []

    def test_malformed_alias_is_a_usage_message(self, db, capsys):
        from sable.app.builtins.dispatch import handle_builtin

        database, _ = db
        handle_builtin("/alias restart the api", database, "s", _config())
        assert "usage" in capsys.readouterr().out.lower()


def _config():
    from sable.core.config.schema import ShellConfig
    return ShellConfig.from_dict({"backend": "ollama", "model": "llama3.1"})
