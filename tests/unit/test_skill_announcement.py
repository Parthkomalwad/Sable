"""Saying which skill is in use, and at what confidence (Task 11).

The Phase 2 gate fixes the wording: run 2 of a repeated goal must say

    using skill deploy-api (0.55)

That line is the only place the confidence loop becomes visible while it is
happening. Without it a user watching a run has no way to tell that a skill
was retrieved at all, let alone which one or how much the shell trusts it,
and "the shell got better at this" stays an assertion in a changelog.

It is also published to the bus, so the sidebar and `/task events` can show
it. `contracts.md` §2.1 promises an unknown kind is carried rather than
rejected, so an older sidebar reading a newer runtime survives this.
"""
from __future__ import annotations

import pytest

from sable.agents.worker import format_skill_announcement


class TestTheWording:
    """Fixed by the gate transcript, so it is a contract, not a preference."""

    def test_names_the_skill_and_its_confidence(self):
        line = format_skill_announcement([
            {"name": "deploy-api", "confidence": 0.55},
        ])
        assert line == "using skill deploy-api (0.55)"

    def test_confidence_is_two_decimals(self):
        """0.5 must render as 0.50: the gate reads 0.55, 0.60, 0.50."""
        line = format_skill_announcement([
            {"name": "deploy-api", "confidence": 0.5},
        ])
        assert "(0.50)" in line

    def test_float_drift_does_not_leak_into_the_line(self):
        """Repeated nudges produce 0.6500000000000001 in the index.

        Formatting is where that stops being visible. A user reading
        "using skill x (0.6500000000000001)" would reasonably assume
        something was broken.
        """
        line = format_skill_announcement([
            {"name": "deploy-api", "confidence": 0.6500000000000001},
        ])
        assert line == "using skill deploy-api (0.65)"

    def test_several_skills_are_all_named(self):
        line = format_skill_announcement([
            {"name": "a", "confidence": 0.9},
            {"name": "b", "confidence": 0.5},
        ])
        assert "a (0.90)" in line
        assert "b (0.50)" in line
        assert line.startswith("using skills ")

    def test_no_skills_produces_no_line(self):
        """Nothing is printed when nothing matched. Silence is the default."""
        assert format_skill_announcement([]) == ""

    def test_a_skill_with_no_recorded_confidence_still_announces(self):
        """A local task skill has no index entry, so no confidence.

        It is still injected, so it must still be named: announcing only
        the indexed ones would misreport what the model actually saw.
        """
        line = format_skill_announcement([{"name": "local-only"}])
        assert "local-only" in line
        assert "(" not in line.split("local-only")[1][:2]
