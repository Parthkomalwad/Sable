"""/skill publish and /skill install (Phase 9 Task 2, B7)."""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import httpx
import pytest

from sable.skills import share
from sable.skills.index import SkillIndex

SKILL = "---\nname: deploy\ndescription: ship it\n---\nrun make deploy\n"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # Never touch a real keyring or key file while signing is exercised.
    monkeypatch.setattr("sable.skills.signing._key", lambda create: b"k" * 32)
    folder = tmp_path / "skills" / "deploy"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(SKILL)
    (folder / "notes.txt").write_text("extra\n")
    return tmp_path


def _run(fn, *args, **kw):
    lines: list[str] = []
    rc = fn(*args, say=lines.append, **kw)
    return rc, "\n".join(lines)


def _archive(path: Path, members, manifest=None, extra=None) -> Path:
    if manifest is None:
        manifest = {"name": "x", "files": [
            {"path": n, "sha256": hashlib.sha256(d).hexdigest()} for n, d in members]}
    with tarfile.open(path, "w:gz") as tar:
        for name, data in [("MANIFEST.json", json.dumps(manifest).encode()), *members]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info in extra or []:
            tar.addfile(info)
    return path


def _publish(home) -> Path:
    from sable.skills import signing
    signing.sign(home / "skills" / "deploy")
    dest = home / "deploy.tar.gz"
    rc, text = _run(share.publish, "deploy", dest)
    assert rc == 0, text
    return dest


def test_publish_writes_manifest(home):
    dest = _publish(home)
    with tarfile.open(dest) as tar:
        manifest = json.loads(tar.extractfile("MANIFEST.json").read())
        names = set(tar.getnames())
    assert manifest["name"] == "deploy"
    assert manifest["signature"] == "signed"
    assert {f["path"] for f in manifest["files"]} == names - {"MANIFEST.json"}
    assert "deploy/SKILL.md" in names


def test_round_trip_installs_unsigned_and_imported(home):
    dest = _publish(home)
    import shutil
    shutil.rmtree(home / "skills" / "deploy")
    rc, text = _run(share.install, str(dest), confirm=lambda q: True, yes=False)
    assert rc == 0, text
    assert "SKILL.md" in text and "run make deploy" in text  # shown before asking
    folder = home / "skills" / "deploy"
    assert (folder / "SKILL.md").read_text() == SKILL
    assert not (folder / ".sable-signature").exists()
    entry = next(e for e in SkillIndex().list_all() if e["name"] == "deploy")
    assert entry["source"] == "imported:deploy.tar.gz"
    assert entry["status"] == "pending"
    from sable.skills import signing
    assert signing.verify(folder) == "unsigned"


def test_install_declined_writes_nothing(home, tmp_path):
    dest = _publish(home)
    import shutil
    shutil.rmtree(home / "skills" / "deploy")
    rc, _ = _run(share.install, str(dest), confirm=lambda q: False, yes=False)
    assert rc == 1
    assert not (home / "skills" / "deploy").exists()


def test_publish_refuses_secrets(home):
    (home / "skills" / "deploy" / "notes.txt").write_text(
        "key = AKIAIOSFODNN7EXAMPLE\n")
    rc, text = _run(share.publish, "deploy", home / "out.tar.gz")
    assert rc == 1
    assert "notes.txt" in text
    assert not (home / "out.tar.gz").exists()


def test_publish_unknown_skill(home):
    rc, _ = _run(share.publish, "nope", home / "out.tar.gz")
    assert rc == 1


@pytest.mark.parametrize("members", [
    [("../evil/SKILL.md", b"x")],
    [("/abs/SKILL.md", b"x")],
    [("a/SKILL.md", b"x"), ("b/SKILL.md", b"y")],
    [("a/notes.txt", b"x")],  # no SKILL.md
    [("SKILL.md", b"x")],  # not in a folder
])
def test_bad_archives_refused(home, members):
    bad = _archive(home / "bad.tar.gz", members)
    rc, text = _run(share.install, str(bad), confirm=lambda q: True, yes=True)
    assert rc == 1
    assert "refusing" in text


def test_link_refused(home):
    link = tarfile.TarInfo("a/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    bad = _archive(home / "bad.tar.gz", [("a/SKILL.md", b"x")], extra=[link])
    rc, text = _run(share.install, str(bad), confirm=lambda q: True, yes=True)
    assert rc == 1 and "not a regular file" in text


def test_hash_mismatch_refused(home):
    manifest = {"files": [{"path": "a/SKILL.md", "sha256": "0" * 64}]}
    bad = _archive(home / "bad.tar.gz", [("a/SKILL.md", b"x")], manifest=manifest)
    rc, text = _run(share.install, str(bad), confirm=lambda q: True, yes=True)
    assert rc == 1 and "sha256" in text
    assert not (home / "skills" / "a").exists()


def test_oversize_unpacked_refused(home):
    big = b"x" * (share.MAX_BYTES + 1)
    bad = _archive(home / "bad.tar.gz", [("a/SKILL.md", big)])
    rc, text = _run(share.install, str(bad), confirm=lambda q: True, yes=True)
    assert rc == 1 and "limit" in text


def test_overwrite_needs_replace_and_backs_up(home):
    dest = _publish(home)
    folder = home / "skills" / "deploy"
    (folder / "SKILL.md").write_text("local edit\n")
    rc, text = _run(share.install, str(dest), confirm=lambda q: True, yes=True)
    assert rc == 1 and "--replace" in text
    assert (folder / "SKILL.md").read_text() == "local edit\n"

    rc, text = _run(share.install, str(dest), confirm=lambda q: True, yes=True,
                    replace=True)
    assert rc == 0, text
    assert (folder / "SKILL.md").read_text() == SKILL
    backups = list((home / ".sable").glob("skill-backup-*/deploy/SKILL.md"))
    assert backups and backups[0].read_text() == "local edit\n"


def test_http_refused(home):
    rc, text = _run(share.install, "http://example.com/s.tar.gz",
                    confirm=lambda q: True, yes=True)
    assert rc == 1 and "https" in text


def _transport(handler):
    return httpx.MockTransport(handler)


def test_https_download(home):
    dest = _publish(home)
    data = dest.read_bytes()
    import shutil
    shutil.rmtree(home / "skills" / "deploy")
    t = _transport(lambda req: httpx.Response(200, content=data))
    rc, text = _run(share.install, "https://skills.example.com/d.tar.gz",
                    confirm=lambda q: True, yes=True, transport=t)
    assert rc == 0, text
    entry = next(e for e in SkillIndex().list_all() if e["name"] == "deploy")
    assert entry["source"] == "imported:skills.example.com"


def test_download_oversize_refused(home):
    t = _transport(lambda req: httpx.Response(200, content=b"x" * (share.MAX_BYTES + 1)))
    rc, text = _run(share.install, "https://e.com/d.tar.gz",
                    confirm=lambda q: True, yes=True, transport=t)
    assert rc == 1 and "MB" in text


def test_redirect_to_http_refused(home):
    t = _transport(lambda req: httpx.Response(
        302, headers={"location": "http://e.com/d.tar.gz"}))
    rc, text = _run(share.install, "https://e.com/d.tar.gz",
                    confirm=lambda q: True, yes=True, transport=t)
    assert rc == 1 and "https" in text
