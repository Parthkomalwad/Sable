"""`sable export`, `import` and `sync` (Phase 7 Task 5, I8).

The portable set is a fixed allowlist: the skills directory, the palace, the
user policy file and the hooks directory. Nothing else ever leaves or enters
through here: not the keyring, the database, logs, sessions, `state/`,
`config.json` or the sync work tree itself.

An archive is untrusted input. `import` validates every member before a
single byte is written: no absolute paths, no `..`, no links or devices,
only names under the allowed roots, and every file must match the sha256 in
`MANIFEST.json`. Extraction goes to a staging directory (with tarfile's
`data` filter where the interpreter has it), is hashed again there, and only
then copied into place, backing up anything it overwrites.

This module sits in `core`, below `policy` and `ui`, so the caller passes in
the secret matcher, the output function and the confirm prompt.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import socket
import subprocess
import tarfile
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

MANIFEST = "MANIFEST.json"
FORMAT_VERSION = 1

Say = Callable[[str], None]
Confirm = Callable[[str], bool]
SecretCheck = Callable[[str], object]


def roots() -> dict[str, Path]:
    """Archive top-level name -> local path. Read at call time so a changed
    HOME (tests, another user) is honoured."""
    home = Path.home()
    sable = home / ".sable"
    return {
        "skills": home / "skills",
        "palace": sable / "palace",
        "policy.toml": sable / "policy.toml",
        "hooks": sable / "hooks",
    }


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect(say: Say) -> dict[str, Path]:
    """Every regular file in the portable set, keyed by archive name."""
    found: dict[str, Path] = {}
    for top, root in roots().items():
        if root.is_symlink():
            say(f"skipped {top}: it is a symlink")
            continue
        if root.is_file():
            found[top] = root
            continue
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                say(f"skipped {top}/{rel}: symlink")
            elif path.is_file():
                found[f"{top}/{rel}"] = path
    return found


def export(dest: Path | None, say: Say, secret_check: SecretCheck) -> int:
    files = collect(say)
    flagged = []
    for name, path in files.items():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if secret_check(text):
            flagged.append(name)
    if flagged:
        say("refusing to export: these files contain what looks like a credential.")
        for name in flagged:
            say(f"  {name}")
        say("Move the secret to the keyring (/secret set) and reference it, then retry.")
        return 1

    host = socket.gethostname()
    now = datetime.now(timezone.utc)
    if dest is None:
        dest = Path.cwd() / f"sable-export-{host}-{now:%Y%m%d}.tar.gz"
    blobs = {name: path.read_bytes() for name, path in files.items()}
    manifest = {
        "version": FORMAT_VERSION,
        "host": host,
        "created": now.isoformat(),
        "files": [{"path": n, "sha256": _sha(b)} for n, b in blobs.items()],
    }
    with tarfile.open(dest, "w:gz") as tar:
        _add(tar, MANIFEST, json.dumps(manifest, indent=2).encode(), 0o644)
        for name, data in blobs.items():
            _add(tar, name, data, files[name].stat().st_mode & 0o755)
    say(f"exported {len(blobs)} files to {dest}")
    return 0


def _add(tar: tarfile.TarFile, name: str, data: bytes, mode: int) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    tar.addfile(info, io.BytesIO(data))


def _check_name(name: str) -> str | None:
    """Why `name` is not allowed in an archive, or None if it is."""
    if name.startswith(("/", "\\")) or ":" in name or "\\" in name:
        return "absolute or non-posix path"
    parts = PurePosixPath(name).parts
    if not parts or any(p in ("..", ".", "") for p in parts):
        return "path traversal"
    top = parts[0]
    if top not in roots():
        return "outside the portable set"
    if top == "policy.toml" and len(parts) != 1:
        return "outside the portable set"
    if top != "policy.toml" and len(parts) < 2:
        return "not a file in the portable set"
    return None


def read_archive(path: Path, say: Say) -> dict[str, bytes] | None:
    """Validate and stage an archive; the verified contents, or None."""
    try:
        tar = tarfile.open(path, "r:gz")
    except (OSError, tarfile.TarError) as exc:
        say(f"cannot read {path}: {exc}")
        return None
    with tar:
        members = tar.getmembers()
        problems = []
        names = set()
        for m in members:
            if not m.isfile():
                problems.append(f"{m.name}: not a regular file")
                continue
            if m.name in names:
                problems.append(f"{m.name}: duplicate entry")
            names.add(m.name)
            if m.name != MANIFEST:
                why = _check_name(m.name)
                if why:
                    problems.append(f"{m.name}: {why}")
        if MANIFEST not in names:
            problems.append(f"{MANIFEST} is missing")
        if problems:
            return _reject(say, problems)
        try:
            manifest = json.loads(tar.extractfile(MANIFEST).read())
            expected = {f["path"]: f["sha256"] for f in manifest["files"]}
        except (ValueError, KeyError, TypeError) as exc:
            return _reject(say, [f"{MANIFEST} unreadable: {exc}"])
        if set(expected) != names - {MANIFEST}:
            return _reject(say, ["the manifest and the archive list different files"])

        with tempfile.TemporaryDirectory() as staging:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(staging, members=members, filter="data")
            else:  # 3.11: our checks above are the only guard
                tar.extractall(staging, members=members)
            out: dict[str, bytes] = {}
            for name, digest in expected.items():
                data = (Path(staging) / name).read_bytes()
                if _sha(data) != digest:
                    problems.append(f"{name}: sha256 does not match the manifest")
                out[name] = data
    if problems:
        return _reject(say, problems)
    return out


def _reject(say: Say, problems: list[str]) -> None:
    say("refusing this archive, nothing was written:")
    for p in problems:
        say(f"  {p}")
    return None


def _dest(name: str) -> Path | None:
    parts = PurePosixPath(name).parts
    root = roots()[parts[0]]
    target = root.joinpath(*parts[1:])
    # An existing symlink anywhere on the way could redirect the write.
    probe = target
    while True:
        if probe.is_symlink():
            return None
        if probe == root or probe.parent == probe:
            break
        probe = probe.parent
    return target


def apply(files: dict[str, bytes], say: Say, confirm: Confirm, yes: bool,
          on_write: Callable[[], None] = lambda: None) -> int:
    """Show the plan, ask, back up what gets overwritten, then write."""
    add, overwrite, same = [], [], 0
    targets = {}
    for name, data in sorted(files.items()):
        target = _dest(name)
        if target is None:
            say(f"refusing: {name} would be written through a symlink")
            return 1
        targets[name] = target
        if not target.exists():
            add.append(name)
        elif target.read_bytes() != data:
            overwrite.append(name)
        else:
            same += 1
    if not add and not overwrite:
        say(f"nothing to change ({same} files already match)")
        return 0
    say(f"add {len(add)}, overwrite {len(overwrite)}, unchanged {same}")
    for name in overwrite:
        say(f"  overwrite {name}")
    if not yes and not confirm("apply? [y/N] "):
        say("cancelled, nothing written")
        return 1
    if overwrite:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        backup = Path.home() / ".sable" / f"import-backup-{stamp}"
        for name in overwrite:
            dst = backup / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(targets[name], dst)
        say(f"backed up {len(overwrite)} files to {backup}")
    for name in add + overwrite:
        target = targets[name]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(files[name])
        if name.startswith("hooks/"):
            target.chmod(0o755)
    on_write()  # the caller reindexes the palace (memory sits above core)
    say(f"wrote {len(add) + len(overwrite)} files")
    return 0


def import_archive(path: Path, say: Say, confirm: Confirm, yes: bool,
                   on_write: Callable[[], None] = lambda: None) -> int:
    files = read_archive(path, say)
    if files is None:
        return 1
    return apply(files, say, confirm, yes, on_write)


# --- sync -----------------------------------------------------------------

def _git(tree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(tree), *args],
                          capture_output=True, text=True, check=False)


def sync(remote: str, say: Say, confirm: Confirm, yes: bool,
         secret_check: SecretCheck,
         on_write: Callable[[], None] = lambda: None) -> int:
    if shutil.which("git") is None:
        say("sync needs git on PATH")
        return 1
    if remote.startswith("-"):
        say("that does not look like a git remote")
        return 1
    tree = Path.home() / ".sable" / "sync"
    if not (tree / ".git").is_dir():
        tree.mkdir(parents=True, exist_ok=True)
        for args in (("init", "-q", "-b", "main"), ("remote", "add", "origin", remote)):
            r = _git(tree, *args)
            if r.returncode:
                say(f"git {args[0]} failed: {r.stderr.strip()}")
                return 1
    else:
        _git(tree, "remote", "set-url", "origin", remote)
    if _git(tree, "config", "user.email").returncode:
        _git(tree, "config", "user.email", f"sable@{socket.gethostname()}")
        _git(tree, "config", "user.name", "sable")

    # Mirror the local set into the tree, refusing secrets as export does.
    files = collect(say)
    flagged = []
    for name, path in files.items():
        try:
            if secret_check(path.read_text(encoding="utf-8")):
                flagged.append(name)
        except (UnicodeDecodeError, OSError):
            pass
    if flagged:
        return _reject(say, [f"{n}: looks like a credential" for n in flagged]) or 1
    for top in roots():
        victim = tree / top
        if victim.is_dir():
            shutil.rmtree(victim)
        elif victim.exists():
            victim.unlink()
    for name, path in files.items():
        dst = tree / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
    _git(tree, "add", "-A", "--", *roots())
    if _git(tree, "diff", "--cached", "--quiet").returncode:
        r = _git(tree, "commit", "-q", "-m", f"sable sync from {socket.gethostname()}")
        if r.returncode:
            say(f"git commit failed: {r.stderr.strip()}")
            return 1

    if _git(tree, "ls-remote", "--exit-code", "--heads", "origin", "main").returncode == 0:
        r = _git(tree, "pull", "-q", "--rebase", "origin", "main")
        if r.returncode:
            _git(tree, "rebase", "--abort")
            say("sync stopped: pulling from the remote conflicts with local changes.")
            say(f"Resolve it by hand in {tree} (nothing was pushed). git said:")
            say(r.stderr.strip() or r.stdout.strip())
            return 1
    if _git(tree, "rev-parse", "--verify", "-q", "HEAD").returncode == 0:
        r = _git(tree, "push", "-q", "origin", "HEAD:main")
        if r.returncode:
            say(f"git push failed (never forced): {r.stderr.strip()}")
            return 1

    # Bring what was pulled back out, under the same rules as an import.
    pulled: dict[str, bytes] = {}
    problems = []
    for path in sorted(tree.rglob("*")):
        rel = path.relative_to(tree).as_posix()
        if rel == ".git" or rel.startswith(".git/") or path.is_dir():
            continue
        if path.is_symlink() or not path.is_file():
            problems.append(f"{rel}: not a regular file")
            continue
        why = _check_name(rel)
        if why:
            problems.append(f"{rel}: {why}")
            continue
        pulled[rel] = path.read_bytes()
    if problems:
        return _reject(say, problems) or 1
    say("pushed; applying what came from the remote")
    return apply(pulled, say, confirm, yes, on_write)
