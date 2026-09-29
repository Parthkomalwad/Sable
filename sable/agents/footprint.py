"""Which paths a step touches (Phase 8, A8 K6): the input to a snapshot.

The step's declared `touches`, plus paths parsed from the command: existing
path arguments, redirection and `tee` targets, `cp`/`mv`/`install`
destinations, `touch`/`mkdir` targets. A path that does not exist yet is
widened to its topmost missing ancestor, so undo removes everything the step
created there (`mkdir -p a/b/c` in an empty dir records `a`). An empty set
means "unknown footprint": nothing to snapshot, and the caller says so.

Static and best effort: a `$(...)` or a script that writes elsewhere is not
seen. That is why the caller skips only commands blast.py calls read-only.
"""
from __future__ import annotations

import os
import shlex

from sable.policy.blast import _unwrap

_SEPARATORS = {"|", "||", "&&", ";", "&", ";;", "|&"}
# Programs whose last non-flag argument is a destination that may not exist.
_DEST_LAST = {"cp", "mv", "install", "ln", "rsync"}
# Programs whose every non-flag argument is a target that may not exist.
_CREATE = {"touch", "mkdir", "tee", "truncate"}


def paths_of(command: str, cwd: str, touches=None) -> set[str]:
    found: set[str] = set()
    for p in touches or ():
        found.add(_widen(_abs(str(p), cwd)))
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return found
    segment: list[str] = []
    for tok in tokens + [";"]:
        if tok in _SEPARATORS:
            found |= _segment(segment, cwd)
            segment = []
        else:
            segment.append(tok)
    return found


def _segment(tokens: list[str], cwd: str) -> set[str]:
    found: set[str] = set()
    words: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in (">", ">>", ">|", "&>", "&>>"):
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            if target and not target.startswith("&") and target != "/dev/null":
                found.add(_widen(_abs(target, cwd)))
            i += 2
            continue
        if tok in ("<", "<<", "<<<", ">&", "<&"):
            i += 2
            continue
        # `2>file` lexes as `2` `>` `file`: the descriptor is not an argument.
        if not (tok.isdigit() and i + 1 < len(tokens) and tokens[i + 1].startswith((">", "<"))):
            words.append(tok)
        i += 1
    words = _unwrap(words)
    if not words:
        return found
    prog = os.path.basename(words[0])
    args = [a for a in words[1:] if not a.startswith("-") or a == "-"]
    for a in args:
        p = _abs(a, cwd)
        if os.path.lexists(p):
            found.add(p)
    if prog in _CREATE:
        found |= {_widen(_abs(a, cwd)) for a in args if a != "-"}
    elif prog in _DEST_LAST and len(args) >= 2:
        dest = _abs(args[-1], cwd)
        if os.path.isdir(dest) and not os.path.islink(dest):
            # Into an existing dir: only the entries the step writes, not the
            # whole directory.
            found.discard(dest)
            found |= {_widen(os.path.join(dest, os.path.basename(_abs(s, cwd).rstrip("/"))))
                      for s in args[:-1]}
        else:
            found.add(_widen(dest))
    return found


def _abs(path: str, cwd: str) -> str:
    """Absolute and normalised, symlinks left alone: undo restores the link."""
    return os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))


def _widen(path: str) -> str:
    """The topmost missing ancestor of a missing path, else the path."""
    while not os.path.lexists(path):
        parent = os.path.dirname(path)
        if parent == path or os.path.lexists(parent):
            return path
        path = parent
    return path


def undo_point(command: str, cwd: str, *, touches=None, agent: str = "", session: str = "") -> int | None:
    """Snapshot what a step will touch; the snapshot id, or None when there
    is nothing to keep (read-only per blast.py, or an unknown footprint).

    A `tool:` call's footprint is its declared paths only. Raises
    snapshots.SnapshotError when the paths are over the size limit, so the
    caller can ask whether to run without an undo point.
    """
    from sable.core import snapshots
    from sable.policy import blast

    if blast.classify(command) is blast.Level.READ_ONLY:
        return None
    found = paths_of("" if command.startswith("tool:") else command, cwd, touches)
    if not found:
        return None
    return snapshots.take(found, command[:200], agent=agent, session=session)
