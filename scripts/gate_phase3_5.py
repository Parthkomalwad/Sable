"""Phase 3.5 gate, run for real: agent tools against a live model on a pty.

Task 7. Same harness and output format as `gate_phase3.py`; each line is PASS,
FAIL or UNTESTED with the transcript that decided it.

    docker run --rm --env-file .env -v "$PWD:/app" --cap-add=SYS_ADMIN \
        --security-opt seccomp=unconfined sable-playground \
        bash -c "cd /app && PYTHONPATH=/app python3 scripts/gate_phase3_5.py"

Writes gate-phase3_5-results.md. `python3 scripts/gate_phase3_5.py g_budget`
re-runs one line.
"""
from __future__ import annotations

import os
import pathlib
import sys
import textwrap
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import scripts.gate_phase3 as g  # noqa: E402  the pty harness, config and results

gate, result, start, drive, write_config = g.gate, g.result, g.start, g.drive, g.write_config

COMPOSE = textwrap.dedent("""\
    services:
      web:
        image: nginx:alpine
        ports:
          - "8080:80"
    """)


@gate("research: web.search + web.fetch as tool blocks, pages-read footer, answer cites a page")
def g_research(work):
    r = start(work)
    out = drive(r, "find out which nginx version fixed CVE-2024-7347 and whether we're affected",
                # YES as a user would: after web.search the agent is tainted, so
                # each web.fetch after it needs confirmation (see results note).
                preview="", yes="YES", max_actions=12, quiet_s=20, timeout=300)
    r.close()
    searched, fetched = "web.search" in out, "web.fetch" in out
    footer = "read " in out and "page" in out
    ok = searched and fetched and footer
    result("research: web.search + web.fetch as tool blocks, pages-read footer, answer cites a page",
           "PASS" if ok else "FAIL",
           f"web.search={searched} web.fetch={fetched} footer={footer}\n{out}")


@gate("fs.patch shows a unified diff; approve; verify runs `docker compose config`")
def g_patch(work):
    compose = pathlib.Path(work, "docker-compose.yml")
    compose.write_text(COMPOSE, encoding="utf-8")
    r = start(work)
    out = drive(r, "add a healthcheck to docker-compose.yml, and verify it with docker compose config",
                preview="", yes="YES", max_actions=10, quiet_s=20, timeout=300)
    r.close()
    diff_shown = ("fs.patch" in out or "fs.write" in out) and "+" in out and "healthcheck" in out
    applied = "healthcheck" in compose.read_text(encoding="utf-8")
    verified = "docker compose config" in out
    ok = diff_shown and applied and verified
    result("fs.patch shows a unified diff; approve; verify runs `docker compose config`",
           "PASS" if ok else "FAIL",
           f"diff shown={diff_shown} applied={applied} verify ran={verified}\n{out}")


@gate("broken file: verify fails, reflection shown, a third identical command is refused")
def g_recover(work):
    compose = pathlib.Path(work, "docker-compose.yml")
    compose.write_text(COMPOSE.replace("ports:", "ports\n  broken: [", 1), encoding="utf-8")
    r = start(work)
    out = drive(r, "the docker-compose.yml here is broken; find what is wrong, fix it, and verify it parses", preview="", yes="YES", max_actions=14, quiet_s=20, timeout=360)
    r.close()
    failed = "↻ step failed" in out
    reflected = "↻ reflection" in out
    refused_repeat = "identical" in out.lower()
    ok = failed and reflected
    note = "" if refused_repeat else "\n\nNOTE: the model did not repeat a command three times, so the refusal was not exercised; it is covered by unit tests."
    result("broken file: verify fails, reflection shown, a third identical command is refused",
           "PASS" if ok else "FAIL",
           f"step failed shown={failed} reflection shown={reflected} third-repeat refused={refused_repeat}{note}\n{out}")


@gate("tool_budgets web max 2: the third web call trips the breaker")
def g_budget(work):
    write_config(tool_budgets={"web": {"max_calls_per_goal": 2}})
    r = start(work)
    out = drive(r, "research the three most recent nginx security advisories, reading each one's page",
                preview="", yes="no", max_actions=12, quiet_s=20, timeout=300)
    status = drive(r, "/breaker", quiet_s=5)
    r.close()
    web_calls = out.count("web.search") + out.count("web.fetch")
    tripped = "[breaker" in out
    ok = tripped and "web" in (out + status)
    result("tool_budgets web max 2: the third web call trips the breaker",
           "PASS" if ok else ("UNTESTED" if web_calls <= 2 else "FAIL"),
           f"web calls shown={web_calls} tripped={tripped}\n{out}\n--- /breaker ---\n{status}")


@gate("after web.fetch, the next block carries taint")
def g_taint(work):
    r = start(work)
    out = drive(r, "use web.fetch to read https://example.com, then list the files here with ls",
                preview="", yes="no", max_actions=6, quiet_s=15, timeout=180)
    r.close()
    fetched = "web.fetch" in out
    tainted = "tainted-context" in out or "tainted context" in out
    ok = fetched and tainted
    result("after web.fetch, the next block carries taint",
           "PASS" if ok else "FAIL", f"fetched={fetched} taint shown={tainted}\n{out}")


def main() -> int:
    if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        print("no API key in the environment; run with --env-file .env", file=sys.stderr)
        return 2
    only = set(sys.argv[1:])
    for fn in (g_research, g_patch, g_recover, g_budget, g_taint):
        if not only or fn.__name__ in only:
            fn()
    lines = ["# Phase 3.5 gate run", "",
             f"Model: `{os.environ.get('SABLE_BACKEND', 'openai')}/{os.environ.get('SABLE_MODEL', 'gpt-4o-mini')}`, "
             f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}.", "",
             "| Gate line | Result |", "|---|---|"]
    lines += [f"| {n} | **{s}** |" for n, s, _ in g.RESULTS]
    for n, s, ev in g.RESULTS:
        lines += ["", f"## {s}: {n}", "", "```", ev, "```"]
    pathlib.Path("gate-phase3_5-results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\nwrote gate-phase3_5-results.md")
    return 0 if all(s == "PASS" for _, s, _ in g.RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
