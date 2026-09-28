"""G5 command palette: Ctrl+P, fuzzy search over what the shell can do.

Sources are the builtins (parsed from the /help text, so the two cannot
drift), skills, clipboard snippets and tasks. Each item carries a `text` to
put at the prompt and whether picking it runs that text at once: only a
builtin whose usage takes no argument runs, everything else is inserted for
the user to finish and press Enter on.

The screen is the clipboard picker's approach (ui/clipboard/manager.py): one
full-screen prompt_toolkit Application over a FormattedTextControl.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    kind: str    # builtin | skill | snippet | task
    label: str
    text: str
    run: bool = False


def builtin_items(help_text: str) -> list[Item]:
    items = []
    for line in help_text.splitlines():
        if not line.startswith("  /"):
            continue
        usage, _, desc = line.strip().partition("  ")
        usage = usage.strip()
        cmd = usage.split()[0]
        takes_args = any(c in usage for c in '<["|') or usage.endswith("=")
        items.append(Item("builtin", f"{usage}  {desc.strip()}",
                          usage if not takes_args else cmd + " ", run=not takes_args))
    return items


def gather(db=None) -> list[Item]:
    """Every palette item. A source that fails to load is left out."""
    from sable.app.builtins.dispatch import _HELP_TEXT

    items = builtin_items(_HELP_TEXT)
    try:
        from sable.skills.index import SkillIndex
        items += [Item("skill", f"{s['name']}  ({s.get('status', '')})", f"/skill show {s['name']}")
                  for s in SkillIndex().list_all()]
    except (ImportError, OSError, ValueError, KeyError):
        pass
    if db is not None:
        try:
            items += [Item("snippet", s["note"] or s["command"], s["command"])
                      for s in db.list_snippets()]
            rows = db._conn.execute("SELECT name, status FROM tasks ORDER BY id DESC").fetchall()
            items += [Item("task", f"{name}  ({status})", f"/task attach {name}")
                      for name, status in rows]
        except (sqlite3.Error, AttributeError, KeyError):
            pass
    return items


def score(query: str, text: str) -> int | None:
    """Subsequence match score, higher is better; None if no match.

    Consecutive letters and a match at the start count for more, which is
    enough to put `/task` above `/tools` for "ta".
    """
    q, t = query.lower(), text.lower()
    if not q:
        return 0
    pos, total, prev = 0, 0, -2
    for ch in q:
        pos = t.find(ch, pos)
        if pos < 0:
            return None
        total += 3 if pos == prev + 1 else 1
        if pos == 0:
            total += 2
        prev, pos = pos, pos + 1
    return total


def search(query: str, items: list[Item]) -> list[Item]:
    scored = [(score(query, f"{i.label} {i.kind}"), n, i) for n, i in enumerate(items)]
    return [i for s, n, i in sorted((x for x in scored if x[0] is not None),
                                    key=lambda x: (-x[0], x[1]))]


def open_palette(db=None) -> Item | None:
    """Show the palette full screen. The picked item, or None on Esc."""
    from prompt_toolkit import Application
    from prompt_toolkit.formatted_text import ANSI
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    from sable.ui.theme import sgr

    items = gather(db)
    query = [""]
    selected = [0]
    reset = "\033[0m"

    def _render():
        hits = search(query[0], items)[:20]
        selected[0] = min(selected[0], max(0, len(hits) - 1))
        lines = [f"{sgr('accent')}  Palette{reset}  > {query[0]}\n\n"]
        for n, item in enumerate(hits):
            mark = f"{sgr('accent')}>{reset}" if n == selected[0] else " "
            lines.append(f"  {mark} {sgr('dim')}{item.kind:<8}{reset} {item.label[:70]}\n")
        if not hits:
            lines.append(f"  {sgr('dim')}no match{reset}\n")
        lines.append(f"\n  {sgr('dim')}type to filter  Enter=pick  Esc=close{reset}\n")
        return ANSI("".join(lines))

    kb = KeyBindings()

    @kb.add("escape", eager=True)
    @kb.add("c-c")
    def _quit(event):
        event.app.exit(result=None)

    @kb.add("up")
    def _up(event):
        selected[0] = max(0, selected[0] - 1)

    @kb.add("down")
    def _down(event):
        selected[0] += 1

    @kb.add("backspace")
    def _back(event):
        query[0] = query[0][:-1]
        selected[0] = 0

    @kb.add("enter")
    def _enter(event):
        hits = search(query[0], items)
        event.app.exit(result=hits[selected[0]] if hits else None)

    @kb.add(Keys.Any)
    def _type(event):
        if event.data.isprintable():
            query[0] += event.data
            selected[0] = 0

    app = Application(
        layout=Layout(Window(FormattedTextControl(text=_render))),
        key_bindings=kb, full_screen=True,
    )
    return app.run()
