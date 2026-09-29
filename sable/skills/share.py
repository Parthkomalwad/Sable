"""`/skill publish` and `/skill install` (Phase 9 Task 2, B7).

A published skill is a tar.gz of one skill folder plus `MANIFEST.json`
(name, version, source, created, per-file sha256, signature status).
Publishing refuses when the secret scanner flags any file.

Installing treats the archive as untrusted input and reuses the Phase 7
checks in `sable.core.portable.read_archive` (regular files only, no
absolute paths or `..`, size caps, every hash matching the manifest), with
the extra rule that everything sits under exactly one folder holding a
SKILL.md. Every file and the SKILL.md text are shown before asking. The
skill lands as `imported:<host or file name>`, pending approval, and
unsigned: an incoming `.sable-signature` is dropped and nothing is signed.
"""
from __future__ import annotations

import json
import re
import shutil
import tarfile
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import httpx

from sable.core import portable
from sable.skills.signing import SIGNATURE_FILE

Say = Callable[[str], None]
Confirm = Callable[[str], bool]

MAX_BYTES = 5 * 2**20
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MAX_REDIRECTS = 3


def _skills_root() -> Path:
    return Path.home() / "skills"


def publish(name: str, dest: Path | None, say: Say) -> int:
    from sable.policy.rules import match_secret
    from sable.skills import signing
    from sable.skills.model import SkillFormatError, parse_skill

    folder = _skills_root() / name
    if not _SLUG.match(name) or folder.is_symlink() or not (folder / "SKILL.md").is_file():
        say(f"no skill folder named '{name}'")
        return 1
    files = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            say(f"skipped {path.relative_to(folder).as_posix()}: symlink")
        elif path.is_file():
            files[f"{name}/{path.relative_to(folder).as_posix()}"] = path.read_bytes()
    flagged = []
    for arc, data in files.items():
        try:
            if match_secret(data.decode("utf-8")):
                flagged.append(arc)
        except UnicodeDecodeError:
            continue
    if flagged:
        say("refusing to publish: these files contain what looks like a credential.")
        for arc in flagged:
            say(f"  {arc}")
        say("Move the secret to the keyring (/secret set) and reference it, then retry.")
        return 1

    try:
        skill = parse_skill(files[f"{name}/SKILL.md"].decode("utf-8"), name=name)
        version, source = str(skill.extra.get("version", "1")), skill.source
    except (UnicodeDecodeError, SkillFormatError):
        version, source = "1", "user"
    manifest = {
        "name": name,
        "version": version,
        "source": source,
        "created": datetime.now(timezone.utc).isoformat(),
        "signature": signing.verify(folder),
        "files": [{"path": n, "sha256": portable.sha256(d)} for n, d in files.items()],
    }
    dest = dest or Path.cwd() / f"{name}.skill.tar.gz"
    with tarfile.open(dest, "w:gz") as tar:
        portable.add_member(tar, portable.MANIFEST, json.dumps(manifest, indent=2).encode(), 0o644)
        for arc, data in files.items():
            portable.add_member(tar, arc, data, 0o644)
    say(f"published {name} ({len(files)} files) to {dest}")
    return 0


def _check_name(name: str) -> str | None:
    why = portable.check_path(name)
    if why:
        return why
    parts = PurePosixPath(name).parts
    if len(parts) < 2:
        return "not inside a skill folder"
    if not _SLUG.match(parts[0]) or parts[0] == "instructions":
        return "not a valid skill name"
    return None


def _download(url: str, dest: Path, say: Say,
              transport: httpx.BaseTransport | None) -> bool:
    try:
        with httpx.Client(timeout=httpx.Timeout(30.0), follow_redirects=False,
                          transport=transport) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                if urlsplit(url).scheme != "https":
                    say(f"refusing {url}: only https URLs are fetched")
                    return False
                with client.stream("GET", url) as resp:
                    if resp.is_redirect:
                        url = str(resp.url.join(resp.headers.get("location", "")))
                        continue
                    if resp.status_code != 200:
                        say(f"download failed: HTTP {resp.status_code}")
                        return False
                    got = bytearray()
                    for chunk in resp.iter_bytes():
                        got += chunk
                        if len(got) > MAX_BYTES:
                            say(f"refusing: larger than {MAX_BYTES // 2**20} MB")
                            return False
                    dest.write_bytes(bytes(got))
                    return True
            say("download failed: too many redirects")
            return False
    except httpx.HTTPError as exc:
        say(f"download failed: {exc}")
        return False


def install(src: str, say: Say, confirm: Confirm, yes: bool,
            replace: bool = False,
            transport: httpx.BaseTransport | None = None) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        if "://" in src:
            origin = urlsplit(src).hostname or "unknown"
            path = Path(tmp) / "skill.tar.gz"
            if not _download(src, path, say, transport):
                return 1
        else:
            path = Path(src)
            origin = path.name
            if not path.is_file():
                say(f"no such file: {src}")
                return 1
            if path.stat().st_size > MAX_BYTES:
                say(f"refusing: larger than {MAX_BYTES // 2**20} MB")
                return 1
        files = portable.read_archive(path, say, _check_name, MAX_BYTES)
    if files is None:
        return 1
    tops = {PurePosixPath(n).parts[0] for n in files}
    if len(tops) != 1:
        say(f"refusing this archive: expected one skill folder, found {len(tops)}")
        return 1
    slug = tops.pop()
    if f"{slug}/SKILL.md" not in files:
        say(f"refusing this archive: {slug}/SKILL.md is missing")
        return 1
    files = {n: d for n, d in files.items() if PurePosixPath(n).name != SIGNATURE_FILE}

    target = _skills_root() / slug
    if target.is_symlink():
        say(f"refusing: {target} is a symlink")
        return 1
    if target.exists() and not replace:
        say(f"a skill named '{slug}' already exists; rerun with --replace "
            "to back it up and overwrite it")
        return 1

    say(f"skill {slug} from {origin}, {len(files)} files:")
    for n, d in sorted(files.items()):
        say(f"  {n}  ({len(d)} bytes)")
    say(f"--- {slug}/SKILL.md ---")
    say(files[f"{slug}/SKILL.md"].decode("utf-8", errors="replace"))
    say("---")
    if not yes and not confirm("install this skill? [y/N] "):
        say("cancelled, nothing written")
        return 1

    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        backup = Path.home() / ".sable" / f"skill-backup-{stamp}" / slug
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(backup))
        say(f"backed up the existing {slug} to {backup}")
    for n, d in files.items():
        dst = _skills_root().joinpath(*PurePosixPath(n).parts)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(d)

    from sable.skills.index import SkillIndex
    keywords = [w for w in re.split(r"[-_.]", slug) if w]
    SkillIndex().add(slug, str(target / "SKILL.md"), keywords, auto_generated=False,
                     status="pending", source=f"imported:{origin}", sign=False)
    say(f"installed {slug} as imported:{origin}, unsigned and pending; "
        f"read it, then /skill approve {slug}")
    return 0
