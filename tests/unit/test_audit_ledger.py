"""F4: the provenance ledger and `/audit`.

Every decision `gate()` makes writes one `audit_ledger` row (who, why, what,
outcome); the call site that then runs the command fills in exit code and
duration with `audit.finish()`. Secrets are redacted before they are stored.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from sable.core import audit


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    import sable.core.db as db

    path = tmp_path / "sessions.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    return path


def _rows(path):
    return audit.query(db_path=path)


def test_record_and_finish_fill_every_field(db_path):
    audit.record("ls -la", agent="w1", role="worker", model="m1", outcome="allowed",
                 tier="allow", goal="list files", cwd="/tmp")
    audit.finish(exit_code=0)
    (row,) = _rows(db_path)
    assert "uid" in row  # None on Windows, which has no os.getuid
    assert (row["agent"], row["model"], row["command"], row["outcome"]) == ("w1", "m1", "ls -la", "allowed")
    assert row["goal"] == "list files" and row["cwd"] == "/tmp"
    assert row["exit_code"] == 0 and row["duration_ms"] is not None


def test_redactor_runs_on_command_and_goal(db_path):
    audit.record("echo AKIAIOSFODNN7EXAMPLE", agent="user", outcome="allowed",
                 goal="use AKIAIOSFODNN7EXAMPLE", redact=lambda t: t.replace("AKIAIOSFODNN7EXAMPLE", "[REDACTED]"))
    (row,) = _rows(db_path)
    assert "AKIA" not in row["command"] and "AKIA" not in row["goal"]


def test_record_never_raises_on_unwritable_db(tmp_path, monkeypatch):
    import sable.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path)  # a directory, not a file
    audit.record("ls", agent="user", outcome="allowed")
    audit.finish(exit_code=0)


def test_query_filters_by_agent_and_since(db_path):
    audit.record("a", agent="x", outcome="allowed")
    audit.record("b", agent="y", outcome="refused")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE audit_ledger SET ts = '2000-01-01T00:00:00+00:00' WHERE command = 'a'")
    conn.commit()
    conn.close()
    assert [r["command"] for r in audit.query(agent="y", db_path=db_path)] == ["b"]
    assert [r["command"] for r in audit.query(since_seconds=3600, db_path=db_path)] == ["b"]


def test_gate_records_refusal_with_rule(db_path):
    from sable.policy.engine import gate

    assert gate("rm -rf /", role="worker", agent="w9", model="m") is False
    (row,) = _rows(db_path)
    # No default rule is `deny`; a worker cannot type YES, so confirm refuses.
    assert row["agent"] == "w9" and row["model"] == "m"
    assert row["tier"] == "confirm" and row["rule"] and row["why"]
    assert row["outcome"] == "unconfirmed"


def test_gate_records_allowed(db_path):
    from sable.policy.engine import gate

    assert gate("echo hi", role="user") is True
    (row,) = _rows(db_path)
    assert (row["agent"], row["outcome"], row["tier"]) == ("user", "allowed", "allow")


def test_gate_redacts_secrets(db_path):
    from sable.policy.engine import gate

    gate("echo AKIAIOSFODNN7EXAMPLE", role="user")
    (row,) = _rows(db_path)
    assert "AKIAIOSFODNN7EXAMPLE" not in row["command"]


def test_parse_since():
    from sable.app.builtins.audit import parse_since

    assert parse_since("30m") == 1800
    assert parse_since("1h") == 3600
    assert parse_since("2d") == 172800
    assert parse_since("soon") is None


def test_audit_builtin_table_and_filters(db_path, capsys):
    from sable.app.builtins.audit import handle_audit

    audit.record("ls", agent="alpha", model="m1", outcome="allowed")
    audit.record("pwd", agent="beta", outcome="refused")
    handle_audit("--since 1h --agent alpha")
    out = capsys.readouterr().out
    assert "alpha" in out and "ls" in out and "beta" not in out


def test_audit_builtin_export_jsonl(db_path, tmp_path, monkeypatch, capsys):
    from sable.app.builtins import audit as builtin

    monkeypatch.setattr(builtin, "EXPORT_DIR", tmp_path / "exports")
    audit.record("ls", agent="alpha", outcome="allowed")
    builtin.handle_audit("--export jsonl")
    (path,) = (tmp_path / "exports").iterdir()
    assert str(path) in capsys.readouterr().out
    (line,) = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["command"] == "ls"


def test_audit_builtin_bad_args(db_path, capsys):
    from sable.app.builtins.audit import handle_audit

    handle_audit("--since soon")
    assert "usage" in capsys.readouterr().out


def test_gate_records_tier_after_taint_bump(db_path):
    from sable.policy.engine import gate

    # confirm + tainted -> deny: the row must show the bumped tier and reason.
    assert gate("rm -rf /", role="worker", agent="w2", tainted=True) is False
    (row,) = _rows(db_path)
    assert (row["tier"], row["outcome"]) == ("deny", "refused")
    assert "tainted context" in row["why"]
