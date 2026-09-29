"""Phase 9 Task 0 (H3): `sable eval`, the task suite on the mock backend."""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys

import pytest

from sable.evals import runner
from sable.ui import state


def _task(tmp_path, name="t", toml='goal = "do it"\ntimeout_s = 5\ntags = ["x"]\n', scripts=True):
    d = tmp_path / name
    d.mkdir()
    (d / "task.toml").write_text(toml)
    if scripts:
        (d / "setup.sh").write_text("true\n")
        (d / "check.sh").write_text("true\n")
    return d


def test_the_suite_has_25_valid_tasks_each_with_a_mock_script():
    tasks = runner.load_tasks()
    assert len(tasks) == 25
    for t in tasks:
        actions = json.loads((t.path / "mock.json").read_text())
        assert actions[-1]["action"] == "done"
        assert all(a["action"] == "run" and a["command"] for a in actions[:-1])


def test_goals_are_unique_so_mock_scripts_cannot_collide():
    goals = [t.goal.lower() for t in runner.load_tasks()]
    assert len(set(goals)) == len(goals)


def test_mock_backend_plays_the_eval_script_for_its_goal():
    from tests.fixtures.mock_llm import MockLLMBackend
    import asyncio

    task = runner.load_tasks(only="config-port-change")[0]
    resp = asyncio.run(MockLLMBackend(mode="orchestrator").complete(
        [{"role": "user", "content": f"<goal>{task.goal}</goal>"}], ""))
    assert resp.action == "run" and "port=9090" in resp.command


def test_load_task_valid(tmp_path):
    t = runner.load_task(_task(tmp_path))
    assert (t.goal, t.timeout_s, t.tags) == ("do it", 5, ("x",))


@pytest.mark.parametrize("toml, why", [
    ('timeout_s = 5\n', "goal"),
    ('goal = ""\n', "goal"),
    ('goal = "g"\ntimeout_s = 0\n', "timeout_s"),
    ('goal = "g"\ntimeout_s = "5"\n', "timeout_s"),
    ('goal = "g"\ntags = "x"\n', "tags"),
    ('goal = \n', "task.toml"),
])
def test_load_task_rejects_bad_toml(tmp_path, toml, why):
    with pytest.raises(ValueError, match=why):
        runner.load_task(_task(tmp_path, toml=toml))


def test_load_task_needs_both_scripts(tmp_path):
    with pytest.raises(ValueError, match="setup.sh"):
        runner.load_task(_task(tmp_path, scripts=False))


def test_only_names_one_task(tmp_path):
    _task(tmp_path, "a")
    _task(tmp_path, "b")
    assert [t.name for t in runner.load_tasks(tmp_path, only="b")] == ["b"]
    with pytest.raises(ValueError, match="no task"):
        runner.load_tasks(tmp_path, only="zzz")


@pytest.mark.parametrize("backend", ["openai", "anthropic"])
def test_paid_backend_refused_unless_named(backend):
    with pytest.raises(ValueError, match="costs money"):
        runner.check_backend(backend, explicit=False)
    runner.check_backend(backend, explicit=True)
    with pytest.raises(ValueError, match="costs money"):
        runner.run([], backend)


def test_cli_defaults_to_mock(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(runner, "run", lambda tasks, backend, **k: seen.append(backend) or [])
    monkeypatch.setattr(runner, "record", lambda *a, **k: "id")
    monkeypatch.setattr(runner, "load_tasks", lambda only=None: [])
    assert runner.main(["--out", str(tmp_path / "r.md")]) == 0
    assert seen == ["mock"]


def test_unknown_backend_refused():
    with pytest.raises(ValueError, match="unknown backend"):
        runner.check_backend("gemini", explicit=True)


def _headless(tmp_path):
    from sable.core.config.schema import ShellConfig

    return runner.HeadlessOrchestrator(goal="g", cwd=str(tmp_path), config=ShellConfig.defaults(),
                                       db_path=str(tmp_path / "s.db"), task_manager=None)


def test_headless_accepts_previews_without_input(tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("prompted"))
    agent = _headless(tmp_path)
    assert agent._confirm_command("ls", "list") == "ls"
    assert agent._confirm_tool("fs.read", {}, "read") is True
    assert agent._review({"action": "done"}) == "done"
    assert agent._undo_point("touch x", None) == (True, None)


def test_headless_refuses_spawn_and_graph(tmp_path):
    agent = _headless(tmp_path)
    agent._handle_spawn({"action": "spawn", "name": "w", "goal": "x"})
    agent._handle_graph({"action": "graph", "lanes": []})
    refusals = [m["content"] for m in agent._history if m["role"] == "user"]
    assert refusals[0].startswith("[spawn refused") and refusals[1].startswith("[graph refused")
    assert agent._spawned == []


def test_typed_yes_is_refused_with_no_stdin(monkeypatch):
    """Headless leaves policy's YES prompt alone; with stdin at /dev/null it
    reads EOF, so a confirm-tier step is refused and the task fails honestly."""
    from sable.policy import engine

    def eof(*a):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    monkeypatch.setenv("SABLE_NO_STEPUP", "1")
    assert engine.gate("rm -rf build", role="orchestrator") is False


def test_record_and_latest_eval(tmp_path):
    db = tmp_path / "s.db"
    assert state.latest_eval(db) is None
    r = [runner.Result("a", True, 1, 120, 0.0, 0.5), runner.Result("b", False, 0, 0, 0.0, 0.1)]
    runner.record(r, "mock", db)
    ev = state.latest_eval(db)
    assert (ev.backend, ev.passed, ev.total) == ("mock", 1, 2)
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0] == 2
    md = runner.markdown(r, "mock")
    assert "1/2 passed" in md and "| a | yes | 1 | 120 |" in md


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"),
                    reason="the orchestrator runs commands in a pty (Linux)")
def test_one_task_end_to_end_on_the_mock_backend():
    task = runner.load_tasks(only="log-error-count")[0]
    [result] = runner.run([task], "mock")
    assert result.passed, result
    assert result.steps == 1 and result.tokens > 0 and result.cost_usd == 0.0
