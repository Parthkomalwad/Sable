"""Rehearsal (Phase 8 Task 3, F2 K5): run a plan on a copy before it runs.

The union of every state-changing step's footprint (`footprint.paths_of`) is
copied to a temp dir, with the snapshot limits and refusals. The steps then
run in order inside bwrap: `--ro-bind / /`, a private /tmp, and each copy
bound over its real path, so commands see the usual paths and only the
copies can change. Then each step's exit code, the diff between copies and
originals, and `a apply  q abort`.

A path that does not exist yet is rehearsed through a copy of its parent,
since a read-only parent could not take the new entry.

Never a fallback to running for real: without bwrap, or when bwrap fails to
start, the result is `available=False` with the reason. Steps that act
outside the filesystem are "not rehearsable" and a state-changing step with
no footprint is "unknown footprint"; neither is run here, and both are shown.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

from sable.agents.footprint import paths_of
from sable.core import snapshots
from sable.policy import blast

MODES = ("auto", "always", "off")
DIFF_CAP = 4000
STEP_TIMEOUT = 300

# Programs whose effect is not on files: a copy cannot show or contain it.
_OUTSIDE = re.compile(
    r"(^|[\s;&|(`])(sudo\s+)?("
    r"systemctl|service|docker|podman|kubectl|curl|wget|kill|pkill|killall|"
    r"reboot|shutdown|poweroff|halt|"
    r"(apt|apt-get|dnf|yum|pip3?|npm|yarn)\s+(\S+\s+)*?(install|remove|uninstall|upgrade|update|purge)"
    r")\b")


class RehearsalError(Exception):
    """The footprint cannot be copied; the message says why."""


@dataclass
class StepResult:
    command: str
    status: str            # ok | failed | not rehearsed
    exit: int | None = None
    tail: str = ""
    note: str = ""         # why not rehearsed: "not rehearsable" | "unknown footprint"


@dataclass
class Rehearsal:
    available: bool
    reason: str = ""
    steps: list[StepResult] = field(default_factory=list)
    diff: str = ""
    changes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.available and not any(s.status == "failed" for s in self.steps)


def mode() -> str:
    """The `rehearse` config value, `auto` when unset or unreadable."""
    from sable.core.config.wizard import CONFIG_PATH
    try:
        data = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    except (OSError, ValueError):
        data = {}
    value = data.get("rehearse") or "auto"
    return value if value in MODES else "auto"


def changing(steps: list[str]) -> list[str]:
    return [s for s in steps if blast.classify(s) is not blast.Level.READ_ONLY]


def wanted(steps: list[str], how: str | None = None) -> bool:
    how = how or mode()
    if how == "off":
        return False
    n = len(changing(steps))
    return n >= 1 if how == "always" else n >= 2


def rehearsable(command: str) -> bool:
    return not _OUTSIDE.search(command)


def _contains(outer: str, inner: str) -> bool:
    try:
        return os.path.commonpath([outer, inner]) == outer
    except ValueError:   # different drives on Windows
        return False


def _footprint(steps: list[str], cwd: str) -> tuple[list[str], dict[int, str]]:
    """(paths to copy, {step index: why not rehearsed})."""
    found: set[str] = set()
    skip: dict[int, str] = {}
    for i, cmd in _with_cwd(steps, cwd):
        c, where = cmd
        if blast.classify(c) is blast.Level.READ_ONLY or _is_cd(c):
            continue
        if not rehearsable(c):
            skip[i] = "not rehearsable"
            continue
        paths = paths_of(c, where)
        if not paths:
            skip[i] = "unknown footprint"
            continue
        for p in paths:
            # A missing path is created in its parent, so the parent is copied.
            found.add(p if os.path.lexists(p) else os.path.dirname(p))
    # Keep only the outermost: a copy of /a already holds /a/b.
    kept = [p for p in sorted(found) if not any(q != p and _contains(q, p) for q in found)]
    return kept, skip


def _is_cd(command: str) -> bool:
    return command.strip() == "cd" or command.strip().startswith("cd ")


def _cd(command: str, cwd: str) -> str:
    target = command.strip()[2:].strip() or os.path.expanduser("~")
    return os.path.normpath(os.path.join(cwd, os.path.expandvars(os.path.expanduser(target))))


def _with_cwd(steps: list[str], cwd: str):
    """(index, (command, cwd it runs in)), following `cd` steps like the executor."""
    for i, c in enumerate(steps):
        yield i, (c, cwd)
        if _is_cd(c):
            cwd = _cd(c, cwd)


def _check(paths: list[str], base: str) -> None:
    """The snapshot refusals and size limit, before anything is copied."""
    store = os.path.normpath(os.path.abspath(snapshots.ROOT))
    total = 0
    for p in paths:
        if _contains(p, store) or _contains(store, p):
            raise RehearsalError(f"{p} contains Sable's own snapshots; not rehearsed")
        if _contains(p, base):
            raise RehearsalError(f"{p} contains the rehearsal's own temp dir; not rehearsed")
        if os.path.islink(p):
            raise RehearsalError(f"{p} is a symlink; a bind over it would reach its target")
        total += snapshots._size(p, snapshots.MAX_BYTES - total)
        if total > snapshots.MAX_BYTES:
            raise RehearsalError("these paths hold more than the 50 MB limit (or over "
                                 f"{snapshots.MAX_ENTRIES} files); not rehearsed")


def _copy(paths: list[str], tmp: str) -> list[tuple[str, str]]:
    """(real, copy) per path."""
    pairs = []
    for n, p in enumerate(paths):
        copy = os.path.join(tmp, str(n))
        if os.path.isdir(p):
            shutil.copytree(p, copy, symlinks=True)
        else:
            shutil.copy2(p, copy)
        pairs.append((p, copy))
    return pairs


def bwrap_argv(command: str, cwd: str, pairs: list[tuple[str, str]]) -> list[str]:
    """Read-only root, private /tmp, the cwd visible, then each copy over its real path.
    Later binds win in bwrap, so the order matters."""
    argv = ["bwrap", "--ro-bind", "/", "/", "--dev-bind", "/dev", "/dev",
            "--tmpfs", "/tmp", "--ro-bind", cwd, cwd]
    for real, copy in pairs:
        argv += ["--bind", copy, real]
    return argv + ["--unshare-pid", "--unshare-net", "--die-with-parent",
                   "--chdir", cwd, "--", "/bin/bash", "-c", command]


def _available() -> str:
    """'' when bwrap works, else why not."""
    if sys.platform == "win32":
        return "rehearsal needs bwrap (Linux); not available on this host"
    if shutil.which("bwrap") is None:
        return "rehearsal needs bwrap, which is not installed"
    from sable.core.health import bwrap_available
    if not bwrap_available():
        return "bwrap is installed but user namespaces do not work here"
    try:
        net = subprocess.run(["bwrap", "--ro-bind", "/", "/", "--unshare-net", "--", "true"],
                             capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        net = None
    if net is None or net.returncode != 0:
        return "bwrap cannot give the rehearsal a private network here (a container without NET_ADMIN?)"
    return ""


def rehearse(steps: list[str], cwd: str, *, run=subprocess.run, probe=_available) -> Rehearsal:
    cwd = os.path.abspath(cwd)
    why = probe()
    if why:
        return Rehearsal(False, why)
    paths, skip = _footprint(steps, cwd)
    tmp = tempfile.mkdtemp(prefix="sable-rehearse-")
    try:
        try:
            _check(paths, os.path.dirname(tmp))
            pairs = _copy(paths, tmp)
        except (RehearsalError, OSError, shutil.Error) as exc:
            return Rehearsal(False, str(exc))
        results: list[StepResult] = []
        for i, (cmd, where) in _with_cwd(steps, cwd):
            if i in skip:
                results.append(StepResult(cmd, "not rehearsed", note=skip[i]))
                continue
            if _is_cd(cmd):
                results.append(StepResult(cmd, "ok", 0))
                continue
            try:
                proc = run(bwrap_argv(cmd, where, pairs), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=STEP_TIMEOUT)
            except subprocess.TimeoutExpired:
                results.append(StepResult(cmd, "failed", None, f"timed out after {STEP_TIMEOUT}s"))
                break
            except OSError as exc:
                return Rehearsal(False, f"bwrap could not start: {exc}")
            output = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode != 0 and (proc.stderr or "").startswith("bwrap:"):
                # bwrap itself refused (a bind it cannot make): not the step's fault.
                return Rehearsal(False, f"bwrap could not start: {proc.stderr.strip()[:300]}")
            status = "ok" if proc.returncode == 0 else "failed"
            results.append(StepResult(cmd, status, proc.returncode, output[-800:]))
            if status == "failed":
                break
        diff, changes = _compare(pairs)
        return Rehearsal(True, "", results, diff, changes)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _compare(pairs: list[tuple[str, str]]) -> tuple[str, list[str]]:
    chunks: list[str] = []
    changes: list[str] = []
    for real, copy in pairs:
        if os.path.isdir(real):
            a = dict(snapshots._walk(real))
            b = dict(snapshots._walk(copy)) if os.path.isdir(copy) else {}
            items = [(f"{real}/{rel}", a.get(rel), b.get(rel)) for rel in sorted(set(a) | set(b))]
        else:
            items = [(real, real, copy if os.path.lexists(copy) else None)]
        for name, old, new in items:
            if new is None:
                changes.append(f"removed {name}")
            elif old is None:
                changes.append(f"added {name}")
                if os.path.isfile(new):
                    chunks.append(_diff(None, new, name))
            elif os.path.isfile(old) and os.path.isfile(new) and snapshots._sha(old) != snapshots._sha(new):
                changes.append(f"changed {name}")
                chunks.append(_diff(old, new, name))
    return "".join(chunks), changes


def _diff(old: str | None, new: str, name: str) -> str:
    a = snapshots._text(old) if old else []
    b = snapshots._text(new)
    if a is None or b is None:
        return f"binary {name} differs\n"
    return "".join(difflib.unified_diff(a, b, f"now:{name}", f"after:{name}"))


def render(r: Rehearsal) -> str:
    if not r.available:
        return f"  rehearsal unavailable: {r.reason}"
    lines = ["  rehearsal (on a copy; nothing real was changed)"]
    for s in r.steps:
        mark = {"ok": "ok", "failed": f"failed (exit {s.exit})"}.get(s.status, f"not rehearsed: {s.note}")
        lines.append(f"    {mark:<34} {s.command}")
        if s.status == "failed" and s.tail:
            lines += [f"      {t}" for t in s.tail.rstrip().splitlines()[-5:]]
    lines += [f"    {c}" for c in r.changes] or ["    no file changes"]
    if r.diff:
        d = r.diff if len(r.diff) <= DIFF_CAP else r.diff[:DIFF_CAP] + f"\n... ({len(r.diff) - DIFF_CAP} more chars)\n"
        lines.append(d.rstrip())
    return "\n".join(lines)


def review(r: Rehearsal, ask=None) -> bool:
    """Show the rehearsal and ask `a apply  q abort`. True means apply."""
    from rich.console import Console
    from rich.text import Text
    Console(highlight=False).print(Text(render(r)))
    try:
        return (ask or input)("  a apply  q abort:  ").strip().lower() == "a"
    except (EOFError, KeyboardInterrupt):
        return False
