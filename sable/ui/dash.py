"""`/dash`: the full-screen command center (Phase 4, Task 3, G1).

Runs as its own process (`python -m sable.ui.dash`), so the shell never
imports textual. Everything it shows comes from `sable.ui.state`, re-read
about once a second. It makes exactly two kinds of write, both through the
existing functions and each on a connection opened for that one call:
`policy.queue.decide_request` (approve / reject) and `policy.breaker.reset`.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.screen import ModalScreen
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static, Tree

from sable.ui import state


class DashApp(App):
    TITLE = "Sable command center"
    CSS = """
    #left { width: 2fr; }
    #right { width: 1fr; border-left: solid $accent; }
    #lanes-box { height: 2fr; }
    #tree { height: 1fr; border-top: solid $accent; }
    #queue { height: 1fr; }
    """
    BINDINGS = [
        ("a", "decide(True)", "Approve"),
        ("r", "decide(False)", "Reject"),
        ("x", "reset", "Reset breaker"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, db_path: str | Path | None = None) -> None:
        super().__init__()
        self.db_path = db_path
        self._items: list[state.InboxItem] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="left"):
                with VerticalScroll(id="lanes-box"):
                    yield Static(id="lanes")
                yield Tree("orchestrator", id="tree")
            with Vertical(id="right"):
                yield Label("Approvals and breaker trips  (a approve, r reject, x reset)")
                yield ListView(id="queue")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data()
        self.query_one("#queue").focus()
        self.set_interval(1.0, self.refresh_data)

    def refresh_data(self) -> None:
        agents = state.agents(self.db_path)
        graph = state.lanes(self.db_path)
        lanes = ["[b]plan graph[/b]  " + "  ".join(f"{escape(i)} ({s})" for i, s in graph), ""] if graph else []
        for a in agents:
            lanes.append(f"[b]{escape(a.name)}[/b]  ({escape(a.badge)})  steps {a.steps}  "
                         f"${a.cost_usd:.4f}  {a.tokens} tok\n  {escape(a.goal)}")
            lanes += [f"  [dim]{escape(line)}[/dim]" for line in state.tail(a.name, 5, self.db_path)]
            lanes.append("")
        ev = state.latest_eval(self.db_path)
        if ev:
            lanes = [f"[b]eval[/b]  {ev.passed}/{ev.total} passed  ({escape(ev.backend)}, "
                     f"{escape(ev.run_id[:10])})", ""] + lanes
        check = state.latest_selfcheck(self.db_path)
        if check:
            lanes = ["[b]self-check[/b]", escape(check), ""] + lanes
        self.query_one("#lanes", Static).update("\n".join(lanes) or "no agents yet")

        tree = self.query_one("#tree", Tree)
        tree.clear()
        tree.root.expand()
        by_badge: dict[str, list[state.Agent]] = {}
        for a in agents:
            by_badge.setdefault(a.badge, []).append(a)
        for badge, group in by_badge.items():
            node = tree.root.add(f"{badge} ({len(group)})", expand=True)
            for a in group:
                node.add_leaf(f"{a.name}  ${a.cost_usd:.4f}")

        items = state.inbox(self.db_path)
        if items != self._items:
            self._items = items
            queue = self.query_one("#queue", ListView)
            keep = queue.index or 0
            queue.clear()
            for i in items:
                head = "APPROVE?" if i.kind == "approval" else "BREAKER"
                queue.append(ListItem(Label(f"{head} #{i.id} {i.agent}: {i.text}\n  {i.why}", markup=False)))
            if items:
                queue.index = min(keep, len(items) - 1)

    def _focused(self) -> state.InboxItem | None:
        index = self.query_one("#queue", ListView).index
        return self._items[index] if index is not None and index < len(self._items) else None

    def _write(self, fn):
        from sable.core.db import DB_PATH

        conn = sqlite3.connect(str(self.db_path or DB_PATH), timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            return fn(conn)
        finally:
            conn.close()

    def action_decide(self, approve: bool) -> None:
        item = self._focused()
        if item is None or item.kind != "approval":
            self.notify("select a pending approval first", severity="warning")
            return
        if not approve:
            self._decide(item, False)   # rejecting only makes things safer: one key
            return
        # An approval stands in for the typed YES a confirm-tier command needs,
        # so it takes a second, deliberate key.
        text = (f"Approve #{item.id} for agent {item.agent}?\n\n  {item.text}\n\n"
                f"rule: {item.why}\n\ny approves, any other key cancels")
        self.push_screen(Confirm(text), lambda yes: self._decide(item, True) if yes else
                         self.notify(f"#{item.id} left pending"))

    def _decide(self, item: state.InboxItem, approve: bool) -> None:
        from sable.policy import queue

        try:
            ok = self._write(lambda c: queue.decide_request(c, item.id, approve=approve))
        except sqlite3.Error as e:
            self.notify(f"could not record the decision: {e}", severity="error")
            return
        verb = "approved" if approve else "rejected"
        self.notify(f"{verb} #{item.id} ({item.agent}: {item.text})" if ok
                    else f"#{item.id} was already decided")
        self.refresh_data()

    def action_reset(self) -> None:
        text = ("Reset the breaker?\n\nEvery open trip is cleared and every paused "
                "sub-agent resumes.\n\ny resets, any other key cancels")
        self.push_screen(Confirm(text), lambda yes: self._reset() if yes else
                         self.notify("breaker left as is"))

    def _reset(self) -> None:
        from sable.policy import breaker

        try:
            n = self._write(breaker.reset)
        except sqlite3.Error as e:
            self.notify(f"could not reset the breaker: {e}", severity="error")
            return
        self.notify(f"breaker reset ({n} trip{'s' if n != 1 else ''} cleared)")
        self.refresh_data()


class Confirm(ModalScreen[bool]):
    """A yes/no box: `y` is yes, any other key is no."""

    DEFAULT_CSS = """
    Confirm { align: center middle; }
    Confirm > Static { width: 70%; height: auto; padding: 1 2; border: thick $warning; background: $surface; }
    """

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text

    def compose(self) -> ComposeResult:
        yield Static(self._text, markup=False)

    def on_key(self, event) -> None:
        event.stop()
        self.dismiss(event.key == "y")

def main() -> None:
    DashApp(sys.argv[1] if len(sys.argv) > 1 else None).run()


if __name__ == "__main__":
    main()
