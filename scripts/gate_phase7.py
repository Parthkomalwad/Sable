"""Phase 7 gate, run for real: memory learned in one session and used in
the next, provenance, /remember and /forget, consolidation, untrusted facts,
sable doctor on an old home, and export/import between two homes, in the
playground with a live model.

    docker run --rm --env-file .env -v "$PWD:/app" --cap-add=SYS_ADMIN \
        --security-opt seccomp=unconfined sable-playground \
        bash -c "cd /app && PYTHONPATH=/app python3 scripts/gate_phase7.py"

Writes gate-phase7-results.md.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import scripts.gate_phase3 as g  # noqa: E402

gate, result, start, drive = g.gate, g.result, g.start, g.drive
ROOT = str(pathlib.Path(__file__).resolve().parents[1])
RESULTS = g.RESULTS
QUESTION = "where does myapp write its logs on this server?"


def _palace():
    from sable.memory import palace
    return palace


def _cli(*args, home=None, input_text=None) -> str:
    env = {**os.environ, "PYTHONPATH": ROOT}
    if home:
        env["HOME"] = str(home)
    p = subprocess.run([sys.executable, "-m", "sable.app.main", *args], capture_output=True,
                       text=True, env=env, input=input_text, timeout=120)
    return p.stdout + p.stderr


@gate("session 1 learns where myapp logs; a fresh session answers from memory with no commands")
def g_learn(work):
    conf = pathlib.Path("/etc/myapp")
    conf.mkdir(parents=True, exist_ok=True)
    (conf / "app.conf").write_text("[logging]\nlog_dir = /var/log/myapp\nlevel = info\n")
    r = start(work)
    first = drive(r, f"{QUESTION} its config is under /etc/myapp", preview="", quiet_s=15,
                  timeout=180, max_actions=5)
    r.close()
    saved = [f for f in _palace().all_facts() if "/var/log/myapp" in f.text]
    r = start(tempfile.mkdtemp(prefix="gate-"))
    second = drive(r, QUESTION, preview="q", quiet_s=15, timeout=120, max_actions=2)
    r.close()
    ran = "↵ run" in second or "$ " in second.split(QUESTION, 1)[-1]
    answered = "/var/log/myapp" in second
    ok = bool(saved) and answered and not ran
    result("session 1 learns where myapp logs; a fresh session answers from memory with no commands",
           "PASS" if ok else "FAIL",
           f"fact saved={bool(saved)} answered={answered} commands in session 2={ran}\n"
           f"--- session 1 ---\n{first}\n--- session 2 ---\n{second}")


@gate("/palace why shows the session, goal and command behind a learned fact")
def g_why(work):
    facts = [f for f in _palace().all_facts() if "/var/log/myapp" in f.text]
    if not facts:
        result("/palace why shows the session, goal and command behind a learned fact", "UNTESTED",
               "no learned fact to inspect (the first line did not save one)")
        return
    r = start(work)
    out = drive(r, f"/palace why {facts[0].id}", quiet_s=3)
    r.close()
    ok = "session:" in out and "goal:" in out and "$ " in out
    result("/palace why shows the session, goal and command behind a learned fact",
           "PASS" if ok else "FAIL", out)


@gate("/remember a fact, find it in the user room, /forget it, recall no longer finds it")
def g_remember(work):
    r = start(work)
    said = drive(r, '/remember "deploys go out on Tuesdays"', quiet_s=3)
    fid = (re.search(r"\bf[0-9a-f]{12}\b", said) or [None])[0]
    listed = drive(r, "/palace user", quiet_s=3)
    forgot = drive(r, f"/forget {fid}", quiet_s=3) if fid else ""
    after = drive(r, "/palace find Tuesdays", quiet_s=3)
    r.close()
    ok = bool(fid) and "Tuesdays" in listed and "forgot" in forgot and "nothing in the palace matches" in after
    result("/remember a fact, find it in the user room, /forget it, recall no longer finds it",
           "PASS" if ok else "FAIL",
           f"--- remember ---\n{said}\n--- list ---\n{listed}\n--- forget ---\n{forgot}\n--- find ---\n{after}")


@gate("two near-duplicate facts from two sessions consolidate into one with both sources")
def g_consolidate(work):
    p = _palace()
    a = p.remember("redis runs on port 6380 on this box", "server", {"session": "gate-a", "goal": "g"})
    b = p.remember("Redis runs on port 6380 on this box!", "server", {"session": "gate-b", "goal": "g"})
    r = start(work)
    out = drive(r, "/palace consolidate", quiet_s=4)
    r.close()
    left = [f for f in p.all_facts() if "6380" in f.text]
    ok = a != b and len(left) == 1 and len(left[0].sources) == 2
    result("two near-duplicate facts from two sessions consolidate into one with both sources",
           "PASS" if ok else "FAIL",
           f"ids before={a},{b} after={[(f.id, f.tier, len(f.sources)) for f in left]}\n{out}")


@gate("a fact learned after reading a web page is untrusted and recalled as such")
def g_untrusted(work):
    r = start(work)
    out = drive(r, "use the web.fetch tool to read https://example.com, then tell me what that "
                   "domain is for and remember it as a fact", preview="", yes="YES",
                quiet_s=15, timeout=180, max_actions=5)
    r.close()
    facts = [f for f in _palace().all_facts() if "example" in f.text.lower()]
    from sable.agents import context
    block = (context.build_recall_message("what is example.com for") or {}).get("content", "")
    ok = bool(facts) and all(f.untrusted for f in facts) and "(untrusted source)" in block
    result("a fact learned after reading a web page is untrusted and recalled as such",
           "PASS" if ok else "FAIL",
           f"facts={[(f.text, f.untrusted) for f in facts]}\n--- recall block ---\n{block}\n--- run ---\n{out}")


@gate("sable doctor on an old home reports, --fix migrates with a backup, then reports clean")
def g_doctor(work):
    home = pathlib.Path(tempfile.mkdtemp(prefix="gate-home-"))
    cfg = home / ".config" / "agentic-shell" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"backend": "openai", "model": "gpt-4o-mini", "setup_complete": True}))
    os.chmod(cfg, 0o644)
    before = _cli("doctor", home=home)
    fixed = _cli("doctor", "--fix", home=home)
    after = _cli("doctor", home=home)
    data = json.loads(cfg.read_text())
    backups = list(cfg.parent.glob("config.json.bak-*"))
    ok = ("schema 1" in before and data.get("schema_version") == 2 and backups
          and "schema 1" not in after and oct(cfg.stat().st_mode)[-3:] == "600")
    result("sable doctor on an old home reports, --fix migrates with a backup, then reports clean",
           "PASS" if ok else "FAIL", f"--- before ---\n{before}\n--- fix ---\n{fixed}\n--- after ---\n{after}")


@gate("sable export on home A, import on home B: skills and palace match, no secrets or state")
def g_portable(work):
    a = pathlib.Path(tempfile.mkdtemp(prefix="gate-a-"))
    b = pathlib.Path(tempfile.mkdtemp(prefix="gate-b-"))
    skill = a / "skills" / "rotate-logs"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: rotate-logs\n---\nlogrotate -f /etc/logrotate.conf\n")
    code = ("from sable.memory import palace; palace.remember('myapp logs to /var/log/myapp', 'server', "
            "{'by': 'gate'})")
    env = {**os.environ, "PYTHONPATH": ROOT, "HOME": str(a)}
    subprocess.run([sys.executable, "-c", code], env=env, check=True)
    (a / ".config" / "agentic-shell").mkdir(parents=True)
    (a / ".config" / "agentic-shell" / "config.json").write_text('{"api_key": "sk-should-not-travel"}')
    archive = a / "export.tar.gz"
    exported = _cli("export", str(archive), home=a)
    listing = subprocess.run(["tar", "tzf", str(archive)], capture_output=True, text=True).stdout
    imported = _cli("import", str(archive), "--yes", home=b)
    count = subprocess.run([sys.executable, "-c", "from sable.memory import palace; print(len(palace.all_facts()))"],
                           env={**os.environ, "PYTHONPATH": ROOT, "HOME": str(b)}, capture_output=True, text=True).stdout.strip()
    ok = ((b / "skills" / "rotate-logs" / "SKILL.md").exists() and count == "1"
          and "config.json" not in listing and "sk-should-not-travel" not in listing)
    result("sable export on home A, import on home B: skills and palace match, no secrets or state",
           "PASS" if ok else "FAIL",
           f"palace facts on B={count}\n--- archive ---\n{listing}\n--- export ---\n{exported}\n--- import ---\n{imported}")


def main() -> int:
    if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        sys.stderr.write("no API key in the environment; run with --env-file .env\n")
        return 2
    only = set(sys.argv[1:])
    for fn in (g_learn, g_why, g_remember, g_consolidate, g_untrusted, g_doctor, g_portable):
        if not only or fn.__name__ in only:
            fn()
    lines = ["# Phase 7 gate run", "",
             f"Model: `{os.environ.get('SABLE_BACKEND', 'openai')}/{os.environ.get('SABLE_MODEL', 'gpt-4o-mini')}`, "
             f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}.", "",
             "| Gate line | Result |", "|---|---|"]
    lines += [f"| {n} | **{s}** |" for n, s, _ in RESULTS]
    for n, s, ev in RESULTS:
        lines += ["", f"## {s}: {n}", "", "```", ev, "```"]
    pathlib.Path("gate-phase7-results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.stdout.write("\nwrote gate-phase7-results.md\n")
    return 0 if all(s == "PASS" for _, s, _ in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
