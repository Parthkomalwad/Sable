"""Phase 7 Task 3 (C3): nightly consolidation, rules only."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from sable.daemon import maintenance
from sable.memory import consolidate, palace
from sable.memory.palace import Fact

NOW = datetime(2026, 9, 29, 2, 30, tzinfo=timezone.utc)
S1 = {"session": "s1", "goal": "g"}
S2 = {"session": "s2", "goal": "g"}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(palace, "ROOT", tmp_path / "palace")
    monkeypatch.setattr(palace, "DB", tmp_path / "s.db")
    monkeypatch.setattr(consolidate, "_publish", lambda payload: None)
    return tmp_path


def put(fid, text, *, room="server", tier="episodic", created="2026-09-28T00:00:00+00:00",
        valid_to="", sources=(S1,), untrusted=False):
    palace.save(Fact(id=fid, room=room, text=text, tier=tier, untrusted=untrusted,
                     created=created, valid_to=valid_to, sources=list(sources)))


def ids():
    return sorted(f.id for f in palace.all_facts())


def test_expired_facts_are_deleted():
    put("f000000000001", "old port was 8080", valid_to="2026-09-28")
    put("f000000000002", "port is 9090", valid_to="2026-09-29")
    s = consolidate.run(NOW)
    assert s.expired == 1 and ids() == ["f000000000002"]


def test_stale_episodic_dropped_semantic_kept():
    put("f000000000001", "nginx logs live in var log", created="2026-08-01T00:00:00+00:00")
    put("f000000000002", "app config under etc myapp", created="2026-08-01T00:00:00+00:00", tier="semantic")
    put("f000000000003", "redis on port 6379", created="2026-09-10T00:00:00+00:00")
    s = consolidate.run(NOW)
    assert s.dropped == 1 and ids() == ["f000000000002", "f000000000003"]


def test_near_duplicates_merge_keeping_older_and_both_sources():
    put("f000000000001", "The nginx logs live in /var/log/nginx", created="2026-09-20T00:00:00+00:00",
        untrusted=True)
    put("f000000000002", "nginx logs live in /var/log/nginx.", created="2026-09-25T00:00:00+00:00",
        sources=(S2, S1), tier="semantic")
    put("f000000000003", "nginx logs live in /var/log/nginx", room="user")  # other room: untouched
    s = consolidate.run(NOW)
    assert s.merged == 1
    f = palace.why("f000000000001")
    assert f.text == "The nginx logs live in /var/log/nginx"
    assert f.sources == [S1, S2] and f.tier == "semantic" and f.untrusted is False
    assert palace.why("f000000000002") is None and palace.why("f000000000003")


def test_different_facts_do_not_merge():
    put("f000000000001", "postgres data in /var/lib/postgresql")
    put("f000000000002", "postgres logs in /var/log/postgresql")
    assert consolidate.run(NOW).merged == 0 and len(ids()) == 2


def test_two_sessions_promote_to_semantic_keeping_untrusted():
    put("f000000000001", "backups run at midnight", sources=(S1, S2), untrusted=True)
    put("f000000000002", "disk is sdb", sources=(S1, {**S1, "goal": "other"}))
    s = consolidate.run(NOW)
    assert s.promoted == 1
    f = palace.why("f000000000001")
    assert f.tier == "semantic" and f.untrusted is True
    assert palace.why("f000000000002").tier == "episodic"


def test_second_run_changes_nothing():
    put("f000000000001", "The nginx logs live in /var/log/nginx", created="2026-09-20T00:00:00+00:00")
    put("f000000000002", "nginx logs live in /var/log/nginx", sources=(S2,))
    put("f000000000003", "gone", valid_to="2026-01-01")
    assert consolidate.run(NOW).changes
    second = consolidate.run(NOW)
    assert second.changes == [] and second.merged == second.promoted == second.expired == 0


def test_daemon_runs_once_per_day_at_the_minute(monkeypatch):
    calls = []
    monkeypatch.setattr(maintenance.consolidate, "run", lambda now=None: calls.append(now))
    conn = sqlite3.connect(":memory:")
    day = datetime(2026, 9, 29, 2, 29)
    assert not maintenance.due(conn, day, "02:30")
    assert maintenance.due(conn, day.replace(minute=30), "02:30")
    assert not maintenance.due(conn, day.replace(minute=30, second=40), "02:30")  # restart, same minute
    assert not maintenance.due(conn, day.replace(hour=14), "02:30")
    assert maintenance.due(conn, datetime(2026, 9, 30, 2, 30), "02:30")
    assert len(calls) == 2


def test_maintenance_time_is_validated():
    from sable.core.config.schema import ShellConfig
    base = {"model": "m"}
    assert ShellConfig.from_dict(base).maintenance_time == "02:30"
    assert ShellConfig.from_dict({**base, "maintenance_time": "23:05"}).to_dict()["maintenance_time"] == "23:05"
    with pytest.raises(ValueError):
        ShellConfig.from_dict({**base, "maintenance_time": "24:00"})


def test_the_summary_reads_as_a_sentence():
    from sable.memory.consolidate import Summary
    text = str(Summary(merged=1, changes=["merged f1 into f2"]))
    assert text.startswith("consolidated: 1 merged, 0 promoted") and "merged f1 into f2" in text
