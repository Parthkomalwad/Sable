"""fs.* tools (J3): confinement, patching, atomic writes, previews, caps."""
from __future__ import annotations

import os

import pytest

from sable.policy import blast
from sable.policy.tiers import Tier
from sable.tools import fs, registry
from sable.tools.base import ToolContext, ToolError


def _ctx(root, role="orchestrator"):
    return ToolContext(role=role, cwd=str(root), agent="t")


def _run(name, args, ctx):
    return registry.get(name).run(args, ctx)


def _can_symlink(tmp_path) -> bool:
    try:
        os.symlink(tmp_path, tmp_path / "_probe")
    except (OSError, NotImplementedError):
        return False
    os.unlink(tmp_path / "_probe")
    return True


@pytest.fixture
def root(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_bytes(b"one\ntwo\nthree\n")
    (tmp_path / "secret.txt").write_bytes(b"outside\n")
    return ws


def test_tiers():
    assert registry.get("fs.write").tier is Tier.CONFIRM
    assert registry.get("fs.patch").tier is Tier.CONFIRM
    for name in ("fs.read", "fs.search", "fs.tree"):
        assert registry.get(name).tier is Tier.ALLOW


def test_read_line_range(root):
    assert _run("fs.read", {"path": "a.txt", "start": 2, "end": 3}, _ctx(root)).output == "two\nthree\n"


@pytest.mark.parametrize("name,args", [
    ("fs.write", {"path": "../x.txt", "content": "x"}),
    ("fs.patch", {"path": "../secret.txt", "diff": "@@ -1 +1 @@\n-outside\n+in\n"}),
    ("fs.tree", {"path": ".."}),
])
def test_dotdot_escape_refused(root, name, args):
    with pytest.raises(ToolError):
        _run(name, args, _ctx(root))
    assert (root.parent / "secret.txt").read_text() == "outside\n"


def test_absolute_path_outside_refused(root):
    with pytest.raises(ToolError):
        _run("fs.write", {"path": str(root.parent / "secret.txt"), "content": "x"}, _ctx(root))


def test_outside_read_taints_orchestrator_and_is_refused_for_worker(root):
    r = _run("fs.read", {"path": "../secret.txt"}, _ctx(root))
    assert r.output == "outside\n" and r.taints
    assert not _run("fs.read", {"path": "a.txt"}, _ctx(root)).taints
    with pytest.raises(ToolError):
        _run("fs.read", {"path": "../secret.txt"}, _ctx(root, "worker"))


def test_symlink_escape_refused(root, tmp_path):
    if not _can_symlink(tmp_path):
        pytest.skip("no symlink privilege on this host")
    os.symlink(tmp_path / "secret.txt", root / "link.txt")
    with pytest.raises(ToolError):
        _run("fs.write", {"path": "link.txt", "content": "pwned"}, _ctx(root))
    with pytest.raises(ToolError):
        _run("fs.read", {"path": "link.txt"}, _ctx(root, "worker"))
    assert (tmp_path / "secret.txt").read_text() == "outside\n"


def test_clean_patch(root):
    diff = "--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n"
    assert _run("fs.patch", {"path": "a.txt", "diff": diff}, _ctx(root)).ok
    assert (root / "a.txt").read_text() == "one\nTWO\nthree\n"


def test_patch_with_wrong_line_number_still_needs_matching_context(root):
    diff = "@@ -7,2 +7,2 @@\n two\n-three\n+3\n"
    _run("fs.patch", {"path": "a.txt", "diff": diff}, _ctx(root))
    assert (root / "a.txt").read_text() == "one\ntwo\n3\n"


def test_rejected_patch_leaves_file_untouched(root):
    before = (root / "a.txt").read_bytes()
    diff = "@@ -1,2 +1,2 @@\n one\n-nope\n+x\n"
    with pytest.raises(ToolError):
        _run("fs.patch", {"path": "a.txt", "diff": diff}, _ctx(root))
    assert (root / "a.txt").read_bytes() == before
    assert os.listdir(root) == ["a.txt"]   # no temp file left behind


def test_failed_patch_through_registry_is_a_structured_failure(root, monkeypatch):
    from sable.policy import engine
    monkeypatch.setattr(engine, "gate", lambda *a, **k: True)
    r = registry.call("fs.patch", {"path": "a.txt", "diff": "no hunks"}, _ctx(root))
    assert not r.ok and "fs.patch failed" in r.output


def test_write_is_atomic(root, monkeypatch):
    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(fs.os, "replace", boom)
    with pytest.raises(ToolError):
        _run("fs.write", {"path": "a.txt", "content": "new"}, _ctx(root))
    assert (root / "a.txt").read_text() == "one\ntwo\nthree\n"
    assert os.listdir(root) == ["a.txt"]
    monkeypatch.undo()
    _run("fs.write", {"path": "b.txt", "content": "new"}, _ctx(root))
    assert (root / "b.txt").read_text() == "new"


def test_preview_shows_coloured_diff(root):
    out = registry.get("fs.write").preview({"path": "a.txt", "content": "one\n2\nthree\n"}, _ctx(root))
    assert f"{fs.RED}-two" in out and f"{fs.GREEN}+2" in out
    out = registry.get("fs.patch").preview(
        {"path": "a.txt", "diff": "@@ -3 +3 @@\n-three\n+3\n"}, _ctx(root))
    assert "-three" in out and "+3" in out
    assert (root / "a.txt").read_text() == "one\ntwo\nthree\n"


def test_confirm_block_prints_the_preview(root, monkeypatch, capsys):
    from sable.agents.orchestrator import OrchestratorAgent
    monkeypatch.setattr("builtins.input", lambda _: "q")
    ok = OrchestratorAgent._confirm_tool(object(), "fs.write", {"path": "a.txt", "content": "x\n"}, "why", _ctx(root))
    assert not ok and "+x" in capsys.readouterr().out


def test_search_and_tree_caps(root, monkeypatch):
    for i in range(20):
        (root / f"f{i}.py").write_text("needle\n")
    monkeypatch.setattr(fs, "MAX_RESULTS", 5)
    monkeypatch.setattr(fs, "MAX_ENTRIES", 5)
    out = _run("fs.search", {"glob": "*.py", "regex": "needle"}, _ctx(root)).output
    assert out.count("needle") == 5 and "stopped at 5" in out
    out = _run("fs.tree", {}, _ctx(root)).output
    assert "stopped at 5" in out


def test_search_does_not_escape(root):
    assert "secret" not in _run("fs.search", {"glob": "../*.txt"}, _ctx(root)).output


def test_blast_levels():
    assert blast.classify(registry.as_command("fs.read", {"path": "x"})) is blast.Level.READ_ONLY
    assert blast.classify(registry.as_command("fs.write", {"path": "x", "content": ""})) is blast.Level.WRITES
    add = registry.as_command("fs.patch", {"path": "x", "diff": "@@ -1 +1,2 @@\n a\n+b\n"})
    rm = registry.as_command("fs.patch", {"path": "x", "diff": "@@ -1,2 +1 @@\n a\n-b\n"})
    assert blast.classify(add) is blast.Level.WRITES
    assert blast.classify(rm) is blast.Level.DESTRUCTIVE
