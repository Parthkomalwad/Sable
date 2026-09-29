"""/plugin add|list|remove (Phase 9 Task 3, H2), against the mock MCP server."""
import json
import sys
from pathlib import Path

import pytest

from sable.app.builtins import plugin
from sable.mcp import servers
from sable.policy import hooks
from sable.skills.index import SkillIndex
from sable.skills.signing import SIGNATURE_FILE
from sable.tools import registry

ROOT = Path(__file__).resolve().parents[2]
SERVER = (ROOT / "tests" / "fixtures" / "mock_mcp_server.py").as_posix()
PY = Path(sys.executable).as_posix()


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"backend": "ollama"}))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(hooks, "HOOKS_DIR", tmp_path / ".sable" / "hooks")
    lines = []
    monkeypatch.setattr(plugin, "_out", lambda s: lines.append(str(s)))
    monkeypatch.setattr(servers, "_loaded", True)
    monkeypatch.setattr(registry, "_loaded", True)
    yield tmp_path, path, lines
    for name in list(servers._clients):
        servers.unregister_server(name)


def _folder(tmp_path, toml=None, skills=("tidy",), hook=("pre_command", "exit 0\n")):
    d = tmp_path / "plug"
    d.mkdir()
    (d / "plugin.toml").write_text(toml or (
        '[plugin]\nname = "demo"\nversion = "1.0"\ndescription = "a demo"\n'
        f'[server]\ncommand = ["{PY}", "{SERVER}", "new-spec"]\n'
        '[tools]\ntrusted = ["t0", "t2"]\n'))
    for s in skills:
        (d / "skills" / s).mkdir(parents=True)
        (d / "skills" / s / "SKILL.md").write_text(f"# {s}\nclean up logs\n")
    if hook:
        (d / "hooks").mkdir()
        (d / "hooks" / hook[0]).write_text(hook[1])
    return d


def _answers(*seq):
    it = iter(seq)
    return lambda prompt: next(it)


def test_add_list_remove_round_trip(env):
    tmp, path, lines = env
    d = _folder(tmp)
    # add? yes; trust t0? yes; trust t2? no; hook? yes
    plugin.handle_plugin(f'add "{d.as_posix()}"', path=path, confirm=_answers(True, True, False, True))
    cfg = json.loads(path.read_text())
    assert cfg["mcp"]["servers"]["demo"]["command"][-1] == "new-spec"
    assert cfg["mcp"]["trusted"] == ["demo.t0"]
    assert servers.label(registry.get("mcp.demo.t0")) == "trusted"
    assert servers.label(registry.get("mcp.demo.t2")) == "preview"
    rec = cfg["plugins"]["demo"]
    assert rec["version"] == "1.0" and rec["skills"] == ["tidy"] and rec["hooks"] == ["pre_command"]
    assert (hooks.HOOKS_DIR / "pre_command").read_text() == "exit 0\n"
    entry = [e for e in SkillIndex().list_all() if e["name"] == "tidy"][0]
    assert entry["source"] == "imported:plugin:demo"
    assert not (tmp / "skills" / "tidy" / SIGNATURE_FILE).exists()

    lines.clear()
    plugin.handle_plugin("list", path=path)
    assert any("demo" in s and "1.0" in s for s in lines)

    plugin.handle_plugin("remove demo", path=path)
    cfg = json.loads(path.read_text())
    assert cfg["plugins"] == {} and cfg["mcp"]["servers"] == {} and cfg["mcp"]["trusted"] == []
    assert registry.get("mcp.demo.t0") is None
    assert not (tmp / "skills" / "tidy").exists()
    assert not (hooks.HOOKS_DIR / "pre_command").exists()
    assert all(e["name"] != "tidy" for e in SkillIndex().list_all())


@pytest.mark.parametrize("toml,why", [
    ("[plugin\n", "plugin.toml"),
    ('[plugin]\nversion = "1"\n[server]\ncommand = ["x"]\n', "name"),
    ('[plugin]\nname = "a b"\nversion = "1"\n[server]\ncommand = ["x"]\n', "name"),
    ('[plugin]\nname = "a"\nversion = "1"\n', "server"),
    ('[plugin]\nname = "a"\nversion = "1"\n[server]\nurl = "http://evil.com"\n', "https"),
    ('[plugin]\nname = "a"\nversion = "1"\n[server]\ncommand = ["x"]\n[tools]\ntrusted = "t0"\n',
     "trusted"),
])
def test_bad_toml_is_a_clear_error_and_changes_nothing(env, toml, why):
    tmp, path, lines = env
    d = _folder(tmp, toml=toml, skills=(), hook=None)
    plugin.handle_plugin(f'add "{d.as_posix()}"', path=path, confirm=_answers())
    assert any(why in s for s in lines), lines
    assert "plugins" not in json.loads(path.read_text())


def test_requested_trust_needs_its_own_yes(env):
    tmp, path, lines = env
    d = _folder(tmp, skills=(), hook=None)
    plugin.handle_plugin(f'add "{d.as_posix()}"', path=path, confirm=_answers(True, False, False))
    assert json.loads(path.read_text())["mcp"]["trusted"] == []
    assert servers.label(registry.get("mcp.demo.t0")) == "preview"


def test_declining_the_plugin_installs_nothing(env):
    tmp, path, lines = env
    d = _folder(tmp)
    plugin.handle_plugin(f'add "{d.as_posix()}"', path=path, confirm=_answers(False))
    assert "mcp" not in json.loads(path.read_text())
    assert registry.get("mcp.demo.t0") is None
    assert not (tmp / "skills" / "tidy").exists()


def test_hooks_never_overwrite(env):
    tmp, path, lines = env
    hooks.HOOKS_DIR.mkdir(parents=True)
    (hooks.HOOKS_DIR / "pre_command").write_text("mine\n")
    d = _folder(tmp, skills=())
    plugin.handle_plugin(f'add "{d.as_posix()}"', path=path, confirm=_answers(True, False, False))
    assert (hooks.HOOKS_DIR / "pre_command").read_text() == "mine\n"
    assert json.loads(path.read_text())["plugins"]["demo"]["hooks"] == []
    plugin.handle_plugin("remove demo", path=path)
    assert (hooks.HOOKS_DIR / "pre_command").read_text() == "mine\n"


def test_remove_only_removes_what_it_installed(env):
    tmp, path, lines = env
    (tmp / "skills" / "tidy").mkdir(parents=True)          # the user's own skill
    (tmp / "skills" / "tidy" / "SKILL.md").write_text("mine")
    d = _folder(tmp, skills=("tidy", "rotate"), hook=None)
    plugin.handle_plugin(f'add "{d.as_posix()}"', path=path, confirm=_answers(True, False, False))
    assert json.loads(path.read_text())["plugins"]["demo"]["skills"] == ["rotate"]
    plugin.handle_plugin("remove demo", path=path)
    assert (tmp / "skills" / "tidy" / "SKILL.md").read_text() == "mine"
    assert not (tmp / "skills" / "rotate").exists()
