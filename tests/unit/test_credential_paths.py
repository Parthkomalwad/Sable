"""Credential files are always at least confirm (follow-up to Phase 3.5).

A model that reads a private key has sent it to its provider. `cat` of a key
and `fs.read` of the same path are the same leak, so one built-in floor
covers both, like `sudo`, and no policy file can relax it.
"""
from __future__ import annotations

import pytest

from sable.policy import engine
from sable.policy.tiers import Tier


@pytest.mark.parametrize("command", [
    "cat ~/.ssh/id_rsa",
    "cat /home/u/.ssh/id_ed25519",
    "less ~/.ssh/deploy_key",
    "cat ~/.ssh/authorized_keys",
    "cat ~/.aws/credentials",
    "ls ~/.gnupg/",
    "cat ~/.netrc",
    "cat ~/.pgpass",
    "cat ~/.git-credentials",
    "cat ~/.docker/config.json",
    "cat ~/.kube/config",
    "cat ~/.config/agentic-shell/config.json",
    "sudo cat /etc/shadow",
    "cat /etc/sudoers",
    'tool:fs.read {"path": "/home/u/.ssh/id_rsa"}',
    'tool:fs.read {"path": ".aws/credentials"}',
])
def test_credential_reads_need_confirm(command):
    d = engine.decide(command)
    assert d.tier.severity >= Tier.CONFIRM.severity
    assert d.rule.name in ("credential-file", "sudo")


@pytest.mark.parametrize("command", [
    "cat ~/.ssh/id_rsa.pub",
    "cat ~/.ssh/known_hosts",
    "cat ~/.ssh/config",
    "grep shadow notes.txt",
    "cat /etc/passwd",
    "ls ~/.aws",
])
def test_non_secrets_stay_allowed(command):
    assert engine.decide(command).tier is Tier.ALLOW


def test_a_user_allow_rule_cannot_relax_it(tmp_path, monkeypatch):
    user = tmp_path / "user.toml"
    user.write_text("[[rule]]\nname = 'cat-ok'\npattern = '^cat '\ntier = 'allow'\n", encoding="utf-8")
    monkeypatch.setattr(engine.rules, "USER_POLICY_PATH", user)
    engine.rules.load.cache_clear()
    assert engine.decide("cat ~/.ssh/id_rsa").tier is Tier.CONFIRM


def test_a_worker_is_refused(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("worker prompted"))
    assert engine.gate("cat ~/.ssh/id_rsa", role="worker") is False
