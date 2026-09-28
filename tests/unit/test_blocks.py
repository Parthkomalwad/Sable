"""Blocks in scrollback (G2): the store, formatting, alt-screen and /block."""
from __future__ import annotations

import sqlite3

import pytest

from sable.core import blocks


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


def test_numbers_are_sequential_and_finish_fills_the_row(conn):
    a = blocks.start(conn, "s1", "/tmp", "ls")
    b = blocks.start(conn, "s1", "/tmp", "pwd")
    assert b == a + 1
    blocks.finish(conn, a, 2, 15, "x\n" * 300, cost_usd=0.01, redact=str.upper)
    row = blocks.get(conn, a)
    assert row["exit_code"] == 2 and row["duration_ms"] == 15
    assert row["cost_usd"] == 0.01
    assert row["output"].splitlines() == ["X"] * 200


def test_output_is_redacted_and_ansi_stripped(conn):
    n = blocks.start(conn, "s", "/", "env")
    blocks.finish(conn, n, 0, 1, "\x1b[31mTOKEN=abc\x1b[0m\r\n",
                  redact=lambda t: t.replace("abc", "[REDACTED]"))
    assert blocks.get(conn, n)["output"] == "TOKEN=[REDACTED]"


def test_recent_lists_newest_first_and_limits(conn):
    for i in range(25):
        blocks.start(conn, "s", "/", f"echo {i}")
    rows = blocks.recent(conn)
    assert len(rows) == 20 and rows[0]["command"] == "echo 24"


def test_get_missing_is_none(conn):
    assert blocks.get(conn, 99) is None


def test_alt_screen_and_fullscreen_detection():
    assert blocks.uses_alt_screen("abc\x1b[?1049hdef")
    assert not blocks.uses_alt_screen("plain")
    assert blocks.is_fullscreen("sudo vim /etc/hosts")
    assert blocks.is_fullscreen("/usr/bin/htop")
    assert not blocks.is_fullscreen("ls -la")


def test_header_and_footer_format():
    assert "#7" in blocks.header(7, "ls") and "ls" in blocks.header(7, "ls")
    f = blocks.footer(1, 1500, 0.0123)
    assert "exit 1" in f and "1.5s" in f and "$0.0123" in f
    assert "$" not in blocks.footer(0, 20, None)
    assert "20ms" in blocks.footer(0, 20, None)


# /block builtin ------------------------------------------------------------

class _DB:
    def __init__(self, c):
        self._conn = c


def _seed(conn):
    n = blocks.start(conn, "s", "/srv", "echo hi")
    blocks.finish(conn, n, 0, 3, "hi\n")
    return n


def test_block_list_show_copy(conn, capsys):
    from sable.app.builtins import block
    n = _seed(conn)
    db = _DB(conn)
    assert block.handle_block("", db, "s")
    assert "echo hi" in capsys.readouterr().out
    block.handle_block(f"{n} show", db, "s")
    assert "hi" in capsys.readouterr().out
    block.handle_block(f"{n} copy", db, "s")
    assert "echo hi" in capsys.readouterr().out
    block.handle_block("99 show", db, "s")
    assert "no block #99" in capsys.readouterr().out
    block.handle_block("x", db, "s")
    assert "usage" in capsys.readouterr().out


def test_rerun_goes_through_gate(conn, monkeypatch):
    from sable.app.builtins import block
    n = _seed(conn)
    calls = []
    monkeypatch.setattr("sable.policy.engine.gate",
                        lambda cmd, role: calls.append((cmd, role)) or False)
    ran = []
    monkeypatch.setattr(block, "run_block", lambda *a, **k: ran.append(a) or (0, ""))
    block.handle_block(f"{n} rerun", _DB(conn), "s")
    assert calls == [("echo hi", "user")] and ran == []

    monkeypatch.setattr("sable.policy.engine.gate", lambda cmd, role: True)
    monkeypatch.setattr("sable.core.audit.finish", lambda code: None)
    block.handle_block(f"{n} rerun", _DB(conn), "s")
    assert ran and ran[0][0] == "echo hi"


def test_run_block_records_and_prints(conn, capsys):
    from sable.app.builtins.block import run_block
    code, _ = run_block("ls", "/", _DB(conn), "s", run=lambda c, d: (3, "out\n"))
    assert code == 3
    printed = capsys.readouterr().out
    assert "#1" in printed and "exit 3" in printed
    assert blocks.get(conn, 1)["exit_code"] == 3


def test_run_block_fullscreen_prints_only_footer(conn, capsys):
    from sable.app.builtins.block import run_block
    run_block("vim x", "/", _DB(conn), "s", run=lambda c, d: (0, "\x1b[?1049hjunk"))
    printed = capsys.readouterr().out
    assert len(printed.splitlines()) == 1  # footer only, no header
    assert "#1" in printed and "exit 0" in printed
    assert blocks.get(conn, 1)["output"] == ""


def test_run_block_without_db_still_runs():
    from sable.app.builtins.block import run_block
    assert run_block("ls", "/", None, "s", run=lambda c, d: (0, "")) == (0, "")
