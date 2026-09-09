"""Input router classifies each line as bash or agentic (NL).

Two modes:
- prefix mode: lines starting with '>>' route to LLM; everything else to bash.
- auto-detect mode: heuristic scoring on bash signals vs NL signals.

Design notes (I3, router accuracy programme):

Routing a real shell command to the LLM is the failure users notice most, so
bash signals are weighted to win outright wherever the line is unambiguously
shell: a pipe, a redirect, an env assignment, a path invocation, a flag, or a
known command name.

`shutil.which()` is a supporting signal, never a decisive one. Whether `make`
or `cargo` happens to be installed is a property of the machine, not of the
user's intent, and an earlier version that leaned on it dropped bash recall to
0.79 on a box without those tools. COMMON_COMMANDS carries that knowledge
instead, so classification is stable across machines.

This module must have no side effects and be independently unit-testable.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from enum import Enum


class Route(Enum):
    BASH = "bash"
    AGENTIC = "agentic"
    AMBIGUOUS = "ambiguous"


# Strong shell syntax: a pipe, redirect, substitution, chain or terminator.
SHELL_SYNTAX = re.compile(r'[|><`]|\$\(|\&\&|\|\||;')
# A flag like -l, --force. Requires a preceding space so "wi-fi" does not match.
FLAG = re.compile(r'(?:^|\s)--?[a-zA-Z][\w-]*')
# VAR=value at the start of a line, the env-assignment prefix form.
ENV_ASSIGNMENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
# ./x, ../x, /usr/bin/x, ~/bin/x
PATH_INVOCATION = re.compile(r'^(?:\./|\.\./|/|~/)\S*')
# A bare word that looks like a file with an extension, e.g. run.sh, main.py
FILE_WITH_EXTENSION = re.compile(r'^\S+\.[A-Za-z0-9]{1,5}$')
# $VAR or ${VAR}
VARIABLE = re.compile(r'\$\{?[A-Za-z_]')
# A heredoc marker: <<EOF, <<'PY', <<-SQL
HEREDOC = re.compile(r'<<-?\s*[\'"]?[A-Za-z_]')

QUESTION_WORDS = {
    'what', 'how', 'why', 'where', 'when', 'which', 'who', 'whose', 'whom',
}
ARTICLES = {'the', 'a', 'an'}
# Pronouns and prose function words that rarely appear in a shell command.
PROSE_WORDS = {
    'me', 'my', 'i', 'you', 'your', 'it', 'its', 'this', 'that', 'these',
    'those', 'is', 'are', 'was', 'were', 'be', 'been', 'am', 'do', 'does',
    'did', 'can', 'could', 'should', 'would', 'will', 'shall', 'may', 'might',
    'must', 'have', 'has', 'had', 'of', 'for', 'to', 'from', 'with', 'without',
    'about', 'into', 'onto', 'over', 'under', 'and', 'or', 'but', 'if', 'then',
    'than', 'so', 'because', 'every', 'all', 'any', 'some', 'much', 'many',
    'more', 'most', 'less', 'least', 'up', 'out', 'off', 'again', 'still',
    'just', 'now', 'here', 'there', 'please', 'not',
}
# Phrases that only appear when the user is talking to an assistant.
INSTRUCTIONAL_PHRASES = [
    'show me', 'find all', 'find every', 'list all', 'list every', 'give me',
    'check if', 'check whether', 'how do', 'how can', 'can you', 'could you',
    'help me', 'tell me', 'i need', 'i want', 'i forgot', 'figure out',
    'work out', 'sort it out', 'set up', 'clean up', 'free up', 'look for',
    'look through', 'go through', 'walk through', 'search the', 'summarise',
    'summarize', 'explain', 'diagnose', 'troubleshoot', 'investigate',
    'do not know', "don't know",
]

# Shell builtins that are never in PATH but must always route to bash.
SHELL_BUILTINS = {
    'cd', 'export', 'source', 'alias', 'unalias', 'exit', 'eval',
    'set', 'unset', 'exec', 'type', 'read', 'echo', 'printf',
    'pushd', 'popd', 'dirs', 'jobs', 'fg', 'bg', 'wait', 'kill',
    'history', 'fc', 'umask', 'ulimit', 'true', 'false', 'let',
    'local', 'return', 'shift', 'trap', 'times', 'logout', 'deactivate',
}

# Command names that are unambiguously shell whether or not they are installed
# here. Keeps routing stable across machines; see the module docstring.
COMMON_COMMANDS = {
    # coreutils and friends
    'ls', 'll', 'la', 'pwd', 'whoami', 'id', 'date', 'uptime', 'hostname',
    'uname', 'df', 'du', 'free', 'top', 'htop', 'btop', 'ps', 'killall',
    'pkill', 'clear', 'reset', 'env', 'printenv', 'which', 'command', 'file',
    'stat', 'basename', 'dirname', 'readlink', 'realpath', 'sleep', 'seq',
    'yes', 'cat', 'tac', 'tail', 'less', 'more', 'wc', 'sort', 'uniq',
    'cut', 'tr', 'rev', 'nl', 'split', 'paste', 'join', 'column', 'tee',
    'xargs', 'nohup', 'watch', 'time', 'timeout', 'nice', 'renice', 'touch',
    'mkdir', 'rmdir', 'cp', 'mv', 'rm', 'ln', 'chmod', 'chown', 'chgrp',
    'find', 'grep', 'egrep', 'fgrep', 'rg', 'ag', 'sed', 'awk', 'diff', 'cmp',
    'patch', 'tar', 'zip', 'unzip', 'gzip', 'gunzip', 'bzip2', 'xz',
    'md5sum', 'sha256sum', 'base64', 'xxd', 'od', 'strings', 'mount', 'umount',
    'lsblk', 'blkid', 'fdisk', 'dd', 'sync', 'lsof', 'strace', 'ltrace',
    # editors, multiplexers, scheduling
    'vim', 'vi', 'nvim', 'nano', 'emacs', 'tmux', 'screen', 'crontab', 'at',
    'sudo', 'su', 'ssh', 'scp', 'sftp', 'rsync',
    # network
    'curl', 'wget', 'ping', 'dig', 'nslookup', 'host', 'traceroute',
    'netstat', 'ss', 'ip', 'ifconfig', 'nc', 'telnet', 'openssl', 'iptables',
    # service management
    'systemctl', 'service', 'journalctl', 'dmesg', 'initctl',
    # vcs and build
    'git', 'svn', 'hg', 'make', 'cmake', 'ninja', 'gradle', 'mvn', 'ant',
    'bazel', 'gcc', 'g++', 'clang', 'ld',
    # languages and package managers
    'python', 'python3', 'pip', 'pip3', 'pytest', 'poetry', 'pipenv', 'uv',
    'node', 'npm', 'npx', 'yarn', 'pnpm', 'deno', 'bun',
    'ruby', 'gem', 'bundle', 'rake', 'perl', 'php', 'composer',
    'go', 'cargo', 'rustc', 'rustup', 'java', 'javac', 'dotnet',
    'apt', 'apt-get', 'dpkg', 'dnf', 'yum', 'rpm', 'pacman', 'zypper',
    'brew', 'snap', 'flatpak', 'nix',
    # containers, infra, data
    'docker', 'podman', 'kubectl', 'helm', 'minikube', 'terraform', 'ansible',
    'vagrant', 'packer', 'aws', 'gcloud', 'az',
    'psql', 'mysql', 'mongo', 'redis-cli', 'sqlite3',
    'jq', 'yq', 'ansible', 'ansible-playbook', 'kubeadm', 'kustomize',
    # shells and runners
    'bash', 'sh', 'zsh', 'fish', 'dash', 'ksh',
}


# Subcommands and argv keywords. A line like "git push origin main" is three
# English-looking words, but "push" makes it argv rather than prose.
SUBCOMMANDS = {
    # git
    'am', 'bisect', 'blame', 'branch', 'checkout', 'cherry-pick',
    'clone', 'commit', 'config', 'diff', 'fetch', 'init',
    'merge', 'pull', 'push', 'rebase', 'reflog', 'remote', 'reset', 'restore',
    'revert', 'stash', 'status', 'submodule', 'switch', 'tag',
    'worktree', 'origin', 'upstream', 'head',
    # docker, kubectl, compose
    'compose', 'build', 'images', 'inspect', 'network', 'prune',
    'ps', 'pull', 'rm', 'rmi', 'stats',
    'system', 'volume', 'exec', 'apply', 'delete', 'describe', 'get',
    'rollout', 'scale', 'port-forward',
    # package managers and build tools
    'install', 'uninstall', 'upgrade', 'list',
    'audit', 'ci', 'publish', 'add', 'clean', 'tidy', 'vendor',
    'migrate', 'runserver', 'collectstatic', 'plan', 'destroy', 'validate',
    # systemctl
    'enable', 'disable', 'reload', 'daemon-reload', 'mask', 'unmask',
}


@dataclass
class RouteExplanation:
    """Why the router decided what it did. Rendered by `/route why`."""

    line: str
    route: Route
    bash_score: int = 0
    nl_score: int = 0
    reasons: list[tuple[str, str, int]] = field(default_factory=list)
    decisive: str = ""

    def add(self, side: str, reason: str, points: int) -> None:
        self.reasons.append((side, reason, points))
        if side == "bash":
            self.bash_score += points
        elif side == "nl":
            self.nl_score += points


def _first_word(stripped: str) -> str:
    return stripped.split()[0].lower() if stripped.split() else ""


def _looks_like_command_name(word: str) -> bool:
    """True if the word is a plausible command token rather than English.

    Command names do not contain spaces, rarely exceed 20 characters, and are
    built from letters, digits, dash, underscore, dot or slash.
    """
    return bool(word) and len(word) <= 20 and re.fullmatch(r'[\w./+-]+', word) is not None


def _is_prose_tail(words: list[str]) -> bool:
    """True if the words after the first are three or more plain English words.

    Real arguments look like arguments: a flag, a path, a filename with an
    extension, a number, or a known subcommand. Three or more bare English
    words in a row is prose, so the line is a goal rather than a command.
    """
    if len(words) < 3:
        return False
    tail = [w.strip('.,?!') for w in words[1:]]
    wordy = [
        w for w in tail
        if w.isalpha()
        and not w.startswith('-')
        and len(w) > 1
        and w.lower() not in SUBCOMMANDS
    ]
    return len(wordy) >= 3 and len(wordy) == len(tail)


def explain(line: str, mode: str = "auto") -> RouteExplanation:
    """Classify a line and return the score breakdown behind the decision."""
    stripped = line.strip()
    exp = RouteExplanation(line=stripped, route=Route.BASH)

    if not stripped:
        exp.decisive = "empty input routes to bash"
        return exp

    if mode == "prefix":
        if stripped.startswith(">>"):
            exp.route = Route.AGENTIC
            exp.decisive = "prefix mode: line starts with >>"
        else:
            exp.decisive = "prefix mode: no >> prefix"
        return exp

    words = stripped.split()
    lowered = stripped.lower()
    first_word = words[0].lower()

    # --- decisive bash signals: shape that only a shell line has ---
    if ENV_ASSIGNMENT.match(stripped):
        exp.route = Route.BASH
        exp.decisive = "starts with a VAR=value env assignment"
        exp.add("bash", exp.decisive, 0)
        return exp

    if PATH_INVOCATION.match(first_word):
        exp.route = Route.BASH
        exp.decisive = f"invokes a path directly ({words[0]})"
        exp.add("bash", exp.decisive, 0)
        return exp

    # A builtin is decisive only when the rest of the line looks like argv.
    # "kill -9 1234" is a command; "kill the nginx process" is a goal that
    # happens to start with a builtin's name.
    if first_word in SHELL_BUILTINS and not _is_prose_tail(words):
        exp.route = Route.BASH
        exp.decisive = f"'{first_word}' is a shell builtin"
        exp.add("bash", exp.decisive, 0)
        return exp

    if HEREDOC.search(stripped):
        exp.route = Route.BASH
        exp.decisive = "contains a heredoc marker"
        exp.add("bash", exp.decisive, 0)
        return exp

    # --- weighted signals ---
    known_command = first_word in COMMON_COMMANDS
    on_path = not known_command and _looks_like_command_name(first_word) and bool(
        shutil.which(first_word)
    )

    if known_command:
        exp.add("bash", f"'{first_word}' is a known command", 3)
    elif on_path:
        exp.add("bash", f"'{first_word}' is on PATH", 2)

    if SHELL_SYNTAX.search(stripped):
        exp.add("bash", "contains shell syntax (pipe, redirect, chain or substitution)", 3)
    if FLAG.search(stripped):
        exp.add("bash", "contains a command flag", 2)
    if VARIABLE.search(stripped):
        exp.add("bash", "references a shell variable", 2)
    if FILE_WITH_EXTENSION.match(stripped):
        exp.add("bash", "is a bare filename with an extension", 2)

    word_set = {w.lower().strip('.,?!') for w in words}
    question_hits = word_set & QUESTION_WORDS
    if question_hits:
        exp.add("nl", f"uses a question word ({', '.join(sorted(question_hits))})", 3)

    if stripped.endswith("?"):
        exp.add("nl", "ends with a question mark", 3)

    article_hits = word_set & ARTICLES
    if article_hits:
        exp.add("nl", f"contains an article ({', '.join(sorted(article_hits))})", 2)

    prose_hits = word_set & PROSE_WORDS
    if prose_hits:
        points = 2 if len(prose_hits) == 1 else 3
        exp.add("nl", f"contains prose words ({', '.join(sorted(prose_hits))})", points)

    for phrase in INSTRUCTIONAL_PHRASES:
        if phrase in lowered:
            exp.add("nl", f"contains the instructional phrase '{phrase}'", 3)
            break

    # Long lines of mostly-English words are goals, not commands.
    if len(words) >= 5 and not SHELL_SYNTAX.search(stripped) and not FLAG.search(stripped):
        exp.add("nl", f"is {len(words)} words of prose with no shell syntax", 2)

    # A real command name followed by bare English is a goal that happens to
    # open with a verb: "kill the nginx process", "find large log files".
    # Real arguments look like arguments: a flag, a path, a filename with an
    # extension, a number, or anything non-alphabetic. Three or more plain
    # English words in a row after the command name is prose, not an argv.
    if (known_command or on_path or first_word in SHELL_BUILTINS) and _is_prose_tail(words):
        exp.add(
            "nl",
            f"'{first_word}' is followed by plain English rather than arguments",
            4,
        )

    if not _looks_like_command_name(first_word):
        exp.add("nl", f"'{words[0]}' is not a plausible command name", 2)

    # Several plain English words and nothing that looks like shell: no command
    # name, no syntax, no flags, no paths. "list big files" is a request.
    if (
        not known_command
        and not on_path
        and len(words) >= 3
        and all(w.strip('.,?!').isalpha() for w in words)
        and not SHELL_SYNTAX.search(stripped)
        and not FLAG.search(stripped)
    ):
        exp.add("nl", "is plain English with no command, flags or shell syntax", 2)

    # --- decide ---
    if exp.bash_score > exp.nl_score:
        exp.route = Route.BASH
        exp.decisive = f"bash score {exp.bash_score} beats NL score {exp.nl_score}"
    elif exp.nl_score > exp.bash_score:
        exp.route = Route.AGENTIC
        exp.decisive = f"NL score {exp.nl_score} beats bash score {exp.bash_score}"
    elif known_command or on_path:
        exp.route = Route.BASH
        exp.decisive = f"scores tie at {exp.bash_score}; '{first_word}' is a real command so bash wins"
    else:
        exp.route = Route.AMBIGUOUS
        exp.decisive = f"scores tie at {exp.bash_score} with no recognised command"

    return exp


def classify(line: str, mode: str = "auto") -> Route:
    """Classify a line of input as BASH, AGENTIC, or AMBIGUOUS.

    Args:
        line: Raw user input string.
        mode: "prefix" or "auto".

    Returns:
        Route enum value.
    """
    return explain(line, mode).route
