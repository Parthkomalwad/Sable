"""Snapshots and undo (Phase 8 Task 2, A8 K6): plain copies, no root, no git.

`take(paths)` copies each path into `~/.sable/snapshots/<id>/` beside a
`manifest.json` (path, kind, mode, sha256, existed). A path that does not
exist yet is recorded as `existed: false`, so `restore` removes what the step
created there. `restore(id)` puts every recorded path back byte for byte and
mode for mode, and inside a recorded directory removes entries the step
added. It touches no path outside the manifest, and checks every entry
(absolute normalised path, copy hashes) before it changes anything.

Not kept: owner and group (restoring them needs root), timestamps beyond what
`copy2` keeps, hard-link identity, extended attributes.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import stat
import time

from sable.core import paths

ROOT = paths.SABLE_HOME / "snapshots"
MAX_BYTES = 50 * 1024 * 1024
RING = 20


class SnapshotError(Exception):
    """A snapshot that cannot be taken or restored; the message says why."""


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _mode(path: str) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def _walk(top: str):
    """(relpath, full path) of every entry under a directory, links not followed."""
    for folder, dirs, files in os.walk(top):
        for name in dirs + files:
            full = os.path.join(folder, name)
            yield os.path.relpath(full, top).replace(os.sep, "/"), full


MAX_ENTRIES = 20_000


def _size(path: str, budget: int = MAX_BYTES) -> int:
    """Bytes under `path`, but stop as soon as `budget` is passed: a command
    like `rm -rf /` must not make Sable walk the whole disk before refusing."""
    if os.path.islink(path):
        return 0
    if not os.path.isdir(path):
        return os.path.getsize(path)
    total = 0
    for n, (_, f) in enumerate(_walk(path)):
        if n > MAX_ENTRIES:
            return budget + 1
        if os.path.isfile(f) and not os.path.islink(f):
            total += os.lstat(f).st_size
            if total > budget:
                return total
    return total


def _ids() -> list[int]:
    if not ROOT.is_dir():
        return []
    return sorted(int(n) for n in os.listdir(ROOT) if n.isdigit())


def take(paths_: set[str] | list[str], label: str = "", *, agent: str = "", session: str = "") -> int:
    """Copy every path; returns the snapshot id. Raises SnapshotError over 50 MB."""
    wanted = sorted({os.path.normpath(os.path.abspath(p)) for p in paths_})
    store = os.path.normpath(os.path.abspath(ROOT))
    for p in wanted:
        # A path holding the store (`rm -rf ~`) would copy the store into
        # itself forever; one inside it is Sable's own history.
        try:
            nested = os.path.commonpath([p, store]) in (p, store)
        except ValueError:   # different drives on Windows
            nested = False
        if nested:
            raise SnapshotError(f"{p} contains Sable's own snapshots; no undo point taken")
    total = 0
    for p in wanted:
        if os.path.lexists(p):
            total += _size(p, MAX_BYTES - total)
        if total > MAX_BYTES:
            raise SnapshotError("these paths hold more than the 50 MB snapshot limit (or over "
                                f"{MAX_ENTRIES} files); no undo point taken")
    ROOT.mkdir(parents=True, exist_ok=True)
    while True:   # mkdir is the lock: a worker and the shell may race here
        sid = (_ids() or [0])[-1] + 1
        folder = ROOT / str(sid)
        try:
            folder.mkdir()
            break
        except FileExistsError:
            continue
    entries = []
    for n, p in enumerate(wanted):
        copy = str(folder / str(n))
        entry = {"path": p, "copy": str(n), "existed": os.path.lexists(p)}
        if not entry["existed"]:
            entry["kind"] = "missing"
        elif os.path.islink(p):
            entry.update(kind="link", target=os.readlink(p))
        elif os.path.isdir(p):
            shutil.copytree(p, copy, symlinks=True)
            entry.update(kind="dir", mode=_mode(p), files={
                rel: (_sha(f) if os.path.isfile(f) and not os.path.islink(f) else None)
                for rel, f in _walk(p)})
        else:
            shutil.copy2(p, copy)
            entry.update(kind="file", mode=_mode(p), sha256=_sha(copy))
        entries.append(entry)
    manifest = {"id": sid, "label": label, "agent": agent, "session": session,
                "time": time.time(), "entries": entries}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    for old in _ids()[:-RING]:
        shutil.rmtree(ROOT / str(old), ignore_errors=True)
    return sid


def manifest(sid: int) -> dict:
    try:
        return json.loads((ROOT / str(int(sid)) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SnapshotError(f"no snapshot s{sid}: {exc}") from exc


def list() -> list[dict]:  # noqa: A001  (the task's name for it)
    """Every snapshot's manifest, newest first."""
    out = []
    for sid in reversed(_ids()):
        try:
            out.append(manifest(sid))
        except SnapshotError:
            continue
    return out


def latest(*, agent: str = "", session: str = "") -> dict | None:
    for m in list():
        if (not agent or m.get("agent") == agent) and (not session or m.get("session") == session):
            return m
    return None


def _check(sid: int, m: dict) -> None:
    """Refuse a manifest that points anywhere it should not, before any write."""
    folder = os.path.realpath(ROOT / str(int(sid)))
    for e in m.get("entries", []):
        p = e.get("path", "")
        if not isinstance(p, str) or not os.path.isabs(p) or os.path.normpath(p) != p or ".." in p.split(os.sep):
            raise SnapshotError(f"s{sid}: manifest path {p!r} is not a clean absolute path; refused")
        if e.get("kind") not in ("missing", "link", "file", "dir"):
            raise SnapshotError(f"s{sid}: unknown kind for {p}; refused")
        if e["kind"] in ("file", "dir"):
            copy = os.path.realpath(os.path.join(folder, str(e.get("copy", ""))))
            if os.path.dirname(copy) != folder:
                raise SnapshotError(f"s{sid}: copy for {p} is outside the snapshot; refused")
            if e["kind"] == "file" and (not os.path.isfile(copy) or _sha(copy) != e.get("sha256")):
                raise SnapshotError(f"s{sid}: the saved copy of {p} does not match its hash; refused")
            if e["kind"] == "dir":
                for rel, digest in (e.get("files") or {}).items():
                    parts = rel.split("/")
                    if rel.startswith("/") or ".." in parts:
                        raise SnapshotError(f"s{sid}: entry {rel!r} under {p} escapes it; refused")
                    f = os.path.join(copy, *parts)
                    if digest is not None and (not os.path.isfile(f) or _sha(f) != digest):
                        raise SnapshotError(f"s{sid}: the saved copy of {p}/{rel} does not match its hash; refused")


def _remove(path: str) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.remove(path)


def _put_file(src: str, dst: str, mode: int) -> None:
    """Copy through a temp file in the same dir, then replace: never half-written."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.isdir(dst) and not os.path.islink(dst):
        shutil.rmtree(dst)
    tmp = f"{dst}.sable-undo-{os.getpid()}"
    shutil.copy2(src, tmp)
    os.chmod(tmp, mode)
    if os.name == "nt" and os.path.isfile(dst):
        os.chmod(dst, stat.S_IREAD | stat.S_IWRITE)   # Windows cannot replace a read-only file
    os.replace(tmp, dst)


def restore(sid: int) -> list[str]:
    """Put every recorded path back. Returns one line per path."""
    m = manifest(sid)
    _check(sid, m)
    folder = str(ROOT / str(int(sid)))
    report = []
    for e in m["entries"]:
        p, kind = e["path"], e["kind"]
        copy = os.path.join(folder, str(e.get("copy", "")))
        if kind == "missing":
            if os.path.lexists(p):
                _remove(p)
                report.append(f"removed {p} (created by the step)")
            else:
                report.append(f"unchanged {p} (still absent)")
        elif kind == "link":
            if os.path.lexists(p):
                _remove(p)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            os.symlink(e["target"], p)
            report.append(f"restored link {p} -> {e['target']}")
        elif kind == "file":
            _put_file(copy, p, e["mode"])
            report.append(f"restored {p}")
        else:
            report.append(_restore_dir(copy, p, e))
    return report


def _restore_dir(copy: str, p: str, e: dict) -> str:
    if os.path.lexists(p) and (os.path.islink(p) or not os.path.isdir(p)):
        os.remove(p)
    os.makedirs(p, exist_ok=True)
    saved = set(e.get("files") or {})
    # Remove what the step added, deepest first. Never follows a symlink out.
    removed = 0
    for folder, dirs, files in os.walk(p, topdown=False):
        for name in files + dirs:
            full = os.path.join(folder, name)
            rel = os.path.relpath(full, p).replace(os.sep, "/")
            if rel not in saved:
                _remove(full)
                removed += 1
    for rel, _ in sorted(_walk(copy)):
        src, dst = os.path.join(copy, *rel.split("/")), os.path.join(p, *rel.split("/"))
        if os.path.islink(src):
            if os.path.lexists(dst):
                _remove(dst)
            os.symlink(os.readlink(src), dst)
        elif os.path.isdir(src):
            if os.path.lexists(dst) and (os.path.islink(dst) or not os.path.isdir(dst)):
                os.remove(dst)
            os.makedirs(dst, exist_ok=True)
        else:
            _put_file(src, dst, _mode(src))
    # Directory modes last, deepest first, so a read-only dir does not block
    # the writes above.
    for rel, _ in sorted(_walk(copy), reverse=True):
        src = os.path.join(copy, *rel.split("/"))
        if os.path.isdir(src) and not os.path.islink(src):
            os.chmod(os.path.join(p, *rel.split("/")), _mode(src))
    os.chmod(p, e["mode"])
    return f"restored {p}/ ({removed} added entries removed)"


def _text(path: str) -> list[str] | None:
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace").splitlines(keepends=True)


def _file_diff(old: str | None, new: str | None, name: str) -> str:
    """old/new are file paths or None for absent."""
    a = _text(old) if old else []
    b = _text(new) if new and os.path.isfile(new) else []
    if a is None or b is None:
        same = old and new and os.path.isfile(new) and _sha(old) == _sha(new)
        return "" if same else f"binary {name} differs\n"
    return "".join(difflib.unified_diff(a, b, f"s:{name}", f"now:{name}"))


def diff(sid: int) -> str:
    """Unified diff from the snapshot to what is on disk now."""
    m = manifest(sid)
    folder = str(ROOT / str(int(sid)))
    chunks = []
    for e in m.get("entries", []):
        p, kind = e["path"], e.get("kind")
        copy = os.path.join(folder, str(e.get("copy", "")))
        if kind == "missing":
            if os.path.lexists(p):
                chunks.append(f"created {p}\n")
        elif kind == "link":
            now = os.readlink(p) if os.path.islink(p) else None
            if now != e.get("target"):
                chunks.append(f"link {p}: {e.get('target')} -> {now}\n")
        elif kind == "file":
            if not os.path.lexists(p):
                chunks.append(f"deleted {p}\n")
            else:
                chunks.append(_file_diff(copy, p, p))
                if _mode(p) != e["mode"]:
                    chunks.append(f"mode {p}: {oct(e['mode'])} -> {oct(_mode(p))}\n")
        else:
            saved = set(e.get("files") or {})
            now = {rel for rel, _ in _walk(p)} if os.path.isdir(p) else set()
            for rel in sorted(saved | now):
                full, old = os.path.join(p, *rel.split("/")), os.path.join(copy, *rel.split("/"))
                name = f"{p}/{rel}"
                if rel not in saved:
                    chunks.append(f"created {name}\n")
                elif rel not in now:
                    chunks.append(f"deleted {name}\n")
                elif os.path.isfile(old) and not os.path.islink(old):
                    chunks.append(_file_diff(old, full, name))
    return "".join(chunks)
