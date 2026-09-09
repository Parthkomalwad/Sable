"""Unit tests for SkillIndex (shell/skills/index.py).

Covers index creation, add/upsert, confidence nudges and their clamps,
keyword ranking order, and needs_update. No LLM calls, no network.
"""
from __future__ import annotations

import json

import pytest

from shell.skills.index import SkillIndex


def _index(tmp_path) -> SkillIndex:
    return SkillIndex(index_path=str(tmp_path / "skills_index.json"))


def test_creates_empty_index_file_on_first_use(tmp_path):
    path = tmp_path / "nested" / "skills_index.json"
    index = SkillIndex(index_path=str(path))
    assert path.exists()
    assert json.loads(path.read_text()) == []
    assert index.list_all() == []


def test_add_auto_generated_starts_at_half_confidence(tmp_path):
    index = _index(tmp_path)
    index.add(name="deploy-api", file="/s/deploy-api.md",
              keywords=["deploy", "api"], auto_generated=True)

    entry = index.list_all()[0]
    assert entry["name"] == "deploy-api"
    assert entry["confidence"] == 0.5
    assert entry["auto_generated"] is True
    assert entry["use_count"] == 0
    assert entry["last_used"] is None
    assert entry["needs_update"] is False
    assert entry["created_at"]


def test_add_manual_starts_at_full_confidence(tmp_path):
    index = _index(tmp_path)
    index.add(name="manual", file="/s/manual.md", keywords=["x"],
              auto_generated=False)
    assert index.list_all()[0]["confidence"] == 1.0


def test_add_same_name_replaces_rather_than_duplicates(tmp_path):
    index = _index(tmp_path)
    index.add(name="dup", file="/s/a.md", keywords=["a"], auto_generated=True)
    index.add(name="dup", file="/s/b.md", keywords=["b"], auto_generated=False)

    entries = index.list_all()
    assert len(entries) == 1
    assert entries[0]["file"] == "/s/b.md"
    assert entries[0]["confidence"] == 1.0


def test_record_use_success_raises_confidence_and_counts(tmp_path):
    index = _index(tmp_path)
    index.add(name="s", file="/s/s.md", keywords=["k"], auto_generated=True)

    index.record_use("s", success=True)

    entry = index.list_all()[0]
    assert entry["confidence"] == pytest.approx(0.55)
    assert entry["use_count"] == 1
    assert entry["last_used"] is not None


def test_record_use_failure_lowers_confidence(tmp_path):
    index = _index(tmp_path)
    index.add(name="s", file="/s/s.md", keywords=["k"], auto_generated=True)

    index.record_use("s", success=False)

    assert index.list_all()[0]["confidence"] == pytest.approx(0.4)


def test_confidence_is_clamped_to_unit_interval(tmp_path):
    index = _index(tmp_path)
    index.add(name="hi", file="/s/hi.md", keywords=["k"], auto_generated=False)
    index.add(name="lo", file="/s/lo.md", keywords=["k"], auto_generated=True)

    for _ in range(5):
        index.record_use("hi", success=True)
    for _ in range(10):
        index.record_use("lo", success=False)

    by_name = {e["name"]: e for e in index.list_all()}
    assert by_name["hi"]["confidence"] == 1.0
    assert by_name["lo"]["confidence"] == 0.0


def test_record_use_on_unknown_name_is_a_no_op(tmp_path):
    index = _index(tmp_path)
    index.add(name="known", file="/s/k.md", keywords=["k"], auto_generated=True)

    index.record_use("missing", success=True)

    assert index.list_all()[0]["confidence"] == 0.5


def test_changes_persist_to_disk(tmp_path):
    path = tmp_path / "skills_index.json"
    first = SkillIndex(index_path=str(path))
    first.add(name="s", file="/s/s.md", keywords=["k"], auto_generated=True)
    first.record_use("s", success=True)

    reloaded = SkillIndex(index_path=str(path))
    assert reloaded.list_all()[0]["confidence"] == pytest.approx(0.55)


def test_get_ranked_matches_keywords_case_insensitively(tmp_path):
    index = _index(tmp_path)
    index.add(name="docker", file="/s/d.md", keywords=["Docker", "Build"],
              auto_generated=True)
    index.add(name="git", file="/s/g.md", keywords=["git"], auto_generated=True)

    ranked = index.get_ranked(["DOCKER"])

    assert [e["name"] for e in ranked] == ["docker"]


def test_get_ranked_orders_by_confidence_then_use_count(tmp_path):
    index = _index(tmp_path)
    index.add(name="low", file="/s/l.md", keywords=["k"], auto_generated=True)
    index.add(name="high", file="/s/h.md", keywords=["k"], auto_generated=False)
    index.add(name="mid", file="/s/m.md", keywords=["k"], auto_generated=True)
    index.record_use("mid", success=True)

    ranked = index.get_ranked(["k"])

    assert [e["name"] for e in ranked] == ["high", "mid", "low"]


def test_get_ranked_returns_empty_when_nothing_matches(tmp_path):
    index = _index(tmp_path)
    index.add(name="s", file="/s/s.md", keywords=["docker"], auto_generated=True)
    assert index.get_ranked(["kubernetes"]) == []


def test_mark_needs_update_sets_flag(tmp_path):
    index = _index(tmp_path)
    index.add(name="s", file="/s/s.md", keywords=["k"], auto_generated=True)

    index.mark_needs_update("s")

    assert index.list_all()[0]["needs_update"] is True
