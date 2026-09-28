"""Phase 5 gate, run for real: sabled, /schedule, /inbox, watchers, a daemon
killed mid-job, and ntfy pushes with a phone approval, in the playground with
a live model.

    docker run --rm --env-file .env -v "$PWD:/app" --cap-add=SYS_ADMIN \
        --security-opt seccomp=unconfined sable-playground \
        bash -c "cd /app && PYTHONPATH=/app python3 scripts/gate_phase5.py"

Writes gate-phase5-results.md. The phone is simulated: the harness reads the
push from the ntfy topic over HTTP and presses the Approve button by sending
the exact request the button carries. Takes about 12 minutes (cron minutes).
"""
from __future__ import annotations

import json
import os
import pathlib
import secrets
import signal
import sqlite3
import subprocess
import sys
import time

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import scripts.gate_phase3 as g  # noqa: E402
from tests.integration.test_repl_pty import _clean  # noqa: E402

gate, result, start, drive = g.gate, g.result, g.start, g.drive
ROOT = str(pathlib.Path(__file__).resolve().parents[1])
LOG = pathlib.Path.home() / ".sable" / "sabled.log"
HEARTBEAT = pathlib.Path.home() / "heartbeat.log"
DAEMON: list[subprocess.Popen] = []


def _db() -> sqlite3.Connection:
    from sable.core.db import DB_PATH
    return sqlite3.connect(str(DB_PATH))


def _rows(sql: str, *args) -> list:
    c = _db()
    try:
        return c.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        c.close()


def daemon_start() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    DAEMON.append(subprocess.Popen(
        [sys.executable, "-m", "sable.app.main", "daemon", "run"], cwd=ROOT,
        env={**os.environ, "PYTHONPATH": ROOT}, stdout=open(LOG, "a"), stderr=subprocess.STDOUT))
    time.sleep(2)


def daemon_kill() -> None:
    for p in DAEMON:
        if p.poll() is None:
            p.send_signal(signal.SIGKILL)
            p.wait()
    DAEMON.clear()


def wait_for(fn, timeout: float, every: float = 3.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        got = fn()
        if got:
            return got
        time.sleep(every)
    return None


def schedule(r, sentence: str) -> str:
    """Type /schedule, approve the plan at its prompt, return what was shown."""
    mark = len(_clean(r.buffer))
    r.sendline(f'/schedule "{sentence}"')
    r.read_until("q cancel", timeout=90)
    r.sendline("")
    r.read_until("scheduled #", timeout=20)
    r.drain(1)
    return _clean(r.buffer)[mark:]


@gate('/schedule "every 2 minutes write the date to ~/heartbeat.log"; /exit; >= 2 lines appear')
def g_heartbeat(work):
    HEARTBEAT.unlink(missing_ok=True)
    r = start(work)
    shown = schedule(r, "every 2 minutes write the date to ~/heartbeat.log")
    r.sendline("/exit")
    r.close()
    lines = wait_for(lambda: HEARTBEAT.exists() and len(HEARTBEAT.read_text().splitlines()) >= 2 and
                     HEARTBEAT.read_text(), timeout=330, every=10)
    runs = _rows("SELECT job, status FROM job_runs ORDER BY id")
    result('/schedule "every 2 minutes write the date to ~/heartbeat.log"; /exit; >= 2 lines appear',
           "PASS" if lines else "FAIL",
           f"--- /schedule ---\n{shown}\n--- ~/heartbeat.log ---\n{lines or '(fewer than 2 lines)'}\n"
           f"--- job_runs ---\n{runs}")


@gate("a confirm-tier job (rm -rf) waits in /inbox, never runs; /inbox approve runs it once")
def g_inbox(work):
    target = pathlib.Path("/tmp/old-builds")
    target.mkdir(exist_ok=True)
    (target / "a.o").write_text("x")
    r = start(work)
    shown = schedule(r, "every minute delete the directory /tmp/old-builds")
    queued = wait_for(lambda: _rows("SELECT id, agent, command FROM policy_queue WHERE status='pending' "
                                    "AND agent LIKE 'daemon:schedule-%'"), timeout=90)
    still_there = target.exists()
    listing = drive(r, "/inbox", quiet_s=3)
    approved = ""
    gone = False
    if queued:
        approved = drive(r, f"/inbox approve a{queued[0][0]}", quiet_s=3)
        gone = bool(wait_for(lambda: not target.exists(), timeout=30, every=2))
    sid = queued[0][1].split("-")[-1] if queued else ""
    if sid:
        drive(r, f"/schedule pause {sid}", quiet_s=2)
    r.close()
    ok = bool(queued) and still_there and gone and "a" in listing
    result("a confirm-tier job (rm -rf) waits in /inbox, never runs; /inbox approve runs it once",
           "PASS" if ok else "FAIL",
           f"queued={queued} untouched before approval={still_there} deleted after={gone}\n"
           f"--- /schedule ---\n{shown}\n--- /inbox ---\n{listing}\n--- approve ---\n{approved}")


@gate("/watch add disk / 0.01 fires one watch.fired event, not one per tick")
def g_watch(work):
    r = start(work)
    added = drive(r, "/watch add disk / 0.01", quiet_s=3)   # Docker Desktop: / is under 1% used
    r.close()
    fired = wait_for(lambda: _rows("SELECT payload_json FROM agent_events WHERE kind='watch.fired'"), timeout=45)
    time.sleep(35)   # one more 30 s check: still over the line, must not fire again
    again = _rows("SELECT payload_json FROM agent_events WHERE kind='watch.fired'")
    for (wid,) in _rows("SELECT id FROM watchers"):
        c = _db(); c.execute("DELETE FROM watchers WHERE id = ?", (wid,)); c.commit(); c.close()
    ok = bool(fired) and len(again) == 1
    result("/watch add disk / 0.01 fires one watch.fired event, not one per tick", "PASS" if ok else "FAIL",
           f"--- /watch ---\n{added}\n--- events ---\n{again}")


@gate("kill sabled mid-job; restart marks the run lost and schedules resume")
def g_kill(work):
    from sable.daemon import schedule as sched
    c = _db()
    sid = sched.add(c, "* * * * *", ["sleep 50"], "gate sleeper", "/tmp")
    c.close()
    job = sched.job_name(sid)
    running = wait_for(lambda: _rows("SELECT id FROM job_runs WHERE job=? AND status='running'", job), timeout=75)
    daemon_kill()
    daemon_start()
    lost = _rows("SELECT status FROM job_runs WHERE id=?", running[0][0]) if running else []
    resumed = wait_for(lambda: _rows("SELECT id, status FROM job_runs WHERE job=? AND id>?", job,
                                     running[0][0] if running else 0), timeout=75)
    c = _db(); sched.remove(c, sid); c.close()
    ok = bool(running) and lost == [("lost",)] and bool(resumed)
    result("kill sabled mid-job; restart marks the run lost and schedules resume", "PASS" if ok else "FAIL",
           f"run killed={running} status after restart={lost} next run={resumed}\n"
           f"--- sabled.log (tail) ---\n{LOG.read_text()[-1500:]}")


@gate("ntfy: a job push arrives; an approval push, answered like the phone, approves once; replay refused")
def g_ntfy(work):
    from sable.policy import queue
    from sable.policy.engine import decide
    topic, reply = f"sable-gate-{secrets.token_hex(8)}", f"sable-gate-{secrets.token_hex(8)}"
    g.write_config(notify={"server": "https://ntfy.sh", "topic": topic, "reply_topic": reply})
    client = httpx.Client(timeout=httpx.Timeout(30.0))

    def pushes():
        r = client.get(f"https://ntfy.sh/{topic}/json", params={"poll": "1", "since": "all"})
        return [json.loads(ln) for ln in r.text.splitlines() if ln.strip()]

    c = _db()
    qid = queue.enqueue(c, "gate-agent", "rm -rf /tmp/gate-phone", decide("rm -rf /tmp/gate-phone"))
    c.close()
    ask = wait_for(lambda: [m for m in pushes() if f"#{qid}" in m.get("title", "")], timeout=60)
    job = wait_for(lambda: [m for m in pushes() if m.get("title", "").startswith("job ")], timeout=150, every=10)
    button = next((a for a in (ask[0].get("actions") or []) if a.get("label") == "Approve"), None) if ask else None
    status, reasons = None, []
    if button:
        client.post(button["url"], content=button["body"])
        status = wait_for(lambda: [s for (s,) in _rows("SELECT status FROM policy_queue WHERE id=?", qid)
                                   if s != "pending"], timeout=40)
        client.post(button["url"], content=button["body"])   # the same tap again
        time.sleep(15)
        reasons = [json.loads(p)["reason"] for (p,) in
                   _rows("SELECT payload_json FROM agent_events WHERE kind='approval.remote'")]
    ok = bool(job) and bool(button) and status == ["approved"] and reasons[-2:] == ["approved", "replay"]
    shown = [(m.get("title"), m.get("message")) for m in pushes()]
    result("ntfy: a job push arrives; an approval push, answered like the phone, approves once; replay refused",
           "PASS" if ok else "FAIL",
           f"job push={bool(job)} approve button={bool(button)} queue status={status} "
           f"remote decisions={reasons}\n--- pushes on the topic ---\n" + "\n".join(map(str, shown)))
    client.close()


def main() -> int:
    if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        print("no API key in the environment; run with --env-file .env", file=sys.stderr)
        return 2
    only = set(sys.argv[1:])
    g.fresh_state()
    daemon_start()
    try:
        for fn in (g_heartbeat, g_inbox, g_watch, g_kill, g_ntfy):
            if not only or fn.__name__ in only:
                fn()
    finally:
        daemon_kill()
    lines = ["# Phase 5 gate run", "",
             f"Model: `{os.environ.get('SABLE_BACKEND', 'openai')}/{os.environ.get('SABLE_MODEL', 'gpt-4o-mini')}`, "
             f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}.", "",
             "| Gate line | Result |", "|---|---|"]
    lines += [f"| {n} | **{s}** |" for n, s, _ in RESULTS]
    for n, s, ev in RESULTS:
        lines += ["", f"## {s}: {n}", "", "```", ev, "```"]
    pathlib.Path("gate-phase5-results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\nwrote gate-phase5-results.md")
    return 0 if all(s == "PASS" for _, s, _ in RESULTS) else 1


RESULTS = g.RESULTS

if __name__ == "__main__":
    sys.exit(main())
