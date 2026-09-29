"""Plan graphs (Phase 8 Task 0, A3): lanes that run in parallel, then a join.

Pure logic, no I/O. A lane is `{"id", "goal", "needs": [ids]}`; the join is
simply a lane that needs the others. The orchestrator owns spawning and
waiting; this module only answers "is this graph sane" and "what can start".
"""
from __future__ import annotations

import re

MAX_LANES = 8
MAX_DEPTH = 5
_ID = re.compile(r"[a-z0-9-]{1,40}")


def validate(lanes) -> list[str]:
    """Every problem with `lanes`, in words the model can act on. Empty is valid."""
    if not isinstance(lanes, list) or not lanes:
        return ["lanes must be a non-empty list"]
    if len(lanes) > MAX_LANES:
        return [f"{len(lanes)} lanes, at most {MAX_LANES}"]
    problems: list[str] = []
    for lane in lanes:
        if not isinstance(lane, dict):
            return ["each lane must be an object with id, goal, needs"]
    ids = [lane.get("id") for lane in lanes]
    for i in ids:
        if not isinstance(i, str) or not _ID.fullmatch(i):
            problems.append(f"bad id {i!r}: use [a-z0-9-], 1 to 40 characters")
    for i in {i for i in ids if isinstance(i, str) and ids.count(i) > 1}:
        problems.append(f"duplicate id {i!r}")
    for lane in lanes:
        if not isinstance(lane.get("goal"), str) or not lane["goal"].strip():
            problems.append(f"lane {lane.get('id')!r} has no goal")
        needs = lane.get("needs", [])
        if not isinstance(needs, list):
            problems.append(f"lane {lane.get('id')!r}: needs must be a list")
            continue
        for n in needs:
            if n not in ids:
                problems.append(f"lane {lane.get('id')!r} needs unknown lane {n!r}")
    if problems:
        return problems

    needs = {lane["id"]: lane.get("needs", []) for lane in lanes}
    depth: dict[str, int] = {}

    def walk(i: str, path: tuple) -> int:
        if i in path:
            raise ValueError(" -> ".join(path + (i,)))
        if i not in depth:
            depth[i] = 1 + max((walk(n, path + (i,)) for n in needs[i]), default=0)
        return depth[i]

    try:
        deepest = max(walk(i, ()) for i in needs)
    except ValueError as exc:
        return [f"cycle: {exc}"]
    if deepest > MAX_DEPTH:
        return [f"graph is {deepest} lanes deep, at most {MAX_DEPTH}"]
    return []


def blocked_by_failure(lanes, failed: set[str]) -> set[str]:
    """Lanes that can never start: they need a failed lane, directly or not."""
    blocked: set[str] = set()
    changed = True
    while changed:
        changed = False
        for lane in lanes:
            i = lane["id"]
            if i not in blocked and i not in failed and any(
                    n in failed or n in blocked for n in lane.get("needs", [])):
                blocked.add(i)
                changed = True
    return blocked


def ready(lanes, done: set[str], failed: set[str]) -> list[str]:
    """Lanes not yet finished whose needs are all done, in the order given."""
    return [lane["id"] for lane in lanes
            if lane["id"] not in done and lane["id"] not in failed
            and all(n in done for n in lane.get("needs", []))]


def tree(lanes) -> str:
    """A small text picture of the graph, one lane per line."""
    lines = []
    for lane in lanes:
        needs = lane.get("needs", [])
        after = f"  (after {', '.join(needs)})" if needs else ""
        lines.append(f"  {'  ' * (_depth(lanes, lane['id']) - 1)}└ {lane['id']}: {lane['goal']}{after}")
    return "\n".join(lines)


def _depth(lanes, i: str) -> int:
    needs = {lane["id"]: lane.get("needs", []) for lane in lanes}
    return 1 + max((_depth(lanes, n) for n in needs[i]), default=0)
