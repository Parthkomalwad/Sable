"""Phase 4 gate, run for real: blocks, ghost text, explain-last-error, /dash,
the sidebar and cold start, on a pty in the playground with a live model.

    docker run --rm --env-file .env -v "$PWD:/app" --cap-add=SYS_ADMIN \
        --security-opt seccomp=unconfined sable-playground \
        bash -c "cd /app && PYTHONPATH=/app python3 scripts/gate_phase4.py"

Writes gate-phase4-results.md. Lines only a human can judge (badge colours,
the sparkline) are saved as screen text and marked LOOK, not claimed.
"""
from __future__ import annotations

import os
import pathlib
import re
import sqlite3
import statistics
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import scripts.gate_phase3 as g  # noqa: E402
from tests.integration.test_repl_pty import _clean  # noqa: E402

gate, result, start, drive = g.gate, g.result, g.start, g.drive
ROOT = str(pathlib.Path(__file__).resolve().parents[1])


def _db() -> str:
    from sable.core.db import DB_PATH
    return str(DB_PATH)


def _screen(argv: list[str], cols: int, rows: int = 40, wait: float = 6.0, keys: list[tuple[float, str]] = ()) -> str:
    """Run a full-screen program on a pty of this size and return what it drew."""
    from ptyprocess import PtyProcessUnicode
    import select
    env = {**os.environ, "PYTHONPATH": ROOT, "TERM": "xterm-256color", "COLUMNS": str(cols), "LINES": str(rows)}
    proc = PtyProcessUnicode.spawn(argv, cwd=ROOT, env=env, dimensions=(rows, cols))
    buf, t0, pending = "", time.monotonic(), list(keys)
    while time.monotonic() - t0 < wait:
        if pending and time.monotonic() - t0 >= pending[0][0]:
            proc.write(pending.pop(0)[1])
        if select.select([proc.fd], [], [], 0.1)[0]:
            try:
                buf += proc.read(4096)
            except (EOFError, OSError):
                break
    try:
        proc.terminate(force=True)
    except (OSError, EOFError):
        pass
    return _clean(buf)


@gate("every command is a numbered block; /block N rerun re-runs it")
def g_blocks(work):
    r = start(work)
    a = drive(r, "echo gate-block-one", quiet_s=4)
    b = drive(r, "false", quiet_s=4)
    nums = re.findall(r"#(\d+)", a + b)
    rerun = drive(r, f"/block {nums[0]} rerun", quiet_s=5) if nums else ""
    listing = drive(r, "/block", quiet_s=4)
    r.close()
    ok = bool(nums) and "gate-block-one" in rerun and "exit" in (a + b)
    result("every command is a numbered block; /block N rerun re-runs it",
           "PASS" if ok else "FAIL", f"blocks seen={nums}\n{a}\n{b}\n--- rerun ---\n{rerun}\n--- /block ---\n{listing}")


@gate("ghost text: `git sta` shows dim `tus` within 150 ms, from this directory's history")
def g_ghost(work):
    r = start(work)
    drive(r, "git status", quiet_s=4)
    mark = len(_clean(r.buffer))
    t0 = time.monotonic()
    r.send("git sta")
    seen = None
    while time.monotonic() - t0 < 1.0:
        r._pump(0.02)
        if "tus" in _clean(r.buffer)[mark:]:
            seen = time.monotonic() - t0
            break
    r.send("\x15")  # Ctrl+U clears the line
    r.close()
    ms = None if seen is None else int(seen * 1000)
    ok = ms is not None and ms <= 150
    result("ghost text: `git sta` shows dim `tus` within 150 ms, from this directory's history",
           "PASS" if ok else "FAIL",
           f"suggestion appeared after {ms} ms (includes pty round trip)" if ms is not None else "no suggestion within 1 s")


@gate("a failing command shows `? explain  ! fix`; `?` explains, `!` proposes a fix block")
def g_explain(work):
    r = start(work)
    fail = drive(r, "ls /definitely/not/here", quiet_s=4)
    why = drive(r, "?", quiet_s=12, timeout=90)
    drive(r, "ls /definitely/not/here", quiet_s=4)
    fix = drive(r, "!", preview="q", yes="no", quiet_s=15, timeout=120, max_actions=2)
    r.close()
    hint = "? explain" in fail and "! fix" in fail
    explained = len(why.strip()) > 40
    proposed = "↵ run" in fix or "cancel" in fix
    ok = hint and explained and proposed
    result("a failing command shows `? explain  ! fix`; `?` explains, `!` proposes a fix block",
           "PASS" if ok else "FAIL",
           f"hint={hint} explained={explained} fix proposed={proposed}\n--- fail ---\n{fail}\n--- ? ---\n{why}\n--- ! ---\n{fix}")


@gate("/dash shows a pending approval; a then y approves it")
def g_dash(work):
    from sable.policy import queue
    from sable.policy.engine import decide
    conn = sqlite3.connect(_db())
    queue.ensure_table(conn)
    qid = queue.enqueue(conn, "gate-worker", "rm -rf build", decide("rm -rf build"))
    conn.close()
    screen = _screen([sys.executable, "-m", "sable.ui.dash"], cols=140,
                     wait=9, keys=[(4.0, "a"), (5.5, "y"), (7.5, "q")])
    conn = sqlite3.connect(_db())
    status = conn.execute("SELECT status FROM policy_queue WHERE id = ?", (qid,)).fetchone()[0]
    conn.close()
    shown = "rm -rf build" in screen and "gate-worker" in screen
    ok = shown and status == "approved"
    note = ("\nNOT TESTED HERE: a real sub-agent continuing after approval needs tmux; "
            "the approval write it reads is what this line checks.")
    result("/dash shows a pending approval; a then y approves it",
           "PASS" if ok else "FAIL", f"item shown={shown} status after a,y={status}{note}\n{screen[-3000:]}")


@gate("sidebar: sections render at 120 cols, hides below 90 (LOOK: badge colours, sparkline)")
def g_sidebar(work):
    wide = _screen([sys.executable, "-m", "sable.ui.sidebar.watch"], cols=120, wait=6)
    narrow = _screen([sys.executable, "-m", "sable.ui.sidebar.watch"], cols=80, wait=6)
    sections = [s for s in ("AGENTS", "INBOX", "COST", "GIT", "SYSTEM") if s in wide.upper()]
    hid = not any(s in narrow.upper() for s in ("AGENTS", "COST", "SYSTEM"))
    ok = len(sections) >= 4 and hid
    result("sidebar: sections render at 120 cols, hides below 90 (LOOK: badge colours, sparkline)",
           "PASS" if ok else "FAIL",
           f"sections at 120 cols={sections} hidden at 80 cols={hid}\n--- 120 cols ---\n{wide[-2500:]}\n--- 80 cols ---\n{narrow[-800:]}")


@gate("cold start < 300 ms: python -m sable.app.main --version")
def g_cold(work):
    times = []
    for _ in range(7):
        t0 = time.monotonic()
        subprocess.run([sys.executable, "-m", "sable.app.main", "--version"], cwd=ROOT,
                       env={**os.environ, "PYTHONPATH": ROOT}, capture_output=True)
        times.append((time.monotonic() - t0) * 1000)
    med = statistics.median(times[1:])   # the first run warms the page cache
    result("cold start < 300 ms: python -m sable.app.main --version",
           "PASS" if med < 300 else "FAIL", f"median {med:.0f} ms over 6 runs: {[round(t) for t in times[1:]]}")


def main() -> int:
    if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        print("no API key in the environment; run with --env-file .env", file=sys.stderr)
        return 2
    only = set(sys.argv[1:])
    for fn in (g_blocks, g_ghost, g_explain, g_dash, g_sidebar, g_cold):
        if not only or fn.__name__ in only:
            fn()
    lines = ["# Phase 4 gate run", "",
             f"Model: `{os.environ.get('SABLE_BACKEND', 'openai')}/{os.environ.get('SABLE_MODEL', 'gpt-4o-mini')}`, "
             f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}.", "",
             "| Gate line | Result |", "|---|---|"]
    lines += [f"| {n} | **{s}** |" for n, s, _ in g.RESULTS]
    for n, s, ev in g.RESULTS:
        lines += ["", f"## {s}: {n}", "", "```", ev, "```"]
    pathlib.Path("gate-phase4-results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\nwrote gate-phase4-results.md")
    return 0 if all(s == "PASS" for _, s, _ in g.RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
