"""Phase 3 Task 2: `decide()` tiers a command, `gate()` acts on the tier.

`is_destructive` answered yes or no and left every caller to decide what
that meant. Six call sites made six choices, and the orchestrator's choice
(refuse outright) contradicted the docs (ask for YES). A tier is decided in
one place, and `gate()` is the one place that turns it into a prompt.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sable.policy import engine
from sable.policy.engine import decide, gate
from sable.policy.tiers import Tier

SABLE = Path(engine.__file__).resolve().parents[1]


class TestDecide:
    def test_unmatched_command_is_allowed(self):
        d = decide("ls -la")
        assert d.tier is Tier.ALLOW and d.rule is None

    def test_matched_rule_returns_its_tier_and_reason(self):
        d = decide("rm -rf /tmp/x")
        assert d.tier is Tier.CONFIRM
        assert d.rule.name == "recursive-delete"
        assert d.why == d.rule.why
        assert d.source.endswith("policy.toml")


class TestGate:
    def _deny(self, monkeypatch):
        rule = engine.rules.Rule(name="no-rm", pattern="^rm ", why="nope", tier=Tier.DENY)
        monkeypatch.setattr(engine.rules, "match", lambda c: rule if c.startswith("rm ") else None)

    def test_allow_runs_without_asking(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("asked"))
        assert gate("ls", role="user") is True

    @pytest.mark.parametrize("role", ["user", "orchestrator"])
    def test_confirm_needs_the_literal_yes(self, monkeypatch, role):
        monkeypatch.setattr("builtins.input", lambda *_: "YES")
        assert gate("rm -rf /tmp/x", role=role) is True
        monkeypatch.setattr("builtins.input", lambda *_: "yes")
        assert gate("rm -rf /tmp/x", role=role) is False

    def test_worker_never_prompts_and_refuses_confirm(self, monkeypatch):
        """Nobody watches a worker's window; a prompt there blocks forever."""
        monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("worker prompted"))
        assert gate("rm -rf /tmp/x", role="worker") is False
        assert gate("ls", role="worker") is True

    @pytest.mark.parametrize("role", ["user", "orchestrator", "worker"])
    def test_deny_refuses_without_asking(self, monkeypatch, role):
        self._deny(monkeypatch)
        monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("asked on deny"))
        assert gate("rm x", role=role) is False


def test_no_legacy_policy_callers():
    """A caller that can skip policy is a caller that can forget it."""
    offenders = []
    for path in SABLE.rglob("*.py"):
        if "policy" in path.relative_to(SABLE).parts[:1]:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module == "sable.policy.engine":
                bad = {a.name for a in node.names} & {"is_destructive", "confirm_destructive"}
                if bad:
                    offenders.append(f"{path.relative_to(SABLE)}: {sorted(bad)}")
    assert not offenders, offenders


def test_legacy_boolean_is_gone():
    assert not hasattr(engine, "is_destructive")


def test_every_repl_bash_path_is_gated():
    """Each `execute_bash` in the REPL sits in a block that called `gate` first.

    Task 2 counted six call sites and missed three: Ctrl+B, offline mode, and
    the exhausted-budget fallback all ran a typed line as bash with no policy
    check. The import guard above could not see them, because they never
    imported anything. This one reads the code.
    """
    src = (SABLE / "app" / "repl.py").read_text(encoding="utf-8")
    ungated = []
    for node in ast.walk(ast.parse(src)):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        seen_gate = False
        for stmt in body:
            text = ast.unparse(stmt)
            runs = "execute_bash(" in text or "run_block(" in text
            if "gate(" in text and not runs:
                seen_gate = True
            elif runs and not isinstance(stmt, (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.FunctionDef)):
                if not seen_gate:
                    ungated.append(stmt.lineno)
    assert not ungated, f"execute_bash with no gate() before it at repl.py lines {ungated}"
