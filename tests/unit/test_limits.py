"""Phase 8 Task 4 (F5): resource limits and network off for sub-agents."""
from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from sable.agents import limits
from sable.agents.sandbox import Sandbox
from sable.core.config.schema import ShellConfig


def test_defaults_and_override():
    assert limits.parse(None) == limits.Limits(mem_mb=2048, cpu_s=None, procs=256, network=True)
    got = limits.parse({"network": False, "cpu_s": 60}, {"mem_mb": 512})
    assert (got.mem_mb, got.cpu_s, got.procs, got.network) == (512, 60, 256, False)


@pytest.mark.parametrize("bad", [
    {"mem_mb": 0}, {"cpu_s": -1}, {"procs": "8"}, {"procs": True},
    {"network": "no"}, {"disk_mb": 5}, ["mem_mb"],
])
def test_validation_rejects(bad):
    with pytest.raises(ValueError):
        limits.parse(bad)


def test_config_validates_and_round_trips():
    cfg = ShellConfig.from_dict({"model": "m", "limits": {"network": False}})
    assert cfg.to_dict()["limits"] == {"network": False}
    with pytest.raises(ValueError):
        ShellConfig.from_dict({"model": "m", "limits": {"mem_mb": -5}})


def _sandbox(tmp_path, bwrap, lim):
    sb = Sandbox.__new__(Sandbox)
    sb._task_dir, sb._shared_read_dir, sb._extra_write_dirs = str(tmp_path), None, []
    sb.use_bwrap, sb.limits = bwrap, lim
    return sb


def test_network_off_adds_unshare_net(tmp_path):
    off = _sandbox(tmp_path, True, limits.parse({"network": False})).wrap_command("true")
    on = _sandbox(tmp_path, True, limits.parse(None)).wrap_command("true")
    assert "--unshare-net" in off and "--unshare-net" not in on


def test_rlimits_prefixed_in_both_modes(tmp_path):
    lim = limits.parse({"mem_mb": 64, "cpu_s": 5, "procs": 32})
    for bwrap in (True, False):
        script = _sandbox(tmp_path, bwrap, lim).wrap_command("true")
        assert script.startswith("ulimit -v 65536 ")
        assert "ulimit -t 5 " in script and "ulimit -u 32 " in script


def test_unsupported_reported_not_silent():
    applied = limits.enforced(limits.parse({"network": False, "cpu_s": 9}),
                              {"mem_mb": True, "cpu_s": False, "procs": True, "network": False})
    assert "NOT enforced" in applied["network"]
    assert "NOT enforced" in applied["cpu_s"]
    assert applied["mem_mb"] == "2048"


def test_supported_network_follows_bwrap():
    assert limits.supported(bwrap=False)["network"] is False
    assert limits.supported(bwrap=True)["network"] is True


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="needs Linux bash")
def test_memory_limit_stops_a_child():
    prefix = limits.ulimit_prefix(limits.parse({"mem_mb": 256, "procs": None}))
    grab = f"{sys.executable} -c 'x = bytearray(1024 * 1024 * 1024)'"
    over = subprocess.run(["bash", "-c", prefix + grab], capture_output=True, text=True)
    assert over.returncode != 0 and "MemoryError" in over.stderr
    ok = subprocess.run(["bash", "-c", prefix + f"{sys.executable} -c 'x = bytearray(1024)'"])
    assert ok.returncode == 0
