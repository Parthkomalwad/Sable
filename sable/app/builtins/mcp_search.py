"""`/mcp search <query> [--add N]` over the MCP Registry (D3).

`--add N` hands the chosen line to `/mcp add` when that builtin exists
(`sable.app.builtins.mcp.handle_mcp`), after a confirm. Until it lands, the
line is printed for the user to run.
"""
from __future__ import annotations

import sys

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from sable.mcp import registry_search
from sable.ui.console import out as _out

USAGE = "usage: /mcp search <query> [--add N]"


def _confirm() -> bool:
    sys.stdout.write("  \033[2m↵ add   q cancel  ›\033[0m ")
    sys.stdout.flush()
    try:
        return input("").strip().lower() != "q"
    except (EOFError, KeyboardInterrupt):
        return False


def handle_mcp_search(argument: str, search=registry_search.search, confirm=_confirm) -> bool:
    words = argument.split()
    add = None
    if "--add" in words:
        i = words.index("--add")
        try:
            add = int(words[i + 1])
        except (IndexError, ValueError):
            _out(USAGE)
            return True
        del words[i:i + 2]
    query = " ".join(words)
    if not query:
        _out(USAGE)
        return True
    try:
        hits = search(query)
    except registry_search.RegistryError as exc:
        _out(f"mcp search: {exc}")
        return True
    if not hits:
        _out(f"no MCP servers match {query!r}")
        return True
    if add is None:
        table = Table(show_lines=False)
        for col in ("#", "name", "transport", "description", "add with"):
            table.add_column(col, overflow="fold")
        for n, h in enumerate(hits, 1):
            needs = f"\n[dim]needs: {escape(', '.join(h.needs))}[/dim]" if h.needs else ""
            table.add_row(str(n), escape(h.name), escape(h.transport),
                          escape(h.description), escape(h.add_line) + needs)
        Console().print(table)
        _out("  /mcp search <query> --add N to add one; replace <placeholders> first")
        return True
    if not 1 <= add <= len(hits):
        _out(f"--add takes 1..{len(hits)}")
        return True
    hit = hits[add - 1]
    _out(f"  {hit.add_line}")
    if hit.needs:
        _out(f"  needs: {', '.join(hit.needs)}")
    try:
        from sable.app.builtins.mcp import handle_mcp
    except ImportError:
        _out("  /mcp add is not available yet: run the line above yourself")
        return True
    if "<" in hit.add_line:
        _out("  fill in the <placeholders> and run the line above yourself")
    elif confirm():
        handle_mcp(hit.add_line[len("/mcp "):])
    return True
