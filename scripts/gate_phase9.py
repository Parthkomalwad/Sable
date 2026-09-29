"""Phase 9 gate, run for real in the playground.

    docker run --rm --env-file .env -v "$PWD:/app" --cap-add=SYS_ADMIN \
        --cap-add=NET_ADMIN --security-opt seccomp=unconfined sable-playground \
        bash -c "cd /app && PYTHONPATH=/app python3 scripts/gate_phase9.py"

The benchmark line runs on the mock backend only (no paid API, by the
user's rule). The telemetry and runbook lines use a few gpt-4o-mini calls,
like earlier gates. Writes gate-phase9-results.md.
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import scripts.gate_phase3 as g  # noqa: E402
from tests.integration.test_repl_pty import _clean  # noqa: E402

gate, result, start, drive = g.gate, g.result, g.start, g.drive
ROOT = str(pathlib.Path(__file__).resolve().parents[1])
RESULTS = g.RESULTS
MOCK = f"{sys.executable} {ROOT}/tests/fixtures/mock_mcp_server.py"


def _answer(r, line: str, replies: dict[str, str], *, timeout: float = 180, quiet: float = 15) -> str:
    mark = len(_clean(r.buffer))
    r.sendline(line)
    t0, last, still, seen = time.monotonic(), mark, time.monotonic(), mark
    while time.monotonic() - t0 < timeout:
        r._pump(1.0)
        text = _clean(r.buffer)
        if len(text) != last:
            last, still = len(text), time.monotonic()
        fresh = text[seen:].rstrip()   # only what arrived after the last answer
        for key, value in replies.items():
            if key in fresh[-120:]:
                r.sendline(value)
                seen, still = len(text), time.monotonic()
                break
        if time.monotonic() - still > quiet:
            break
    return _clean(r.buffer)[mark:]


def _db():
    from sable.core.db import DB_PATH
    return sqlite3.connect(str(DB_PATH))


@gate("sable eval on the mock backend runs the task suite and writes a results table (no paid API)")
def g_eval(work):
    out_file = pathlib.Path(work) / "eval.md"
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
    env.update(PYTHONPATH=ROOT, SABLE_MOCK_LLM="1")
    p = subprocess.run([sys.executable, "-m", "sable.app.main", "eval", "--backend", "mock", "--out", str(out_file)],
                       capture_output=True, text=True, env=env, timeout=1800)
    table = out_file.read_text() if out_file.exists() else ""
    rows = [ln for ln in table.splitlines() if ln.startswith("| ") and not ln.startswith("| task")
            and not ln.startswith("|---")]
    passed = sum(1 for ln in rows if "| yes |" in ln)
    ok = len(rows) >= 25 and passed == len(rows)
    result("sable eval on the mock backend runs the task suite and writes a results table (no paid API)",
           "PASS" if ok else "FAIL", f"{passed}/{len(rows)} passed (API keys removed from the environment)\n{table}\n{p.stderr[-800:]}")


@gate("/skill doctor flags a skill made to fail")
def g_doctor(work):
    from sable.skills.index import SkillIndex
    skills = pathlib.Path.home() / "skills"
    folder = skills / "flaky-deploy"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text("---\nname: flaky-deploy\n---\nmake deploy\n")
    idx = SkillIndex()
    idx.add("flaky-deploy", str(folder / "SKILL.md"), ["deploy"], auto_generated=False)
    data = json.loads(pathlib.Path(idx._path if hasattr(idx, "_path") else skills / "skills_index.json").read_text())
    for e in data:
        if e.get("name") == "flaky-deploy":
            e["confidence"], e["use_count"] = 0.1, 5
    (skills / "skills_index.json").write_text(json.dumps(data))
    r = start(work)
    out = drive(r, "/skill doctor", quiet_s=4)
    r.close()
    ok = "flaky-deploy" in out and "failing" in out
    result("/skill doctor flags a skill made to fail", "PASS" if ok else "FAIL", out)


@gate("/plugin add registers its MCP tools (preview until trusted); /plugin remove takes them away")
def g_plugin(work):
    plug = pathlib.Path(work) / "hello-plugin"
    plug.mkdir()
    cmd = json.dumps(MOCK.split() + ["new-spec"])
    (plug / "plugin.toml").write_text(
        f'[plugin]\nname = "hello"\nversion = "0.1.0"\ndescription = "gate plugin"\n\n'
        f'[server]\ncommand = {cmd}\n')
    r = start(work)
    added = _answer(r, f"/plugin add {plug}", {"[y/N]": "y"}, quiet=6)
    listing = drive(r, "/mcp list", quiet_s=5)
    removed = _answer(r, "/plugin remove hello", {"[y/N]": "y"}, quiet=5)
    after = drive(r, "/mcp list", quiet_s=5)
    r.close()
    ok = "mcp.hello." in listing and "preview" in listing and "mcp.hello." not in after
    result("/plugin add registers its MCP tools (preview until trusted); /plugin remove takes them away",
           "PASS" if ok else "FAIL", f"--- add ---\n{added}\n--- list ---\n{listing}\n--- remove ---\n{removed}\n--- after ---\n{after}")


@gate("with otel.endpoint set, a goal's traces arrive at a local OTLP/HTTP collector")
def g_otel(work):
    bodies = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            bodies.append((self.path, self.rfile.read(int(self.headers.get("Content-Length", 0)))))
            self.send_response(200); self.end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    g.write_config(otel={"endpoint": f"http://127.0.0.1:{srv.server_port}", "headers": {}, "service_name": "sable"})
    r = start(work)
    out = drive(r, "how many files are in /app/docs? use ls", preview="", quiet_s=12, timeout=120, max_actions=3)
    r.sendline("/exit")
    time.sleep(8)
    r.close()
    srv.shutdown()
    g.write_config()
    names = set()
    for path, body in bodies:
        try:
            for rs in json.loads(body)["resourceSpans"]:
                for ss in rs["scopeSpans"]:
                    names |= {s["name"] for s in ss["spans"]}
        except (ValueError, KeyError):
            pass
    ok = any(p == "/v1/traces" for p, _ in bodies) and "goal" in names and "model.call" in names
    result("with otel.endpoint set, a goal's traces arrive at a local OTLP/HTTP collector", "PASS" if ok else "FAIL",
           f"posts={len(bodies)} span names={sorted(names)}\n{out[-1500:]}")


@gate("@b df -h runs over SSH through host b's own Sable; a risky command is queued there, not run")
def g_hosts(work):
    sh = lambda c: subprocess.run(["bash", "-c", c], capture_output=True, text=True)
    sh("id ops >/dev/null 2>&1 || useradd -m -s /bin/bash ops; mkdir -p /root/.ssh /home/ops/.ssh; "
       "[ -f /root/.ssh/id_ed25519 ] || ssh-keygen -q -t ed25519 -N '' -f /root/.ssh/id_ed25519; "
       "cp /root/.ssh/id_ed25519.pub /home/ops/.ssh/authorized_keys; chown -R ops:ops /home/ops/.ssh; "
       "chmod 700 /home/ops/.ssh; chmod 600 /home/ops/.ssh/authorized_keys; mkdir -p /run/sshd; /usr/sbin/sshd; "
       "ssh-keyscan -H localhost >> /root/.ssh/known_hosts 2>/dev/null; "
       "printf '#!/bin/sh\\nexec env PYTHONPATH=/app python3 -m sable.app.main \"$@\"\\n' > /usr/local/bin/sable-mcp; "
       "chmod 755 /usr/local/bin/sable-mcp; mkdir -p /tmp/scratch")
    r = start(work)
    added = drive(r, "/host add b ops@localhost --sable /usr/local/bin/sable-mcp", quiet_s=3)
    tested = drive(r, "/host test b", quiet_s=8, timeout=60)
    df = _answer(r, "@b df -h", {"cancel": ""}, quiet=8, timeout=60)
    rm = _answer(r, "@b rm -rf /tmp/scratch", {"cancel": ""}, quiet=8, timeout=60)
    r.close()
    ok = "Filesystem" in df and "queued on b" in rm and os.path.isdir("/tmp/scratch")
    result("@b df -h runs over SSH through host b's own Sable; a risky command is queued there, not run",
           "PASS" if ok else "FAIL", f"--- add ---\n{added}\n--- test ---\n{tested}\n--- df ---\n{df}\n--- rm ---\n{rm}")


@gate("a failure-signal goal fixed with a passing verify drafts a runbook; the next identical alert offers it in /inbox")
def g_runbook(work):
    from sable.memory import runbooks
    app = pathlib.Path("/srv/app")
    app.mkdir(parents=True, exist_ok=True)
    (app / "health.txt").write_text("DOWN\n")
    r = start(work)
    out = _answer(r, "watcher #1 is down: /srv/app/health.txt says DOWN. fix it by writing UP into that file, "
                     "and verify with grep -q UP /srv/app/health.txt",
                  {"cancel  ›": "", "q cancel ›": "", "type YES to confirm:": "YES", "[b]ash or [a]gentic?": "a",
                   "a apply  q abort:": "a", "[y/N]": "y"}, timeout=180)
    drafted = "runbook drafted" in out
    (app / "health.txt").write_text("DOWN\n")
    conn = _db()
    offered = runbooks.offer(conn, 1, "/srv/app/health.txt says DOWN")
    conn.close()
    inbox = drive(r := start(work), "/inbox", quiet_s=4)
    r.close()
    ok = drafted and bool(offered) and "runbook" in inbox
    result("a failure-signal goal fixed with a passing verify drafts a runbook; the next identical alert offers it in /inbox",
           "PASS" if ok else "FAIL", f"drafted={drafted} offered={offered}\n--- goal ---\n{out[-2500:]}\n--- inbox ---\n{inbox}")


@gate("sable share --approve-only: a link approves one inbox item once; the audit names the approver")
def g_share(work):
    import httpx
    from sable.policy import queue
    from sable.policy.engine import decide
    topic, reply = f"sable-gate-{secrets.token_hex(8)}", f"sable-gate-{secrets.token_hex(8)}"
    g.write_config(notify={"server": "https://ntfy.sh", "topic": topic, "reply_topic": reply})
    env = {**os.environ, "PYTHONPATH": ROOT}
    daemon = subprocess.Popen([sys.executable, "-m", "sable.app.main", "daemon", "run"], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    started = subprocess.run([sys.executable, "-m", "sable.app.main", "share", "--approve-only", "--name", "alice",
                              "--ttl", "30m"], capture_output=True, text=True, env=env).stdout
    share_topic = next((w for w in started.split() if w.startswith("sable-share") or "-share-" in w), None)
    conn = _db()
    queue.ensure_table(conn)
    qid = queue.enqueue(conn, "gate-agent", "rm -rf /tmp/gate-share", decide("rm -rf /tmp/gate-share"))
    conn.close()
    client = httpx.Client(timeout=httpx.Timeout(30.0))
    shares = sqlite3.connect(str(__import__("sable.core.db", fromlist=["DB_PATH"]).DB_PATH)).execute(
        "SELECT topic, reply_topic FROM shares ORDER BY rowid DESC LIMIT 1").fetchone()
    push = button = None
    for _ in range(20):
        msgs = [json.loads(ln) for ln in client.get(f"https://ntfy.sh/{shares[0]}/json",
                                                     params={"poll": "1", "since": "all"}).text.splitlines() if ln.strip()]
        push = next((m for m in msgs if f"#{qid}" in (m.get("title", "") + m.get("message", ""))), None)
        if push:
            button = next((a for a in push.get("actions") or [] if a.get("label") == "Approve"), None)
            break
        time.sleep(5)
    status = None
    if button:
        client.post(button["url"], content=button["body"])
        for _ in range(12):
            time.sleep(5)
            conn = _db()
            status = conn.execute("SELECT status FROM policy_queue WHERE id = ?", (qid,)).fetchone()[0]
            conn.close()
            if status != "pending":
                break
    audit = pathlib.Path("/var/log/agentic-shell/audit.log")
    audit_text = audit.read_text() if audit.exists() else ""
    named = "approver=alice" in audit_text
    daemon.terminate()
    client.close()
    g.write_config()
    ok = bool(button) and status == "approved" and named
    result("sable share --approve-only: a link approves one inbox item once; the audit names the approver",
           "PASS" if ok else "FAIL",
           f"share started:\n{started}\npush={bool(push)} button={bool(button)} status={status} approver named={named}")


def main() -> int:
    only = set(sys.argv[1:])
    for fn in (g_eval, g_doctor, g_plugin, g_otel, g_hosts, g_runbook, g_share):
        if not only or fn.__name__ in only:
            fn()
    lines = ["# Phase 9 gate run", "",
             f"Model for live lines: `{os.environ.get('SABLE_BACKEND', 'openai')}/{os.environ.get('SABLE_MODEL', 'gpt-4o-mini')}`; "
             f"benchmark on the mock backend. {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}.", "",
             "| Gate line | Result |", "|---|---|"]
    lines += [f"| {n} | **{s}** |" for n, s, _ in RESULTS]
    for n, s, ev in RESULTS:
        lines += ["", f"## {s}: {n}", "", "```", ev, "```"]
    pathlib.Path("gate-phase9-results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.stdout.write("\nwrote gate-phase9-results.md\n")
    return 0 if all(s == "PASS" for _, s, _ in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
