"""`sable share` (Phase 9, K12): approve-only and read-only sharing via ntfy.

No network: notify.send is replaced by a recorder, polls are not run.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from sable.daemon import approvals, notify, share
from sable.policy import queue
from sable.policy.engine import decide
from sable.policy.tiers import Tier

SETTINGS = {"server": "https://ntfy.example"}


class _C(sqlite3.Connection):
    """A connection that can carry the recorders."""


@pytest.fixture
def conn(tmp_path, monkeypatch):
    audited, published, sent = [], [], []
    monkeypatch.setattr(approvals, "_audit", lambda reason, qid, who="": audited.append((reason, qid, who)))
    monkeypatch.setattr(approvals, "_publish", lambda payload: published.append(payload))
    monkeypatch.setattr(notify, "settings", lambda: SETTINGS)
    monkeypatch.setattr(notify, "send", lambda title, body, actions=(), topic=None:
                        sent.append((title, body, list(actions), topic)) or True)
    monkeypatch.setattr(approvals, "POLL_EVERY_S", 10**9)
    monkeypatch.setattr(approvals, "_poll", lambda *a, **k: None)  # no network
    monkeypatch.setattr(share, "_last_poll", {})
    c = sqlite3.connect(tmp_path / "s.db", factory=_C)
    approvals.ensure_tables(c)
    c.audited, c.published, c.sent = audited, published, sent  # type: ignore[attr-defined]
    yield c
    c.close()


def _queue(conn, cmd="rm -rf /tmp/old-builds"):
    return queue.enqueue(conn, "daemon:prune", cmd, decide(cmd))


def _status(conn, qid):
    return conn.execute("SELECT status FROM policy_queue WHERE id = ?", (qid,)).fetchone()[0]


def _pushed_token(conn, qid):
    """The token in the Approve button pushed for `qid`."""
    for title, _, actions, _ in conn.sent:
        if f"#{qid}" in title and actions:
            return actions[0]["body"].split()[2]
    raise AssertionError(f"#{qid} not pushed")


def test_ttl_parsing():
    assert share.parse_ttl("1h") == 3600
    assert share.parse_ttl("30m") == 1800
    for bad in ("", "0h", "1d", "abc", "25h"):
        with pytest.raises(ValueError):
            share.parse_ttl(bad)


def test_name_is_restricted():
    with pytest.raises(ValueError):
        share.create(sqlite3.connect(":memory:"), "approve", 3600, name="eve cmd='x'")


class TestApproveOnly:
    def test_pending_items_pushed_to_share_topic_with_bound_tokens(self, conn):
        before = _queue(conn, "rm -rf /tmp/a")
        sh = share.create(conn, "approve", 3600, name="Asha")
        share.share_tick(conn)
        after = _queue(conn, "rm -rf /tmp/b")
        share.share_tick(conn)
        pushed = [t for t in conn.sent if t[3] == sh["topic"]]
        assert [p[0].split("#")[1].split("?")[0] for p in pushed] == [str(before), str(after)]
        row = conn.execute("SELECT share_id, approver FROM approval_tokens WHERE qid = ?",
                           (before,)).fetchone()
        assert row == (sh["id"], "Asha")
        # the button posts to the share's reply topic, never the owner's
        assert pushed[0][2][0]["url"] == f"https://ntfy.example/{sh['reply_topic']}"
        share.share_tick(conn)
        assert len([t for t in conn.sent if t[3] == sh["topic"]]) == 2  # once each

    def test_approve_records_approver_and_share(self, conn):
        qid = _queue(conn)
        sh = share.create(conn, "approve", 3600, name="Asha")
        share.share_tick(conn)
        tok = _pushed_token(conn, qid)
        assert approvals.handle_reply(conn, f"yes {qid} {tok}", sh["id"]) == (True, "approved")
        assert _status(conn, qid) == "approved"
        assert conn.audited[-1] == ("approved", qid, f" approver=Asha share={sh['id']}")
        assert conn.published[-1]["approver"] == "Asha"
        assert conn.published[-1]["share"] == sh["id"]

    def test_replay_refused(self, conn):
        qid = _queue(conn)
        sh = share.create(conn, "approve", 3600, name="Asha")
        share.share_tick(conn)
        tok = _pushed_token(conn, qid)
        assert approvals.handle_reply(conn, f"no {qid} {tok}", sh["id"])[0]
        assert approvals.handle_reply(conn, f"yes {qid} {tok}", sh["id"]) == (False, "replay")
        assert _status(conn, qid) == "rejected"

    def test_token_bound_to_qid(self, conn):
        qa, qb = _queue(conn, "rm -rf /tmp/a"), _queue(conn, "rm -rf /tmp/b")
        sh = share.create(conn, "approve", 3600, name="Asha")
        share.share_tick(conn)
        ta = _pushed_token(conn, qa)
        assert approvals.handle_reply(conn, f"yes {qb} {ta}", sh["id"]) == (False, "bad token")
        assert _status(conn, qb) == "pending"

    def test_token_bound_to_share(self, conn):
        qid = _queue(conn)
        a = share.create(conn, "approve", 3600, name="Asha")
        b = share.create(conn, "approve", 3600, name="Ben")
        share.share_tick(conn)
        tok_a = [p for p in conn.sent if p[3] == a["topic"]][0][2][0]["body"].split()[2]
        # not on another share's reply topic, not on the owner's
        assert approvals.handle_reply(conn, f"yes {qid} {tok_a}", b["id"]) == (False, "wrong share")
        assert approvals.handle_reply(conn, f"yes {qid} {tok_a}") == (False, "wrong share")
        assert _status(conn, qid) == "pending"

    def test_owner_token_does_not_work_on_share(self, conn):
        qid = _queue(conn)
        sh = share.create(conn, "approve", 3600, name="Asha")
        own = approvals.issue(conn, qid)
        assert approvals.handle_reply(conn, f"yes {qid} {own}", sh["id"]) == (False, "wrong share")

    def test_token_expiry(self, conn):
        qid = _queue(conn)
        sh = share.create(conn, "approve", 3 * 3600, name="Asha")
        share.share_tick(conn)
        tok = _pushed_token(conn, qid)
        conn.execute("UPDATE approval_tokens SET created_at = created_at - 3601")
        assert approvals.handle_reply(conn, f"yes {qid} {tok}", sh["id"]) == (False, "expired")

    def test_stop_revokes_immediately(self, conn):
        qid = _queue(conn)
        sh = share.create(conn, "approve", 3600, name="Asha")
        share.share_tick(conn)
        tok = _pushed_token(conn, qid)
        assert share.stop(conn, sh["id"]) == 1
        assert approvals.handle_reply(conn, f"yes {qid} {tok}", sh["id"]) == (False, "share ended")
        assert _status(conn, qid) == "pending"
        assert conn.audited[-1][2] == f" approver=Asha share={sh['id']}"
        n = len(conn.sent)
        _queue(conn, "rm -rf /tmp/c")
        share.share_tick(conn)
        assert len(conn.sent) == n

    def test_share_ttl_ends_approvals(self, conn):
        qid = _queue(conn)
        sh = share.create(conn, "approve", 3600, name="Asha")
        share.share_tick(conn)
        tok = _pushed_token(conn, qid)
        conn.execute("UPDATE shares SET expires_at = ?", (time.time() - 1,))
        assert approvals.handle_reply(conn, f"yes {qid} {tok}", sh["id"]) == (False, "share ended")

    def test_deny_never_offered(self, conn, monkeypatch):
        qid = _queue(conn)
        monkeypatch.setattr(share, "decide", lambda c: type("D", (), {"tier": Tier.DENY})())
        share.create(conn, "approve", 3600, name="Asha")
        share.share_tick(conn)
        assert not any(f"#{qid}" in t[0] for t in conn.sent)
        assert conn.execute("SELECT COUNT(*) FROM approval_tokens").fetchone()[0] == 0

    def test_list_and_stop_all(self, conn):
        share.create(conn, "approve", 3600, name="Asha")
        share.create(conn, "read", 3600)
        assert len(share.list_active(conn)) == 2
        assert share.stop(conn) == 2
        assert share.list_active(conn) == []


class TestReadOnly:
    def test_snapshot_content_is_redacted(self, conn):
        conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, name TEXT, goal TEXT, status TEXT)")
        conn.execute("INSERT INTO tasks (name, goal, status) VALUES "
                     "('deploy', 'push with AWS_SECRET_ACCESS_KEY=abcd1234abcd1234abcd1234', 'running')")
        conn.execute("CREATE TABLE token_events (timestamp TEXT, cost_usd REAL)")
        conn.execute("INSERT INTO token_events VALUES ('2999-01-01T00:00:00+00:00', 0.25)")
        _queue(conn, "export API_KEY=sk-live-0123456789abcdef0123456789")
        text = share.snapshot(conn)
        assert "deploy (running)" in text
        assert "inbox: 1 waiting" in text
        assert "cost today: $0.2500" in text
        assert "abcd1234abcd1234" not in text
        assert "sk-live-0123456789" not in text

    def test_pushed_every_five_minutes_until_ttl(self, conn, monkeypatch):
        sh = share.create(conn, "read", 3600)
        clock = [time.time()]
        monkeypatch.setattr(share.time, "time", lambda: clock[0])
        share.share_tick(conn)
        share.share_tick(conn)
        assert len([t for t in conn.sent if t[3] == sh["topic"]]) == 1
        clock[0] += 301
        share.share_tick(conn)
        assert len([t for t in conn.sent if t[3] == sh["topic"]]) == 2
        clock[0] += 3600
        share.share_tick(conn)
        assert len([t for t in conn.sent if t[3] == sh["topic"]]) == 2
        assert share.list_active(conn) == []
        assert conn.execute("SELECT status FROM shares").fetchone()[0] == "expired"

    def test_read_only_share_offers_no_buttons(self, conn):
        _queue(conn)
        share.create(conn, "read", 3600)
        share.share_tick(conn)
        assert all(not t[2] for t in conn.sent)
        assert conn.execute("SELECT COUNT(*) FROM approval_tokens").fetchone()[0] == 0


def test_replies_polled_on_the_share_reply_topic_with_its_id(conn, monkeypatch):
    polls = []
    monkeypatch.setattr(approvals, "_poll", lambda c, s, topic, key, sid: polls.append((topic, sid)))
    sh = share.create(conn, "approve", 3600, name="Asha")
    share.share_tick(conn)
    assert polls == [(sh["reply_topic"], sh["id"])]
