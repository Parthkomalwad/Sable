"""Telemetry sidebar process."""
from __future__ import annotations
import sys, time, os, subprocess
from typing import List
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns

_start_time = datetime.now()
SIDEBAR_WIDTH = 44

# ── helpers ──────────────────────────────────────────────

def _uptime():
    delta = datetime.now() - _start_time
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s"

def _cpu_usage():
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        fields = list(map(int, line.strip().split()[1:]))
        idle, total = fields[3], sum(fields)
        _cpu_usage._prev = getattr(_cpu_usage, "_prev", (idle, total))
        prev_idle, prev_total = _cpu_usage._prev
        _cpu_usage._prev = (idle, total)
        d_total = total - prev_total
        d_idle = idle - prev_idle
        return f"{100*(d_total-d_idle)/d_total:.0f}%" if d_total else "0%"
    except: return "n/a"

def _mem_usage():
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                info[k.strip()] = int(v.strip().split()[0])
        total, avail = info["MemTotal"], info["MemAvailable"]
        used = total - avail
        pct = 100 * used // total
        color = "color(203)" if pct > 85 else "color(221)" if pct > 65 else "color(114)"
        return f"{used//1024}MB/{total//1024}MB ({pct}%)", color
    except: return "n/a", "color(114)"

def _cpu_color(val: str) -> str:
    try:
        n = int(val.rstrip("%"))
        return "color(203)" if n > 85 else "color(221)" if n > 65 else "color(114)"
    except: return "color(114)"

def _last_command(db):
    try:
        row = db._conn.execute(
            "SELECT command FROM token_events WHERE command IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row and row[0]:
            cmd = row[0]
            return cmd[:30] + "…" if len(cmd) > 30 else cmd
        return "—"
    except: return "n/a"

def _current_dir():
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-t", "0.0", "-p", "#{pane_current_path}"],
            capture_output=True, text=True, timeout=1
        )
        path = result.stdout.strip()
        if path:
            home = os.path.expanduser("~")
            if path.startswith(home): path = "~" + path[len(home):]
            return path[-28:] if len(path) > 28 else path
    except: pass
    return os.getcwd()

def _get_config_model():
    try:
        import json
        from pathlib import Path
        cfg = json.loads((Path.home() / ".config/agentic-shell/config.json").read_text())
        return cfg.get("model", "unknown")
    except: return "unknown"

def _git_status():
    """Return (branch, staged, modified, untracked, ahead, behind) or None if not a git repo."""
    try:
        cwd = _current_dir().replace("~", os.path.expanduser("~"))
        branch = subprocess.run(
            ["git", "-C", cwd, "branch", "--show-current"],
            capture_output=True, text=True, timeout=1
        ).stdout.strip()
        if not branch:
            return None
        status = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain"],
            capture_output=True, text=True, timeout=1
        ).stdout.strip()
        modified  = sum(1 for l in status.splitlines() if l and l[1] in "MD ")
        staged    = sum(1 for l in status.splitlines() if l and l[0] in "MADR")
        untracked = sum(1 for l in status.splitlines() if l.startswith("??"))
        # Ahead/behind vs remote
        ahead, behind = 0, 0
        ab = subprocess.run(
            ["git", "-C", cwd, "rev-list", "--left-right", "--count", f"HEAD...@{{u}}"],
            capture_output=True, text=True, timeout=1
        ).stdout.strip()
        if ab:
            parts = ab.split()
            if len(parts) == 2:
                ahead, behind = int(parts[0]), int(parts[1])
        return branch, staged, modified, untracked, ahead, behind
    except: return None

def _top_procs():
    """Return list of (pid, cpu%, mem%, name) for top 3 CPU consumers."""
    try:
        out = subprocess.run(
            ["ps", "aux", "--sort=-%cpu"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip().splitlines()
        rows = []
        for line in out[1:6]:
            parts = line.split(None, 10)
            if len(parts) >= 11:
                name = parts[10].split("/")[-1][:16]
                rows.append((parts[1], parts[2], parts[3], name))
        return rows[:4]
    except: return []

def _network_ip():
    """Return primary non-loopback IP."""
    try:
        out = subprocess.run(
            ["hostname", "-I"], capture_output=True, text=True, timeout=1
        ).stdout.strip()
        ips = [ip for ip in out.split() if not ip.startswith("127.")]
        return ips[0] if ips else "—"
    except: return "—"

def _disk_usage():
    """Return (used, total, pct) for root filesystem."""
    try:
        out = subprocess.run(
            ["df", "-h", "/"], capture_output=True, text=True, timeout=1
        ).stdout.strip().splitlines()
        if len(out) >= 2:
            parts = out[1].split()
            return parts[2], parts[1], parts[4]   # used, total, pct
    except: pass
    return "n/a", "n/a", "n/a"

# ── panel builders ────────────────────────────────────────

def _panel_session(db, model):
    try:
        today = db.get_today_stats()
        cost_str = f"${today['cost']:.4f}  ({today['calls']} calls)"
        cost_style = "color(203)" if today['cost'] > 0.10 else "color(221)" if today['cost'] > 0.01 else "color(114)"
    except Exception:
        cost_str, cost_style = "—", "color(238)"
    t = Text()
    t.append("Model  ", style="color(238)"); t.append(model + "\n", style="color(141) bold")
    t.append("Uptime ", style="color(238)"); t.append(_uptime() + "\n", style="color(153)")
    t.append("CWD    ", style="color(238)"); t.append(_current_dir() + "\n", style="color(153)")
    t.append("Today  ", style="color(238)"); t.append(cost_str + "\n", style=cost_style)
    return Panel(t, title="[color(141) bold]✦ session[/color(141) bold]", border_style="color(55)", padding=(0, 1))

def _panel_system():
    cpu = _cpu_usage()
    mem, mem_color = _mem_usage()
    used, total, pct = _disk_usage()
    ip = _network_ip()
    t = Text()
    t.append("CPU    ", style="color(238)"); t.append(cpu + "\n", style=_cpu_color(cpu))
    t.append("RAM    ", style="color(238)"); t.append(mem + "\n", style=mem_color)
    t.append("Disk   ", style="color(238)"); t.append(f"{used}/{total} ({pct})\n", style="color(153)")
    t.append("IP     ", style="color(238)"); t.append(ip + "\n", style="color(153)")
    return Panel(t, title="[color(141) bold]⬡ system[/color(141) bold]", border_style="color(55)", padding=(0, 1))

def _panel_git():
    result = _git_status()
    t = Text()
    if result is None:
        t.append("not a git repo\n", style="color(238)")
    else:
        branch, staged, modified, untracked, ahead, behind = result
        t.append("Branch  ", style="color(238)"); t.append(branch + "\n", style="color(141) bold")
        staged_color   = "color(114)" if staged == 0 else "color(221)"
        modified_color = "color(114)" if modified == 0 else "color(203)"
        untracked_color= "color(114)" if untracked == 0 else "color(238)"
        t.append("Staged  ", style="color(238)"); t.append(f"{staged} file(s)\n", style=staged_color)
        t.append("Changed ", style="color(238)"); t.append(f"{modified} file(s)\n", style=modified_color)
        t.append("New     ", style="color(238)"); t.append(f"{untracked} file(s)\n", style=untracked_color)
        if ahead or behind:
            sync = ""
            if ahead:  sync += f"↑{ahead} "
            if behind: sync += f"↓{behind}"
            sync_color = "color(221)" if behind else "color(114)"
            t.append("Sync    ", style="color(238)"); t.append(sync.strip() + "\n", style=sync_color)
    return Panel(t, title="[color(141) bold] git[/color(141) bold]", border_style="color(55)", padding=(0, 1))

def _cpu_bar(pct_str: str, width: int = 10) -> tuple[str, str]:
    """Return (bar_string, color) for a CPU percentage string like '42.3'."""
    try:
        pct = float(pct_str)
    except (ValueError, TypeError):
        return "?" * width, "color(238)"
    filled = int(pct / 100 * width)
    bar = "█" * filled + "░" * (width - filled)
    color = "color(203)" if pct > 50 else "color(221)" if pct > 20 else "color(114)"
    return bar, color


def _panel_processes():
    procs = _top_procs()
    t = Text()
    t.append(f"{'NAME':<14} {'CPU BAR':>10} {'%':>5}\n", style="color(238)")
    for pid, cpu, mem, name in procs:
        bar, bar_color = _cpu_bar(cpu)
        t.append(f"{name:<14} ", style="color(250)")
        t.append(f"{bar}", style=bar_color)
        t.append(f" {float(cpu):>4.1f}%\n", style=bar_color)
    return Panel(t, title="[color(141) bold]⚙ processes[/color(141) bold]", border_style="color(55)", padding=(0, 1))

def _panel_tokens(db):
    today = db.get_today_stats()
    stats = db.get_stats(days=7)
    t = Text()
    t.append("Cost     ", style="color(238)"); t.append(f"${today['cost']:.4f}\n", style="color(114) bold")
    t.append("Tokens   ", style="color(238)"); t.append(f"{today['tokens']:,}\n", style="color(153)")
    t.append("Calls    ", style="color(238)"); t.append(f"{today['calls']}\n", style="color(250)")
    if today["calls"] > 0:
        t.append("Avg/call ", style="color(238)"); t.append(f"${today['cost']/today['calls']:.4f}\n", style="color(114)")
    t.append("\n")
    table = Table(show_header=True, header_style="color(141)", box=None, padding=(0, 1))
    table.add_column("Date",  style="color(238)", width=11)
    table.add_column("Calls", justify="right", width=5, style="color(250)")
    table.add_column("Cost",  justify="right", width=8, style="color(114)")
    for row in stats[:5]:
        table.add_row(row["day"], str(row["calls"]), f"${row['cost']:.4f}" if row["cost"] else "$0.0000")
    from io import StringIO
    buf = StringIO()
    Console(file=buf, force_terminal=False, width=SIDEBAR_WIDTH - 4).print(table)
    t.append(buf.getvalue())
    return Panel(t, title="[color(141) bold]◈ tokens[/color(141) bold]", border_style="color(55)", padding=(0, 1))

def _panel_shortcuts():
    t = Text()
    # Commands group
    t.append("── commands ──────────────────\n", style="color(55)")
    commands = [
        ("/help",    "all commands"),
        ("/history", "cmd history + costs"),
        ("/stats",   "7-day token table"),
        ("/memory",  "session context"),
        ("/model",   "current model"),
        ("/mode",    "auto ↔ prefix routing"),
        ("/config",  "edit settings"),
        ("/new",     "new tmux session"),
        ("/exit",    "quit shell"),
    ]
    for key, desc in commands:
        t.append(f"{key:<10}", style="color(141)")
        t.append(f" {desc}\n", style="color(238)")
    # Keys group
    t.append("── keys ──────────────────────\n", style="color(55)")
    keys = [
        ("Ctrl+R",   "search history"),
        ("Ctrl+B",   "next cmd → bash"),
        ("Ctrl+T",   "toggle sidebar"),
        ("Tab",      "complete path/cmd"),
        ("→",        "accept suggestion"),
        (">>",       "force AI prefix"),
    ]
    for key, desc in keys:
        t.append(f"{key:<10}", style="color(141) bold")
        t.append(f" {desc}\n", style="color(238)")
    return Panel(t, title="[color(141) bold]? shortcuts[/color(141) bold]", border_style="color(55)", padding=(0, 1))

# ── main render loop ──────────────────────────────────────

def _render_all(db, model) -> str:
    from io import StringIO
    buf = StringIO()
    bc = Console(file=buf, force_terminal=True, width=SIDEBAR_WIDTH)
    bc.print(_panel_session(db, model))
    bc.print(_panel_system())
    bc.print(_panel_git())
    bc.print(_panel_processes())
    bc.print(_panel_tokens(db))
    bc.print(_panel_shortcuts())
    return buf.getvalue()


def _diff_write(prev_lines: List[str], new_lines: List[str]) -> None:
    """Rewrite only lines that changed. Cursor moves by line number — no full clear."""
    out = []
    for i, new_line in enumerate(new_lines):
        prev = prev_lines[i] if i < len(prev_lines) else None
        if new_line != prev:
            # move to row i+1, col 1; erase to end of line; write new content
            out.append(f"\033[{i+1};1H\033[K{new_line}")
    if out:
        sys.stdout.write("".join(out))
        sys.stdout.flush()


def run():
    from shell.telemetry.db import Database
    db = Database()
    model = "unknown"
    try:
        while True:
            m = db.get_last_model()
            if m != "unknown":
                model = m
            elif model == "unknown":
                model = _get_config_model()

            frame = _render_all(db, model)
            sys.stdout.write("\033[H")
            sys.stdout.write(frame)
            sys.stdout.flush()
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        db.close()

if __name__ == "__main__":
    run()
