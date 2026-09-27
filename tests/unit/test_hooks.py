"""Phase 3 Task 4: lifecycle hooks.

Same contract as Claude Code's hooks, so a user who has written one knows
this one: JSON on stdin, exit 0 allows, exit 2 blocks and stdout says why.
Any other failure is reported and does not block: a broken hook must not
wedge the shell. Hooks run after policy and can only make it stricter.
"""
from __future__ import annotations

import json
import sys

import pytest

from sable.policy import engine, hooks
from sable.policy.tiers import Tier


@pytest.fixture
def hook_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOKS_DIR", tmp_path)
    # Run hook scripts through this interpreter so the tests work on Windows,
    # where a shebang means nothing.
    monkeypatch.setattr(hooks, "_argv", lambda p: [sys.executable, str(p)])
    return tmp_path


def write_hook(d, name, body):
    (d / name).write_text("import json, sys\npayload = json.load(sys.stdin)\n" + body, encoding="utf-8")


class TestRun:
    def test_no_hook_is_a_no_op(self, hook_dir):
        r = hooks.run("pre_command", {"command": "ls"})
        assert not r.blocked and r.message == "" and r.context == ""

    def test_hook_receives_json_on_stdin(self, hook_dir, tmp_path):
        out = tmp_path / "seen.json"
        write_hook(hook_dir, "pre_command", f"open({str(out)!r}, 'w').write(json.dumps(payload))\n")
        hooks.run("pre_command", {"command": "ls -la"})
        seen = json.loads(out.read_text())
        assert seen["hook"] == "pre_command" and seen["command"] == "ls -la"

    def test_exit_2_blocks_with_stdout_as_the_reason(self, hook_dir):
        write_hook(hook_dir, "pre_command", "print('no curl here'); sys.exit(2)\n")
        r = hooks.run("pre_command", {"command": "curl x"})
        assert r.blocked and r.message == "no curl here"

    def test_other_failure_reports_and_does_not_block(self, hook_dir, capsys):
        write_hook(hook_dir, "pre_command", "sys.exit(1)\n")
        assert not hooks.run("pre_command", {"command": "ls"}).blocked
        assert "pre_command" in capsys.readouterr().err

    def test_hang_is_killed_and_does_not_block(self, hook_dir, monkeypatch, capsys):
        monkeypatch.setattr(hooks, "HOOK_TIMEOUT", 0.5)
        write_hook(hook_dir, "pre_command", "import time; time.sleep(30)\n")
        assert not hooks.run("pre_command", {"command": "ls"}).blocked
        assert "timed out" in capsys.readouterr().err

    def test_json_stdout_injects_context(self, hook_dir):
        write_hook(hook_dir, "post_command", "print(json.dumps({'context': 'prod db, be careful'}))\n")
        assert hooks.run("post_command", {"command": "ls"}).context == "prod db, be careful"

    def test_plain_stdout_is_shown_not_discarded(self, hook_dir):
        write_hook(hook_dir, "post_command", "print('logged')\n")
        r = hooks.run("post_command", {"command": "ls"})
        assert r.message == "logged" and r.context == ""


class TestGateRunsPreCommand:
    def test_hook_blocks_an_allowed_command(self, hook_dir):
        write_hook(hook_dir, "pre_command", "sys.exit(2 if 'curl' in payload['command'] else 0)\n")
        assert engine.gate("curl https://x", role="user") is False
        assert engine.gate("ls", role="user") is True

    def test_hook_sees_the_policy_decision(self, hook_dir, tmp_path, monkeypatch):
        out = tmp_path / "seen.json"
        write_hook(hook_dir, "pre_command", f"open({str(out)!r}, 'w').write(json.dumps(payload))\n")
        monkeypatch.setattr("builtins.input", lambda *_: "YES")
        engine.gate("rm -rf /tmp/x", role="orchestrator")
        seen = json.loads(out.read_text())
        assert seen["tier"] == Tier.CONFIRM and seen["rule"] == "recursive-delete" and seen["role"] == "orchestrator"

    def test_hook_cannot_loosen_a_deny(self, hook_dir, monkeypatch):
        """Hooks run after policy: a refused command never reaches them."""
        rule = engine.rules.Rule(name="no", pattern="x", why="w", tier=Tier.DENY)
        monkeypatch.setattr(engine.rules, "match", lambda c: rule)
        write_hook(hook_dir, "pre_command", "raise SystemExit('hook ran')\n")
        assert engine.gate("anything", role="user") is False

    def test_declined_confirm_never_runs_the_hook(self, hook_dir, tmp_path, monkeypatch):
        out = tmp_path / "ran"
        write_hook(hook_dir, "pre_command", f"open({str(out)!r}, 'w').write('x')\n")
        monkeypatch.setattr("builtins.input", lambda *_: "no")
        assert engine.gate("rm -rf /tmp/x", role="user") is False
        assert not out.exists()
