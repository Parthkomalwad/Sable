"""Snapshots and undo (Phase 8 Task 2): restore must be exact."""
import json
import os
import stat

import pytest

from sable.core import snapshots


@pytest.fixture(autouse=True)
def _root(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "ROOT", tmp_path / "snaps")


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_edited_file_round_trip(tmp_path):
    f = tmp_path / "a.conf"
    f.write_bytes(b"one\r\ntwo\x00bin")
    sid = snapshots.take([str(f)], "edit")
    f.write_bytes(b"changed")
    assert "a.conf" in snapshots.diff(sid)
    snapshots.restore(sid)
    assert f.read_bytes() == b"one\r\ntwo\x00bin"
    assert snapshots.diff(sid) == ""


def test_created_file_is_removed(tmp_path):
    f = tmp_path / "new" / "deep.txt"
    sid = snapshots.take([str(tmp_path / "new")])
    f.parent.mkdir()
    f.write_text("x")
    assert "created" in snapshots.diff(sid)
    snapshots.restore(sid)
    assert not (tmp_path / "new").exists()


def test_deleted_file_comes_back(tmp_path):
    f = tmp_path / "gone.txt"
    f.write_text("keep me\n")
    sid = snapshots.take([str(f)])
    f.unlink()
    assert "deleted" in snapshots.diff(sid)
    snapshots.restore(sid)
    assert f.read_text() == "keep me\n"


def test_chmod_is_undone(tmp_path):
    f = tmp_path / "script.sh"
    f.write_text("echo hi\n")
    os.chmod(f, 0o644)
    before = _mode(f)
    sid = snapshots.take([str(f)])
    os.chmod(f, 0o444)
    assert _mode(f) != before
    snapshots.restore(sid)
    assert _mode(f) == before
    assert f.read_text() == "echo hi\n"


def test_directory_tree_round_trip(tmp_path):
    d = tmp_path / "app"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_text("a")
    (d / "sub" / "b.txt").write_text("b")
    sid = snapshots.take([str(d)])
    (d / "a.txt").write_text("A!")
    (d / "sub" / "b.txt").unlink()
    (d / "added.txt").write_text("new")
    (d / "newdir").mkdir()
    (d / "newdir" / "x").write_text("x")
    snapshots.restore(sid)
    got = sorted(str(p.relative_to(d)).replace(os.sep, "/") for p in d.rglob("*"))
    assert got == ["a.txt", "sub", "sub/b.txt"]
    assert (d / "a.txt").read_text() == "a"
    assert (d / "sub" / "b.txt").read_text() == "b"


def test_restore_touches_nothing_outside(tmp_path):
    f, other = tmp_path / "f", tmp_path / "other"
    f.write_text("1")
    sid = snapshots.take([str(f)])
    other.write_text("step also wrote this")
    snapshots.restore(sid)
    assert other.read_text() == "step also wrote this"


def test_size_limit_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "MAX_BYTES", 10)
    f = tmp_path / "big"
    f.write_bytes(b"x" * 11)
    with pytest.raises(snapshots.SnapshotError, match="50 MB"):
        snapshots.take([str(f)])
    assert snapshots.list() == []


def test_ring_prunes_oldest(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "RING", 3)
    f = tmp_path / "f"
    f.write_text("x")
    ids = [snapshots.take([str(f)]) for _ in range(5)]
    assert [m["id"] for m in snapshots.list()] == ids[:-4:-1]


def test_tampered_manifest_path_refused(tmp_path):
    f, victim = tmp_path / "f", tmp_path / "victim"
    f.write_text("snap")
    victim.write_text("precious")
    sid = snapshots.take([str(f)])
    mpath = snapshots.ROOT / str(sid) / "manifest.json"
    m = json.loads(mpath.read_text())
    m["entries"][0]["path"] = str(tmp_path) + os.sep + "sub" + os.sep + ".." + os.sep + "victim"
    mpath.write_text(json.dumps(m))
    with pytest.raises(snapshots.SnapshotError):
        snapshots.restore(sid)
    assert victim.read_text() == "precious"


def test_tampered_copy_refused(tmp_path):
    f = tmp_path / "f"
    f.write_text("snap")
    sid = snapshots.take([str(f)])
    (snapshots.ROOT / str(sid) / "0").write_text("evil")
    f.write_text("current")
    with pytest.raises(snapshots.SnapshotError, match="hash"):
        snapshots.restore(sid)
    assert f.read_text() == "current"


def test_latest_filters_by_agent(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    a = snapshots.take([str(f)], agent="orchestrator", session="1")
    snapshots.take([str(f)], agent="w1")
    assert snapshots.latest(agent="orchestrator", session="1")["id"] == a
