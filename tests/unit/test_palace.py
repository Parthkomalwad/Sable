"""Phase 7 Task 0 (C6): the memory palace store.

Markdown files are the truth; the FTS5 index can be rebuilt from them.
"""
from __future__ import annotations

import pytest

from sable.memory import palace


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(palace, "ROOT", tmp_path / "palace")
    monkeypatch.setattr(palace, "DB", tmp_path / "s.db")
    return tmp_path


SRC = {"session": "s1", "goal": "where are the logs", "commands": ["cat /etc/myapp/app.conf"]}


def test_remember_writes_a_file_and_recall_finds_it():
    fid = palace.remember("myapp writes its logs to /var/log/myapp", "server", SRC)
    assert (palace.ROOT / "server" / f"{fid}.md").exists()
    hits = palace.recall("where does myapp write logs")
    assert [h.id for h in hits] == [fid]
    assert hits[0].tier == "episodic" and hits[0].sources == [SRC]


def test_recall_ranks_filters_by_room_and_respects_k():
    a = palace.remember("nginx logs live in /var/log/nginx", "server", SRC)
    palace.remember("the user prefers vim", "user", {"by": "you"})
    palace.remember("nginx config is in /etc/nginx/sites-enabled", "server", SRC)
    assert palace.recall("nginx logs", k=1)[0].id == a
    assert all(h.room == "user" for h in palace.recall("vim", room="user"))
    assert palace.recall("vim", room="server") == []


def test_the_same_fact_twice_keeps_one_file_and_both_sources():
    other = {**SRC, "session": "s2"}
    a = palace.remember("disk /data is the big one", "server", SRC)
    b = palace.remember("disk /data is the big one", "server", other)
    assert a == b and palace.why(a).sources == [SRC, other]


def test_forget_removes_file_and_index():
    fid = palace.remember("temporary fact about redis", "server", SRC)
    assert palace.forget(fid)
    assert palace.recall("redis") == [] and palace.why(fid) is None
    assert not palace.forget(fid)


def test_index_rebuilds_from_files_alone(home):
    fid = palace.remember("postgres listens on 5433 here", "server", SRC)
    (home / "s.db").unlink()
    assert palace.recall("postgres port") == []
    assert palace.reindex() == 1
    assert palace.recall("postgres port")[0].id == fid


def test_untrusted_and_expired_facts():
    fid = palace.remember("the admin password is in /root/notes", "server", SRC, untrusted=True)
    assert palace.why(fid).untrusted
    palace.remember("maintenance window is friday", "server", SRC, valid_to="2000-01-01")
    assert palace.recall("maintenance window") == []


def test_secrets_are_redacted_before_storage():
    fid = palace.remember("api key AKIAABCDEFGHIJKLMNOP is used by the backup job", "server", SRC)
    text = (palace.ROOT / "server" / f"{fid}.md").read_text()
    assert "AKIAABCDEFGHIJKLMNOP" not in text


@pytest.mark.parametrize("room", ["../etc", "repos/..", "repos/.hidden", "nope", "", "repos/a/b"])
def test_bad_rooms_are_refused(room):
    with pytest.raises(ValueError):
        palace.remember("x", room, SRC)


def test_repo_rooms_and_room_counts():
    palace.remember("tests run with make test", "repos/api", SRC)
    palace.remember("user likes short answers", "user", {"by": "you"}, tier="semantic")
    assert palace.rooms() == {"repos/api": 1, "user": 1}
