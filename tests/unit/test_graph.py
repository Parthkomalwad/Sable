"""Plan graphs (Phase 8 Task 0, A3): validation, readiness, and the handler."""
import json
from unittest.mock import MagicMock

from sable.agents import graph


def L(id, needs=()):
    return {"id": id, "goal": f"do {id}", "needs": list(needs)}


# ── validate ──────────────────────────────────────────────────────────

def test_valid_graph_with_join():
    assert graph.validate([L("db"), L("lint"), L("tests", ["db"]), L("join", ["tests", "lint"])]) == []


def test_empty_graph_is_a_problem():
    assert graph.validate([])


def test_duplicate_ids():
    assert any("duplicate" in p for p in graph.validate([L("a"), L("a")]))


def test_bad_id():
    assert graph.validate([L("Bad Id")])
    assert graph.validate([L("x" * 41)])


def test_unknown_need():
    assert any("unknown" in p for p in graph.validate([L("a", ["ghost"])]))


def test_cycle():
    assert any("cycle" in p for p in graph.validate([L("a", ["b"]), L("b", ["a"])]))
    assert any("cycle" in p for p in graph.validate([L("a", ["a"])]))


def test_depth_limit():
    chain = [L("l0")] + [L(f"l{i}", [f"l{i-1}"]) for i in range(1, 5)]
    assert graph.validate(chain) == []  # depth 5
    chain.append(L("l5", ["l4"]))
    assert any("deep" in p for p in graph.validate(chain))


def test_too_many_lanes():
    assert any("lanes" in p for p in graph.validate([L(f"a{i}") for i in range(9)]))


def test_missing_goal_and_bad_needs():
    assert graph.validate([{"id": "a", "goal": "", "needs": []}])
    assert graph.validate([{"id": "a", "goal": "x", "needs": "b"}])
    assert graph.validate(["not a dict"])


# ── ready / blocked ───────────────────────────────────────────────────

LANES = [L("a"), L("b"), L("c", ["a"]), L("d", ["c"]), L("join", ["b", "d"])]


def test_ready_initial():
    assert graph.ready(LANES, set(), set()) == ["a", "b"]


def test_ready_after_done():
    assert graph.ready(LANES, {"a", "b"}, set()) == ["c"]


def test_failure_blocks_dependants_transitively_not_siblings():
    assert graph.blocked_by_failure(LANES, {"a"}) == {"c", "d", "join"}
    assert "b" in graph.ready(LANES, set(), {"a"})
    assert graph.ready(LANES, {"b"}, {"a"}) == []


# ── orchestrator handler ──────────────────────────────────────────────

def _orch(tmp_path, spawn_result):
    """An orchestrator whose manager 'finishes' each lane at once on the bus."""
    from sable.agents.orchestrator import OrchestratorAgent
    from sable.core.events.types import EventKind

    config = MagicMock()
    config.tasks_base_dir = str(tmp_path / "tasks")
    manager = MagicMock()
    orch = OrchestratorAgent(goal="g", cwd=str(tmp_path), config=config,
                             db_path=str(tmp_path / "t.db"), task_manager=manager)
    started = []

    def spawn(name, **_):
        started.append(name)
        ok = spawn_result.get(name, True)
        if ok is None:
            raise RuntimeError("Not inside a tmux session")
        orch._bus.publish(name, EventKind.COMPLETED if ok else EventKind.FAILED,
                          {"result": f"{name} ok"} if ok else {"reason": f"{name} broke"})

    manager.spawn.side_effect = spawn
    return orch, started


def _report(orch):
    return orch._history[-1]["content"]


def test_handler_runs_lanes_in_dependency_order(tmp_path):
    orch, started = _orch(tmp_path, {})
    orch._handle_graph({"action": "graph", "lanes": LANES})
    assert started.index("a") < started.index("c") < started.index("d") < started.index("join")
    assert set(started) == {"a", "b", "c", "d", "join"}
    report = _report(orch)
    assert "join: done" in report and "failed" not in report
    kinds = [(e.payload["id"], e.payload["status"]) for e in orch._bus.since(0) if e.kind == "graph.lane"]
    assert ("join", "running") in kinds and ("join", "done") in kinds


def test_handler_failure_blocks_dependants(tmp_path):
    orch, started = _orch(tmp_path, {"a": False})
    orch._handle_graph({"action": "graph", "lanes": LANES})
    assert set(started) == {"a", "b"}
    report = _report(orch)
    assert "a: failed" in report and "b: done" in report
    assert "c: blocked" in report and "join: blocked" in report


def test_handler_spawn_error_is_a_failed_lane(tmp_path):
    orch, started = _orch(tmp_path, {"b": None})
    orch._handle_graph({"action": "graph", "lanes": [L("a"), L("b"), L("j", ["a", "b"])]})
    report = _report(orch)
    assert "a: done" in report and "b: failed" in report and "j: blocked" in report


def test_handler_invalid_graph_goes_back_to_model(tmp_path):
    orch, started = _orch(tmp_path, {})
    orch._handle_graph({"action": "graph", "lanes": [L("a", ["b"]), L("b", ["a"])]})
    assert started == []
    assert "invalid graph" in _report(orch) and "cycle" in _report(orch)


def test_graph_is_a_recognised_action(tmp_path):
    orch, _ = _orch(tmp_path, {})
    raw = json.dumps({"action": "graph", "lanes": [L("a")], "explanation": "x"})
    assert orch._parse_action(raw)["action"] == "graph"
    response = MagicMock(action="graph", raw=raw, explanation="x")
    assert json.loads(orch._extract_raw(response))["lanes"] == [L("a")]


def test_state_lanes_reads_latest_graph(tmp_path):
    from sable.ui import state

    orch, _ = _orch(tmp_path, {})
    orch._handle_graph({"action": "graph", "lanes": [L("a"), L("b", ["a"])]})
    assert state.lanes(str(tmp_path / "t.db")) == [("a", "done"), ("b", "done")]
