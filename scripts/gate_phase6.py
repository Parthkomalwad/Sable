"""Phase 6 gate, run for real: MCP servers as Sable tools, trust, a server's
question answered in the shell, registry search, and `sable --mcp-serve`
driven by an MCP client, in the playground with a live model.

    docker run --rm --env-file .env -v "$PWD:/app" --cap-add=SYS_ADMIN \
        --security-opt seccomp=unconfined sable-playground \
        bash -c "cd /app && PYTHONPATH=/app python3 scripts/gate_phase6.py"

Needs the playground image with Node and @modelcontextprotocol/server-filesystem
(docker/Dockerfile.playground). Writes gate-phase6-results.md.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import scripts.gate_phase3 as g  # noqa: E402
from tests.integration.test_repl_pty import _clean  # noqa: E402

gate, result, start, drive = g.gate, g.result, g.start, g.drive
ROOT = str(pathlib.Path(__file__).resolve().parents[1])
MOCK = f"{sys.executable} {ROOT}/tests/fixtures/mock_mcp_server.py"
RESULTS = g.RESULTS


def _answer(r, line: str, until: str, reply: str, *, timeout: float = 150) -> str:
    """Type `line`; Enter at each tool preview; at the `until` prompt, type `reply` once."""
    mark = len(_clean(r.buffer))
    r.sendline(line)
    replied, t0, last, quiet = False, time.monotonic(), mark, time.monotonic()
    while time.monotonic() - t0 < timeout:
        r._pump(1.0)
        text = _clean(r.buffer)
        new = text[mark:]
        if len(text) != last:
            last, quiet = len(text), time.monotonic()
        tail = new[-400:]
        if not replied and until in tail and tail.rstrip().endswith("›"):
            r.sendline(reply)
            replied = True
            continue
        if "cancel" in tail and tail.rstrip().endswith("›"):
            r.sendline("")
            continue
        if time.monotonic() - quiet > 15:
            break
    return _clean(r.buffer)[mark:]


@gate("/mcp add fs (filesystem server) lists its tools; a plain-English goal calls one")
def g_fs(work):
    r = start(work)
    added = drive(r, "/mcp add fs mcp-server-filesystem /app", quiet_s=6, timeout=60)
    goal = drive(r, "use the filesystem MCP tools to count the markdown files directly in /app/docs",
                 preview="", quiet_s=15, timeout=180, max_actions=6)
    r.close()
    listed, called = "mcp.fs." in added, "mcp.fs." in goal
    result("/mcp add fs (filesystem server) lists its tools; a plain-English goal calls one",
           "PASS" if listed and called else "FAIL",
           f"tools listed={listed} tool called={called}\n--- add ---\n{added}\n--- goal ---\n{goal}")


@gate("/mcp trust fs.list_directory -> /mcp list shows it allow, the rest confirm")
def g_trust(work):
    r = start(work)
    drive(r, "/mcp add fs mcp-server-filesystem /app", quiet_s=6, timeout=60)
    trusted = drive(r, "/mcp trust fs.list_directory", quiet_s=3)
    listing = drive(r, "/mcp list", quiet_s=5)
    r.close()
    row = next((ln for ln in listing.splitlines()
                if "list_directory" in ln and "list_directory_" not in ln), "")
    ok = "allow" in row and "confirm" in listing
    result("/mcp trust fs.list_directory -> /mcp list shows it allow, the rest confirm",
           "PASS" if ok else "FAIL", f"row: {row}\n--- trust ---\n{trusted}\n--- list ---\n{listing}")


@gate("a server that asks for input (MRTR) gets an in-shell prompt; the answer goes back once")
def g_elicit(work):
    r = start(work)
    drive(r, f"/mcp add mock {MOCK} input-required", quiet_s=5, timeout=60)
    out = _answer(r, "call the mock server's ask tool with no arguments and tell me what it said",
                  "name", "ada")
    r.close()
    ok = "asks:" in out and "hi ada" in out
    result("a server that asks for input (MRTR) gets an in-shell prompt; the answer goes back once",
           "PASS" if ok else "FAIL", out)


@gate("/mcp search filesystem shows registry results with an /mcp add line")
def g_search(work):
    r = start(work)
    out = drive(r, "/mcp search filesystem", quiet_s=8, timeout=60)
    r.close()
    result("/mcp search filesystem shows registry results with an /mcp add line",
           "PASS" if "/mcp add" in out else "FAIL", out)


@gate("sable --mcp-serve: run_command 'ls' runs and is audited; 'rm -rf /tmp/x' is queued, not run")
def g_serve(work):
    from sable.core.db import AUDIT_LOG_PATH, DB_PATH
    from sable.mcp.client import Client
    from sable.mcp.transports import StdioTransport
    target = pathlib.Path("/tmp/x")
    target.mkdir(exist_ok=True)
    env = {"PYTHONPATH": ROOT, "SABLE_ALLOW_ROOT": "1"}
    c = Client(StdioTransport([sys.executable, "-m", "sable.app.main", "--mcp-serve"], env=env))
    try:
        info = c.discover()
        tools = [t["name"] for t in c.list_tools()]
        ls = c.call_tool("run_command", {"command": "ls /app/docs"})
        rm = c.call_tool("run_command", {"command": "rm -rf /tmp/x"})
    finally:
        c.close()
    audit = AUDIT_LOG_PATH.read_text() if AUDIT_LOG_PATH.exists() else ""
    conn = sqlite3.connect(str(DB_PATH))
    queued = conn.execute(
        "SELECT agent, command, status FROM policy_queue WHERE command = 'rm -rf /tmp/x'").fetchall()
    conn.close()
    ok = ("roadmap-phases.md" in ls.text and not ls.is_error and "ls /app/docs" in audit
          and "queued" in rm.text and target.exists() and bool(queued) and queued[0][0].startswith("mcp:"))
    result("sable --mcp-serve: run_command 'ls' runs and is audited; 'rm -rf /tmp/x' is queued, not run",
           "PASS" if ok else "FAIL",
           f"era={info.era} tools={tools}\nls ok={not ls.is_error} audited={'ls /app/docs' in audit}\n"
           f"rm reply: {rm.text}\n/tmp/x still there={target.exists()} queue={queued}")


@gate("no Node on PATH -> /mcp add of an npx server says how to install it, saves nothing")
def g_nonode(work):
    code = ("from sable.app.builtins import mcp\n"
            "mcp.handle_mcp('add fsx npx -y @modelcontextprotocol/server-filesystem /app')\n"
            "from sable.mcp import servers\n"
            "print('SAVED' if 'fsx' in servers.load_config()['servers'] else 'NOT SAVED')")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                       env={**os.environ, "PYTHONPATH": ROOT, "PATH": "/nonexistent"})
    out = p.stdout + p.stderr
    ok = "NOT SAVED" in out and ("node" in out.lower() or "npx" in out.lower())
    result("no Node on PATH -> /mcp add of an npx server says how to install it, saves nothing",
           "PASS" if ok else "FAIL", out[-1500:])


def main() -> int:
    if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        sys.stderr.write("no API key in the environment; run with --env-file .env\n")
        return 2
    only = set(sys.argv[1:])
    for fn in (g_fs, g_trust, g_elicit, g_search, g_serve, g_nonode):
        if not only or fn.__name__ in only:
            fn()
    lines = ["# Phase 6 gate run", "",
             f"Model: `{os.environ.get('SABLE_BACKEND', 'openai')}/{os.environ.get('SABLE_MODEL', 'gpt-4o-mini')}`, "
             f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}.", "",
             "| Gate line | Result |", "|---|---|"]
    lines += [f"| {n} | **{s}** |" for n, s, _ in RESULTS]
    for n, s, ev in RESULTS:
        lines += ["", f"## {s}: {n}", "", "```", ev, "```"]
    pathlib.Path("gate-phase6-results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.stdout.write("\nwrote gate-phase6-results.md\n")
    return 0 if all(s == "PASS" for _, s, _ in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
