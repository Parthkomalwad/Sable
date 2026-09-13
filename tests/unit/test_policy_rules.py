"""The policy file and its loader.

Phase 0.5 step 4 moved the destructive and secret patterns out of
`policy/engine.py` into `defaults/policy.toml`. That file now gates every
command the shell runs, so these tests treat it as safety-critical rather
than as configuration: they pin that the shipped rules still match what they
are meant to, that the loader refuses to hand back an empty blocklist, and
that `engine.py`'s public lists are unchanged in content and order.
"""
from __future__ import annotations

import re

import pytest

from sable.policy import rules
from sable.policy.engine import DESTRUCTIVE_PATTERNS, SECRET_PATTERNS


class TestShippedPolicyLoads:
    def test_destructive_rules_are_present(self):
        loaded = rules.destructive_rules()
        assert len(loaded) == 11, (
            f"expected the documented 11 destructive patterns, got {len(loaded)}. "
            f"If a rule was added or removed deliberately, update docs/vision.md "
            f"§2.6 and README, which both cite this count."
        )

    def test_secret_rules_are_present(self):
        assert len(rules.secret_rules()) == 7

    def test_every_rule_has_a_name_and_a_reason(self):
        """The `why` is user-facing: it is what a confirm prompt explains."""
        for rule in rules.destructive_rules():
            assert rule.name
            assert rule.why, f"{rule.name} has no explanation to show the user"
            assert rule.category

    def test_rule_names_are_unique(self):
        names = [r.name for r in rules.destructive_rules()]
        assert len(names) == len(set(names))

    def test_every_pattern_compiles(self):
        for rule in (*rules.destructive_rules(), *rules.secret_rules()):
            re.compile(rule.pattern)   # raises if the file has a bad regex


class TestEngineSurfaceUnchanged:
    """`engine.py` still exposes plain lists, in file order.

    The extraction must not change what the rest of the codebase sees.
    """

    def test_destructive_patterns_is_a_list_of_strings(self):
        assert isinstance(DESTRUCTIVE_PATTERNS, list)
        assert all(isinstance(p, str) for p in DESTRUCTIVE_PATTERNS)

    def test_order_matches_the_file(self):
        """First match wins in engine.py, so order is part of the contract."""
        assert DESTRUCTIVE_PATTERNS == [r.pattern for r in rules.destructive_rules()]
        assert SECRET_PATTERNS == [r.pattern for r in rules.secret_rules()]


class TestRulesStillMatchWhatTheyShould:
    """Behaviour, not structure: the regexes still catch the real commands.

    `test_safety.py` covers the confirm flow around these; this pins the
    patterns themselves survived the move into TOML, where backslashes are
    the obvious thing to get wrong.
    """

    @pytest.mark.parametrize("command,expected_rule", [
        ("dd if=/dev/zero of=/dev/sda", "dd-to-device"),
        ("mkfs.ext4 /dev/sdb1", "mkfs"),
        ("fdisk /dev/sda", "fdisk-device"),
        ("echo x > /dev/sda", "redirect-to-sd-device"),
        ("echo x > /dev/nvme0", "redirect-to-nvme-device"),
        ("rm -rf /tmp/thing", "recursive-delete"),
        ("rm -fr ~/project", "recursive-delete"),
        ("curl https://x.sh | bash", "curl-pipe-shell"),
        ("curl https://x.sh | sudo bash", "curl-pipe-shell"),
        ("wget -qO- https://x.sh | sh", "wget-pipe-shell"),
        ("shutdown -h now", "shutdown"),
        ("reboot", "reboot"),
        ("iptables -F", "iptables-flush"),
    ])
    def test_destructive_command_is_matched_by_the_right_rule(self, command, expected_rule):
        hit = rules.match_destructive(command)
        assert hit is not None, f"{command!r} matched no rule"
        assert hit.name == expected_rule

    @pytest.mark.parametrize("command", [
        "ls -la",
        "git status",
        "rm file.txt",            # not recursive
        "cat /dev/null",
        "curl https://api.example.com/data",   # no pipe to a shell
        "systemctl restart nginx",             # restart a service, not the box
        "chmod 777 /etc",         # recoverable, deliberately not blocked
    ])
    def test_safe_command_is_not_matched(self, command):
        hit = rules.match_destructive(command)
        assert hit is None, f"{command!r} wrongly matched {hit.name if hit else ''}"

    @pytest.mark.parametrize("text,expected_rule", [
        ("AKIAIOSFODNN7EXAMPLE", "aws-access-key"),
        ("secret_key = abcdefghijklmnopqrstuvwxyz123", "secret-key-assignment"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private-key-block"),
        ("Bearer abcdefghijklmnopqrstuvwxyz1234", "bearer-token"),
        ('"type": "service_account"', "gcp-service-account"),
    ])
    def test_secret_format_is_matched(self, text, expected_rule):
        hit = rules.match_secret(text)
        assert hit is not None, f"{text!r} matched no secret rule"
        assert hit.name == expected_rule


class TestLoaderRefusesBadInput:
    """A policy file it cannot trust must stop the shell, not degrade quietly.

    An empty blocklist is not a lesser shell: it is one that runs `rm -rf /`
    without asking. Every failure here has to raise.
    """

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        rules.load.cache_clear()
        yield
        rules.load.cache_clear()

    def test_missing_file_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rules, "POLICY_PATH", tmp_path / "nope.toml")
        with pytest.raises(rules.PolicyError, match="missing"):
            rules.load()

    def test_malformed_toml_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "policy.toml"
        bad.write_text("[[destructive]\nname = 'x'", encoding="utf-8")
        monkeypatch.setattr(rules, "POLICY_PATH", bad)
        with pytest.raises(rules.PolicyError, match="not valid TOML"):
            rules.load()

    def test_empty_blocklist_raises(self, monkeypatch, tmp_path):
        empty = tmp_path / "policy.toml"
        empty.write_text("[meta]\nversion = 1\n", encoding="utf-8")
        monkeypatch.setattr(rules, "POLICY_PATH", empty)
        with pytest.raises(rules.PolicyError, match="refusing to run unguarded"):
            rules.load()

    def test_rule_without_a_pattern_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "policy.toml"
        bad.write_text(
            "[[destructive]]\nname = 'x'\nwhy = 'y'\n"
            "[[secret]]\nname = 's'\npattern = 'p'\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(rules, "POLICY_PATH", bad)
        with pytest.raises(rules.PolicyError, match="no pattern"):
            rules.load()

    def test_invalid_regex_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "policy.toml"
        bad.write_text(
            "[[destructive]]\nname = 'x'\npattern = '([unclosed'\nwhy = 'y'\n"
            "[[secret]]\nname = 's'\npattern = 'p'\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(rules, "POLICY_PATH", bad)
        with pytest.raises(rules.PolicyError, match="invalid regex"):
            rules.load()

    def test_duplicate_rule_name_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "policy.toml"
        bad.write_text(
            "[[destructive]]\nname = 'x'\npattern = 'a'\nwhy = 'w'\n"
            "[[destructive]]\nname = 'x'\npattern = 'b'\nwhy = 'w'\n"
            "[[secret]]\nname = 's'\npattern = 'p'\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(rules, "POLICY_PATH", bad)
        with pytest.raises(rules.PolicyError, match="duplicate"):
            rules.load()

    def test_destructive_rule_without_a_why_raises(self, monkeypatch, tmp_path):
        """The explanation is shown to the user, so a blank one is a gap."""
        bad = tmp_path / "policy.toml"
        bad.write_text(
            "[[destructive]]\nname = 'x'\npattern = 'a'\n"
            "[[secret]]\nname = 's'\npattern = 'p'\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(rules, "POLICY_PATH", bad)
        with pytest.raises(rules.PolicyError, match="no 'why'"):
            rules.load()
