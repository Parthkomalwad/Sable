"""Phase 3 Task 3: the user file, the admin floor, sudo and root.

Precedence is defaults, then /etc/sable/policy.toml, then ~/.sable/policy.toml.
A user rule may make a command stricter, never looser. The comparison is on
tier severity, not file order: the user file is read last and still loses.
"""
from __future__ import annotations

import pytest

from sable.policy import engine, privilege, rules
from sable.policy.tiers import Tier


@pytest.fixture
def layers(monkeypatch, tmp_path):
    admin, user = tmp_path / "admin.toml", tmp_path / "user.toml"
    monkeypatch.setattr(rules, "ADMIN_POLICY_PATH", admin)
    monkeypatch.setattr(rules, "USER_POLICY_PATH", user)
    rules.load.cache_clear()
    yield admin, user
    rules.load.cache_clear()


def _rule(name, pattern, tier):
    return f"[[rule]]\nname = '{name}'\npattern = '{pattern}'\ntier = '{tier}'\n"


class TestUserFile:
    def test_user_deny_is_enforced_and_needs_no_why(self, layers):
        _, user = layers
        user.write_text(_rule("no-rm-rf", "^rm -rf", "deny"), encoding="utf-8")
        d = engine.decide("rm -rf /tmp/x")
        assert d.tier is Tier.DENY
        assert d.source == str(user)
        assert d.why   # a fallback reason, never blank in the prompt

    def test_user_allow_cannot_loosen_a_shipped_confirm(self, layers):
        _, user = layers
        user.write_text(_rule("rm-ok", "^rm ", "allow"), encoding="utf-8")
        d = engine.decide("rm -rf /tmp/x")
        assert d.tier is Tier.CONFIRM and d.rule.name == "recursive-delete"

    def test_user_rule_can_gate_an_unmatched_command(self, layers):
        _, user = layers
        user.write_text(_rule("no-curl", "^curl ", "confirm"), encoding="utf-8")
        assert engine.decide("curl https://x").tier is Tier.CONFIRM

    def test_malformed_user_file_warns_and_keeps_defaults(self, layers, capsys):
        _, user = layers
        user.write_text("[[rule]\nbroken", encoding="utf-8")
        assert engine.decide("rm -rf /tmp/x").tier is Tier.CONFIRM
        assert str(user) in capsys.readouterr().err

    def test_missing_user_file_is_fine(self, layers):
        assert engine.decide("ls").tier is Tier.ALLOW


class TestAdminFloor:
    def test_user_allow_against_admin_deny_stays_denied(self, layers):
        admin, user = layers
        admin.write_text(_rule("no-docker-prune", "docker system prune", "deny"), encoding="utf-8")
        user.write_text(_rule("prune-ok", "docker", "allow"), encoding="utf-8")
        d = engine.decide("docker system prune -af")
        assert d.tier is Tier.DENY and d.source == str(admin)

    def test_malformed_admin_file_refuses_to_start(self, layers):
        """A typo must not silently remove the floor users cannot loosen."""
        admin, _ = layers
        admin.write_text("[[rule]\nbroken", encoding="utf-8")
        with pytest.raises(rules.PolicyError):
            rules.load()


class TestSudo:
    @pytest.mark.parametrize("cmd", ["sudo apt update", "ls && sudo reboot", "echo x | sudo tee /etc/f"])
    def test_sudo_is_always_at_least_confirm(self, layers, cmd):
        assert engine.decide(cmd).tier.severity >= Tier.CONFIRM.severity

    def test_user_allow_cannot_relax_sudo(self, layers):
        _, user = layers
        user.write_text(_rule("apt-ok", "apt", "allow"), encoding="utf-8")
        d = engine.decide("sudo apt update")
        assert d.tier is Tier.CONFIRM and d.rule.name == "sudo"

    def test_word_sudo_in_an_argument_is_not_escalation(self, layers):
        assert engine.decide("man sudo").tier is Tier.ALLOW
        assert engine.decide("grep sudoers /etc/group").tier is Tier.ALLOW


def test_tier_severity_orders_allow_confirm_deny():
    assert Tier.ALLOW.severity < Tier.CONFIRM.severity < Tier.DENY.severity


class TestRoot:
    def test_root_is_refused(self, monkeypatch):
        monkeypatch.setattr(privilege, "_euid", lambda: 0)
        assert privilege.root_refusal() is not None

    def test_user_is_not(self, monkeypatch):
        monkeypatch.setattr(privilege, "_euid", lambda: 1000)
        assert privilege.root_refusal() is None
