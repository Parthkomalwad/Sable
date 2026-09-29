"""Phase 8 Task 3 (F2 K5): rehearse a plan on a copy before it runs."""
from __future__ import annotations

import os
import sqlite3
import sys
import types

import pytest

from sable.agents import rehearse
from sable.core import snapshots
from sable.daemon import jobs
from sable.policy import queue


def _ok(argv, **kw):
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def _run(tmp_path, steps, run=_ok):
    return rehearse.rehearse(steps, str(tmp_path), run=run, probe=lambda: "")


class TestClassify:
    def test_outside_filesystem_is_not_rehearsable(self):
        for cmd in ("systemctl restart nginx", "sudo service x stop", "docker run x",
                    "curl -o f http://x", "wget http://x", "pip install requests",
                    "apt-get install -y jq", "kill 12", "pkill nginx", "reboot"):
            assert not rehearse.rehearsable(cmd), cmd
        assert rehearse.rehearsable("sed -i s/a/b/ f.txt")

    def test_not_rehearsable_and_unknown_are_shown_not_run(self, tmp_path):
        (tmp_path / "f").write_text("x")
        ran = []
        r = _run(tmp_path, ["systemctl restart x", "make build", "touch f"],
                 run=lambda argv, **kw: ran.append(argv[-1]) or _ok(argv))
        assert [(s.status, s.note) for s in r.steps] == [
            ("not rehearsed", "not rehearsable"), ("not rehearsed", "unknown footprint"), ("ok", "")]
        assert ran == ["touch f"]
        text = rehearse.render(r)
        assert "not rehearsable" in text and "unknown footprint" in text

    def test_read_only_steps_run_but_add_no_footprint(self, tmp_path):
        (tmp_path / "f").write_text("x")
        paths, skip = rehearse._footprint(["cat f", "ls"], str(tmp_path))
        assert paths == [] and skip == {}

    def test_wanted(self):
        two = ["touch a", "rm b"]
        assert rehearse.wanted(two, "auto")
        assert not rehearse.wanted(["touch a", "ls"], "auto")
        assert rehearse.wanted(["touch a"], "always")
        assert not rehearse.wanted(two, "off")


class TestFootprintCopy:
    def test_missing_path_copies_its_parent_and_nested_are_merged(self, tmp_path):
        (tmp_path / "d").mkdir()
        (tmp_path / "d" / "f").write_text("x")
        paths, _ = rehearse._footprint(["touch d/new", "rm d/f"], str(tmp_path))
        assert paths == [str(tmp_path / "d")]

    def test_refuses_path_holding_snapshot_store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(snapshots, "ROOT", tmp_path / "home" / ".sable" / "snapshots")
        with pytest.raises(rehearse.RehearsalError, match="snapshots"):
            rehearse._check([str(tmp_path / "home")], str(tmp_path / "elsewhere"))

    def test_refuses_path_holding_temp_dir(self, tmp_path):
        with pytest.raises(rehearse.RehearsalError, match="temp dir"):
            rehearse._check([str(tmp_path)], str(tmp_path / "tmpbase"))

    def test_size_limit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(snapshots, "MAX_BYTES", 10)
        (tmp_path / "big").write_text("x" * 100)
        r = _run(tmp_path, ["rm big", "touch big2"])
        assert not r.available and "limit" in r.reason
        assert (tmp_path / "big").exists()


class TestBwrap:
    def test_argv(self):
        argv = rehearse.bwrap_argv("sed -i s/a/b/ f", "/w", [("/w/f", "/t/0"), ("/etc/x", "/t/1")])
        assert argv[:3] == ["bwrap", "--ro-bind", "/"] and argv[3] == "/"
        assert ["--tmpfs", "/tmp"] == argv[argv.index("--tmpfs"):argv.index("--tmpfs") + 2]
        s = " ".join(argv)
        assert "--bind /t/0 /w/f" in s and "--bind /t/1 /etc/x" in s
        # copies are bound after the read-only root, so they win
        assert s.index("--ro-bind / /") < s.index("--bind /t/0")
        assert "--unshare-net" in argv
        assert argv[-3:] == ["/bin/bash", "-c", "sed -i s/a/b/ f"] and argv[argv.index("--chdir") + 1] == "/w"

    def test_unavailable_without_bwrap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rehearse.shutil, "which", lambda name: None)
        monkeypatch.setattr(rehearse.sys, "platform", "linux")
        called = []
        r = rehearse.rehearse(["touch a", "touch b"], str(tmp_path), run=lambda *a, **k: called.append(a))
        assert not r.available and "not installed" in r.reason and called == []
        assert "unavailable" in rehearse.render(r)

    def test_bwrap_refusing_to_start_is_unavailable(self, tmp_path):
        (tmp_path / "f").write_text("x")
        r = _run(tmp_path, ["touch f"], run=lambda argv, **kw: types.SimpleNamespace(
            returncode=1, stdout="", stderr="bwrap: No permissions to create new namespace"))
        assert not r.available and "could not start" in r.reason

    def test_stops_at_first_failure_and_temp_dir_is_removed(self, tmp_path, monkeypatch):
        (tmp_path / "f").write_text("x")
        made = []
        real_mkdtemp = rehearse.tempfile.mkdtemp
        monkeypatch.setattr(rehearse.tempfile, "mkdtemp", lambda **kw: made.append(real_mkdtemp(**kw)) or made[-1])
        codes = iter([0, 2])
        r = _run(tmp_path, ["touch f", "rm f", "touch f"], run=lambda argv, **kw: types.SimpleNamespace(
            returncode=next(codes), stdout="", stderr="nope"))
        assert [s.status for s in r.steps] == ["ok", "failed"] and not r.passed
        assert not os.path.exists(made[0])


class TestFlow:
    def test_apply_runs_for_real_abort_does_not(self, tmp_path, monkeypatch):
        planner = pytest.importorskip("sable.agents.planner")  # ptyprocess: Linux only
        ran = []
        monkeypatch.setattr(planner, "execute_bash", lambda cmd, cwd, env=None: ran.append(cmd) or (0, ""))
        monkeypatch.setattr(planner, "gate", lambda cmd, role: True)
        monkeypatch.setattr(planner.audit, "finish", lambda code: None)
        monkeypatch.setattr(rehearse, "mode", lambda: "auto")
        monkeypatch.setattr(rehearse, "rehearse", lambda steps, cwd: rehearse.Rehearsal(True, "", [
            rehearse.StepResult(s, "ok", 0) for s in steps]))
        steps = ["touch a", "touch b"]
        monkeypatch.setattr("builtins.input", lambda prompt="": "q")
        assert planner.execute_plan(steps, str(tmp_path)) == 1 and ran == []
        monkeypatch.setattr("builtins.input", lambda prompt="": "a")
        assert planner.execute_plan(steps, str(tmp_path)) == 0 and ran == steps

    def test_unavailable_falls_back_to_plain_confirm(self, tmp_path, monkeypatch):
        planner = pytest.importorskip("sable.agents.planner")
        ran = []
        monkeypatch.setattr(planner, "execute_bash", lambda cmd, cwd, env=None: ran.append(cmd) or (0, ""))
        monkeypatch.setattr(planner, "gate", lambda cmd, role: True)
        monkeypatch.setattr(planner.audit, "finish", lambda code: None)
        monkeypatch.setattr(rehearse, "mode", lambda: "auto")
        monkeypatch.setattr(rehearse, "rehearse", lambda steps, cwd: rehearse.Rehearsal(False, "no bwrap"))
        monkeypatch.setattr("builtins.input", lambda prompt="": "q")
        assert planner.execute_plan(["touch a", "touch b"], str(tmp_path)) == 1 and ran == []


class TestDaemon:
    @pytest.fixture
    def conn(self, tmp_path, monkeypatch):
        monkeypatch.setattr(jobs, "_audit", lambda cwd, cmd: None)
        monkeypatch.setattr(rehearse, "mode", lambda: "auto")
        c = sqlite3.connect(tmp_path / "s.db")
        yield c
        c.close()

    def test_failed_rehearsal_queues_the_run(self, conn, tmp_path, monkeypatch):
        monkeypatch.setattr(rehearse, "rehearse", lambda steps, cwd: rehearse.Rehearsal(True, "", [
            rehearse.StepResult("touch a", "failed", 1)]))
        ran = []
        rid = jobs.run_plan(conn, "j", ["touch a"], cwd=str(tmp_path), run=lambda c, w, **k: ran.append(c) or "")
        status, output = conn.execute("SELECT status, output FROM job_runs WHERE id = ?", (rid,)).fetchone()
        assert status == "waiting" and "rehearsal failed" in output and ran == []
        assert queue.pending(conn)

    def test_unavailable_rehearsal_queues_the_run(self, conn, tmp_path, monkeypatch):
        monkeypatch.setattr(rehearse, "rehearse", lambda steps, cwd: rehearse.Rehearsal(False, "no bwrap"))
        rid = jobs.run_plan(conn, "j", ["touch a"], cwd=str(tmp_path), run=lambda c, w, **k: "")
        status, output = conn.execute("SELECT status, output FROM job_runs WHERE id = ?", (rid,)).fetchone()
        assert status == "waiting" and "no bwrap" in output

    def test_passing_rehearsal_runs(self, conn, tmp_path, monkeypatch):
        monkeypatch.setattr(rehearse, "rehearse", lambda steps, cwd: rehearse.Rehearsal(True, "", [
            rehearse.StepResult("touch a", "ok", 0)]))
        ran = []
        rid = jobs.run_plan(conn, "j", ["touch a"], cwd=str(tmp_path),
                            run=lambda c, w, **k: ran.append(c) or "(no output; exit 0)")
        assert conn.execute("SELECT status FROM job_runs WHERE id = ?", (rid,)).fetchone()[0] == "ok"
        assert ran == ["touch a"]


def _bwrap_works() -> bool:
    return sys.platform != "win32" and not rehearse._available()


@pytest.mark.skipif(not _bwrap_works(), reason="needs working bwrap")
def test_real_rehearsal_leaves_the_file_alone(tmp_path):
    d = tmp_path / "work"
    d.mkdir()
    f = d / "notes.txt"
    f.write_text("a line\nanother a\n")
    before = f.read_bytes()
    r = rehearse.rehearse([f"sed -i s/a/b/ {f}", f"touch {d / 'new.txt'}"], str(d))
    assert r.available, r.reason
    assert [s.status for s in r.steps] == ["ok", "ok"], rehearse.render(r)
    assert f.read_bytes() == before
    assert not (d / "new.txt").exists()
    assert "-a line" in r.diff and "+b line" in r.diff
    assert f"added {d / 'new.txt'}" in r.changes
