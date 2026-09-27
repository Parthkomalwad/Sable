"""docs.* tools (J7): man pages, --help, tldr and package metadata.

These are internal lookups, not user commands, so they run as argv lists
through `subprocess.run` with a timeout rather than on a pty: nothing here is
interactive and no shell ever sees the arguments. Every name is checked
against `_NAME` before anything runs. Output is local documentation, so none
of these taint (plan section 0.3), except `docs.pkg` answered by npm, which
is remote registry metadata. A missing binary or a failed lookup is
`ok=False` with a message the model can read; it is not an exception.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

from sable.core.health import bwrap_available
from sable.policy.tiers import Tier
from sable.tools.base import Tool, ToolContext, ToolResult
from sable.tools.registry import register

TIMEOUT = 5
CAP = 16_000
# A plain program or package name: no paths, spaces, shell metacharacters,
# and no leading "-" or "." so it cannot be read as an option or "..".
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_SECTION = re.compile(r"^[0-9][a-z]*$")
_OVERSTRIKE = re.compile(r".\x08")


def _fail(msg: str) -> ToolResult:
    return ToolResult(ok=False, output=msg)


def _cap(text: str) -> str:
    if len(text) <= CAP:
        return text
    return text[:CAP] + f"\n[truncated: {len(text) - CAP} more chars]"


def _run(argv: list[str], env: dict | None = None) -> subprocess.CompletedProcess | str:
    """The completed process, or an error string for the model."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT,
                              env=env, stdin=subprocess.DEVNULL, errors="replace")
    except subprocess.TimeoutExpired:
        return f"{os.path.basename(argv[0])} timed out after {TIMEOUT}s"
    except OSError as exc:
        return f"could not run {argv[0]}: {exc}"


def _bad_name(name: str) -> str | None:
    if not _NAME.match(name):
        return f"{name!r} is not a plain program or package name"
    return None


def man(args: dict, ctx: ToolContext) -> ToolResult:
    cmd, section = args["cmd"], args.get("section")
    if err := _bad_name(cmd):
        return _fail(err)
    if section is not None and not _SECTION.match(section):
        return _fail(f"{section!r} is not a man section")
    man_bin = shutil.which("man")
    if man_bin is None:
        return _fail("man is not installed")
    argv = [man_bin, "-P", "cat"] + ([section] if section else []) + [cmd]
    res = _run(argv, env={**os.environ, "MANWIDTH": "80", "MAN_KEEP_FORMATTING": ""})
    if isinstance(res, str):
        return _fail(res)
    if res.returncode != 0 or not res.stdout.strip():
        return _fail(f"no man page for {cmd}")
    return ToolResult(ok=True, output=_cap(_OVERSTRIKE.sub("", res.stdout)))


def help_(args: dict, ctx: ToolContext) -> ToolResult:
    cmd = args["cmd"]
    if err := _bad_name(cmd):
        return _fail(err)
    path = shutil.which(cmd)
    if path is None:
        return _fail(f"{cmd}: program not found on PATH")
    # Only --help: "-h" is not always help (`shutdown -h` halts the machine).
    if bwrap_available():
        # Read-only root, no network, and an empty /run so a --help that
        # misbehaves cannot reach D-Bus, systemd or docker.sock.
        prefix = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                  "--tmpfs", "/tmp", "--tmpfs", "/run"]
        if not os.path.islink("/var/run"):
            prefix += ["--tmpfs", "/var/run"]
        prefix += ["--clearenv", "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
                   "--setenv", "HOME", "/tmp", "--setenv", "LANG", "C.UTF-8",
                   "--unshare-net", "--unshare-pid", "--die-with-parent", "--"]
    elif ctx.role == "worker":
        return _fail("docs.help is refused for workers without bwrap: it would run "
                     "unattended and unsandboxed; try docs.man")
    else:
        # The orchestrator's tool calls are previewed, so unsandboxed is acceptable.
        prefix = []
    res = _run(prefix + [path, "--help"])
    if isinstance(res, str):
        return _fail(res)
    text = (res.stdout or res.stderr).strip()
    if res.returncode == 0 and text:
        return ToolResult(ok=True, output=_cap(text))
    return _fail(_cap(text) or f"{cmd} printed no help")


def tldr(args: dict, ctx: ToolContext) -> ToolResult:
    cmd = args["cmd"]
    if err := _bad_name(cmd):
        return _fail(err)
    tldr_bin = shutil.which("tldr")
    if tldr_bin is None:
        return _fail("tldr is not installed; try docs.man or docs.help")
    res = _run([tldr_bin, cmd])
    if isinstance(res, str):
        return _fail(res)
    if res.returncode != 0:
        return _fail(f"no tldr page for {cmd}")
    return ToolResult(ok=True, output=_cap(res.stdout))


_PKG = [("apt-cache", ["show"]), ("pip", ["show"]), ("npm", ["view", "--json"])]


def pkg(args: dict, ctx: ToolContext) -> ToolResult:
    name = args["name"]
    if err := _bad_name(name):
        return _fail(err)
    for tool, sub in _PKG:
        binary = shutil.which(tool)
        if binary is None:
            continue
        res = _run([binary, *sub, name])
        if not isinstance(res, str) and res.returncode == 0 and res.stdout.strip():
            # npm view fetches registry metadata anyone can author: untrusted.
            return ToolResult(ok=True, output=_cap(f"[{tool}]\n{res.stdout}"),
                              taints=tool == "npm")
    return _fail(f"no package {name!r} found via apt-cache, pip or npm")


for _tool in (
    Tool("docs.man", "the man page for a program, as plain text",
         {"cmd": "string", "section?": "string"}, Tier.ALLOW, man),
    Tool("docs.help", "a program's --help output, run sandboxed with no network",
         {"cmd": "string"}, Tier.ALLOW, help_),
    Tool("docs.tldr", "the tldr page for a program, if tldr is installed",
         {"cmd": "string"}, Tier.ALLOW, tldr),
    Tool("docs.pkg", "package metadata from apt-cache, pip or npm",
         {"name": "string"}, Tier.ALLOW, pkg),
):
    register(_tool)
