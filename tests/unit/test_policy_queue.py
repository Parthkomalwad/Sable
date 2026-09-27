"""Phase 3: the approval queue for unattended agents.

A worker cannot be prompted, so a `confirm` command is queued instead of
refused outright. `/approve <id>` lets it run the next time the worker
proposes it, once. A `deny` is never queued and can never be approved.
"""
from __future__ import annotations

import sqlite3

import pytest

from sable.policy import engine, queue
from sable.policy.tiers import Tier


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    queue.ensure_table(c)
    return c


def _confirm_decision():
    return engine.decide("rm -rf /tmp/build")


class TestQueue:
    def test_enqueue_and_list_pending(self, conn):
        qid = queue.enqueue(conn, "deploy", "rm -rf /tmp/build", _confirm_decision())
        (row,) = queue.pending(conn)
        assert row["id"] == qid and row["agent"] == "deploy"
        assert row["rule"] == "recursive-delete" and row["why"]

    def test_same_request_twice_is_one_row(self, conn):
        a = queue.enqueue(conn, "deploy", "rm -rf /tmp/build", _confirm_decision())
        b = queue.enqueue(conn, "deploy", "rm -rf /tmp/build", _confirm_decision())
        assert a == b and len(queue.pending(conn)) == 1

    def test_approved_command_is_taken_once(self, conn):
        qid = queue.enqueue(conn, "deploy", "rm -rf /tmp/build", _confirm_decision())
        assert queue.take_approved(conn, "deploy", "rm -rf /tmp/build") is False
        assert queue.decide_request(conn, qid, approve=True) is True
        assert queue.take_approved(conn, "deploy", "rm -rf /tmp/build") is True
        assert queue.take_approved(conn, "deploy", "rm -rf /tmp/build") is False

    def test_approval_is_for_that_agent_and_command_only(self, conn):
        qid = queue.enqueue(conn, "deploy", "rm -rf /tmp/build", _confirm_decision())
        queue.decide_request(conn, qid, approve=True)
        assert queue.take_approved(conn, "other", "rm -rf /tmp/build") is False
        assert queue.take_approved(conn, "deploy", "rm -rf /tmp/other") is False

    def test_rejected_is_not_pending_and_not_runnable(self, conn):
        qid = queue.enqueue(conn, "deploy", "rm -rf /tmp/build", _confirm_decision())
        queue.decide_request(conn, qid, approve=False)
        assert queue.pending(conn) == []
        assert queue.take_approved(conn, "deploy", "rm -rf /tmp/build") is False

    def test_deciding_an_unknown_or_decided_id_is_false(self, conn):
        qid = queue.enqueue(conn, "deploy", "rm -rf /tmp/build", _confirm_decision())
        queue.decide_request(conn, qid, approve=False)
        assert queue.decide_request(conn, qid, approve=True) is False
        assert queue.decide_request(conn, 999, approve=True) is False


class TestGateApproved:
    def test_approved_confirm_runs_for_a_worker_without_a_prompt(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("prompted"))
        assert engine.gate("rm -rf /tmp/build", role="worker", approved=True) is True

    def test_approval_never_lifts_a_deny(self, monkeypatch):
        rule = engine.rules.Rule(name="no", pattern="x", why="w", tier=Tier.DENY)
        monkeypatch.setattr(engine.rules, "match", lambda c: rule)
        assert engine.gate("anything", role="worker", approved=True) is False


class TestApproveBuiltin:
    class _DB:
        def __init__(self, conn):
            self._conn = conn

    def _run(self, db, arg, capsys):
        from sable.app.builtins.dispatch import _handle_approve_builtin
        assert _handle_approve_builtin(arg, db) is True
        return capsys.readouterr().out

    def test_lists_then_approves(self, conn, capsys):
        qid = queue.enqueue(conn, "deploy", "rm -rf /tmp/build", _confirm_decision())
        db = self._DB(conn)
        assert f"#{qid}" in self._run(db, "", capsys)
        assert "approved" in self._run(db, str(qid), capsys)
        assert "nothing waiting" in self._run(db, "", capsys)

    def test_reject_and_bad_input(self, conn, capsys):
        qid = queue.enqueue(conn, "deploy", "rm -rf /tmp/build", _confirm_decision())
        db = self._DB(conn)
        assert "rejected" in self._run(db, f"reject {qid}", capsys)
        assert "no pending" in self._run(db, str(qid), capsys)
        assert "usage" in self._run(db, "abc", capsys)
