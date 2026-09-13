"""Unit tests for SkillIndex (sable/skills/index.py).

Covers index creation, add/upsert, confidence nudges and their clamps,
ranking order, the approval state Phase 2 gates injection on, and
needs_update. No LLM calls, no network.

Phase 2 (B1, B3) changed one assertion in this file deliberately.
`test_get_ranked_orders_by_confidence_then_use_count` pinned a two-key sort
on confidence then use_count; ranking is now
`confidence x recency x use_count x match`, so that test was rewritten
rather than the formula bent to preserve it. The two other `get_ranked`
tests were unaffected and are untouched.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from sable.skills.index import SkillIndex


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


def test_get_ranked_puts_the_more_confident_skill_first(tmp_path):
    """Rewritten in Phase 2: ranking is no longer a two-key sort.

    The old test pinned `confidence` then `use_count` as sort keys. Ranking
    is now a product of confidence, recency, use_count and match quality, so
    what survives is the property that actually matters to a caller: with
    everything else equal, the skill the shell trusts more comes first.
    """
    index = _index(tmp_path)
    index.add(name="low", file="/s/l.md", keywords=["k"], auto_generated=True)
    index.add(name="high", file="/s/h.md", keywords=["k"], auto_generated=False)

    ranked = index.get_ranked(["k"])

    assert [e["name"] for e in ranked] == ["high", "low"]


def test_get_ranked_returns_empty_when_nothing_matches(tmp_path):
    index = _index(tmp_path)
    index.add(name="s", file="/s/s.md", keywords=["docker"], auto_generated=True)
    assert index.get_ranked(["kubernetes"]) == []


def test_mark_needs_update_sets_flag(tmp_path):
    index = _index(tmp_path)
    index.add(name="s", file="/s/s.md", keywords=["k"], auto_generated=True)

    index.mark_needs_update("s")

    assert index.list_all()[0]["needs_update"] is True


class TestApprovalState:
    """Phase 2's gate: a skill is never injected without a human saying yes.

    `status` is what gates injection, so the rules here are the whole point
    of the phase. `add()` still defaults to "enabled" because every caller
    that exists today writes a skill the user asked for; the crystalliser
    opts into "pending" deliberately (Tasks 6 and 7), rather than the
    default flipping underneath callers a task early.
    """

    def test_add_defaults_to_enabled(self, tmp_path):
        index = _index(tmp_path)
        index.add(name="s", file="/s/s.md", keywords=["k"], auto_generated=False)
        assert index.list_all()[0]["status"] == "enabled"

    def test_a_skill_can_be_added_pending(self, tmp_path):
        index = _index(tmp_path)
        index.add(name="draft", file="/s/d.md", keywords=["k"],
                  auto_generated=True, status="pending")
        assert index.list_all()[0]["status"] == "pending"

    def test_an_unknown_status_is_refused(self, tmp_path):
        """A typo must not produce a skill that is silently never injected."""
        index = _index(tmp_path)
        with pytest.raises(ValueError, match="status"):
            index.add(name="s", file="/s/s.md", keywords=["k"],
                      auto_generated=True, status="aproved")

    def test_pending_skills_are_never_ranked(self, tmp_path):
        """The load-bearing assertion of the whole approval gate."""
        index = _index(tmp_path)
        index.add(name="draft", file="/s/d.md", keywords=["k"],
                  auto_generated=True, status="pending")
        assert index.get_ranked(["k"]) == []

    def test_disabled_skills_are_never_ranked(self, tmp_path):
        index = _index(tmp_path)
        index.add(name="off", file="/s/o.md", keywords=["k"],
                  auto_generated=False, status="disabled")
        assert index.get_ranked(["k"]) == []

    def test_approve_enables_a_pending_skill(self, tmp_path):
        index = _index(tmp_path)
        index.add(name="draft", file="/s/d.md", keywords=["k"],
                  auto_generated=True, status="pending")

        assert index.approve("draft") is True

        assert index.list_all()[0]["status"] == "enabled"
        assert [e["name"] for e in index.get_ranked(["k"])] == ["draft"]

    def test_approving_keeps_the_confidence_it_was_drafted_with(self, tmp_path):
        """Approval says "you may run this", not "I vouch for it".

        The gate expects an approved draft to start at 0.5 and earn its way
        up, so approval must not double as a confidence boost.
        """
        index = _index(tmp_path)
        index.add(name="draft", file="/s/d.md", keywords=["k"],
                  auto_generated=True, status="pending")
        index.approve("draft")
        assert index.list_all()[0]["confidence"] == 0.5

    def test_disable_is_reversible(self, tmp_path):
        index = _index(tmp_path)
        index.add(name="s", file="/s/s.md", keywords=["k"], auto_generated=False)

        index.disable("s")
        assert index.get_ranked(["k"]) == []

        index.approve("s")
        assert [e["name"] for e in index.get_ranked(["k"])] == ["s"]

    def test_reject_removes_the_entry(self, tmp_path):
        """The folder on disk is left alone; only the index entry goes.

        Rejecting is not deleting: the draft stays readable so a user can
        see what was proposed, and Task 8's `/skill reject` says so.
        """
        index = _index(tmp_path)
        index.add(name="draft", file="/s/d.md", keywords=["k"],
                  auto_generated=True, status="pending")

        assert index.reject("draft") is True

        assert index.list_all() == []

    def test_approve_and_reject_report_an_unknown_name(self, tmp_path):
        index = _index(tmp_path)
        assert index.approve("missing") is False
        assert index.reject("missing") is False

    def test_pending_lists_only_drafts(self, tmp_path):
        """Backs the startup line and `/skill list`'s draft marker."""
        index = _index(tmp_path)
        index.add(name="live", file="/s/l.md", keywords=["k"], auto_generated=False)
        index.add(name="draft", file="/s/d.md", keywords=["k"],
                  auto_generated=True, status="pending")

        assert [e["name"] for e in index.pending()] == ["draft"]

    def test_an_index_written_before_phase_2_has_no_status(self, tmp_path):
        """Upgrade path: entries on disk today predate the field entirely.

        Treating a missing status as pending would silently stop injecting
        every skill a user already has, which is the same failure the
        migration was careful to avoid.
        """
        path = tmp_path / "skills_index.json"
        path.write_text(json.dumps([{
            "name": "old", "file": "/s/o.md", "keywords": ["k"],
            "auto_generated": False, "confidence": 0.8, "use_count": 3,
            "last_used": None, "needs_update": False, "created_at": "2026-01-01",
        }]), encoding="utf-8")

        index = SkillIndex(index_path=str(path))

        assert [e["name"] for e in index.get_ranked(["k"])] == ["old"]


class TestNudge:
    """`nudge()` is the name the feedback loop calls the existing deltas by.

    Task 5 calls this once per skill at a run's terminal state. It is a thin
    alias over `record_use` rather than a second scoring path, so there is
    one place confidence can move.
    """

    def test_success_matches_record_use(self, tmp_path):
        index = _index(tmp_path)
        index.add(name="s", file="/s/s.md", keywords=["k"], auto_generated=True)

        index.nudge("s", success=True)

        entry = index.list_all()[0]
        assert entry["confidence"] == pytest.approx(0.55)
        assert entry["use_count"] == 1

    def test_failure_lowers_confidence(self, tmp_path):
        index = _index(tmp_path)
        index.add(name="s", file="/s/s.md", keywords=["k"], auto_generated=True)

        index.nudge("s", success=False)

        assert index.list_all()[0]["confidence"] == pytest.approx(0.4)

    def test_an_unknown_name_is_a_no_op(self, tmp_path):
        """A skill removed between use and grading must not raise.

        Task 5 grades after a run finishes, by which time `/skill reject`
        may have removed the entry.
        """
        index = _index(tmp_path)
        index.nudge("missing", success=True)   # must not raise

    def test_the_gate_sequence_holds(self, tmp_path):
        """The Phase 2 gate, as arithmetic: 0.50 -> 0.55 -> 0.60 -> 0.50.

        Two successes then a deliberate break. If this drifts, the gate
        transcript stops being reproducible.
        """
        index = _index(tmp_path)
        index.add(name="deploy-api", file="/s/d.md", keywords=["deploy"],
                  auto_generated=True, status="pending")
        index.approve("deploy-api")

        assert index.list_all()[0]["confidence"] == pytest.approx(0.50)
        index.nudge("deploy-api", success=True)
        assert index.list_all()[0]["confidence"] == pytest.approx(0.55)
        index.nudge("deploy-api", success=True)
        assert index.list_all()[0]["confidence"] == pytest.approx(0.60)
        index.nudge("deploy-api", success=False)
        assert index.list_all()[0]["confidence"] == pytest.approx(0.50)


class TestRankingFormula:
    """`confidence x recency x use_count x match`.

    Each factor is pinned by holding the other three equal. The absolute
    scores are deliberately not asserted: they are a heuristic that will be
    tuned, and a test that pins the number rather than the ordering would
    make tuning impossible without rewriting the suite.
    """

    def _age(self, index, name, days):
        """Backdate a skill's last_used, so recency can be tested at all."""
        when = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        for entry in index._data:
            if entry["name"] == name:
                entry["last_used"] = when
        index._save()

    def test_a_recently_used_skill_outranks_a_stale_one(self, tmp_path):
        index = _index(tmp_path)
        index.add(name="stale", file="/s/s.md", keywords=["k"], auto_generated=True)
        index.add(name="fresh", file="/s/f.md", keywords=["k"], auto_generated=True)
        index.nudge("stale", success=True)
        index.nudge("fresh", success=True)
        self._age(index, "stale", days=365)

        assert [e["name"] for e in index.get_ranked(["k"])][0] == "fresh"

    def test_a_more_used_skill_outranks_a_less_used_one(self, tmp_path):
        index = _index(tmp_path)
        index.add(name="rare", file="/s/r.md", keywords=["k"], auto_generated=True)
        index.add(name="common", file="/s/c.md", keywords=["k"], auto_generated=True)
        # Equal confidence: three successes then two failures nets to the
        # same 0.5, but leaves very different use counts.
        for _ in range(3):
            index.nudge("common", success=True)
        for _ in range(3):
            index.nudge("common", success=False)
        for _ in range(3):
            index.nudge("common", success=True)

        by_name = {e["name"]: e for e in index.list_all()}
        assert by_name["common"]["use_count"] > by_name["rare"]["use_count"]
        assert [e["name"] for e in index.get_ranked(["k"])][0] == "common"

    def test_a_better_keyword_match_outranks_a_weaker_one(self, tmp_path):
        """Matching two of the goal's words beats matching one."""
        index = _index(tmp_path)
        index.add(name="weak", file="/s/w.md", keywords=["deploy"],
                  auto_generated=True)
        index.add(name="strong", file="/s/s.md", keywords=["deploy", "api"],
                  auto_generated=True)

        ranked = index.get_ranked(["deploy", "api"])

        assert [e["name"] for e in ranked][0] == "strong"

    def test_a_never_used_skill_still_ranks(self, tmp_path):
        """A fresh skill has use_count 0 and no last_used.

        If either factor multiplied to zero, an approved draft could never
        be retrieved and so could never earn its first success. The gate's
        run 2 depends on this.
        """
        index = _index(tmp_path)
        index.add(name="brand-new", file="/s/n.md", keywords=["k"],
                  auto_generated=True)

        assert [e["name"] for e in index.get_ranked(["k"])] == ["brand-new"]

    def test_ranking_is_stable_for_identical_skills(self, tmp_path):
        """Equal scores must not reorder between calls.

        An unstable sort would make the announcement in Task 11 name a
        different skill on each run for no reason the user could see.
        """
        index = _index(tmp_path)
        for name in ("a", "b", "c"):
            index.add(name=name, file=f"/s/{name}.md", keywords=["k"],
                      auto_generated=True)

        first = [e["name"] for e in index.get_ranked(["k"])]
        second = [e["name"] for e in index.get_ranked(["k"])]

        assert first == second
