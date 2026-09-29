"""`/skill doctor` rules (Phase 9 Task 1, B4). Temp index, fake signing key."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from sable.skills import doctor, signing
from sable.skills.index import SkillIndex

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setattr(signing, "_key", lambda create: b"k" * 32)


def _add(tmp_path, index, name, body, keywords=(), **fields):
    folder = tmp_path / "skills" / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: x\nstatus: enabled\n---\n{body}\n",
        encoding="utf-8")
    index.add(name, str(folder / "SKILL.md"), list(keywords), auto_generated=False)
    entry = index._find(name)
    entry.update({"last_used": NOW.isoformat(), "use_count": 5, **fields})
    index._save()
    return folder


@pytest.fixture
def index(tmp_path):
    return SkillIndex(str(tmp_path / "skills_index.json"))


def _kinds(index):
    return {(f.skill, f.kind, f.proposal) for f in doctor.diagnose(index, now=NOW)}


def test_healthy_skill_yields_nothing(tmp_path, index):
    _add(tmp_path, index, "deploy", "run make deploy", ["deploy"])
    assert doctor.diagnose(index, now=NOW) == []


def test_failing(tmp_path, index):
    _add(tmp_path, index, "flaky", "run it", confidence=0.2, use_count=4)
    assert _kinds(index) == {("flaky", "failing", "repair")}


def test_low_confidence_with_few_runs_is_not_failing(tmp_path, index):
    _add(tmp_path, index, "new", "run it", confidence=0.2, use_count=2)
    assert _kinds(index) == set()


def test_stale(tmp_path, index):
    old = (NOW - timedelta(days=61)).isoformat()
    _add(tmp_path, index, "old", "run it", last_used=old)
    assert _kinds(index) == {("old", "stale", "retire")}


def test_duplicate(tmp_path, index):
    body = "restart nginx service then check status with systemctl"
    _add(tmp_path, index, "nginx-restart", body, ["nginx", "restart"])
    _add(tmp_path, index, "nginx-restart2", body, ["nginx", "restart"])
    found = [f for f in doctor.diagnose(index, now=NOW) if f.kind == "duplicate"]
    assert len(found) == 1
    assert found[0].proposal == "merge"
    assert "nginx-restart" in found[0].detail


def test_unsigned_and_tampered(tmp_path, index):
    a = _add(tmp_path, index, "a", "alpha body")
    b = _add(tmp_path, index, "b", "beta words")
    (a / signing.SIGNATURE_FILE).unlink()
    (b / "SKILL.md").write_text("changed", encoding="utf-8")
    assert _kinds(index) == {("a", "unsigned", "sign"), ("b", "tampered", "sign")}


def test_nothing_is_mutated(tmp_path, index):
    folder = _add(tmp_path, index, "flaky", "run it", confidence=0.1,
                  last_used=(NOW - timedelta(days=90)).isoformat())
    (folder / signing.SIGNATURE_FILE).unlink()
    before_index = (tmp_path / "skills_index.json").read_text()
    before_file = (folder / "SKILL.md").read_text()
    assert doctor.diagnose(index, now=NOW)
    assert (tmp_path / "skills_index.json").read_text() == before_index
    assert (folder / "SKILL.md").read_text() == before_file
    assert not (folder / signing.SIGNATURE_FILE).exists()
    assert json.loads(before_index)[0]["confidence"] == 0.1


def test_builtin_prints_table_and_changes_nothing(tmp_path, monkeypatch, capsys):
    from sable.app.builtins import skill as builtin
    idx = SkillIndex(str(tmp_path / "skills_index.json"))
    _add(tmp_path, idx, "[bold]x", "run it", confidence=0.1)
    before = (tmp_path / "skills_index.json").read_text()
    monkeypatch.setattr(builtin, "_index", lambda: idx)
    monkeypatch.setattr(builtin, "_skills_root", lambda: tmp_path / "skills")
    assert builtin._handle_skill_builtin(["doctor"])
    shown = capsys.readouterr().out
    assert "[bold]x" in shown and "/skill show" in shown
    assert (tmp_path / "skills_index.json").read_text() == before
