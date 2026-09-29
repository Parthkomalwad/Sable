"""Which paths a step touches (Phase 8 Task 2)."""
import os

from sable.agents.footprint import paths_of


def _p(tmp_path, *names):
    return {os.path.normpath(os.path.join(str(tmp_path), n)) for n in names}


def test_sed_in_place(tmp_path):
    (tmp_path / "a.conf").write_text("x")
    assert paths_of("sed -i 's/a/b/' a.conf", str(tmp_path)) == _p(tmp_path, "a.conf")


def test_redirections(tmp_path):
    assert paths_of("echo hi > out.txt", str(tmp_path)) == _p(tmp_path, "out.txt")
    assert paths_of("echo hi >> log 2>&1", str(tmp_path)) == _p(tmp_path, "log")
    assert paths_of("ls 2>/dev/null", str(tmp_path)) == set()
    assert paths_of("make 2>err.txt", str(tmp_path)) == _p(tmp_path, "err.txt")


def test_tee(tmp_path):
    assert paths_of("echo x | tee -a notes.txt", str(tmp_path)) == _p(tmp_path, "notes.txt")
    assert paths_of("echo x | sudo tee new/deep/f", str(tmp_path)) == _p(tmp_path, "new")


def test_cp_mv(tmp_path):
    (tmp_path / "src").write_text("s")
    assert paths_of("cp src dst", str(tmp_path)) == _p(tmp_path, "src", "dst")
    (tmp_path / "dir").mkdir()
    assert paths_of("mv -f src dir/", str(tmp_path)) == _p(tmp_path, "src", "dir/src")


def test_rm_and_flags(tmp_path):
    (tmp_path / "old").mkdir()
    assert paths_of("rm -rf old", str(tmp_path)) == _p(tmp_path, "old")


def test_mkdir_touch_record_topmost_new(tmp_path):
    assert paths_of("mkdir -p a/b/c", str(tmp_path)) == _p(tmp_path, "a")
    assert paths_of("touch new.txt", str(tmp_path)) == _p(tmp_path, "new.txt")


def test_chained_and_declared(tmp_path):
    (tmp_path / "f").write_text("x")
    got = paths_of("chmod 600 f; echo y > g", str(tmp_path), touches=["h"])
    assert got == _p(tmp_path, "f", "g", "h")


def test_unparseable_gives_declared_only(tmp_path):
    assert paths_of("echo 'unterminated", str(tmp_path), touches=["t"]) == _p(tmp_path, "t")


def test_undo_point_skips_read_only_and_snapshots_writes(tmp_path):
    from sable.agents.footprint import undo_point
    from sable.core import snapshots

    (tmp_path / "f").write_text("x")
    assert undo_point("cat f", str(tmp_path)) is None
    sid = undo_point("echo y > f", str(tmp_path), agent="orchestrator")
    (tmp_path / "f").write_text("y\n")
    snapshots.restore(sid)
    assert (tmp_path / "f").read_text() == "x"
    assert undo_point('tool:fs.write {"path": "g"}', str(tmp_path), touches=["g"]) is not None
