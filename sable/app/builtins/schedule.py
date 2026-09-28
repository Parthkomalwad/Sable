"""`/schedule`: a sentence in, an approved cron plan stored (Phase 5, E2).

One model call drafts `{cron, plan, summary}`; you approve that exact plan
and the daemon runs it verbatim, under policy (plan section 0.1).
"""
from __future__ import annotations

import os
import shlex

from sable.agents import runtime
from sable.daemon import cron, schedule
from sable.llm.prompts import load
from sable.ui.console import out as _out

_USAGE = 'usage: /schedule "<sentence>" | list | pause N | resume N | run-now N | rm N'


def _backend(config):
    from sable.llm.registry import build_backend
    return build_backend(config, role="orchestrator")


def _parse(raw: str) -> dict | None:
    d = runtime.parse_json_action(raw)
    return d if isinstance(d.get("cron"), str) and isinstance(d.get("plan"), list) else None


def draft(backend, sentence: str) -> dict:
    """The model's `{cron, plan, summary}`; ValueError with the reason if unusable."""
    system = load("schedule")
    raw = runtime.call_llm(backend, [{"role": "user", "content": sentence}], system).raw
    d = _parse(raw)
    if d is None:  # fallback step 3: ask it to extract the JSON it meant
        raw = runtime.call_llm(backend, [
            {"role": "user", "content": sentence}, {"role": "assistant", "content": raw},
            {"role": "user", "content": "Reply with only the JSON object."}], system).raw
        d = _parse(raw)
    if d is None:
        raise ValueError(f"could not read a schedule from the model's answer:\n{raw}")
    plan = [str(s).strip() for s in d["plan"] if str(s).strip()]
    if not plan:
        raise ValueError("the model proposed an empty plan")
    cron.parse(d["cron"])
    return {"cron": d["cron"], "plan": plan, "summary": str(d.get("summary", "")).strip()}


def _create(sentence: str, conn, config, ask) -> None:
    from sable.policy.engine import decide
    from sable.policy.tiers import Tier

    try:
        d = draft(_backend(config), sentence)
    except (ValueError, runtime.AgentError, OSError) as exc:
        _out(f"{exc}\nTry rephrasing the sentence.")
        return
    _out(f"{d['summary']}\n  when: {cron.describe(d['cron'])}  ({d['cron']})")
    notes = {Tier.CONFIRM: "  will wait in /inbox", Tier.DENY: "  refused: the run fails here"}
    for i, step in enumerate(d["plan"], 1):
        tier = decide(step).tier
        _out(f"  {i}. [{tier.value}] {step}{notes.get(tier, '')}")
    try:
        answer = ask("  ↵ approve  q cancel ").strip()
    except (EOFError, KeyboardInterrupt):
        answer = "q"
    if answer:
        _out("cancelled")
        return
    sid = schedule.add(conn, d["cron"], d["plan"], d["summary"], os.getcwd())
    _out(f"scheduled #{sid}; it runs while sabled is running (sable daemon status)")


def handle_schedule(argument: str, conn, config, ask=input) -> bool:
    """`/schedule "<sentence>"` and list/pause/resume/run-now/rm. Always True."""
    if conn is None:
        _out("schedules need the session database, which is unavailable")
        return True
    parts = argument.split()
    if not parts:
        _out(_USAGE)
        return True
    if parts == ["list"]:
        rows = schedule.list_all(conn)
        if not rows:
            _out("no schedules")
        for r in rows:
            state = "paused" if r["paused"] else cron.describe(r["cron"])
            _out(f"  #{r['id']}  {state}  {r['summary']}  ({len(r['steps'])} step(s))")
        return True
    actions = {"pause": lambda n: schedule.set_paused(conn, n, True),
               "resume": lambda n: schedule.set_paused(conn, n, False),
               "rm": lambda n: schedule.remove(conn, n),
               "run-now": lambda n: schedule.run_now(conn, n)}
    if parts[0] in actions:
        if len(parts) != 2 or not parts[1].isdigit():
            _out(_USAGE)
        elif actions[parts[0]](int(parts[1])):
            _out(f"{parts[0]} #{parts[1]}: done")
        else:
            _out(f"no schedule #{parts[1]}" + (", or a run is in flight" if parts[0] == "run-now" else ""))
        return True
    try:
        sentence = " ".join(shlex.split(argument))
    except ValueError:
        sentence = argument
    _create(sentence, conn, config, ask)
    return True
