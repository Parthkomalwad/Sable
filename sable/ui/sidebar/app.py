"""The sidebar, as a Textual app (Phase 4, G1 sidebar half).

Runs in its own tmux pane, started by `python -m sable.ui.sidebar.watch`,
which launches this and falls back to the old Rich loop if textual cannot
import. The shell never imports this module.

Reads only: agents and the inbox from `ui/state.py`, cost and snippets over
the same read-only connection. Data is gathered in a worker thread once a
second, so a slow git or tmux call never stalls rendering or resizing.
Below 90 terminal columns it collapses to one line and comes back when
widened. Inside tmux "terminal" means the tmux window, not this pane.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from datetime import date, timedelta

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Sparkline, Static

from sable.ui import state
from sable.ui.sidebar import watch

MIN_COLUMNS = 90

BADGE_STYLE = {
    "thinking": "cyan",
    "running": "green",
    "blocked": "yellow",
    state.AWAITING: "bold magenta",
    "done": "dim green",
    "failed": "red",
}

SHORTCUTS = [("/dash", "command center"), ("/clip", "snippets"), ("/stats", "7-day tokens"),
             ("Ctrl+T", "toggle sidebar"), ("Ctrl+G", "steer an agent"), (">>", "force AI")]


@dataclass
class Snapshot:
    agents: list = field(default_factory=list)
    inbox: list = field(default_factory=list)
    today: float = 0.0
    days: list = field(default_factory=list)   # oldest first, today last
    git: str = ""
    system: str = ""
    snippets: list = field(default_factory=list)
    window_width: int | None = None


def _window_width() -> int | None:
    """The tmux window's width, or None outside tmux."""
    if not os.environ.get("TMUX"):
        return None
    try:
        out = subprocess.run(["tmux", "display-message", "-p", "#{window_width}"],
                             capture_output=True, text=True, timeout=1).stdout.strip()
        return int(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _git() -> str:
    """Branch and dirty count for the shell pane's cwd (the sidebar's outside tmux)."""
    g = watch._git_status()
    if g is None:
        return "not a git repo"
    branch, staged, modified, untracked, ahead, behind = g
    dirty = staged + modified + untracked
    sync = (f"  +{ahead}" if ahead else "") + (f"  -{behind}" if behind else "")
    return f"{branch}  {dirty} dirty{sync}"


def _system() -> str:
    try:
        load = "load %.2f %.2f %.2f" % os.getloadavg()
    except (AttributeError, OSError):
        load = "load n/a"
    return f"{load}\nmem  {watch._mem_usage()[0]}"


def snapshot(db_path=None, days: int = 7) -> Snapshot:
    """Everything the sidebar shows, read once. Never writes."""
    since = date.today() - timedelta(days=days - 1)
    per_day = dict(state._rows(db_path, "SELECT substr(timestamp, 1, 10), COALESCE(SUM(cost_usd), 0) "
                                        "FROM token_events WHERE timestamp >= ? GROUP BY 1",
                               (since.isoformat(),)))
    # ponytail: days bucket by UTC date; today's figure uses local midnight like core/db.
    from sable.core.db import local_day_start_utc
    today = state._rows(db_path, "SELECT COALESCE(SUM(cost_usd), 0) FROM token_events WHERE timestamp >= ?",
                        (local_day_start_utc(),))
    snips = state._rows(db_path, "SELECT note, command FROM snippets "
                                 "ORDER BY use_count DESC, created_at DESC LIMIT 3")
    return Snapshot(
        agents=state.agents(db_path, limit=8),
        inbox=state.inbox(db_path),
        today=float(today[0][0]) if today else 0.0,
        days=[float(per_day.get((since + timedelta(days=i)).isoformat(), 0)) for i in range(days)],
        git=_git(),
        system=_system(),
        snippets=[f"{note}: {cmd}" if note else cmd for note, cmd in snips],
        window_width=_window_width(),
    )


class SidebarApp(App):
    CSS = """
    Screen { padding: 0 1; }
    .title { color: $accent; text-style: bold; margin-top: 1; }
    Sparkline { height: 2; }
    #narrow { color: $text-muted; }
    """

    def __init__(self, db_path=None):
        super().__init__()
        self.db_path = db_path
        self._tmux_width: int | None = None

    def compose(self) -> ComposeResult:
        yield Static("widen to see the sidebar", id="narrow")
        with Vertical(id="body"):
            yield Static("AGENTS", classes="title")
            yield Static(id="agents")
            yield Static(id="inbox", classes="title")
            yield Static("COST", classes="title")
            yield Static(id="cost")
            yield Sparkline([], id="spark")
            yield Static("GIT", classes="title")
            yield Static(id="git")
            yield Static("SYSTEM", classes="title")
            yield Static(id="system")
            yield Static("SNIPPETS", classes="title")
            yield Static(id="snippets")
            yield Static("KEYS", classes="title")
            yield Static(Text.assemble(*[t for k, d in SHORTCUTS for t in ((f"{k:<8}", "bold"), f"{d}\n")]))

    def on_mount(self) -> None:
        self._fit()
        self._collect()
        self.set_interval(1.0, self._collect)

    def on_resize(self, event) -> None:
        self._fit(event.size.width)

    def _fit(self, own_width: int | None = None) -> None:
        wide = (self._tmux_width or own_width or self.size.width) >= MIN_COLUMNS
        self.query_one("#narrow").display = not wide
        self.query_one("#body").display = wide

    def _collect(self) -> None:
        self.run_worker(self._gather, thread=True, exclusive=True, group="collect")

    def _gather(self) -> None:
        snap = snapshot(self.db_path)
        self.call_from_thread(self._apply, snap)

    def _apply(self, s: Snapshot) -> None:
        self._tmux_width = s.window_width
        self._fit()
        agents = Text()
        for a in s.agents:
            agents.append(f"{a.name[:14]:<14} ")
            agents.append(f"{a.badge}\n", style=BADGE_STYLE.get(a.badge, ""))
        self.query_one("#agents", Static).update(agents or Text("no agents", style="dim"))
        inbox = Text(f"INBOX ({len(s.inbox)})\n")
        for item in s.inbox[:3]:
            inbox.append(f"{item.agent}: {item.text[:30]}\n", style="magenta" if item.kind == "approval" else "red")
        self.query_one("#inbox", Static).update(inbox)
        self.query_one("#cost", Static).update(f"today ${s.today:.4f}  (7 days ${sum(s.days):.4f})")
        self.query_one("#spark", Sparkline).data = s.days
        self.query_one("#git", Static).update(s.git)
        self.query_one("#system", Static).update(s.system)
        self.query_one("#snippets", Static).update(
            "\n".join(x[:40] for x in s.snippets) or Text("/clip add \"cmd\" to save one", style="dim"))


def main() -> None:
    SidebarApp().run()


if __name__ == "__main__":
    main()
