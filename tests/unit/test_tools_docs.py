"""docs.* tools (J7): local lookups, subprocess mocked throughout."""
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sable.policy.tiers import Tier
from sable.tools import docs, registry
from sable.tools.base import ToolContext

CTX = ToolContext(role="worker", cwd="/tmp", agent="a1")
NAMES = ["docs.man", "docs.help", "docs.tldr", "docs.pkg"]


def _done(out="", code=0):
    return SimpleNamespace(stdout=out, stderr="", returncode=code)


def _which(name):
    return f"/usr/bin/{name}"


@pytest.mark.parametrize("name", NAMES)
def test_registered_allow_tier(name):
    tool = registry.get(name)
    assert tool is not None and tool.tier is Tier.ALLOW


@pytest.mark.parametrize("bad", ["ls;rm", "../x", "/bin/ls", "a b", "", "$(id)", "-rf", "x|y"])
@pytest.mark.parametrize("tool", ["docs.help", "docs.man", "docs.tldr"])
def test_rejects_non_program_names(tool, bad):
    with patch("subprocess.run") as run:
        res = registry.get(tool).run({"cmd": bad}, CTX)
    assert not res.ok and not run.called


@pytest.mark.parametrize("bad", ["ls;rm", "../x", "/bin/ls", "a b"])
def test_pkg_rejects_bad_names(bad):
    with patch("subprocess.run") as run:
        res = registry.get("docs.pkg").run({"name": bad}, CTX)
    assert not res.ok and not run.called


def test_help_program_not_on_path():
    with patch("shutil.which", return_value=None), patch("subprocess.run") as run:
        res = docs.help_({"cmd": "nosuchprog"}, CTX)
    assert not res.ok and "not found" in res.output and not run.called


ORCH = ToolContext(role="orchestrator", cwd="/tmp", agent="o")


def test_help_runs_argv_without_shell_and_no_taint():
    with patch("shutil.which", side_effect=_which), \
         patch.object(docs, "bwrap_available", return_value=False), \
         patch("subprocess.run", return_value=_done("usage: ls")) as run:
        res = docs.help_({"cmd": "ls"}, ORCH)
    assert res.ok and res.output == "usage: ls" and res.taints is False
    argv = run.call_args.args[0]
    assert argv == ["/usr/bin/ls", "--help"]
    assert not run.call_args.kwargs.get("shell")
    assert run.call_args.kwargs["timeout"] == docs.TIMEOUT


def test_help_never_retries_with_dash_h():
    with patch("shutil.which", side_effect=_which), \
         patch.object(docs, "bwrap_available", return_value=False), \
         patch("subprocess.run", return_value=_done("", 1)) as run:
        res = docs.help_({"cmd": "shutdown"}, ORCH)
    assert not res.ok and run.call_count == 1
    assert "-h" not in run.call_args.args[0]


def test_worker_refused_without_bwrap():
    with patch("shutil.which", side_effect=_which), \
         patch.object(docs, "bwrap_available", return_value=False), \
         patch("subprocess.run") as run:
        res = docs.help_({"cmd": "ls"}, CTX)
    assert not res.ok and "unsandboxed" in res.output and not run.called


def test_help_uses_bwrap_without_network_or_run():
    with patch("shutil.which", side_effect=_which), \
         patch.object(docs, "bwrap_available", return_value=True), \
         patch("subprocess.run", return_value=_done("usage")) as run:
        res = docs.help_({"cmd": "ls"}, CTX)
    argv = run.call_args.args[0]
    assert res.ok and argv[0] == "bwrap" and "--unshare-net" in argv and "--clearenv" in argv
    assert any(argv[i:i + 2] == ["--tmpfs", "/run"] for i in range(len(argv)))
    assert argv[-2:] == ["/usr/bin/ls", "--help"]


def test_timeout_is_a_result_not_a_crash():
    with patch("shutil.which", side_effect=_which), \
         patch.object(docs, "bwrap_available", return_value=False), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ls", 5)):
        res = docs.help_({"cmd": "ls"}, ORCH)
    assert not res.ok and "timed out" in res.output


def test_output_is_capped():
    with patch("shutil.which", side_effect=_which), \
         patch("subprocess.run", return_value=_done("x" * (docs.CAP * 2))):
        res = docs.man({"cmd": "ls"}, CTX)
    assert res.ok and len(res.output) < docs.CAP + 100 and "truncated" in res.output


def test_man_strips_overstrike_and_passes_section():
    raw = "N\bNA\bAM\bME\bE _\bl_\bs"
    with patch("shutil.which", side_effect=_which), \
         patch("subprocess.run", return_value=_done(raw)) as run:
        res = docs.man({"cmd": "ls", "section": "1"}, CTX)
    assert res.output == "NAME ls"
    assert run.call_args.args[0] == ["/usr/bin/man", "-P", "cat", "1", "ls"]
    assert run.call_args.kwargs["env"]["MANWIDTH"] == "80"


def test_man_rejects_bad_section():
    with patch("subprocess.run") as run:
        res = docs.man({"cmd": "ls", "section": "1;rm"}, CTX)
    assert not res.ok and not run.called


def test_tldr_not_installed():
    with patch("shutil.which", return_value=None):
        res = docs.tldr({"cmd": "tar"}, CTX)
    assert not res.ok and "not installed" in res.output


def test_pkg_tries_each_manager():
    def which(name):
        return None if name == "apt-cache" else _which(name)
    outs = [_done("", 1), _done('{"name": "left-pad"}')]
    with patch("shutil.which", side_effect=which), patch("subprocess.run", side_effect=outs) as run:
        res = docs.pkg({"name": "left-pad"}, CTX)
    assert res.ok and "left-pad" in res.output and res.taints is True
    assert [c.args[0][0] for c in run.call_args_list] == ["/usr/bin/pip", "/usr/bin/npm"]


def test_pkg_pip_does_not_taint():
    def which(name):
        return None if name == "apt-cache" else _which(name)
    with patch("shutil.which", side_effect=which), patch("subprocess.run", return_value=_done("Name: rich")):
        res = docs.pkg({"name": "rich"}, CTX)
    assert res.ok and res.taints is False


def test_pkg_nothing_found():
    with patch("shutil.which", return_value=None):
        res = docs.pkg({"name": "x"}, CTX)
    assert not res.ok
