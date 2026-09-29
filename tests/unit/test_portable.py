"""sable export / import / sync (Phase 7 Task 5, I8)."""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from sable.core import portable
from sable.policy.rules import match_secret


@pytest.fixture
def home(tmp_path, monkeypatch):
    def switch(name: str) -> Path:
        h = tmp_path / name
        h.mkdir(exist_ok=True)
        monkeypatch.setenv("HOME", str(h))
        monkeypatch.setenv("USERPROFILE", str(h))
        return h
    return switch


def _seed(h: Path) -> None:
    (h / "skills").mkdir()
    (h / "skills" / "deploy.md").write_text("# deploy\nrun make deploy\n")
    (h / "skills" / "skills_index.json").write_text("[]")
    (h / ".sable" / "palace" / "server").mkdir(parents=True)
    (h / ".sable" / "palace" / "server" / "f1.md").write_text("logs: /var/log/app\n")
    (h / ".sable" / "policy.toml").write_text("# user policy\n")
    (h / ".sable" / "hooks").mkdir()
    (h / ".sable" / "hooks" / "pre_command").write_text("#!/bin/sh\nexit 0\n")
    # never exported
    (h / ".sable" / "state").mkdir()
    (h / ".sable" / "state" / "x.tsv").write_text("state")
    (h / ".sable" / "sable.db").write_text("db")


def _archive(path: Path, members: list[tuple[str, bytes]], manifest=None, extra=None):
    if manifest is None:
        manifest = {"version": 1, "host": "t", "created": "now", "files": [
            {"path": n, "sha256": hashlib.sha256(d).hexdigest()} for n, d in members]}
    with tarfile.open(path, "w:gz") as tar:
        for name, data in [("MANIFEST.json", json.dumps(manifest).encode()), *members]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info in extra or []:
            tar.addfile(info)
    return path


def _run(fn, *args, **kw):
    lines: list[str] = []
    rc = fn(*args, say=lines.append, **kw)
    return rc, "\n".join(lines)


def test_round_trip(home, tmp_path):
    _seed(home("a"))
    dest = tmp_path / "out.tar.gz"
    rc, _ = _run(portable.export, dest, secret_check=match_secret)
    assert rc == 0
    with tarfile.open(dest) as tar:
        names = set(tar.getnames())
    assert "skills/deploy.md" in names and "policy.toml" in names
    assert "hooks/pre_command" in names and "palace/server/f1.md" in names
    assert not any("state" in n or "sable.db" in n for n in names)

    b = home("b")
    called = []
    rc, text = _run(portable.import_archive, dest, confirm=lambda p: True,
                    yes=False, on_write=lambda: called.append(1))
    assert rc == 0, text
    assert (b / "skills" / "deploy.md").read_text() == "# deploy\nrun make deploy\n"
    assert (b / ".sable" / "palace" / "server" / "f1.md").exists()
    assert called == [1]
    assert not (b / ".sable" / "state").exists()


def test_secret_refused(home, tmp_path):
    h = home("a")
    _seed(h)
    (h / "skills" / "leak.md").write_text("key AKIAIOSFODNN7EXAMPLE here\n")
    dest = tmp_path / "out.tar.gz"
    rc, text = _run(portable.export, dest, secret_check=match_secret)
    assert rc == 1 and "skills/leak.md" in text
    assert not dest.exists()


@pytest.mark.parametrize("name", ["../evil.md", "/etc/passwd", "skills/../../x",
                                  "config.json", "state/x", "C:/x"])
def test_bad_names_rejected(home, tmp_path, name):
    h = home("b")
    arc = _archive(tmp_path / "bad.tar.gz", [(name, b"x")])
    rc, text = _run(portable.import_archive, arc, confirm=lambda p: True, yes=True)
    assert rc == 1 and "refusing" in text
    assert not (h / "skills").exists() and not (h / ".sable").exists()


def test_symlink_rejected(home, tmp_path):
    h = home("b")
    link = tarfile.TarInfo("skills/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    arc = _archive(tmp_path / "l.tar.gz", [("skills/a.md", b"a")], extra=[link])
    rc, text = _run(portable.import_archive, arc, confirm=lambda p: True, yes=True)
    assert rc == 1 and "skills/link" in text
    assert not (h / "skills").exists()


def test_hash_mismatch_rejected(home, tmp_path):
    h = home("b")
    manifest = {"version": 1, "files": [{"path": "skills/a.md", "sha256": "0" * 64}]}
    arc = _archive(tmp_path / "h.tar.gz", [("skills/a.md", b"a")], manifest=manifest)
    rc, text = _run(portable.import_archive, arc, confirm=lambda p: True, yes=True)
    assert rc == 1 and "sha256" in text
    assert not (h / "skills").exists()


def test_overwrite_backs_up_and_declined_writes_nothing(home, tmp_path):
    h = home("b")
    (h / "skills").mkdir()
    (h / "skills" / "a.md").write_text("old")
    arc = _archive(tmp_path / "o.tar.gz", [("skills/a.md", b"new"), ("skills/b.md", b"b")])

    rc, text = _run(portable.import_archive, arc, confirm=lambda p: False, yes=False)
    assert rc == 1 and (h / "skills" / "a.md").read_text() == "old"
    assert "overwrite skills/a.md" in text

    rc, _ = _run(portable.import_archive, arc, confirm=lambda p: False, yes=True)
    assert rc == 0
    assert (h / "skills" / "a.md").read_text() == "new"
    backups = list((h / ".sable").glob("import-backup-*/skills/a.md"))
    assert len(backups) == 1 and backups[0].read_text() == "old"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_sync_round_trip(home, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)

    _seed(home("a"))
    rc, text = _run(portable.sync, str(remote), confirm=lambda p: True, yes=True,
                    secret_check=match_secret)
    assert rc == 0, text

    b = home("b")
    rc, text = _run(portable.sync, str(remote), confirm=lambda p: True, yes=True,
                    secret_check=match_secret)
    assert rc == 0, text
    assert (b / "skills" / "deploy.md").exists()
    assert (b / ".sable" / "policy.toml").read_text() == "# user policy\n"
    assert not (b / ".sable" / "state").exists()


def test_an_oversized_archive_is_refused_before_staging(tmp_path, monkeypatch):
    import io, tarfile
    from sable.core import portable
    monkeypatch.setattr(portable, "MAX_TOTAL", 10)
    arc = tmp_path / "big.tar.gz"
    with tarfile.open(arc, "w:gz") as tar:
        data = b"x" * 100
        info = tarfile.TarInfo("skills/a/SKILL.md"); info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    said = []
    assert portable.read_archive(arc, said.append) is None
    assert any("MB unpacked" in s for s in said)
