"""Phase 5 Task 3 (E5): ntfy pushes and single-use phone approvals.

No network: httpx is replaced by a MockTransport or a monkeypatched post.
A reply approves exactly one pending item, once, with the right token, within
an hour; everything else is refused, audited and published.
"""
from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

from sable.daemon import approvals, notify
from sable.policy import queue
from sable.policy.engine import decide
from sable.policy.tiers import Tier

SETTINGS = {"server": "https://ntfy.example", "topic": "t", "reply_topic": "r"}


class _Conn(sqlite3.Connection):
    """A connection that can carry the audit and publish records."""


@pytest.fixture
def conn(tmp_path, monkeypatch):
    audited, published = [], []
    monkeypatch.setattr(approvals, "_audit", lambda reason, qid: audited.append((reason, qid)))
    monkeypatch.setattr(approvals, "_publish", lambda payload: published.append(payload))
    monkeypatch.setattr(notify, "_token", lambda: None)
    c = sqlite3.connect(tmp_path / "s.db", factory=_Conn)
    c.audited, c.published = audited, published
    approvals.ensure_tables(c)
    yield c
    c.close()


def _pending(conn, cmd="rm -rf /tmp/old-builds"):
    qid = queue.enqueue(conn, "daemon:prune", cmd, decide(cmd))
    return qid, approvals.issue(conn, qid)


def _status(conn, qid):
    return conn.execute("SELECT status FROM policy_queue WHERE id = ?", (qid,)).fetchone()[0]


class TestSend:
    def test_posts_json_and_redacts(self, monkeypatch):
        sent = {}

        def fake_post(url, json, headers, timeout):
            sent.update(url=url, json=json, headers=headers, timeout=timeout)
            return httpx.Response(200, request=httpx.Request("POST", url))

        monkeypatch.setattr(notify, "settings", lambda: SETTINGS)
        monkeypatch.setattr(notify, "_token", lambda: "tk_secret")
        monkeypatch.setattr(notify.httpx, "post", fake_post)
        assert notify.send("hi", "key AKIAIOSFODNN7EXAMPLE leaked")
        assert sent["url"] == "https://ntfy.example"
        assert sent["json"]["topic"] == "t" and "AKIAIOSFODNN7EXAMPLE" not in sent["json"]["message"]
        assert sent["headers"]["Authorization"] == "Bearer tk_secret"
        assert sent["timeout"].read == 30.0

    def test_failure_returns_false(self, monkeypatch):
        def boom(*a, **kw):
            raise httpx.ConnectError("down")
        monkeypatch.setattr(notify, "settings", lambda: SETTINGS)
        monkeypatch.setattr(notify, "_token", lambda: None)
        monkeypatch.setattr(notify.httpx, "post", boom)
        assert notify.send("hi", "there") is False

    def test_no_topic_sends_nothing(self, monkeypatch):
        monkeypatch.setattr(notify, "settings", lambda: {"server": "https://ntfy.sh"})
        monkeypatch.setattr(notify.httpx, "post", lambda *a, **k: pytest.fail("posted"))
        assert notify.send("hi", "there") is False


class TestReplies:
    def test_good_token_approves_once(self, conn):
        qid, tok = _pending(conn)
        assert approvals.handle_reply(conn, f"yes {qid} {tok}") == (True, "approved")
        assert _status(conn, qid) == "approved"
        assert approvals.handle_reply(conn, f"yes {qid} {tok}") == (False, "replay")
        assert _status(conn, qid) == "approved"

    def test_no_rejects(self, conn):
        qid, tok = _pending(conn)
        assert approvals.handle_reply(conn, f"no {qid} {tok}") == (True, "rejected")
        assert _status(conn, qid) == "rejected"

    def test_wrong_id_is_refused(self, conn):
        qa, ta = _pending(conn, "rm -rf /tmp/a")
        qb, _ = _pending(conn, "rm -rf /tmp/b")
        assert approvals.handle_reply(conn, f"yes {qb} {ta}") == (False, "bad token")
        assert _status(conn, qb) == "pending"
        # the refused attempt did not burn the right token for its own id
        assert approvals.handle_reply(conn, f"yes {qa} {ta}")[0]

    def test_expired_is_refused(self, conn):
        qid, tok = _pending(conn)
        conn.execute("UPDATE approval_tokens SET created_at = created_at - 3601")
        assert approvals.handle_reply(conn, f"yes {qid} {tok}") == (False, "expired")
        assert _status(conn, qid) == "pending"

    @pytest.mark.parametrize("text", ["", "yes", "yes 1", "maybe 1 x", "yes one x",
                                      "yes 1 x extra", "yes 1 té"])
    def test_malformed_is_refused(self, conn, text):
        _pending(conn)
        assert approvals.handle_reply(conn, text) == (False, "malformed")

    def test_unknown_id_is_refused(self, conn):
        assert approvals.handle_reply(conn, "yes 99 abc") == (False, "unknown")

    def test_never_tier_is_refused(self, conn, monkeypatch):
        qid, tok = _pending(conn)
        # A policy change after queueing made it deny: it must not be approvable.
        monkeypatch.setattr(approvals, "decide",
                            lambda c: type("D", (), {"tier": Tier.DENY})())
        assert approvals.handle_reply(conn, f"yes {qid} {tok}") == (False, "never tier")
        assert _status(conn, qid) == "pending"

    @pytest.mark.parametrize("status", ["approved", "rejected", "used"])
    def test_a_non_pending_row_is_never_changed(self, conn, status):
        qid, tok = _pending(conn)
        conn.execute("UPDATE policy_queue SET status = ? WHERE id = ?", (status, qid))
        for word in ("yes", "no"):
            ok, _ = approvals.handle_reply(conn, f"{word} {qid} {tok}")
            assert not ok and _status(conn, qid) == status

    def test_every_refusal_is_audited_and_published(self, conn):
        approvals.handle_reply(conn, "garbage")
        assert conn.audited == [("malformed", None)]
        assert conn.published[-1] == {"ok": False, "reason": "malformed", "qid": None}


class TestTick:
    def _transport(self, posts, replies):
        def handler(req):
            if req.method == "POST":
                posts.append(json.loads(req.content))
                return httpx.Response(200)
            batch = replies.pop(0) if replies else []
            return httpx.Response(200, text="\n".join(json.dumps(r) for r in batch))
        return httpx.MockTransport(handler)

    def test_pushes_once_and_approves_from_a_reply(self, conn, monkeypatch, tmp_path):
        posts, replies = [], []
        client = httpx.Client(transport=self._transport(posts, replies))
        monkeypatch.setattr(notify, "settings", lambda: SETTINGS)
        monkeypatch.setattr(notify, "_client", lambda: client)
        monkeypatch.setattr(approvals, "_events", lambda cursor: ([], 0))
        monkeypatch.setattr(approvals, "POLL_EVERY_S", 0)
        qid = queue.enqueue(conn, "daemon:prune", "rm -rf /tmp/x", decide("rm -rf /tmp/x"))
        approvals.tick(conn)
        approvals.tick(conn)
        pushes = [p for p in posts if p.get("actions")]
        assert len(pushes) == 1
        yes = pushes[0]["actions"][0]
        assert yes["url"] == "https://ntfy.example/r" and yes["body"].startswith(f"yes {qid} ")
        assert "headers" not in yes   # the access token never rides in a button
        replies.append([{"id": "m1", "event": "message", "message": yes["body"]}])
        approvals.tick(conn)
        assert _status(conn, qid) == "approved"
        replies.append([{"id": "m1", "event": "message", "message": yes["body"]}])
        approvals.tick(conn)  # the same reply again is a replay, still approved once
        assert conn.audited[-1] == ("replay", qid)


class TestBuiltin:
    def test_setup_writes_config_and_keyring_only_gets_the_token(self, tmp_path, monkeypatch):
        from sable.app.builtins import notify as nb
        from sable.core.config import wizard
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"theme": "mono"}))
        monkeypatch.setattr(wizard, "CONFIG_PATH", cfg)
        stored = {}
        monkeypatch.setattr(nb.keyring, "store_api_key", lambda s, k: stored.update({s: k}))
        monkeypatch.setattr(notify, "_token", lambda: stored.get("ntfy"))
        answers = iter(["https://ntfy.example", "t", "r"])
        nb._handle_notify_builtin("setup", ask=lambda p: next(answers), secret=lambda p: "tk_x")
        data = json.loads(cfg.read_text())
        assert data["theme"] == "mono"
        assert data["notify"] == {"server": "https://ntfy.example", "topic": "t", "reply_topic": "r"}
        assert "tk_x" not in cfg.read_text() and stored == {"ntfy": "tk_x"}


def test_a_failed_approval_push_is_retried(conn, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "settings", lambda: SETTINGS)
    monkeypatch.setattr(approvals, "_events", lambda cursor: ([], 0))
    monkeypatch.setattr(approvals, "POLL_EVERY_S", 10**9)
    results = iter([False, True])
    monkeypatch.setattr(notify, "send", lambda *a, **k: sent.append(a[0]) or next(results))
    queue.enqueue(conn, "daemon:prune", "rm -rf /tmp/x", decide("rm -rf /tmp/x"))
    approvals.tick(conn)
    approvals.tick(conn)
    approvals.tick(conn)
    assert len(sent) == 2   # failed, retried and sent, then nothing more
