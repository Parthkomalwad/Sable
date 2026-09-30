"""Phase 4 Task 2: the Textual sidebar, driven headless against a temp DB."""
from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from datetime import datetime, timezone

import pytest

pytest.importorskip("textual")

from sable.ui.sidebar import app as sidebar


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE tasks (id INTEGER PRIMARY KEY, name TEXT, goal TEXT, status TEXT,
                            step_count INTEGER, last_output TEXT);
        CREATE TABLE task_events (task_name TEXT, prompt_tokens INT, completion_tokens INT, cost_usd REAL);
        CREATE TABLE policy_queue (id INTEGER PRIMARY KEY, agent TEXT, command TEXT, rule TEXT,
                                   why TEXT, status TEXT);
        CREATE TABLE breaker_trips (id INTEGER PRIMARY KEY, job TEXT, reason TEXT, status TEXT);
        CREATE TABLE token_events (id INTEGER PRIMARY KEY, timestamp TEXT, prompt_tokens INT,
                                   completion_tokens INT, total_tokens INT, cost_usd REAL);
        CREATE TABLE snippets (id INTEGER PRIMARY KEY, command TEXT, note TEXT, tags TEXT,
                               use_count INT, created_at TEXT);
    """)
    c.executemany("INSERT INTO tasks (name, goal, status, step_count, last_output) VALUES (?,?,?,?,?)", [
        ("build", "build it", "running", 3, ""),
        ("deploy", "deploy it", "running", 1, ""),
        ("old", "old work", "failed", 7, ""),
    ])
    c.execute("INSERT INTO policy_queue (agent, command, rule, why, status) VALUES "
              "('deploy', 'rm -rf build', 'recursive-delete', 'no undo', 'pending')")
    c.execute("INSERT INTO token_events (timestamp, prompt_tokens, completion_tokens, total_tokens, cost_usd) "
              "VALUES (?, 1500, 250, 1750, 0.25)",
              (datetime.now(timezone.utc).isoformat(),))
    c.execute("INSERT INTO snippets (command, note, tags, use_count) VALUES ('make test', 'tests', '', 3)")
    c.commit()
    c.close()
    # Keep the test off the host: no git or tmux subprocesses.
    monkeypatch.setattr(sidebar, "_git", lambda: "not a git repo")
    monkeypatch.setattr(sidebar, "_window_width", lambda: None)
    return path


def test_snapshot_reads_badges_inbox_cost_snippets(db):
    snap = sidebar.snapshot(db)
    assert [(a.name, a.badge) for a in snap.agents] == [
        ("old", "failed"), ("deploy", "awaiting approval"), ("build", "running")]
    assert len(snap.inbox) == 1
    assert snap.today == pytest.approx(0.25)
    assert snap.days[-1] == pytest.approx(0.25) and len(snap.days) == 7
    assert snap.snippets == ["tests: make test"]


def _run(db, size, check, resize_to=None):
    async def go():
        app = sidebar.SidebarApp(db_path=db)
        async with app.run_test(size=size) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            check(app, size[0])
            if resize_to:
                await pilot.resize_terminal(*resize_to)
                await pilot.pause()
                check(app, resize_to[0])
    asyncio.run(go())


def _text(app, wid):
    return str(app.query_one(f"#{wid}").render())


def test_badges_and_inbox_render(db):
    def check(app, _w):
        agents = _text(app, "agents")
        assert "awaiting approval" in agents and "running" in agents and "failed" in agents
        assert app.query_one("#inbox-box").display
        assert "INBOX · 1" in _text(app, "inbox-h")
        assert "rm -rf build" in _text(app, "inbox")
        session = _text(app, "cost")
        assert "$0.2500" in session and "1.5k in" in session and "250 out" in session
    _run(db, (100, 50), check)


def test_the_inbox_hides_when_nothing_is_waiting(db):
    c = sqlite3.connect(db)
    c.execute("DELETE FROM policy_queue")
    c.commit()
    c.close()
    _run(db, (100, 50), lambda app, _w: not_shown(app))


def not_shown(app):
    assert not app.query_one("#inbox-box").display


def test_hides_below_90_columns_and_comes_back(db):
    def check(app, width):
        narrow = app.query_one("#narrow")
        body = app.query_one("#body")
        assert narrow.display == (width < 90)
        assert body.display == (width >= 90)
    _run(db, (100, 50), check, resize_to=(80, 50))
    _run(db, (80, 50), check, resize_to=(120, 50))


def test_never_writes_the_db(db):
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    _run(db, (100, 50), lambda app, w: None)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert not db.with_name(db.name + "-wal").exists()


def test_line_meter_fills_in_proportion_and_goes_red_past_90():
    low = sidebar.line_meter(25, sidebar.GREEN, width=12)
    assert low.plain == "━" * 12
    assert low.spans[0].end == 3 and sidebar.GREEN in str(low.spans[0].style)
    high = sidebar.line_meter(95, sidebar.GREEN, width=12)
    assert sidebar.RED in str(high.spans[0].style)
    assert sidebar.line_meter(140, sidebar.GREEN, width=12).spans[0].end == 12


def test_week_chart_is_three_rows_of_bars_over_weekday_initials():
    from datetime import date
    wed = date(2026, 9, 30)
    lines = sidebar.week_chart([0, 0, 0, 0, 0, 50, 100], today=wed).plain.splitlines()
    assert len(lines) == sidebar.CHART_ROWS + 1
    assert lines[0].rstrip().endswith("██")          # today reaches the top row
    assert lines[-2].startswith("▁▁ ▁▁")              # zero days keep a floor
    assert lines[-1].split() == list("TFSSMTW")


def test_a_quiet_week_still_shows_seven_slots():
    lines = sidebar.week_chart([0] * 7).plain.splitlines()
    assert lines[-2].split() == ["▁▁"] * 7


def test_human_numbers():
    assert [sidebar.human(n) for n in (12, 1234, 2_500_000)] == ["12", "1.2k", "2.5M"]


def test_slow_git_status_still_shows_the_branch(monkeypatch):
    """The Windows playground bind mount makes `git status` take over two
    seconds. A timeout there used to report "not a git repo"."""
    import subprocess as sp
    from sable.ui.sidebar import watch

    def fake_run(cmd, **kw):
        if "status" in cmd:
            raise sp.TimeoutExpired(cmd, kw.get("timeout"))
        out = "main" if "branch" in cmd else "0\t0"
        return sp.CompletedProcess(cmd, 0, stdout=out, stderr="")
    monkeypatch.setattr(watch, "_current_dir", lambda: "/app")
    monkeypatch.setattr(sidebar.subprocess, "run", fake_run)
    assert sidebar._git() == "main  status slow"


def test_git_reports_dirty_count(monkeypatch):
    import subprocess as sp
    from sable.ui.sidebar import watch

    def fake_run(cmd, **kw):
        out = {"branch": "main", "status": " M a\n?? b", "rev-list": "2\t0"}
        return sp.CompletedProcess(cmd, 0, stdout=next(v for k, v in out.items() if k in cmd), stderr="")
    monkeypatch.setattr(watch, "_current_dir", lambda: "/app")
    monkeypatch.setattr(sidebar.subprocess, "run", fake_run)
    assert sidebar._git() == "main  2 dirty  ↑2"


def test_git_never_takes_the_index_lock(monkeypatch):
    """A polling `git status` that refreshes the index holds index.lock, and
    the user's own commit then fails with "File exists"."""
    import subprocess as sp
    from sable.ui.sidebar import watch
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return sp.CompletedProcess(cmd, 0, stdout="main" if "branch" in cmd else "", stderr="")
    monkeypatch.setattr(watch, "_current_dir", lambda: "/app")
    monkeypatch.setattr(sidebar.subprocess, "run", fake_run)
    sidebar._git()
    assert seen and all("--no-optional-locks" in cmd for cmd in seen)
