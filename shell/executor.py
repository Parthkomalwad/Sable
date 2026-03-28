"""Bash executor.

All commands run inside a ptyprocess so interactive programs (vim, htop, ssh)
work correctly. cd is intercepted and handled via os.chdir() — never subprocess.
Simple file-view commands (cat, head, tail of a single file) are intercepted and
rendered with Rich syntax highlighting for a better reading experience.

Never use print() here; use Rich Console for all output.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from ptyprocess import PtyProcessUnicode
from rich.console import Console
from rich.syntax import Syntax
from rich.panel import Panel

console = Console(highlight=False)

# Commands we intercept for Rich rendering: cat/head/tail with a single plain filepath
_VIEW_RE = re.compile(r'^(cat|head|tail)\s+(-n\s*\d+\s+)?([^\s|&;<>]+)$')

# Commands we intercept for Rich ls rendering.
# Allowed flags: l(ong) a(ll) h(uman) A(lmost-all) F(classify) s(size) 1(one-per-line).
# -R (recursive) and --color are intentionally excluded — fall through to pty.
_LS_RE = re.compile(r'^ls(\s+(-[lahAFs1]+))?\s*([^\s|&;<>]*)$')

# Track previous directory for 'cd -' command
_prev_cwd: str = ""


def _rich_cat(filepath: str, command: str) -> tuple[int, str]:
    """Render a file with syntax highlighting inside a panel."""
    path = Path(filepath).expanduser()
    if not path.exists():
        # Fall through to normal pty execution so error message is accurate
        return _pty_exec(command, os.getcwd())
    try:
        content = path.read_text(errors="replace")
    except (PermissionError, OSError):
        return _pty_exec(command, os.getcwd())

    # Detect language from extension for syntax highlighting
    suffix = path.suffix.lstrip(".").lower()
    lang_map = {
        "py": "python", "js": "javascript", "ts": "typescript",
        "sh": "bash", "bash": "bash", "zsh": "bash",
        "json": "json", "yaml": "yaml", "yml": "yaml",
        "toml": "toml", "md": "markdown", "html": "html",
        "css": "css", "sql": "sql", "go": "go", "rs": "rust",
        "c": "c", "cpp": "cpp", "h": "c", "java": "java",
        "xml": "xml", "ini": "ini", "cfg": "ini", "conf": "ini",
        "dockerfile": "dockerfile", "tf": "hcl",
    }
    lang = lang_map.get(suffix, "text")
    # Special case: files named Dockerfile, Makefile, etc.
    if path.name in ("Dockerfile", "Makefile", "Vagrantfile"):
        lang = path.name.lower()

    syn = Syntax(
        content, lang,
        theme="monokai",
        line_numbers=True,
        word_wrap=False,
    )
    title = f"[color(238)]{filepath}[/color(238)]"
    console.print(Panel(syn, title=title, border_style="color(55)", padding=(0, 1)))
    sys.stdout.flush()
    return 0, content


def _fmt_size(n: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.0f}P"


def _rich_ls(path: str, flags: str) -> tuple[int, str]:
    """Render directory listing with Rich columns."""
    import stat as _stat
    from rich.columns import Columns
    from rich.text import Text

    target = path or os.getcwd()
    target = os.path.expanduser(os.path.expandvars(target))

    try:
        entries = list(os.scandir(target))
    except PermissionError:
        sys.stdout.write(f"ls: {target}: Permission denied\n")
        sys.stdout.flush()
        return 1, ""
    except FileNotFoundError:
        sys.stdout.write(f"ls: {target}: No such file or directory\n")
        sys.stdout.flush()
        return 1, ""

    show_hidden = 'a' in flags or 'A' in flags
    if not show_hidden:
        entries = [e for e in entries if not e.name.startswith('.')]

    entries.sort(key=lambda e: e.name.lower())

    use_long = 'l' in flags

    if use_long:
        from rich.table import Table
        table = Table(show_header=False, box=None, padding=(0, 1), show_edge=False)
        table.add_column("perms", style="color(238)", no_wrap=True)
        table.add_column("size", justify="right", style="color(238)", no_wrap=True)
        table.add_column("name", no_wrap=True)

        for entry in entries:
            try:
                st = entry.stat(follow_symlinks=False)
                mode = _stat.filemode(st.st_mode)
                size = _fmt_size(st.st_size) if 'h' in flags else str(st.st_size)
            except OSError:
                mode, size = "?---------", "?"

            if entry.is_dir(follow_symlinks=False):
                name_text = Text(entry.name + "/", style="color(75) bold")
            elif entry.is_symlink():
                name_text = Text(entry.name, style="color(141)")
            elif entry.is_file() and os.access(entry.path, os.X_OK):
                name_text = Text(entry.name + "*", style="color(114) bold")
            else:
                name_text = Text(entry.name, style="color(253)")

            table.add_row(mode, size, name_text)

        console.print(table)
    else:
        from rich.columns import Columns
        from rich.text import Text
        items = []
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                items.append(Text(entry.name + "/", style="color(75) bold"))
            elif entry.is_symlink():
                items.append(Text(entry.name, style="color(141)"))
            elif entry.is_file() and os.access(entry.path, os.X_OK):
                items.append(Text(entry.name + "*", style="color(114) bold"))
            else:
                items.append(Text(entry.name, style="color(253)"))

        if items:
            console.print(Columns(items, equal=True, expand=False))

    sys.stdout.flush()
    return 0, ""


def _pty_exec(command: str, cwd: str) -> tuple[int, str]:
    """Run command in a pty, streaming output directly to stdout."""
    proc = PtyProcessUnicode.spawn(["/bin/bash", "-c", command], cwd=cwd)
    output = []
    while True:
        try:
            chunk = proc.read(1024)
            sys.stdout.write(chunk)
            sys.stdout.flush()
            output.append(chunk)
        except EOFError:
            break
    proc.wait()
    return proc.exitstatus or 0, "".join(output)


def execute_bash(command: str, cwd: str) -> tuple[int, str]:
    """Execute a shell command, returning (exit_code, combined_output).

    Special cases:
    - 'cd' calls os.chdir() on the Python process.
    - 'cat/head/tail <file>' renders with Rich syntax highlighting.
    - Everything else runs in a PtyProcessUnicode.

    Args:
        command: Shell command string to execute.
        cwd: Current working directory for the subprocess.

    Returns:
        Tuple of (exit_code, output_string).
    """
    stripped = command.strip()

    # cd interception — MUST use os.chdir, never subprocess
    if stripped.startswith("cd"):
        global _prev_cwd
        rest = stripped[2:].strip()

        if rest == "-":
            target = _prev_cwd or os.path.expanduser("~")
        else:
            target = rest or os.path.expanduser("~")
            target = os.path.expandvars(os.path.expanduser(target))

        old_cwd = os.getcwd()
        try:
            os.chdir(target)
            _prev_cwd = old_cwd
            return 0, ""
        except FileNotFoundError:
            sys.stdout.write(f"cd: {target}: No such file or directory\n")
            sys.stdout.flush()
            return 1, ""
        except NotADirectoryError:
            sys.stdout.write(f"cd: {target}: Not a directory\n")
            sys.stdout.flush()
            return 1, ""

    # Rich ls interception
    ls_m = _LS_RE.match(stripped)
    if ls_m and not any(c in stripped for c in ('|', '>', '<', '&', ';')):
        flags = ls_m.group(2) or ""
        path = ls_m.group(3) or ""
        return _rich_ls(path, flags)

    # Rich file-view interception for cat/head/tail of a single plain file
    m = _VIEW_RE.match(stripped)
    if m:
        filepath = m.group(3)
        return _rich_cat(filepath, stripped)

    # All other commands run in a pty so interactive programs work correctly
    return _pty_exec(stripped, cwd)
