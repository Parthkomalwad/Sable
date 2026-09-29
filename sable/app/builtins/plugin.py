"""`/plugin add|list|remove` (Phase 9 Task 3, H2).

A plugin is a folder with `plugin.toml`:

    [plugin]  name, version, description
    [server]  command = [...]  or  url = "https://..."; optional env / headers
              (`$SECRET:name` allowed, resolved as for /mcp)
    [tools]   trusted = ["tool_a"]   a request: each one is asked about separately

plus optional `skills/<name>/SKILL.md` and `hooks/<event>` next to it. No Python
from the plugin is imported. The server is registered through sable.mcp.servers
under the plugin's name, so its tools start as shell-only previews like any
/mcp server. Skills arrive as `imported:plugin:<name>`, unsigned. Hooks are
shown and confirmed one by one and never overwrite an existing hook. What was
installed is recorded under `plugins` in config.json, and `/plugin remove`
takes back exactly that.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import stat
import tomllib
from pathlib import Path
from typing import Callable

from sable.ui.console import out as _out

_USAGE = "usage: /plugin add <folder> | /plugin list | /plugin remove <name>"


def _ask(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


# --- config --------------------------------------------------------------

def _load(path) -> dict:
    from sable.mcp import servers
    p = servers._path(path)
    return json.loads(p.read_text()) if p.exists() else {}


def _save_plugins(plugins: dict, path) -> None:
    from sable.mcp import servers
    p = servers._path(path)
    data = _load(path)
    data["plugins"] = plugins
    p.write_text(json.dumps(data, indent=2))
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)


def _plugins(path) -> dict:
    got = _load(path).get("plugins")
    return dict(got) if isinstance(got, dict) else {}


# --- the manifest --------------------------------------------------------

def _str_map(v, what: str) -> dict:
    if v is None:
        return {}
    if not isinstance(v, dict) or not all(isinstance(x, str) for x in v.values()):
        raise ValueError(f"[server] {what} must be a table of strings")
    return v


def parse(folder: Path) -> dict:
    """The validated manifest as {name, version, description, server, trusted}.

    Raises ValueError with a message that says what to fix.
    """
    from sable.mcp import servers

    f = folder / "plugin.toml"
    try:
        raw = tomllib.loads(f.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {f}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"plugin.toml is not valid TOML: {exc}") from exc
    meta, srv, tools = raw.get("plugin"), raw.get("server"), raw.get("tools", {})
    if not isinstance(meta, dict):
        raise ValueError("plugin.toml needs a [plugin] table")
    name, version = meta.get("name"), meta.get("version")
    if not isinstance(name, str) or not servers.SERVER_NAME.match(name):
        raise ValueError("[plugin] name must be letters, digits, - or _")
    if not isinstance(version, str) or not version:
        raise ValueError("[plugin] version must be a non-empty string")
    if not isinstance(srv, dict):
        raise ValueError("plugin.toml needs a [server] table")
    cmd, url = srv.get("command"), srv.get("url")
    if bool(cmd) == bool(url):
        raise ValueError("[server] needs exactly one of command = [...] or url = \"https://...\"")
    if cmd is not None:
        if not isinstance(cmd, list) or not all(isinstance(c, str) for c in cmd):
            raise ValueError("[server] command must be a list of strings")
        spec = {"command": cmd, "env": _str_map(srv.get("env"), "env")}
    else:
        if not isinstance(url, str):
            raise ValueError("[server] url must be a string")
        problem = servers.check_url(url)
        if problem:
            raise ValueError(f"[server] url: {problem}")
        spec = {"url": url, "headers": _str_map(srv.get("headers"), "headers")}
    spec = {k: v for k, v in spec.items() if v}
    trusted = tools.get("trusted", []) if isinstance(tools, dict) else None
    if not isinstance(trusted, list) or not all(isinstance(t, str) for t in trusted):
        raise ValueError("[tools] trusted must be a list of tool names")
    return {"name": name, "version": version, "description": str(meta.get("description", "")),
            "server": spec, "trusted": trusted}


def _entries(folder: Path, sub: str, want_dir: bool) -> list[Path]:
    """Plain (non-symlink) children of folder/sub: skill folders or hook files."""
    d = folder / sub
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if not p.is_symlink()
                  and (p.is_dir() and (p / "SKILL.md").is_file() if want_dir else p.is_file()))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# --- commands ------------------------------------------------------------

def _add(folder: Path, path, confirm: Callable[[str], bool]) -> None:
    from sable.mcp import servers
    from sable.policy import hooks
    from sable.skills.index import SkillIndex
    from sable.skills.signing import SIGNATURE_FILE

    try:
        m = parse(folder)
    except ValueError as exc:
        _out(f"/plugin: {exc}")
        return
    name = m["name"]
    cfg = servers.load_config(path)
    if name in cfg["servers"] or name in _plugins(path):
        _out(f"/plugin: {name!r} is already installed (or an MCP server has that name)")
        return
    skills, hook_files = _entries(folder, "skills", True), _entries(folder, "hooks", False)

    try:
        client = servers.connect(name, m["server"])
    except (servers.McpError, OSError) as exc:
        _out(f"/plugin: could not connect to {name}'s server: {exc}")
        return
    tool_names = [str(t.get("name", "")) for t in client.list_tools()]
    wanted = [t for t in m["trusted"] if t in tool_names]

    _out(f"plugin {name} {m['version']}: {m['description']}")
    _out(f"  server: {m['server'].get('url') or ' '.join(m['server']['command'])}")
    _out(f"  tools ({len(tool_names)}): {', '.join(tool_names) or 'none'} (preview until trusted)")
    if m["trusted"]:
        _out(f"  asks to trust: {', '.join(m['trusted'])}")
        missing = sorted(set(m["trusted"]) - set(wanted))
        if missing:
            _out(f"    not offered by the server, ignored: {', '.join(missing)}")
    _out(f"  skills: {', '.join(s.name for s in skills) or 'none'} (imported, unsigned)")
    _out(f"  hooks: {', '.join(h.name for h in hook_files) or 'none'} (each shown and asked about)")
    if not confirm(f"install plugin {name}? [y/N] "):
        client.close()
        _out("cancelled, nothing installed")
        return

    # Trust is a separate yes per tool: the plugin can only ask.
    trusted = [servers.tool_key(name, t) for t in wanted
               if confirm(f"  trust mcp.{servers.tool_key(name, t)} (workers may call it)? [y/N] ")]
    cfg["servers"][name] = m["server"]
    cfg["trusted"] = [k for k in cfg["trusted"] if k not in trusted] + trusted
    servers.register_server(name, client, cfg["trusted"])
    servers.save_config(cfg, path)

    installed_skills = []
    root = Path.home() / "skills"
    index = SkillIndex()
    for s in skills:
        dst = root / s.name
        if dst.exists():
            _out(f"  skill {s.name} exists already, left alone")
            continue
        # Links are skipped, never followed: a link to ~/.ssh/id_rsa in a
        # plugin's skill folder must not copy the key into ~/skills.
        shutil.copytree(s, dst, symlinks=True,
                        ignore=lambda d, names: [n for n in names if os.path.islink(os.path.join(d, n))])
        index.add(s.name, str(dst / "SKILL.md"), [s.name], auto_generated=False,
                  status="pending", source=f"imported:plugin:{name}", sign=False)
        _out(f"  skill {s.name} installed as pending: /skill show {s.name}, then /skill approve {s.name}")
        (dst / SIGNATURE_FILE).unlink(missing_ok=True)  # imported: never signed
        installed_skills.append(s.name)

    installed_hooks = {}
    for h in hook_files:
        dst = hooks.HOOKS_DIR / h.name
        if dst.exists():
            _out(f"  hook {h.name} exists already, not overwritten")
            continue
        _out(f"  --- hook {h.name} ---")
        _out(h.read_text(encoding="utf-8", errors="replace"))
        _out("  ---")
        if not confirm(f"  install hook {h.name}? [y/N] "):
            continue
        hooks.HOOKS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(h, dst)
        dst.chmod(0o755)
        installed_hooks[h.name] = _sha(dst)

    plugins = _plugins(path)
    plugins[name] = {"version": m["version"], "folder": str(folder.resolve()),
                     "skills": installed_skills, "hooks": sorted(installed_hooks),
                     "hook_sha256": installed_hooks, "trusted": trusted}
    _save_plugins(plugins, path)
    _out(f"installed {name}: {len(tool_names)} tools, {len(trusted)} trusted, "
         f"{len(installed_skills)} skills, {len(installed_hooks)} hooks")


def _list(path) -> None:
    plugins = _plugins(path)
    if not plugins:
        _out("no plugins; add one with /plugin add <folder>")
        return
    for name, rec in plugins.items():
        _out(f"{name} {rec.get('version', '?')}  {rec.get('folder', '')}")
        _out(f"  skills: {', '.join(rec.get('skills', [])) or 'none'}  "
             f"hooks: {', '.join(rec.get('hooks', [])) or 'none'}  "
             f"trusted: {', '.join(rec.get('trusted', [])) or 'none'}")


def _remove(name: str, path) -> None:
    from sable.mcp import servers
    from sable.policy import hooks
    from sable.skills.index import SkillIndex

    plugins = _plugins(path)
    rec = plugins.pop(name, None)
    if rec is None:
        _out(f"no plugin {name!r}")
        return
    cfg = servers.load_config(path)
    cfg["servers"].pop(name, None)
    cfg["trusted"] = [k for k in cfg["trusted"] if not k.startswith(name + ".")]
    servers.save_config(cfg, path)
    servers.unregister_server(name)

    index = SkillIndex()
    for s in rec.get("skills", []):
        shutil.rmtree(Path.home() / "skills" / s, ignore_errors=True)
        index.remove(s)
    for h, sha in rec.get("hook_sha256", {}).items():
        p = hooks.HOOKS_DIR / h
        if p.is_file() and _sha(p) == sha:
            p.unlink()
        elif p.exists():
            _out(f"  hook {h} was changed since install, left in place")
    _save_plugins(plugins, path)
    _out(f"removed plugin {name}")


def handle_plugin(argument: str, path=None, confirm: Callable[[str], bool] = _ask) -> bool:
    try:
        parts = shlex.split(argument, posix=os.name != "nt") if os.name != "nt" else \
            [p.strip('"') for p in shlex.split(argument, posix=False)]
    except ValueError as exc:
        _out(f"/plugin: {exc}")
        return True
    sub, args = (parts[0], parts[1:]) if parts else ("list", [])
    try:
        if sub == "add" and len(args) == 1:
            _add(Path(args[0]).expanduser(), path, confirm)
        elif sub == "list" and not args:
            _list(path)
        elif sub == "remove" and len(args) == 1:
            _remove(args[0], path)
        else:
            _out(_USAGE)
    except (OSError, ValueError) as exc:
        _out(f"/plugin: {exc}")
    return True
