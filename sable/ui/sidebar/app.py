"""The sidebar, as a Textual app (Phase 4, G1 sidebar half).

Runs in its own tmux pane, started by `python -m sable.ui.sidebar.watch`,
which launches this and falls back to the old Rich loop if textual cannot
import. The shell never imports this module.

Reads only: agents and the inbox from `ui/state.py`, cost, tokens and
snippets over the same read-only connection. Data is gathered in a worker
thread once a second, so a slow tmux call never stalls rendering or
resizing. Git runs in its own worker every few seconds: on a bind-mounted
repo (the Windows playground) `git status` takes over two seconds, and
sharing a worker with everything else froze the whole panel that long.
Below 90 terminal columns it collapses to one line and comes back when
widened. Inside tmux "terminal" means the tmux window, not this pane.

The look follows the README's telemetry mockup: quiet uppercase labels, no
boxes, thin line meters, and a short bar chart for the week. Everything that
is not waiting on the user stays dim; colour is saved for state.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from sable.ui import state
from sable.ui.sidebar import watch

MIN_COLUMNS = 90
BAR_WIDTH = 24
CHART_ROWS = 3
BARS = " ▁▂▃▄▅▆▇█"
WEEKDAYS = "MTWTFSS"
GIT_EVERY = 5.0

# The README palette.
PURPLE = "#8b7cf6"
PURPLE_DIM = "#4d4690"
GREEN = "#5fcb7a"
AMBER = "#e5b454"
RED = "#f07178"
TEXT = "#e6e9ee"
DIM = "#7d8794"
FAINT = "#4b535e"
TRACK = "#2a313b"

AGENT_DOT = {
    "thinking": PURPLE,
    "running": GREEN,
    "blocked": AMBER,
    state.AWAITING: AMBER,
    "done": DIM,
    "failed": RED,
}
LIVE = {"thinking", "running", "blocked", state.AWAITING}

# One line at the pane's 44 usable columns; a wrapped hint looks broken.
KEYS = [("/dash", ""), ("/clip", ""), ("^T", "sidebar"), ("^G", "steer")]


@dataclass
class Snapshot:
    agents: list = field(default_factory=list)
    inbox: list = field(default_factory=list)
    today: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    budget: int | None = None
    days: list = field(default_factory=list)          # cost per day, oldest first
    day_tokens: list = field(default_factory=list)    # tokens per day, oldest first
    cpu: float | None = None
    mem: tuple | None = None                          # (used_mb, total_mb, percent)
    disk: float | None = None
    load: tuple | None = None
    model: str = ""
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
    """`branch  N dirty  ↑a ↓b` for the shell pane's cwd, or "not a git repo".

    The branch lookup is fast everywhere; the status scan is what is slow on
    a bind mount, so it gets its own, longer timeout and a timeout there
    still shows the branch.
    """
    cwd = watch._current_dir().replace("~", os.path.expanduser("~"))

    def git(*args, timeout):
        # --no-optional-locks: `status` otherwise takes index.lock to refresh
        # the index, and a sidebar polling every few seconds then collides
        # with the user's own commits ("index.lock: File exists").
        return subprocess.run(["git", "--no-optional-locks", "-C", cwd, *args], capture_output=True,
                              text=True, timeout=timeout).stdout.strip()
    try:
        branch = git("branch", "--show-current", timeout=2)
    except (OSError, subprocess.SubprocessError):
        return "not a git repo"
    if not branch:
        return "not a git repo"
    try:
        dirty = len(git("status", "--porcelain", timeout=6).splitlines())
        detail = f"{dirty} dirty" if dirty else "clean"
    except (OSError, subprocess.SubprocessError):
        detail = "status slow"
    try:
        ahead, behind = (int(x) for x in git("rev-list", "--left-right", "--count",
                                             "HEAD...@{u}", timeout=2).split())
        detail += (f"  ↑{ahead}" if ahead else "") + (f"  ↓{behind}" if behind else "")
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return f"{branch}  {detail}"


def _cpu() -> float | None:
    try:
        return float(watch._cpu_usage().rstrip("%"))
    except ValueError:
        return None


def _mem() -> tuple | None:
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                info[k.strip()] = int(v.strip().split()[0])
        total, used = info["MemTotal"], info["MemTotal"] - info["MemAvailable"]
        return used // 1024, total // 1024, 100 * used / total
    except (OSError, ValueError, KeyError, ZeroDivisionError):
        return None


def _disk() -> float | None:
    try:
        u = shutil.disk_usage("/")
        return 100 * u.used / u.total
    except (OSError, ZeroDivisionError):
        return None


def _load() -> tuple | None:
    try:
        return os.getloadavg()
    except (AttributeError, OSError):
        return None


def _config() -> dict:
    try:
        return json.loads((Path.home() / ".config/agentic-shell/config.json").read_text())
    except (OSError, ValueError):
        return {}


def snapshot(db_path=None, days: int = 7) -> Snapshot:
    """Everything the sidebar shows except git, read once. Never writes."""
    since = date.today() - timedelta(days=days - 1)
    per_day = {d: (c, t) for d, c, t in state._rows(
        db_path, "SELECT substr(timestamp, 1, 10), COALESCE(SUM(cost_usd), 0), "
                 "COALESCE(SUM(total_tokens), 0) FROM token_events WHERE timestamp >= ? GROUP BY 1",
        (since.isoformat(),))}
    # ponytail: days bucket by UTC date; today's figure uses local midnight like core/db.
    from sable.core.db import local_day_start_utc
    today = state._rows(db_path, "SELECT COALESCE(SUM(cost_usd), 0) FROM token_events WHERE timestamp >= ?",
                        (local_day_start_utc(),))
    split = state._rows(db_path, "SELECT COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0) "
                                 "FROM token_events WHERE timestamp >= ?", (local_day_start_utc(),))
    snips = state._rows(db_path, "SELECT note, command FROM snippets "
                                 "ORDER BY use_count DESC, created_at DESC LIMIT 3")
    cfg = _config()
    keys = [(since + timedelta(days=i)).isoformat() for i in range(days)]
    return Snapshot(
        agents=state.agents(db_path, limit=8),
        inbox=state.inbox(db_path),
        today=float(today[0][0]) if today else 0.0,
        tokens_in=int(split[0][0]) if split else 0,
        tokens_out=int(split[0][1]) if split else 0,
        budget=cfg.get("daily_token_budget"),
        days=[float(per_day.get(k, (0, 0))[0]) for k in keys],
        day_tokens=[int(per_day.get(k, (0, 0))[1]) for k in keys],
        cpu=_cpu(),
        mem=_mem(),
        disk=_disk(),
        load=_load(),
        model=cfg.get("model") or watch._get_config_model(),
        snippets=[f"{note}: {cmd}" if note else cmd for note, cmd in snips],
        window_width=_window_width(),
    )


def human(n: float) -> str:
    """1234 -> 1.2k, 2_500_000 -> 2.5M."""
    for unit, size in (("M", 1e6), ("k", 1e3)):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return f"{int(n)}"


def line_meter(pct: float, colour: str, width: int = BAR_WIDTH) -> Text:
    """A thin rule filled to `pct`: the README's cpu / mem / disk bars.

    Turns red past 90 whatever its own colour, since that is the one reading
    worth interrupting for.
    """
    pct = max(0.0, min(100.0, pct))
    filled = round(pct / 100 * width)
    t = Text()
    t.append("━" * filled, style=RED if pct > 90 else colour)
    t.append("━" * (width - filled), style=TRACK)
    return t


def week_chart(values: list[float], today: date | None = None, rows: int = CHART_ROWS) -> Text:
    """Vertical bars, `rows` lines tall in eighth-blocks, weekday initials under.

    Each bar is two cells wide. Taller bars are brighter and today's is the
    accent, so the shape reads before the numbers do. A zero day keeps a
    faint floor so the week always shows seven slots.
    """
    today = today or date.today()
    peak = max(values) if values and max(values) > 0 else 0.0
    eighths = [round(v / peak * rows * 8) if peak else 0 for v in values]
    out = Text()
    for r in range(rows - 1, -1, -1):
        for i, h in enumerate(eighths):
            last = i == len(values) - 1
            cell = max(0, min(8, h - r * 8))
            if cell:
                share = values[i] / peak if peak else 0
                colour = PURPLE if last or share > 0.66 else PURPLE_DIM if share < 0.33 else "#6a5fc4"
                out.append(BARS[cell] * 2, style=colour)
            elif r == 0:
                out.append("▁▁", style=TRACK)
            else:
                out.append("  ")
            if not last:
                out.append(" ")
        out.append("\n")
    for i in range(len(values)):
        day = today - timedelta(days=len(values) - 1 - i)
        last = i == len(values) - 1
        out.append(WEEKDAYS[day.weekday()] + " ", style=TEXT if last else FAINT)
        if not last:
            out.append(" ")
    return out


class SidebarApp(App):
    CSS = f"""
    Screen {{ padding: 1 2; background: ansi_default; }}
    .h {{ color: {DIM}; text-style: bold; margin-top: 1; }}
    #session-h {{ margin-top: 0; }}
    #inbox-h {{ color: {AMBER}; }}
    #keys {{ dock: bottom; color: {FAINT}; }}
    #narrow {{ color: {DIM}; }}
    """

    def __init__(self, db_path=None):
        # The terminal's own background, so the sidebar and the shell pane
        # beside it are one surface rather than two blacks with a seam.
        super().__init__(ansi_color=True)
        self.db_path = db_path
        self._tmux_width: int | None = None

    def compose(self) -> ComposeResult:
        yield Static("widen to see the sidebar", id="narrow")
        with Vertical(id="body"):
            yield Static("SESSION", classes="h", id="session-h")
            yield Static(id="cost")
            with Vertical(id="inbox-box"):
                yield Static("INBOX", classes="h", id="inbox-h")
                yield Static(id="inbox")
            yield Static("AGENTS", classes="h")
            yield Static(id="agents")
            yield Static("SYSTEM", classes="h")
            yield Static(id="system")
            yield Static("GIT", classes="h")
            yield Static(Text("checking…", style=FAINT), id="git")
            yield Static("7-DAY TOKENS", classes="h")
            yield Static(id="tokens")
            with Vertical(id="snip-box"):
                yield Static("SNIPPETS", classes="h")
                yield Static(id="snippets")
        keys = Text()
        for i, (k, d) in enumerate(KEYS):
            keys.append(k, style=DIM)
            keys.append((f" {d}" if d else "") + ("   " if i < len(KEYS) - 1 else ""))
        yield Static(keys, id="keys")

    def on_mount(self) -> None:
        self._fit()
        self._collect()
        self._collect_git()
        self.set_interval(1.0, self._collect)
        self.set_interval(GIT_EVERY, self._collect_git)

    def on_resize(self, event) -> None:
        self._fit(event.size.width)

    def _fit(self, own_width: int | None = None) -> None:
        wide = (self._tmux_width or own_width or self.size.width) >= MIN_COLUMNS
        self.query_one("#narrow").display = not wide
        self.query_one("#body").display = wide
        self.query_one("#keys").display = wide

    def _collect(self) -> None:
        self.run_worker(self._gather, thread=True, exclusive=True, group="collect")

    def _gather(self) -> None:
        snap = snapshot(self.db_path)
        self.call_from_thread(self._apply, snap)

    def _collect_git(self) -> None:
        self.run_worker(self._gather_git, thread=True, exclusive=True, group="git")

    def _gather_git(self) -> None:
        self.call_from_thread(self._apply_git, _git())

    def _apply_git(self, g: str) -> None:
        if g == "not a git repo":
            t = Text(g, style=FAINT)
        else:
            branch, _, detail = g.partition("  ")
            t = Text(branch, style=f"bold {TEXT}")
            if detail:
                colour = GREEN if detail.startswith("clean") else FAINT if "slow" in detail else AMBER
                t.append(f"  {detail}", style=colour)
        self.query_one("#git", Static).update(t)

    def _apply(self, s: Snapshot) -> None:
        self._tmux_width = s.window_width
        self._fit()

        session = Text()
        session.append("cost ", style=DIM)
        session.append(f"${s.today:.4f}", style=f"bold {TEXT}")
        session.append(f"\n{human(s.tokens_in)} in · {human(s.tokens_out)} out", style=DIM)
        if s.model and s.model != "unknown":
            session.append(f" · {s.model}", style=FAINT)
        if s.budget:
            used = s.tokens_in + s.tokens_out
            session.append("\n")
            session.append_text(line_meter(100 * used / s.budget, PURPLE))
            session.append(f" {100 * used / s.budget:.0f}%", style=DIM)
        self.query_one("#cost", Static).update(session)

        self.query_one("#inbox-box").display = bool(s.inbox)
        self.query_one("#inbox-h", Static).update(f"INBOX · {len(s.inbox)}")
        inbox = Text()
        for i, item in enumerate(s.inbox[:3]):
            inbox.append("▲ " if item.kind == "approval" else "✕ ",
                         style=AMBER if item.kind == "approval" else RED)
            inbox.append(f"{item.agent}", style=f"bold {TEXT}")
            inbox.append(f"  {item.text[:26]}", style=DIM)
            if i < min(len(s.inbox), 3) - 1:
                inbox.append("\n")
        if len(s.inbox) > 3:
            inbox.append(f"\n+{len(s.inbox) - 3} more · /inbox", style=FAINT)
        self.query_one("#inbox", Static).update(inbox)

        agents = Text()
        for i, a in enumerate(s.agents[:5]):
            live = a.badge in LIVE
            agents.append("● ", style=AGENT_DOT.get(a.badge, DIM))
            agents.append(a.name[:30], style=f"bold {TEXT}" if live else DIM)
            detail = f"{a.badge} · step {a.steps}" if live else f"{a.badge} · {a.steps} steps"
            if a.cost_usd:
                detail += f" · ${a.cost_usd:.4f}"
            agents.append(f"\n  {detail}", style=AMBER if a.badge == state.AWAITING else FAINT)
            if i < min(len(s.agents), 5) - 1:
                agents.append("\n")
        self.query_one("#agents", Static).update(agents or Text("no agents running", style=FAINT))

        system = Text()
        for label, pct, colour, tail in (
                ("cpu", s.cpu, GREEN, ""),
                ("mem", s.mem[2] if s.mem else None, PURPLE,
                 f"{s.mem[0] / 1024:.1f}G" if s.mem else ""),
                ("disk", s.disk, AMBER, "")):
            if pct is None:
                continue
            system.append(f"{label:<5}", style=DIM)
            system.append_text(line_meter(pct, colour))
            system.append(f" {pct:>3.0f}%", style=TEXT)
            if tail:
                system.append(f" {tail}", style=FAINT)
            system.append("\n")
        system.append("load ", style=DIM)
        system.append("  ".join(f"{x:.2f}" for x in s.load) if s.load else "n/a", style=FAINT)
        self.query_one("#system", Static).update(system)

        chart = week_chart(s.day_tokens)
        chart.append(f"\n{human(sum(s.day_tokens))} tokens · ${sum(s.days):.4f}", style=FAINT)
        self.query_one("#tokens", Static).update(chart)

        self.query_one("#snip-box").display = bool(s.snippets)
        snips = Text()
        for i, x in enumerate(s.snippets):
            snips.append(f"{i + 1} ", style=FAINT)
            snips.append(x[:36], style=TEXT)
            if i < len(s.snippets) - 1:
                snips.append("\n")
        self.query_one("#snippets", Static).update(snips)


def main() -> None:
    SidebarApp().run()


if __name__ == "__main__":
    main()
