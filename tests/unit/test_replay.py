"""Prompt replay (Phase 1, I9).

`agent_turns` stores what each model turn actually saw. The property that
matters most is redaction: this is the table a user is most likely to cat,
export, or paste into a bug report, so a secret in a command must not reach it.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from sable.core.events.replay import AgentTurn, ReplayLog, record_turn
from sable.policy.engine import redact_text


@pytest.fixture
def log(tmp_path):
    instance = ReplayLog(db_path=tmp_path / "sessions.db", redact=redact_text)
    try:
        yield instance
    finally:
        instance.close()


def _messages(*contents: str) -> list[dict]:
    return [{"role": "user", "content": c} for c in contents]


class TestRecord:
    def test_returns_the_new_row_id(self, log):
        first = log.record(
            agent="w1", role="worker", turn=1,
            system_prompt="be helpful", messages=_messages("hi"), response="{}",
        )
        assert first == 1

    def test_round_trips_the_message_list(self, log):
        messages = [
            {"role": "system", "content": "[GOAL] build it"},
            {"role": "user", "content": "what now"},
            {"role": "assistant", "content": '{"command": "ls"}'},
        ]
        log.record(
            agent="w1", role="worker", turn=1,
            system_prompt="prompt", messages=messages, response="answer",
        )

        stored = log.turns_for("w1")[0]

        assert [m["role"] for m in stored.messages] == ["system", "user", "assistant"]
        assert stored.messages[0]["content"] == "[GOAL] build it"
        assert stored.response == "answer"

    def test_stores_the_metadata(self, log):
        log.record(
            agent="w1", role="worker", turn=7,
            system_prompt="p", messages=[], response="r",
            model="claude-sonnet-5", prompt_tokens=120, completion_tokens=30,
            cost_usd=0.0042,
        )

        stored = log.turns_for("w1")[0]

        assert stored.turn == 7
        assert stored.model == "claude-sonnet-5"
        assert stored.prompt_tokens == 120
        assert stored.completion_tokens == 30
        assert stored.cost_usd == pytest.approx(0.0042)

    def test_record_never_raises_on_a_dead_connection(self, log):
        """Same contract as the bus: an agent recording its own reasoning must
        not die because the recording failed."""
        log.close()
        assert log.record(
            agent="w1", role="worker", turn=1,
            system_prompt="p", messages=[], response="r",
        ) is None

    def test_record_never_raises_on_a_malformed_message_list(self, log):
        """A caller passing the wrong shape loses the record, not the run."""
        assert log.record(
            agent="w1", role="worker", turn=1,
            system_prompt="p", messages=["not a dict"], response="r",
        ) is None

    def test_creates_its_own_table(self, tmp_path):
        """A worker is a fresh process and may record before Database exists."""
        with ReplayLog(db_path=tmp_path / "fresh.db") as fresh:
            assert fresh.record(
                agent="w1", role="worker", turn=1,
                system_prompt="p", messages=[], response="r",
            ) == 1


class TestRedaction:
    """Redaction happens on the way in. Redacting on read would leave the
    secret sitting in the database.

    The redactor is injected rather than imported: `core` sits below `policy`
    in the layering rule, so `replay.py` takes a callable and the agents (which
    may import `policy`) supply `strip_secrets`. The `log` fixture passes it,
    as every caller in Sable does.
    """

    SECRET = "sk-proj-abc123XYZ789defGHI456jklMNO012"

    def test_an_uninjected_log_stores_verbatim(self, tmp_path):
        """The opt-out, pinned deliberately.

        A ReplayLog built without a redactor does not invent one. That is why
        every real caller passes `redact_text`, and why this test exists: so
        the default is a decision on the record rather than an oversight.
        """
        with ReplayLog(db_path=tmp_path / "raw.db") as raw:
            raw.record(
                agent="w1", role="worker", turn=1,
                system_prompt="p", messages=_messages(self.SECRET), response="r",
            )
            assert self.SECRET in raw.turns_for("w1")[0].messages[0]["content"]

    def test_record_turn_forwards_the_redactor(self, tmp_path):
        path = tmp_path / "sessions.db"
        record_turn(
            path, redact=redact_text,
            agent="w1", role="worker", turn=1,
            system_prompt="p", messages=_messages(f"export K={self.SECRET}"),
            response="r",
        )
        with ReplayLog(db_path=path) as log:
            assert self.SECRET not in log.turns_for("w1")[0].messages[0]["content"]

    def test_a_secret_in_a_message_does_not_reach_the_table(self, log, tmp_path):
        log.record(
            agent="w1", role="worker", turn=1,
            system_prompt="p",
            messages=_messages(f"export API_KEY={self.SECRET}"),
            response="{}",
        )

        raw = sqlite3.connect(str(tmp_path / "sessions.db")).execute(
            "SELECT messages_json FROM agent_turns"
        ).fetchone()[0]

        assert self.SECRET not in raw
        assert "REDACTED" in raw

    def test_a_secret_in_the_response_does_not_reach_the_table(self, log, tmp_path):
        log.record(
            agent="w1", role="worker", turn=1,
            system_prompt="p", messages=[],
            response=json.dumps({"command": f"curl -H 'key: {self.SECRET}'"}),
        )

        raw = sqlite3.connect(str(tmp_path / "sessions.db")).execute(
            "SELECT response FROM agent_turns"
        ).fetchone()[0]

        assert self.SECRET not in raw

    def test_a_secret_in_the_system_prompt_does_not_reach_the_table(self, log, tmp_path):
        log.record(
            agent="w1", role="worker", turn=1,
            system_prompt=f"your token is {self.SECRET}", messages=[], response="r",
        )

        raw = sqlite3.connect(str(tmp_path / "sessions.db")).execute(
            "SELECT system_prompt FROM agent_turns"
        ).fetchone()[0]

        assert self.SECRET not in raw

    def test_an_aws_key_is_redacted_too(self, log, tmp_path):
        """Structured credentials are caught by pattern, not entropy."""
        log.record(
            agent="w1", role="worker", turn=1,
            system_prompt="p", messages=_messages("AKIAIOSFODNN7EXAMPLE"), response="r",
        )

        raw = sqlite3.connect(str(tmp_path / "sessions.db")).execute(
            "SELECT messages_json FROM agent_turns"
        ).fetchone()[0]

        assert "AKIAIOSFODNN7EXAMPLE" not in raw

    def test_ordinary_text_survives(self, log):
        """Redaction must not mangle the thing it is meant to preserve."""
        log.record(
            agent="w1", role="worker", turn=1,
            system_prompt="p", messages=_messages("ls -la /var/log"), response="r",
        )
        assert "/var/log" in log.turns_for("w1")[0].messages[0]["content"]


class TestTurnsFor:
    def test_empty_for_an_unknown_agent(self, log):
        assert log.turns_for("nobody") == []

    def test_returns_turns_oldest_first(self, log):
        for turn in (1, 2, 3):
            log.record(
                agent="w1", role="worker", turn=turn,
                system_prompt="p", messages=[], response=f"turn {turn}",
            )
        assert [t.turn for t in log.turns_for("w1")] == [1, 2, 3]

    def test_filters_by_agent(self, log):
        log.record(agent="w1", role="worker", turn=1,
                   system_prompt="p", messages=[], response="a")
        log.record(agent="w2", role="worker", turn=1,
                   system_prompt="p", messages=[], response="b")
        assert [t.agent for t in log.turns_for("w1")] == ["w1"]

    def test_respects_the_limit(self, log):
        for turn in range(10):
            log.record(agent="w1", role="worker", turn=turn,
                       system_prompt="p", messages=[], response="r")
        assert len(log.turns_for("w1", limit=3)) == 3


class TestLatest:
    def test_none_when_nothing_recorded(self, log):
        assert log.latest() is None

    def test_returns_the_most_recent_across_all_agents(self, log):
        log.record(agent="w1", role="worker", turn=1,
                   system_prompt="p", messages=[], response="first")
        log.record(agent="w2", role="worker", turn=1,
                   system_prompt="p", messages=[], response="second")
        assert log.latest().response == "second"

    def test_can_be_scoped_to_one_agent(self, log):
        log.record(agent="w1", role="worker", turn=1,
                   system_prompt="p", messages=[], response="mine")
        log.record(agent="w2", role="worker", turn=1,
                   system_prompt="p", messages=[], response="theirs")
        assert log.latest(agent="w1").response == "mine"

    def test_none_for_an_agent_with_no_turns(self, log):
        log.record(agent="w1", role="worker", turn=1,
                   system_prompt="p", messages=[], response="r")
        assert log.latest(agent="w2") is None


class TestAgents:
    def test_empty_when_nothing_recorded(self, log):
        assert log.agents() == []

    def test_lists_most_recently_active_first(self, log):
        log.record(agent="old", role="worker", turn=1,
                   system_prompt="p", messages=[], response="r")
        log.record(agent="new", role="worker", turn=1,
                   system_prompt="p", messages=[], response="r")
        assert log.agents() == ["new", "old"]

    def test_lists_each_agent_once(self, log):
        for turn in (1, 2, 3):
            log.record(agent="w1", role="worker", turn=turn,
                       system_prompt="p", messages=[], response="r")
        assert log.agents() == ["w1"]


class TestFromRow:
    def test_a_corrupt_messages_blob_stays_visible(self, log, tmp_path):
        """A bad write is surfaced, not silently dropped: the same principle
        the bus applies to a corrupt payload."""
        log._conn.execute(
            "INSERT INTO agent_turns (ts, agent, role, turn, model, system_prompt, "
            "messages_json, response, prompt_tokens, completion_tokens, cost_usd) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-01-01T00:00:00+00:00", "w1", "worker", 1, "m", "p",
             "{not json", "r", 0, 0, 0.0),
        )
        log._conn.commit()

        stored = log.turns_for("w1")[0]

        assert stored.messages == [{"role": "?", "content": "{not json"}]

    def test_a_non_list_messages_blob_is_wrapped(self, log):
        turn = AgentTurn.from_row(
            (1, "ts", "w1", "worker", 1, "m", "p", '{"a": 1}', "r", 0, 0, 0.0)
        )
        assert turn.messages == [{"role": "?", "content": "{'a': 1}"}]


class TestRecordTurnHelper:
    def test_writes_and_closes(self, tmp_path):
        path = tmp_path / "sessions.db"
        assert record_turn(
            path, agent="w1", role="worker", turn=1,
            system_prompt="p", messages=_messages("hi"), response="r",
        ) == 1

        with ReplayLog(db_path=path) as log:
            assert len(log.turns_for("w1")) == 1

    def test_returns_none_rather_than_raising_on_a_bad_path(self, tmp_path):
        """A path that cannot be opened must not take down the agent."""
        unusable = tmp_path / "a-file"
        unusable.write_text("not a database")
        (tmp_path / "a-file").chmod(0o000)
        result = record_turn(
            tmp_path / "a-file" / "nested.db",
            agent="w1", role="worker", turn=1,
            system_prompt="p", messages=[], response="r",
        )
        assert result is None


class TestCrossProcessDurability:
    """The orchestrator and a worker are genuinely separate processes."""

    def test_a_second_log_sees_the_first_log_writes(self, tmp_path):
        path = tmp_path / "sessions.db"
        with ReplayLog(db_path=path) as writer, ReplayLog(db_path=path) as reader:
            writer.record(agent="w1", role="worker", turn=1,
                          system_prompt="p", messages=[], response="cross")
            assert reader.turns_for("w1")[0].response == "cross"

    def test_turns_survive_reopening(self, tmp_path):
        path = tmp_path / "sessions.db"
        with ReplayLog(db_path=path) as first:
            first.record(agent="w1", role="worker", turn=1,
                         system_prompt="p", messages=[], response="r")
        with ReplayLog(db_path=path) as second:
            assert len(second.turns_for("w1")) == 1


class TestSchema:
    def test_database_creates_the_table(self, tmp_path, monkeypatch):
        """Not only the defensive _ensure_table: the real schema must carry it."""
        from pathlib import Path
        from unittest.mock import patch

        path = tmp_path / "sessions.db"
        with patch("sable.core.db.DB_PATH", Path(path)):
            from sable.core.db import Database

            db = Database()
            try:
                tables = {
                    row[0]
                    for row in db._conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            finally:
                db.close()

        assert "agent_turns" in tables
