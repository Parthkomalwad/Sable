"""Phase 2's owed gate line: run 2 of a goal uses fewer turns than run 1.

The Phase 2 plan (Task 13) left this "untested, not passed": a goal naming
every command leaves a skill nothing to save, and a bare goal was not
discoverable from the agent's cwd. This one is both vague and discoverable:
the workspace has a README that says how to deploy, so run 1 has to find the
procedure and run 2 can take it from the approved skill.

    docker run --rm --env-file .env -v "$PWD:/app" --cap-add=SYS_ADMIN \
        --security-opt seccomp=unconfined sable-playground \
        bash -c "cd /app && PYTHONPATH=/app python3 scripts/gate_phase2_turns.py"
"""
from __future__ import annotations

import pathlib
import re
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import scripts.gate_phase3 as g  # noqa: E402

README = """# api

## Deploying

Deploys are three steps, always in this order, from this directory:

1. `./build.sh`
2. `./migrate.sh`
3. `./restart.sh`

Each prints OK when it worked.
"""


def _workspace() -> str:
    work = tempfile.mkdtemp(prefix="gate-p2-")
    pathlib.Path(work, "README.md").write_text(README, encoding="utf-8")
    for step in ("build", "migrate", "restart"):
        p = pathlib.Path(work, f"{step}.sh")
        p.write_text(f"#!/bin/sh\necho '{step}: OK'\ntouch .{step}-done\n", encoding="utf-8")
        p.chmod(0o755)
    return work


def _actions(out: str) -> int:
    """Model actions shown in a run: one preview block each."""
    return len(re.findall(r"↵ run", out))


def main() -> int:
    g.fresh_state()
    shutil.rmtree(pathlib.Path.home() / "skills", ignore_errors=True)
    work = _workspace()
    r = g.start(work)
    run1 = g.drive(r, "deploy the api", preview="", yes="YES", max_actions=12, quiet_s=25, timeout=360)
    skills = g.drive(r, "/skill list", quiet_s=5)
    r.close()

    drafts = re.findall(r"^\s*([a-z0-9-]+)\s+draft", skills, re.MULTILINE)
    approved = ""
    work2 = _workspace()
    r = g.start(work2)
    if drafts:
        approved = g.drive(r, f"/skill approve {drafts[0]}", quiet_s=5)
    run2 = g.drive(r, "deploy the api", preview="", yes="YES", max_actions=12, quiet_s=25, timeout=360)
    r.close()

    n1, n2 = _actions(run1), _actions(run2)
    done1 = all(pathlib.Path(work, f".{s}-done").exists() for s in ("build", "migrate", "restart"))
    done2 = all(pathlib.Path(work2, f".{s}-done").exists() for s in ("build", "migrate", "restart"))
    used = "using skill" in run2
    status = ("PASS" if drafts and used and done1 and done2 and n2 < n1
              else "UNTESTED" if not drafts else "FAIL")
    summary = (f"run 1: {n1} actions, deployed={done1}\n"
               f"draft skills after run 1: {drafts or 'none'}\n"
               f"run 2: {n2} actions, deployed={done2}, skill announced={used}")
    print(f"[{status}] run 2 uses fewer turns than run 1\n{summary}")
    keep = lambda t: "\n".join(l for l in t.splitlines() if l.strip() and not l.strip().startswith(g._SPINNER))
    pathlib.Path("gate-phase2-turns-results.md").write_text(
        f"# Phase 2 owed gate line\n\n**{status}**\n\n```\n{summary}\n```\n\n"
        f"## Run 1\n\n```\n{keep(run1)[-4000:]}\n```\n\n## /skill list\n\n```\n{keep(skills)}\n```\n\n"
        f"## Approve\n\n```\n{keep(approved)}\n```\n\n## Run 2\n\n```\n{keep(run2)[-4000:]}\n```\n",
        encoding="utf-8")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
