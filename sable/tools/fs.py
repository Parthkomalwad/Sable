"""Structured file tools (J3): read, write, patch, search, tree.

Every path is resolved with `realpath` against `ctx.cwd` (the orchestrator's
cwd, a worker's workspace), so `..`, an absolute path or a symlink that lands
outside it is refused. One exception: the orchestrator may `fs.read` a file
outside its cwd, and the result taints it, the same as `cat` of an outside
file in `policy/taint.py`. A worker is refused outright.

Writes never leave a half-written file: the new content goes to a temp file
in the same directory, then `os.replace`. A patch that does not apply is a
ToolError and the file is untouched.
"""
from __future__ import annotations

import difflib
import os
import re
import tempfile
from pathlib import Path

from sable.policy.tiers import Tier
from sable.tools.base import Tool, ToolContext, ToolError, ToolResult
from sable.tools.registry import register

MAX_FILE_BYTES = 2_000_000     # fs.read / fs.patch refuse bigger files
MAX_OUTPUT_BYTES = 100_000     # any tool's output is cut here
MAX_RESULTS = 200              # fs.search
MAX_ENTRIES = 500              # fs.tree
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv"}

GREEN, RED, CYAN, RESET = "\033[38;5;114m", "\033[38;5;203m", "\033[36m", "\033[0m"


def _root(ctx: ToolContext) -> str:
    return os.path.realpath(ctx.cwd)


def _inside(real: str, root: str) -> bool:
    try:
        return os.path.commonpath([real, root]) == root
    except ValueError:   # different drives on Windows
        return False


# A live run (Phase 7 gate) showed a model retrying fs tools on /etc until it
# gave up: say what works instead.
OUTSIDE_HINT = ("fs tools only work inside the working directory; for system paths such as "
                "/etc or /var/log, run a shell command instead, e.g. cat /etc/app.conf or ls /etc/app")


def _resolve(path: str, ctx: ToolContext, *, read: bool = False) -> tuple[str, bool]:
    """(real path, outside root). Raises ToolError when outside is not allowed."""
    root = _root(ctx)
    real = os.path.realpath(os.path.join(root, os.path.expanduser(path)))
    if _inside(real, root):
        return real, False
    if read and ctx.role == "orchestrator":
        return real, True
    raise ToolError(f"{path!r} resolves outside {root}; refused. {OUTSIDE_HINT}")


def _cap(text: str) -> str:
    data = text.encode()
    if len(data) <= MAX_OUTPUT_BYTES:
        return text
    return data[:MAX_OUTPUT_BYTES].decode(errors="ignore") + f"\n[truncated at {MAX_OUTPUT_BYTES} bytes]"


def _load(real: str) -> str:
    if not os.path.isfile(real):
        raise ToolError(f"{real} is not a file")
    if os.path.getsize(real) > MAX_FILE_BYTES:
        raise ToolError(f"{real} is over {MAX_FILE_BYTES} bytes")
    with open(real, encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def _atomic_write(real: str, content: str) -> None:
    folder = os.path.dirname(real)
    if not os.path.isdir(folder):
        raise ToolError(f"directory {folder} does not exist")
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".sable-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        if os.path.exists(real):
            os.chmod(tmp, os.stat(real).st_mode & 0o7777)
        os.replace(tmp, real)
    except OSError as exc:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise ToolError(f"write failed: {exc}") from exc


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


def apply_patch(old: str, diff: str) -> str:
    """Apply a unified diff to `old`. Context and removed lines must match
    exactly; a hunk may sit at a different line than its header says (models
    miscount), but never before the previous hunk. Raises ToolError."""
    lines = old.splitlines(keepends=True)
    key = [ln.rstrip("\r\n") for ln in lines]
    eol = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    diff_lines = diff.splitlines()
    out: list[str] = []
    pos = hunks = 0
    i = 0
    while i < len(diff_lines):
        m = _HUNK.match(diff_lines[i])
        i += 1
        if not m:
            continue
        hunks += 1
        before: list[str] = []
        after: list[str] = []
        while i < len(diff_lines) and not diff_lines[i].startswith("@@"):
            ln = diff_lines[i]
            tag, body = (ln[:1] or " "), ln[1:]
            if tag == "\\":        # "\ No newline at end of file"
                i += 1
                continue
            if tag not in " -+":
                break              # the next file's header, or trailing junk
            if tag in " -":
                before.append(body)
            if tag in " +":
                after.append(body)
            i += 1
        start = int(m.group(1)) - (0 if m.group(2) == "0" else 1)
        n = len(before)
        candidates = [start] + [s for s in range(pos, len(key) - n + 1) if s != start]
        at = next((s for s in candidates if s >= pos and key[s:s + n] == before), None)
        if at is None:
            raise ToolError(f"hunk {hunks} does not apply: its context does not match the file")
        out += lines[pos:at]
        out += [a + eol for a in after]
        pos = at + n
        # Keep a missing final newline missing if the hunk ran to the end.
        if pos == len(lines) and lines and not lines[-1].endswith("\n") and out:
            out[-1] = out[-1].rstrip("\r\n")
    if not hunks:
        raise ToolError("no @@ hunks in diff")
    return "".join(out + lines[pos:])


def _diff(path: str, old: str, new: str) -> str:
    rows = []
    for ln in difflib.unified_diff(old.splitlines(), new.splitlines(),
                                   f"a/{path}", f"b/{path}", lineterm=""):
        colour = (CYAN if ln.startswith("@@") else GREEN if ln.startswith("+")
                  else RED if ln.startswith("-") else "")
        rows.append(f"{colour}{ln}{RESET}" if colour else ln)
    return "\n".join(rows) or "(no change)"


def _current(real: str) -> str:
    return _load(real) if os.path.exists(real) else ""


# ---------------------------------------------------------------- tools

def _read(args: dict, ctx: ToolContext) -> ToolResult:
    real, outside = _resolve(args["path"], ctx, read=True)
    lines = _load(real).splitlines(keepends=True)
    start = max(args.get("start", 1), 1)
    end = args.get("end", len(lines))
    return ToolResult(ok=True, output=_cap("".join(lines[start - 1:end])), taints=outside)


def _write(args: dict, ctx: ToolContext) -> ToolResult:
    real, _ = _resolve(args["path"], ctx)
    _atomic_write(real, args["content"])
    return ToolResult(ok=True, output=f"wrote {len(args['content'].encode())} bytes to {args['path']}")


def _preview_write(args: dict, ctx: ToolContext) -> str:
    real, _ = _resolve(args["path"], ctx)
    return _diff(args["path"], _current(real), args["content"])


def _patch(args: dict, ctx: ToolContext) -> ToolResult:
    real, _ = _resolve(args["path"], ctx)
    new = apply_patch(_load(real), args["diff"])
    _atomic_write(real, new)
    return ToolResult(ok=True, output=f"patched {args['path']}")


def _preview_patch(args: dict, ctx: ToolContext) -> str:
    real, _ = _resolve(args["path"], ctx)
    old = _load(real)
    return _diff(args["path"], old, apply_patch(old, args["diff"]))


def _search(args: dict, ctx: ToolContext) -> ToolResult:
    root = _root(ctx)
    try:
        regex = re.compile(args["regex"]) if args.get("regex") else None
    except re.error as exc:
        raise ToolError(f"bad regex: {exc}") from exc
    hits: list[str] = []
    try:
        if os.path.isabs(args["glob"]) or args["glob"].startswith("~"):
            raise ToolError(f"the glob must be relative to the working directory. {OUTSIDE_HINT}")
        matches = Path(root).glob(args["glob"])
        for p in matches:
            real = os.path.realpath(p)
            if not _inside(real, root) or not p.is_file() or _SKIP_DIRS & set(p.parts):
                continue
            rel = os.path.relpath(p, root)
            if regex is None:
                hits.append(rel)
            elif os.path.getsize(real) <= MAX_FILE_BYTES:
                with open(real, encoding="utf-8", errors="replace") as fh:
                    hits += [f"{rel}:{n}: {ln.rstrip()}" for n, ln in enumerate(fh, 1) if regex.search(ln)]
            if len(hits) >= MAX_RESULTS:
                return ToolResult(ok=True, output=_cap("\n".join(hits[:MAX_RESULTS])
                                                       + f"\n[stopped at {MAX_RESULTS} results]"))
    except (ValueError, NotImplementedError, OSError) as exc:
        raise ToolError(f"search failed: {exc}") from exc
    return ToolResult(ok=True, output=_cap("\n".join(hits)) or "(no matches)")


def _tree(args: dict, ctx: ToolContext) -> ToolResult:
    real, _ = _resolve(args.get("path", "."), ctx)
    if not os.path.isdir(real):
        raise ToolError(f"{args.get('path', '.')} is not a directory")
    depth = max(args.get("depth", 2), 1)
    rows: list[str] = []

    def walk(folder: str, level: int) -> None:
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return
        for name in names:
            if len(rows) >= MAX_ENTRIES:
                return
            full = os.path.join(folder, name)
            is_dir = os.path.isdir(full) and not os.path.islink(full)
            rows.append("  " * level + name + ("/" if is_dir else ""))
            if is_dir and name not in _SKIP_DIRS and level + 1 < depth:
                walk(full, level + 1)

    walk(real, 0)
    if len(rows) >= MAX_ENTRIES:
        rows.append(f"[stopped at {MAX_ENTRIES} entries]")
    return ToolResult(ok=True, output="\n".join(rows) or "(empty)")


register(Tool(
    name="fs.read", tier=Tier.ALLOW, run=_read,
    description="read a text file, optionally lines start..end (1-based, inclusive)",
    schema={"path": "string", "start?": "integer", "end?": "integer"},
))
register(Tool(
    name="fs.write", tier=Tier.CONFIRM, run=_write, preview=_preview_write,
    description="create or replace a file with content; prefer fs.patch for edits",
    schema={"path": "string", "content": "string"},
))
register(Tool(
    name="fs.patch", tier=Tier.CONFIRM, run=_patch, preview=_preview_patch,
    description="apply a unified diff (@@ hunks, exact context) to one file",
    schema={"path": "string", "diff": "string"},
))
register(Tool(
    name="fs.search", tier=Tier.ALLOW, run=_search,
    description=f"files matching a glob (e.g. **/*.py), or lines matching regex in them; max {MAX_RESULTS}",
    schema={"glob": "string", "regex?": "string"},
))
register(Tool(
    name="fs.tree", tier=Tier.ALLOW, run=_tree,
    description=f"directory listing to depth (default 2); max {MAX_ENTRIES} entries",
    schema={"path?": "string", "depth?": "integer"},
))
