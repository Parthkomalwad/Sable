"""Router accuracy gate against the labelled corpus (I3).

Routing a real shell command to the LLM is the failure users notice most, so
bash recall carries the tighter bound. Thresholds come from
docs/roadmap-phases.md Phase 0: bash >= 0.99, agentic >= 0.95.

Ambiguous is not gated. It is a small, inherently subjective slice (a bare
"restart" really could be either), and forcing it up would mean asking the user
more often, which is the behaviour the gate exists to avoid.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from sable.agents.router import Route, classify

CORPUS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "router_corpus.tsv"

BASH_RECALL_FLOOR = 0.99
AGENTIC_RECALL_FLOOR = 0.95
MIN_CORPUS_ROWS = 500


def _load_corpus() -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with io.open(CORPUS_PATH, encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, start=1):
            line = raw.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            text, sep, label = line.partition("\t")
            assert sep, f"{CORPUS_PATH.name}:{lineno} is not tab separated"
            assert label in {"bash", "agentic", "ambiguous"}, (
                f"{CORPUS_PATH.name}:{lineno} has unknown label {label!r}"
            )
            rows.append((text, label))
    return rows


CORPUS = _load_corpus()


def _recall(label: str) -> tuple[int, int, list[str]]:
    """Return (hits, total, misses) for one label."""
    expected = [(text, actual) for text, actual in CORPUS if actual == label]
    misses = [
        f"{text!r} -> {classify(text).value}"
        for text, _ in expected
        if classify(text).value != label
    ]
    return len(expected) - len(misses), len(expected), misses


class TestCorpus:
    def test_corpus_is_large_enough(self):
        assert len(CORPUS) >= MIN_CORPUS_ROWS

    def test_corpus_covers_every_label(self):
        labels = {label for _, label in CORPUS}
        assert labels == {"bash", "agentic", "ambiguous"}

    def test_corpus_has_no_duplicate_inputs(self):
        seen: dict[str, str] = {}
        duplicates = []
        for text, label in CORPUS:
            if text in seen:
                duplicates.append(f"{text!r} labelled {seen[text]} and {label}")
            seen[text] = label
        assert not duplicates, "duplicate corpus rows: " + "; ".join(duplicates)


class TestAccuracy:
    def test_bash_recall_meets_the_gate(self):
        hits, total, misses = _recall("bash")
        recall = hits / total
        assert recall >= BASH_RECALL_FLOOR, (
            f"bash recall {recall:.4f} < {BASH_RECALL_FLOOR} "
            f"({hits}/{total}). Sent to the LLM instead of bash:\n  "
            + "\n  ".join(misses)
        )

    def test_agentic_recall_meets_the_gate(self):
        hits, total, misses = _recall("agentic")
        recall = hits / total
        assert recall >= AGENTIC_RECALL_FLOOR, (
            f"agentic recall {recall:.4f} < {AGENTIC_RECALL_FLOOR} "
            f"({hits}/{total}). Routed to bash instead of the LLM:\n  "
            + "\n  ".join(misses)
        )

    def test_no_agentic_line_is_silently_run_as_bash_with_shell_syntax(self):
        """A goal misrouted to bash is bad; one containing shell metacharacters
        would actually execute something. There must be none."""
        dangerous = [
            text
            for text, label in CORPUS
            if label == "agentic"
            and classify(text) is Route.BASH
            and any(ch in text for ch in "|>;&`")
        ]
        assert not dangerous, dangerous


class TestExplanationsAgreeWithClassify:
    @pytest.mark.parametrize("text,label", CORPUS[::37])
    def test_explain_route_matches_classify(self, text, label):
        from sable.agents.router import explain

        assert explain(text).route is classify(text)
