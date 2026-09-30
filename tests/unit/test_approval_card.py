"""The approval card every confirm draws: shape, wrapping, the recorded answer."""
from __future__ import annotations

import re

from sable.policy import approval, blast
from sable.policy.blast import Level

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(s: str) -> list[str]:
    return ANSI.sub("", s).splitlines()


def test_every_row_has_both_edges_at_the_same_column():
    rows = _plain(approval.command_card("Creating a script", "echo hi > a.py",
                                        blast.COLOURS[Level.WRITES], "writes"))
    assert rows[0].strip().startswith("╭─ ◆ WAITING FOR YOU")
    assert rows[0].rstrip().endswith("writes ─╮")
    assert rows[-1].strip().startswith("╰") and rows[-1].rstrip().endswith("╯")
    widths = {len(r.rstrip()) for r in rows}
    assert len(widths) == 1, widths


def test_a_long_command_wraps_inside_the_box():
    cmd = "find / -name '*.log' -mtime +30 " + "-o -name '*.tmp' " * 12
    rows = _plain(approval.card("◆ WAITING FOR YOU", "", [(approval.CMD, f"$ {cmd}")], width=60))
    body = rows[1:-1]
    assert len(body) > 1 and all(len(r.rstrip()) == 60 + 2 for r in rows)
    assert body[1].startswith("  │   ")          # continuation lines are indented under the $


def test_colour_follows_the_risk_level():
    red = blast.COLOURS[Level.DESTRUCTIVE]
    assert red + "╭" in approval.command_card("x", "rm -rf build", red, "destructive")


def test_keys_line_and_outcomes():
    assert ANSI.sub("", approval.keys(("↵", "run"), ("q", "cancel"))).strip() == "↵ run   q cancel  ›"
    assert "✓ approved" in ANSI.sub("", approval.outcome(True))
    assert "✗ cancelled" in ANSI.sub("", approval.outcome(False))


def test_orchestrator_confirm_draws_the_card_and_records_the_answer(monkeypatch, capsys):
    from sable.agents.orchestrator import OrchestratorAgent as Orchestrator
    agent = object.__new__(Orchestrator)
    monkeypatch.setattr("builtins.input", lambda *_: "")
    assert Orchestrator._confirm_command(agent, "ls -la", "list files") == "ls -la"
    out = ANSI.sub("", capsys.readouterr().out)
    assert "WAITING FOR YOU" in out and "$ ls -la" in out and "✓ approved" in out

    monkeypatch.setattr("builtins.input", lambda *_: "q")
    assert Orchestrator._confirm_command(agent, "ls -la", "list files") is None
    assert "✗ cancelled" in ANSI.sub("", capsys.readouterr().out)


def test_policy_yes_confirm_uses_the_card(monkeypatch, capsys):
    from sable.policy import engine
    cmd = "rm -rf /var/log/old"
    monkeypatch.setattr("builtins.input", lambda *_: "YES")
    assert engine._confirm(cmd, engine.decide(cmd)) is True
    out = ANSI.sub("", capsys.readouterr().out)
    assert "⚠ " in out and f"$ {cmd}" in out and "destructive" in out and "✓ approved" in out
