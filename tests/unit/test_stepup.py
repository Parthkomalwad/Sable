"""Phase 8 Task 5 (K7): step-up approval for deny-tier commands."""
from __future__ import annotations

import base64
import hashlib

import pytest

from sable.core import audit
from sable.policy import engine, stepup
from sable.policy.tiers import Tier

KEY = b"12345678901234567890"
SECRET = base64.b32encode(KEY).decode()
T = 1_111_111_109  # step 37037036


class _Tty:
    def isatty(self):
        return True


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Secret in a fake keyring, state in tmp, audit captured, a tty."""
    store = {stepup.SERVICE: SECRET}
    from sable.core.config import keyring
    monkeypatch.setattr(keyring, "lookup", lambda s: store.get(s))
    monkeypatch.setattr(keyring, "store_api_key", lambda s, k: store.__setitem__(s, k))
    monkeypatch.setattr(keyring, "delete", lambda s: store.pop(s, None) is not None)
    db = tmp_path / "s.db"
    real_verify = stepup.verify
    monkeypatch.setattr(stepup, "verify", lambda code, now=None, db_path=None:
                        real_verify(code, now=now, db_path=db_path or db))
    monkeypatch.setattr(stepup, "phone_available", lambda: False)
    lines = []
    monkeypatch.setattr(audit, "write_action", lambda a, c, exit_code=None: lines.append((a, c)))
    monkeypatch.setattr(audit, "record", lambda *a, **k: lines.append(("record", k["outcome"])))
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.delenv("SABLE_NO_STEPUP", raising=False)
    stepup._grants.clear()
    return {"db": db, "store": store, "lines": lines, "verify": real_verify}


# ── TOTP ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("t,expected", [
    (59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
    (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130"),
])
def test_rfc6238_sha1_vectors(t, expected):
    assert stepup.totp(KEY, t, digits=8, digest=hashlib.sha1) == expected


def test_rfc6238_sha256_vector():
    key = b"12345678901234567890123456789012"
    assert stepup.totp(key, 59, digits=8, digest=hashlib.sha256) == "46119246"


def test_drift_window_one_step_each_side(env, tmp_path):
    v = env["verify"]
    assert v(stepup.totp(KEY, T - 30), now=T, db_path=tmp_path / "a.db")
    assert v(stepup.totp(KEY, T + 30), now=T, db_path=tmp_path / "b.db")
    assert not v(stepup.totp(KEY, T - 60), now=T, db_path=tmp_path / "c.db")
    assert not v(stepup.totp(KEY, T + 60), now=T, db_path=tmp_path / "d.db")


def test_wrong_and_malformed_codes(env):
    good = stepup.totp(KEY, T)
    bad = f"{(int(good) + 1) % 10**6:06d}"
    for code in (bad, "", "12345", "1234567", "abcdef", good + "x"):
        assert not stepup.verify(code, now=T)


def test_replay_refused_same_code_and_older_codes(env):
    code = stepup.totp(KEY, T)
    assert stepup.verify(code, now=T)
    assert not stepup.verify(code, now=T)
    assert not stepup.verify(stepup.totp(KEY, T - 30), now=T)  # older step
    assert stepup.verify(stepup.totp(KEY, T + 30), now=T + 30)  # next step fine


def test_replay_refused_after_restart(env):
    code = stepup.totp(KEY, T)
    assert stepup.verify(code, now=T)
    stepup._grants.clear()  # a new process has no memory; only the table
    assert not env["verify"](code, now=T, db_path=env["db"])


def test_no_secret_means_no_code_works(env):
    env["store"].clear()
    assert not stepup.verify(stepup.totp(KEY, T), now=T)


def test_setup_stores_secret_and_uri(env, monkeypatch):
    monkeypatch.setattr(stepup, "_connect", lambda db=None: _conn(env["db"]))
    key, uri = stepup.setup()
    assert env["store"][stepup.SERVICE] == key
    assert len(base64.b32decode(key)) == 20
    assert uri.startswith("otpauth://totp/Sable") and f"secret={key}" in uri
    assert "digits=6" in uri and "period=30" in uri


def _conn(path):
    import sqlite3
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE IF NOT EXISTS stepup_state (key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
    return c


# ── grants ──────────────────────────────────────────────────────────────

def test_grant_used_once_and_only_for_exact_command():
    stepup._grants.clear()
    stepup.grant("rm -rf /data", now=100)
    assert not stepup.take("rm -rf /data ", now=101)  # not the exact string
    stepup.grant("rm -rf /data", now=100)
    assert stepup.take("rm -rf /data", now=101)
    assert not stepup.take("rm -rf /data", now=101)


def test_grant_expires_after_two_minutes():
    stepup.grant("x", now=100)
    assert not stepup.take("x", now=100 + stepup.GRANT_TTL_S + 1)


# ── the offer in gate() ─────────────────────────────────────────────────

def _deny(monkeypatch):
    rule = engine.rules.Rule(name="no-rm", pattern="^rm ", why="nope", tier=Tier.DENY)
    monkeypatch.setattr(engine.rules, "match", lambda c: rule if c.startswith("rm ") else None)
    monkeypatch.setattr(engine.hooks, "run", lambda *a, **k: type("R", (), {"blocked": False})())
    monkeypatch.setattr(engine.hooks, "show", lambda *a, **k: None)


def _answers(monkeypatch, *answers):
    it = iter(answers)
    real = stepup.offer
    monkeypatch.setattr(stepup, "offer", lambda command: real(
        command, ask=lambda *_: next(it), secret=lambda *_: next(it)))


@pytest.fixture
def tty_out(monkeypatch):
    import sys
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)


@pytest.mark.parametrize("role", ["user", "orchestrator"])
def test_valid_code_runs_deny_once(env, monkeypatch, tty_out, role):
    _deny(monkeypatch)
    monkeypatch.setattr(stepup.time, "time", lambda: T)
    code = stepup.totp(KEY, T)
    _answers(monkeypatch, "s", code, "s", code)
    assert engine.gate("rm -rf /data", role=role) is True
    assert engine.gate("rm -rf /data", role=role) is False  # same code again
    assert ("record", "stepped_up") in env["lines"]
    assert ("record", "refused") in env["lines"]
    assert not stepup._grants


def test_wrong_code_refused_and_cancel_refused(env, monkeypatch, tty_out):
    _deny(monkeypatch)
    _answers(monkeypatch, "s", "000000", "q")
    assert engine.gate("rm -rf /data", role="user") is False
    assert engine.gate("rm -rf /data", role="user") is False
    outcomes = [c for a, c in env["lines"] if a == "stepup"]
    assert "factor=totp outcome=refused" in outcomes[0]
    assert "factor=none outcome=cancelled" in outcomes[1]


def test_audit_never_contains_code_or_secret(env, monkeypatch, tty_out):
    _deny(monkeypatch)
    monkeypatch.setattr(stepup.time, "time", lambda: T)
    code = stepup.totp(KEY, T)
    _answers(monkeypatch, "s", code, "s", "123456")
    engine.gate("rm -rf /data", role="user")
    engine.gate("rm -rf /data", role="user")
    text = repr(env["lines"])
    assert code not in text and "123456" not in text and SECRET not in text
    assert "factor=totp outcome=granted rm -rf /data" in text


@pytest.mark.parametrize("role", ["worker", "daemon", "mcp"])
def test_unattended_roles_never_offered(env, monkeypatch, tty_out, role):
    _deny(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("offered"))
    monkeypatch.setattr(stepup, "offer", lambda *a, **k: pytest.fail("offered"))
    assert engine.gate("rm -rf /data", role=role) is False


@pytest.mark.parametrize("marker", ["daemon", "mcp-serve"])
def test_daemon_and_mcp_serve_processes_never_offered(env, monkeypatch, tty_out, marker):
    """sabled's run() and `--mcp-serve` main() set SABLE_NO_STEPUP."""
    monkeypatch.setenv("SABLE_NO_STEPUP", "1")
    _deny(monkeypatch)
    monkeypatch.setattr(stepup, "offer", lambda *a, **k: pytest.fail("offered"))
    assert engine.gate("rm -rf /data", role="user") is False


def test_daemon_and_mcp_serve_set_the_marker():
    from pathlib import Path
    root = Path(engine.__file__).resolve().parents[1]
    for rel in ("daemon/service.py", "mcp/serve.py"):
        assert 'os.environ["SABLE_NO_STEPUP"] = "1"' in (root / rel).read_text(encoding="utf-8")


def test_non_tty_never_offered(env, monkeypatch):
    _deny(monkeypatch)

    class _Pipe:
        def isatty(self):
            return False
    monkeypatch.setattr("sys.stdin", _Pipe())
    monkeypatch.setattr(stepup, "offer", lambda *a, **k: pytest.fail("offered"))
    assert engine.gate("rm -rf /data", role="user") is False


def test_not_set_up_never_offered(env, monkeypatch, tty_out):
    env["store"].clear()
    _deny(monkeypatch)
    monkeypatch.setattr(stepup, "offer", lambda *a, **k: pytest.fail("offered"))
    assert engine.gate("rm -rf /data", role="user") is False


def test_phone_factor_needs_matching_reply(env, monkeypatch):
    from sable.daemon import notify
    monkeypatch.setattr(notify, "settings", lambda: {"server": "https://n", "topic": "t",
                                                     "reply_topic": "r"})
    sent = {}
    monkeypatch.setattr(notify, "send", lambda title, body, actions=(): sent.update(
        title=title, body=actions[0]["body"]) or True)

    class _R:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    replies = []
    monkeypatch.setattr(notify, "_client", lambda: type("C", (), {
        "get": staticmethod(lambda *a, **k: _R(replies.pop(0) if replies else ""))}))
    clock = iter(range(0, 1000, 10))
    tick = lambda: next(clock)  # noqa: E731
    replies.extend(['{"event":"message","message":"stepup yes wrong"}'])
    assert stepup.phone("rm -rf /data", wait=30, sleep=lambda s: None, clock=tick) is False
    assert "Step-up: allow `rm -rf /data` once?" == sent["title"]

    def _reply(*a, **k):
        return _R('{"event":"message","message":"%s"}' % sent["body"])
    monkeypatch.setattr(notify, "_client", lambda: type("C", (), {"get": staticmethod(_reply)}))
    assert stepup.phone("rm -rf /data", wait=30, sleep=lambda s: None, clock=tick) is True


def test_stepup_builtin_setup_confirms_first_code(env, monkeypatch):
    from sable.app.builtins import stepup as b
    monkeypatch.setattr(stepup, "_connect", lambda db=None: _conn(env["db"]))
    monkeypatch.setattr(b, "_out", lambda *a: None)
    assert b._handle_stepup_builtin("setup", secret=lambda *_: "000000")
    assert stepup.SERVICE not in env["store"]  # wrong first code: off again
    new = {}

    def _code(*_):
        new["k"] = env["store"][stepup.SERVICE]
        return stepup.totp(base64.b32decode(new["k"]), stepup.time.time())
    b._handle_stepup_builtin("setup", secret=_code)
    assert env["store"].get(stepup.SERVICE) == new["k"]
    b._handle_stepup_builtin("off")
    assert stepup.SERVICE not in env["store"]
