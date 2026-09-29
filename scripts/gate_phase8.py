"""Phase 8 gate, run for real: a plan graph with the reviewer, a rehearsed
plan applied then undone, step-up approval, sub-agent limits and signed
skills, in the playground with a live model.

    docker run --rm --env-file .env -v "$PWD:/app" --cap-add=SYS_ADMIN \
        --cap-add=NET_ADMIN --security-opt seccomp=unconfined sable-playground \
        bash -c "cd /app && PYTHONPATH=/app python3 scripts/gate_phase8.py"

NET_ADMIN lets bwrap make a private network as root, which rehearsal and
network-off sub-agents need in a container. The first three lines go
through the model; the last three drive the real code with no model, and
say so. Writes gate-phase8-results.md.
"""
from __future__ import annotations

import base64
import os
import pathlib
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import scripts.gate_phase3 as g  # noqa: E402
from tests.integration.test_repl_pty import _clean  # noqa: E402

gate, result, start, drive = g.gate, g.result, g.start, g.drive
ROOT = str(pathlib.Path(__file__).resolve().parents[1])
RESULTS = g.RESULTS
SITE = pathlib.Path("/srv/site")
CONF = "server {\n    listen 80;\n    server_name example.test;\n}\n"


def _answer(r, line: str, replies: dict[str, str], *, timeout: float = 240, quiet: float = 20) -> str:
    """Type `line`; when a prompt ending in one of `replies`' keys shows, send its value."""
    mark = len(_clean(r.buffer))
    r.sendline(line)
    t0, last, still = time.monotonic(), mark, time.monotonic()
    answered_at = -1
    while time.monotonic() - t0 < timeout:
        r._pump(1.0)
        text = _clean(r.buffer)
        if len(text) != last:
            last, still = len(text), time.monotonic()
        tail = text[mark:][-300:].rstrip()
        for key, value in replies.items():
            if key in tail[-120:] and len(text) != answered_at:
                r.sendline(value)
                answered_at = len(text)
                still = time.monotonic()
                break
        if time.monotonic() - still > quiet:
            break
    return _clean(r.buffer)[mark:]


@gate("a goal with independent parts becomes a plan graph with a join, and the reviewer gives a verdict")
def g_graph(work):
    subprocess.run(["tmux", "new-session", "-d", "-s", "gate8"], check=False)
    r = start(work)
    out = _answer(r, "in parallel: count the markdown files in /app/docs, and count the python files in "
                     "/app/sable/agents; then, once both are done, write both numbers to join.txt in that "
                     "last sub-agent's own working directory",
                  {"cancel  ›": "", "q cancel ›": "", "type YES to confirm:": "YES", "[y/N]": "y",
                   "[b]ash or [a]gentic?": "a"},
                  timeout=900, quiet=300)   # the graph waits on its lanes silently
    r.close()
    tasks = subprocess.run([sys.executable, "-c", "import sqlite3; from sable.core.db import DB_PATH; "
                            "print(sqlite3.connect(str(DB_PATH)).execute('select name, status from tasks').fetchall())"],
                           capture_output=True, text=True, env={**os.environ, "PYTHONPATH": ROOT}).stdout
    out += f"\n--- tasks table ---\n{tasks}"
    panes = subprocess.run(["bash", "-c", "for w in $(tmux list-windows -a -F '#{window_id}'); do "
                            "echo \"== $w\"; tmux capture-pane -p -t $w | tail -15; done"],
                           capture_output=True, text=True).stdout
    out += f"\n--- lane windows ---\n{panes}"
    graph = "plan graph" in out and "(after " in out
    reviewed = "reviewer:" in out
    ok = graph and reviewed
    result("a goal with independent parts becomes a plan graph with a join, and the reviewer gives a verdict",
           "PASS" if ok else "FAIL", f"graph shown={graph} reviewer={reviewed}\n{out}")


@gate("a 3-step config change is rehearsed on a copy first, the real file untouched until apply")
def g_rehearse(work):
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "nginx.conf").write_text(CONF)
    r = start(work)
    seen = {}

    def before_apply():
        seen["during"] = (SITE / "nginx.conf").read_text()
        return "a"
    out = _answer(r, "run exactly these three commands as one run action with a plan: "
                     "sed -i 's/listen 80;/listen 8080;/' /srv/site/nginx.conf ; "
                     "sed -i '1i # managed by sable' /srv/site/nginx.conf ; "
                     "cp /srv/site/nginx.conf /srv/site/nginx.conf.bak",
                  {"q cancel  ›": "", "cancel ›": "", "a apply  q abort:": "a", "type YES to confirm:": "YES",
                   "[b]ash or [a]gentic?": "a"},
                  timeout=300)
    r.close()
    rehearsed = "rehearsal" in out and "nothing real was changed" in out
    after = (SITE / "nginx.conf").read_text()
    applied = "8080" in after and "# managed by sable" in after
    ok = rehearsed and applied
    result("a 3-step config change is rehearsed on a copy first, the real file untouched until apply",
           "PASS" if ok else "FAIL", f"rehearsed={rehearsed} applied={applied}\n--- file after ---\n{after}\n{out}")


@gate("/undo restores the changed files byte for byte")
def g_undo(work):
    before = CONF
    if "8080" not in (SITE / "nginx.conf").read_text():
        result("/undo restores the changed files byte for byte", "UNTESTED",
               "the rehearsal line did not apply its change, so there is nothing to undo")
        return
    r = start(work)
    listing = drive(r, "/undo list", quiet_s=3)
    import re as _re
    ids = sorted({int(n) for n in _re.findall(r"^\s*s(\d+)\s", listing, _re.M)}, reverse=True)
    outs = []
    for sid in ids:   # a new session: undo by id, newest first (K6, across sessions)
        outs.append(_answer(r, f"/undo {sid}", {"[y/N]": "y"}, timeout=30, quiet=4))
        if (SITE / "nginx.conf").read_text() == before:
            break
    r.close()
    now = (SITE / "nginx.conf").read_text()
    ok = now == before
    result("/undo restores the changed files byte for byte", "PASS" if ok else "FAIL",
           f"restored exactly={ok}\n--- file now ---\n{now}\n--- list ---\n{listing}\n" + "\n".join(outs))


@gate("step-up: a valid code runs a deny-tier command once; replay, a wrong code and the daemon are refused (no model)")
def g_stepup(work):
    from sable.policy import stepup
    from sable.policy.engine import decide
    from sable.policy.tiers import Tier
    key = b"12345678901234567890"
    stepup._secret = lambda: base64.b32encode(key).decode()
    db = pathlib.Path(tempfile.mkdtemp()) / "s.db"
    real_verify = stepup.verify
    stepup.verify = lambda code, now=None, db_path=None: real_verify(code, now=now, db_path=db)
    cmd = "rm -rf /"
    denied = decide(cmd).tier is Tier.DENY
    code = stepup.totp(key, time.time())
    first = stepup.offer(cmd, ask=lambda _: "s", secret=lambda _: code) and stepup.take(cmd)
    once = not stepup.take(cmd)
    replay = stepup.offer(cmd, ask=lambda _: "s", secret=lambda _: code)
    wrong = stepup.offer(cmd, ask=lambda _: "s", secret=lambda _: "000000")
    daemon = stepup.eligible("daemon") or stepup.eligible("worker")
    ok = denied and first and once and not replay and not wrong and not daemon
    result("step-up: a valid code runs a deny-tier command once; replay, a wrong code and the daemon are refused (no model)",
           "PASS" if ok else "FAIL",
           f"deny tier={denied} granted once={first} grant used up={once} replay refused={not replay} "
           f"wrong refused={not wrong} daemon/worker offered={daemon}")


@gate("a sub-agent with network off cannot connect out; one over its memory limit is stopped (no model)")
def g_limits(work):
    from sable.agents import limits, runtime
    from sable.agents.sandbox import Sandbox
    from sable.core.limits import Limits
    task = tempfile.mkdtemp(prefix="gate-task-")
    net = Sandbox(task, limits=Limits(network=False)).wrap_command(
        "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3); print('CONNECTED')\"")
    mem = Sandbox(task, limits=Limits(mem_mb=128)).wrap_command(
        "python3 -c \"x = bytearray(512 * 1024 * 1024); print('ALLOCATED')\"")
    net_out = runtime.run_command(net, task, timeout=30)
    mem_out = runtime.run_command(mem, task, timeout=30)
    blocked = "CONNECTED" not in net_out
    capped = "ALLOCATED" not in mem_out and "MemoryError" in mem_out
    ok = blocked and capped
    result("a sub-agent with network off cannot connect out; one over its memory limit is stopped (no model)",
           "PASS" if ok else "FAIL",
           f"host supports: {limits.supported()}\nnetwork blocked={blocked} memory capped={capped}\n"
           f"--- network ---\n{net_out[-800:]}\n--- memory ---\n{mem_out[-800:]}")


@gate("an unsigned imported skill makes commands one tier stricter; after /skill sign it does not (no model)")
def g_skills(work):
    from sable.agents import runtime
    from sable.skills import signing
    folder = pathlib.Path(tempfile.mkdtemp()) / "rotate-logs"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: rotate-logs\n---\nlogrotate -f /etc/logrotate.conf\n")
    before = signing.trust_of(folder / "SKILL.md")
    floor_before = runtime.skill_floor([{"trust": before}])
    signed = signing.sign(folder)
    after = signing.trust_of(folder / "SKILL.md") if signed else "unsigned (no signing key: keyring unavailable)"
    floor_after = runtime.skill_floor([{"trust": after}])
    (folder / "SKILL.md").write_text("tampered\n")
    tampered = signing.trust_of(folder / "SKILL.md")
    ok = (before == "unsigned" and floor_before is not None and after == "signed"
          and floor_after is None and tampered == "tampered")
    result("an unsigned imported skill makes commands one tier stricter; after /skill sign it does not (no model)",
           "PASS" if ok else "FAIL",
           f"before={before} floor={floor_before} after={after} floor={floor_after} after edit={tampered}")


def main() -> int:
    if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        sys.stderr.write("no API key in the environment; run with --env-file .env\n")
        return 2
    only = set(sys.argv[1:])
    for fn in (g_graph, g_rehearse, g_undo, g_stepup, g_limits, g_skills):
        if not only or fn.__name__ in only:
            fn()
    lines = ["# Phase 8 gate run", "",
             f"Model: `{os.environ.get('SABLE_BACKEND', 'openai')}/{os.environ.get('SABLE_MODEL', 'gpt-4o-mini')}`, "
             f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}.", "",
             "| Gate line | Result |", "|---|---|"]
    lines += [f"| {n} | **{s}** |" for n, s, _ in RESULTS]
    for n, s, ev in RESULTS:
        lines += ["", f"## {s}: {n}", "", "```", ev, "```"]
    pathlib.Path("gate-phase8-results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.stdout.write("\nwrote gate-phase8-results.md\n")
    return 0 if all(s == "PASS" for _, s, _ in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
