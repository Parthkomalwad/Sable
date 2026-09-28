"""K1: ghost text ranked by directory, then recency."""
from __future__ import annotations

from sable.ui.prompt.ghost import CwdHistorySuggest


def _ghost(tmp_path, rows):
    g = CwdHistorySuggest(path=tmp_path / "h", load=False)
    g._ready.set()
    for cwd, cmd in rows:
        g.record(cwd, cmd)
    return g


def test_same_directory_beats_newer_elsewhere(tmp_path):
    g = _ghost(tmp_path, [("/a", "git push origin main"), ("/b", "git pull")])
    assert g.suggest("git p", "/a") == "ush origin main"


def test_recency_within_directory_and_fallback(tmp_path):
    g = _ghost(tmp_path, [("/a", "make test"), ("/a", "make build")])
    assert g.suggest("make", "/a") == " build"
    assert g.suggest("make", "/z") == " build"


def test_prefix_only(tmp_path):
    g = _ghost(tmp_path, [("/a", "docker ps")])
    assert g.suggest("ps", "/a") is None
    assert g.suggest("docker ps", "/a") is None
    assert g.suggest("", "/a") is None


def test_not_ready_suggests_nothing(tmp_path):
    g = CwdHistorySuggest(path=tmp_path / "h", load=False)
    g.record("/a", "ls -la")
    assert g.suggest("ls", "/a") is None


def test_budget_exceeded_suggests_nothing(tmp_path):
    g = _ghost(tmp_path, [("/x", f"cmd {i}") for i in range(600)])
    assert g.suggest("zzz", "/a", budget=-1) is None


def test_persists_and_reloads(tmp_path):
    _ghost(tmp_path, [("/a", "echo one")])
    g = CwdHistorySuggest(path=tmp_path / "h", load=False)
    g._load()
    assert g.suggest("echo", "/a") == " one"
