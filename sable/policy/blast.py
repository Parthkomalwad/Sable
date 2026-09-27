"""Blast radius (F3): how much a command can change, as a colour on its block.

Static first, per the Phase 3 decision: a colour must not add a network
round trip to every command.

1. A policy rule that matched decides it from its `category`.
2. Else a heuristic over each pipeline segment: known read-only programs are
   green, known writers and output redirects are amber.
3. Else `_model_level`, cached by command hash. Today that seam returns
   UNKNOWN and calls nothing; a model call goes there, and a failure in it
   must return UNKNOWN, never READ_ONLY.
"""
from __future__ import annotations

import hashlib
import re
import shlex
from enum import Enum

from sable.policy import rules
from sable.policy.tiers import Tier


class Level(Enum):
    READ_ONLY = "read-only"
    WRITES = "writes"
    DESTRUCTIVE = "destructive"
    UNKNOWN = "unknown"


COLOURS = {
    Level.READ_ONLY: "\033[38;5;114m",    # green
    Level.WRITES: "\033[38;5;214m",       # amber
    Level.DESTRUCTIVE: "\033[38;5;203m",  # red
    Level.UNKNOWN: "\033[2;37m",          # dim
}

# Every shipped category is destructive; an unknown one from a user's policy
# file is too, since a rule exists because someone thought it dangerous.
_CATEGORY = {
    "disk": Level.DESTRUCTIVE,
    "delete": Level.DESTRUCTIVE,
    "remote-code": Level.DESTRUCTIVE,
    "system-state": Level.DESTRUCTIVE,
}

_READ_ONLY = frozenset(
    "ls ll la du df cat less more head tail grep egrep fgrep rg wc sort uniq cut "
    "tr awk jq stat file which whereis type pwd whoami id groups hostname uname "
    "date uptime free ps top htop pgrep lsof ss netstat ip ifconfig ping dig "
    "nslookup host printenv echo printf tree diff cmp md5sum sha256sum "
    "journalctl dmesg lsblk blkid mount history column basename dirname "
    "realpath readlink nproc lscpu vmstat iostat w who last man help "
    "find sed".split()
)
_GIT_READ_ONLY = frozenset("status log diff show branch remote blame shortlog describe rev-parse ls-files".split())
_WRITERS = frozenset(
    "rm rmdir mv cp mkdir touch chmod chown chgrp ln tee truncate install "
    "tar zip unzip gzip gunzip apt apt-get dnf yum pip pip3 npm kill pkill "
    "killall systemctl service useradd userdel crontab docker git make".split()
)
_SEGMENTS = re.compile(r"\|\|?|&&|;")
# `>` or `>>` to anything but /dev/null or another descriptor.
_REDIRECT = re.compile(r"(?<![0-9&])>>?(?!&)\s*(?!/dev/null)\S")

_cache: dict[str, Level] = {}


def classify(command: str) -> Level:
    rule = rules.match(command)
    if rule is not None and rule.tier is not Tier.ALLOW:
        return _CATEGORY.get(rule.category, Level.DESTRUCTIVE)
    level = _static(command)
    if level is not Level.UNKNOWN or not command.strip():
        return level
    key = hashlib.sha256(command.encode()).hexdigest()
    if key not in _cache:
        _cache[key] = _model_level(command)
    return _cache[key]


def _static(command: str) -> Level:
    if not command.strip():
        return Level.UNKNOWN
    if _REDIRECT.search(command):
        return Level.WRITES
    levels = [_segment(s) for s in _SEGMENTS.split(command) if s.strip()]
    if Level.WRITES in levels:
        return Level.WRITES
    # `$(...)` or backticks run a command the segment split cannot see.
    if levels and all(lv is Level.READ_ONLY for lv in levels) and not (
            "$(" in command or "`" in command):
        return Level.READ_ONLY
    return Level.UNKNOWN


def _segment(segment: str) -> Level:
    try:
        words = shlex.split(segment)
    except ValueError:
        return Level.UNKNOWN
    words = _unwrap(words)
    if not words:
        return Level.UNKNOWN
    prog, args = words[0], words[1:]
    if prog == "git":
        return Level.READ_ONLY if args and args[0] in _GIT_READ_ONLY else Level.WRITES
    if prog == "find" and _FIND_WRITES & set(args):
        return Level.WRITES
    if prog == "sed" and _sed_writes(args):
        return Level.WRITES
    if prog == "sort" and any(a == "-o" or a.startswith(("-o", "--output")) for a in args):
        return Level.WRITES
    if prog in ("awk", "gawk", "mawk", "nawk") and any(
            "system(" in a or ">" in a or "|" in a for a in args):
        return Level.UNKNOWN   # can run or write anything; not provably read-only
    if prog in _READ_ONLY:
        return Level.READ_ONLY
    if prog in _WRITERS:
        return Level.WRITES
    return Level.UNKNOWN


_FIND_WRITES = frozenset("-delete -exec -execdir -ok -okdir -fprint -fprint0 -fprintf -fls".split())

# Programs that run another program, with their options that take a value.
# `timeout` also takes a positional duration before the program.
_WRAPPERS = {
    "sudo": {"-u", "-g", "-C", "-h", "-p"},
    "env": {"-u", "-C", "-S", "--unset", "--chdir"},
    "nice": {"-n", "--adjustment"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "nohup": set(),
    "time": {"-f", "-o"},
    "stdbuf": {"-i", "-o", "-e"},
    "xargs": {"-n", "-I", "-i", "-d", "-P", "-L", "-l", "-s", "-a", "-E", "-e"},
}


def _unwrap(words: list[str]) -> list[str]:
    """Strip wrappers, their options and VAR=x, leaving the real program."""
    while words:
        head = words[0]
        if re.match(r"^\w+=", head):
            words = words[1:]
            continue
        if head not in _WRAPPERS:
            return words
        takes_value = _WRAPPERS[head]
        words = words[1:]
        while words and words[0].startswith("-") and words[0] != "-":
            opt = words.pop(0)
            if opt == "--":
                break
            if opt in takes_value and words:
                words.pop(0)
        if head == "timeout" and words:
            words = words[1:]   # the duration
    return words


# sed's `w file` command / flag, and the `e` command / flag, both act outside
# stdout. A heuristic over the script text; a false WRITES is the safe error.
_SED_W = re.compile(r"(^|[\s;{}!0-9$/])w\s")
_SED_E = re.compile(r"(^|[\s;{}])e(\s|$|;)|/[gpiImM0-9]*e[gpiImM0-9w]*(\s|$|;|})")


def _sed_writes(args: list[str]) -> bool:
    if any(a.startswith(("-i", "--in-place")) for a in args):
        return True
    return any(_SED_W.search(a) or _SED_E.search(a) for a in args if not a.startswith("-"))


def _model_level(command: str) -> Level:
    """Seam for asking the summariser model about a command no rule knows.

    Not wired in this release: returns UNKNOWN without a call. When it is,
    an unreachable model must yield UNKNOWN, never READ_ONLY.
    """
    return Level.UNKNOWN


def tag(command: str) -> str:
    """The coloured `● level` marker shown on preview and confirm blocks."""
    level = classify(command)
    return f"{COLOURS[level]}● {level.value}\033[0m"
