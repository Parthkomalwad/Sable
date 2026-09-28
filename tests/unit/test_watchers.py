"""Phase 5 Task 2 (E3): watchers.

Each check is a pure function over injected readers, so nothing here touches a
real disk or network. A watcher fires once per state change, not every tick.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from sable.daemon import watchers
from sable.policy import queue


def _r(**kw):
    base = dict(disk=lambda p: (100, 50, 50), mtime=lambda p: 1.0,
                size=lambda p: 0, read=lambda p, off: b"", http=lambda u: 200)
    base.update(kw)
    return SimpleNamespace(**base)


FULL = dict(disk=lambda p: (100, 95, 5))


class TestChecks:
    def test_disk_fires_once_when_over(self):
        r = _r(**FULL)
        state, _, fired = watchers.check("disk", "/", "90", None, 0, r)
        assert state == "over" and fired
        assert watchers.check("disk", "/", "90", state, 0, r)[2] is None

    def test_disk_under_does_not_fire_and_rearms(self):
        state, _, fired = watchers.check("disk", "/", "90", "over", 0, _r())
        assert (state, fired) == ("ok", None)

    def test_file_first_look_is_silent_then_change_fires(self):
        state, _, fired = watchers.check("file", "/f", "", None, 0, _r())
        assert fired is None
        assert watchers.check("file", "/f", "", state, 0, _r(mtime=lambda p: 2.0))[2]
        assert watchers.check("file", "/f", "", state, 0, _r())[2] is None

    def test_log_reads_only_new_bytes(self):
        data = b"ok\nERROR disk\n"
        r = _r(size=lambda p: len(data), read=lambda p, off: data[off:])
        state, off, fired = watchers.check("log", "/l", "ERROR", None, 0, r)
        assert fired is None and off == len(data)  # starts at the end
        state, off2, fired = watchers.check("log", "/l", "ERROR", state, 3, r)
        assert "ERROR disk" in fired and off2 == len(data)
        assert watchers.check("log", "/l", "ERROR", state, off2, r)[2] is None

    def test_log_truncation_resets_offset(self):
        data = b"ERROR again\n"
        r = _r(size=lambda p: len(data), read=lambda p, off: data[off:])
        _, off, fired = watchers.check("log", "/l", "ERROR", "seen", 500, r)
        assert fired and off == len(data)

    def test_http_fires_on_unexpected_status_once(self):
        r = _r(http=lambda u: 502)
        state, _, fired = watchers.check("http", "http://localhost:8080", "200", "ok", 0, r)
        assert fired
        assert watchers.check("http", "http://localhost:8080", "200", state, 0, r)[2] is None

    def test_http_refuses_non_local_urls(self):
        with pytest.raises(ValueError):
            watchers.validate("http", "http://example.com", "200")


@pytest.fixture
def conn(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(watchers, "_publish", lambda kind, payload: events.append((kind, payload)))
    monkeypatch.setattr(watchers, "_checked", {})
    c = sqlite3.connect(tmp_path / "s.db")
    yield c, events
    c.close()


class TestHandler:
    def test_notify_publishes_and_throttles(self, conn):
        c, events = conn
        watchers.add(c, "disk", "/", "90")
        watchers.run_due(c, now=1000.0, readers=_r(**FULL))
        assert [e[0] for e in events] == ["watch.fired"]
        watchers.run_due(c, now=1010.0, readers=_r())  # throttled, never checked
        watchers.run_due(c, now=1031.0, readers=_r(**FULL))  # still over: no refire
        assert len(events) == 1

    def test_approve_tier_waits_then_runs_once_approved(self, conn, monkeypatch):
        c, _ = conn
        monkeypatch.setattr(watchers.jobs, "_audit", lambda cwd, cmd: None)
        wid = watchers.add(c, "disk", "/", "90", tier="approve", steps=["df -h", "rm -rf /tmp/x"])
        watchers.run_due(c, now=1.0, readers=_r(**FULL))
        assert [(p["agent"], p["command"]) for p in queue.pending(c)] == [(f"daemon:watch:{wid}", "df -h")]
        ran = []
        run = lambda cmd, cwd, **kw: ran.append(cmd) or "(no output; exit 0)"
        watchers.jobs.resume_waiting(c, run=run)
        assert ran == []                                   # still pending: nothing runs
        queue.decide_request(c, queue.pending(c)[0]["id"], approve=True)
        watchers.jobs.resume_waiting(c, run=run)
        assert ran == ["df -h"]                            # the confirm-tier rm waits again
        assert [p["command"] for p in queue.pending(c)] == ["rm -rf /tmp/x"]
        assert c.execute("SELECT count(*) FROM policy_queue WHERE status = 'approved'").fetchone()[0] == 0

    def test_run_tier_goes_through_run_plan(self, conn, monkeypatch):
        c, _ = conn
        calls = []
        monkeypatch.setattr(watchers.jobs, "run_plan",
                            lambda conn, job, steps, cwd: calls.append((job, steps)))
        wid = watchers.add(c, "disk", "/", "90", tier="run", steps=["df -h"])
        watchers.run_due(c, now=1.0, readers=_r(**FULL))
        assert calls == [(f"watch:{wid}", ["df -h"])]

    def test_list_and_remove(self, conn):
        c, _ = conn
        wid = watchers.add(c, "file", "/etc/hosts", "")
        assert [w["id"] for w in watchers.list_all(c)] == [wid]
        assert watchers.remove(c, wid) and not watchers.list_all(c)


class TestBuiltin:
    def _db(self, c):
        return SimpleNamespace(_conn=c)

    def test_add_list_rm(self, conn):
        from sable.app.builtins.watch import handle_watch
        c, _ = conn
        assert handle_watch("add disk / 90", self._db(c))
        assert watchers.list_all(c)[0]["tier"] == "notify"
        assert handle_watch("list", self._db(c))
        handle_watch("rm 1", self._db(c))
        assert not watchers.list_all(c)

    def test_run_tier_needs_confirm(self, conn, monkeypatch):
        from sable.app.builtins.watch import handle_watch
        c, _ = conn
        line = 'add disk / 90 --tier run --do "df -h; du -sh /var"'
        monkeypatch.setattr("builtins.input", lambda *a: "q")
        handle_watch(line, self._db(c))
        assert not watchers.list_all(c)
        monkeypatch.setattr("builtins.input", lambda *a: "")
        handle_watch(line, self._db(c))
        assert watchers.list_all(c)[0]["steps"] == ["df -h", "du -sh /var"]

    def test_bad_args_add_nothing(self, conn):
        from sable.app.builtins.watch import handle_watch
        c, _ = conn
        handle_watch("add http http://example.com 200", self._db(c))
        handle_watch("add disk / lots", self._db(c))
        handle_watch("add log /var/log/x [", self._db(c))
        assert not watchers.list_all(c)
