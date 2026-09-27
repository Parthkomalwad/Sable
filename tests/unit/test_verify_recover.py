"""Phase 3.5 Task 4 (J4, J5): verify-after-act and reflect-and-recover.

A `verify` on a `run` or `tool` action is checked by the runtime; a failed
check is a failure grading and the breaker count. A failed step gets one
reflection note, the retry ladder is bounded, and the third identical command
in a goal is refused in code.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sable.agents import runtime
from sable.policy import engine


def _no_run(cmd):
    raise AssertionError("no command expected")


class TestVerifyForms:
    def test_command_pass_and_fail(self, tmp_path):
        assert runtime.run_verify("true", output="", cwd=str(tmp_path), run=lambda c: "ok") is None
        f = runtime.run_verify("false", output="", cwd=str(tmp_path),
                               run=lambda c: "(no output; exit 1)")
        assert f["verify"] == "failed" and f["check"] == "false"

    def test_exit(self, tmp_path):
        kw = dict(cwd=str(tmp_path), run=_no_run)
        assert runtime.run_verify({"exit": 0}, output="fine", **kw) is None
        assert runtime.run_verify({"exit": 0}, output="bad\n[exit 2]", **kw)["got"] == 2

    def test_stdout_contains(self, tmp_path):
        kw = dict(cwd=str(tmp_path), run=_no_run)
        assert runtime.run_verify({"stdout_contains": "up"}, output="service up", **kw) is None
        assert runtime.run_verify({"stdout_contains": "up"}, output="down", **kw)["verify"] == "failed"

    def test_file_exists(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        kw = dict(output="", cwd=str(tmp_path), run=_no_run)
        assert runtime.run_verify({"file_exists": "a.txt"}, **kw) is None
        assert runtime.run_verify({"file_exists": "b.txt"}, **kw)["got"] == "missing"

    def test_http_status(self, tmp_path, monkeypatch):
        import httpx
        monkeypatch.setattr(httpx, "get", lambda url, **kw: SimpleNamespace(status_code=200))
        kw = dict(output="", cwd=str(tmp_path), run=_no_run)
        ok = {"http_status": {"url": "http://localhost:8080/health", "status": 200}}
        assert runtime.run_verify(ok, **kw) is None
        bad = {"http_status": {"url": "http://127.0.0.1/", "status": 204}}
        assert runtime.run_verify(bad, **kw)["got"] == 200

    def test_http_status_is_localhost_only_for_now(self, tmp_path, monkeypatch):
        import httpx
        monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("must not fetch"))
        f = runtime.run_verify({"http_status": {"url": "http://169.254.169.254/", "status": 200}},
                               output="", cwd=str(tmp_path), run=_no_run)
        assert "localhost" in f["got"]

    def test_unknown_form_fails(self, tmp_path):
        f = runtime.run_verify({"vibes": "good"}, output="", cwd=str(tmp_path), run=_no_run)
        assert f["got"] == "unknown verify form"


class TestRepeatGuard:
    def test_third_identical_is_refused_and_whitespace_does_not_hide_it(self):
        g = runtime.RepeatGuard()
        for cmd in ("ls  -la", "ls -la"):
            key = runtime.action_key({"command": cmd})
            assert g.refuse(key) is None
            g.ran(key)
        assert "refused" in g.refuse(runtime.action_key({"command": " ls -la "}))
        assert g.refuse(runtime.action_key({"command": "ls"})) is None

    def test_tool_calls_key_on_name_and_args(self):
        a = runtime.action_key({"action": "tool", "name": "echo", "args": {"a": 1, "b": 2}})
        b = runtime.action_key({"tool": {"name": "echo", "args": {"b": 2, "a": 1}}})
        assert a == b


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@pytest.fixture
def orch(tmp_path, monkeypatch):
    from sable.agents.orchestrator import OrchestratorAgent
    cfg = MagicMock(); cfg.tasks_base_dir = str(tmp_path); cfg.model_for.return_value = "m"
    agent = OrchestratorAgent(goal="g", cwd=str(tmp_path), config=cfg,
                              db_path=str(tmp_path / "s.db"), task_manager=MagicMock())
    monkeypatch.setattr(agent, "_confirm_command", lambda cmd, expl: cmd)
    monkeypatch.setattr("builtins.input", lambda *a: "")
    return agent


def _run(agent, command, verify=None):
    action = {"action": "run", "command": command, "explanation": "e"}
    if verify is not None:
        action["verify"] = verify
    agent._handle_run(action)


class TestOrchestrator:
    def test_failed_verify_counts_as_failure(self, orch, monkeypatch):
        monkeypatch.setattr(orch, "_run_command", lambda c, **k: "done")
        _run(orch, "make", {"stdout_contains": "OK"})
        out = orch._steps[-1]["output"]
        assert runtime.exit_code_of(out) == 1 and orch._run_failed()
        assert '"verify": "failed"' in orch._history[-1]["content"]

    def test_passing_verify_is_not_a_failure(self, orch, monkeypatch):
        monkeypatch.setattr(orch, "_run_command", lambda c, **k: "OK")
        _run(orch, "make", {"stdout_contains": "OK"})
        assert not orch._run_failed() and "[reflect]" not in orch._history[-1]["content"]

    def test_one_reflection_per_failure_and_the_ladder_is_bounded(self, orch, monkeypatch):
        monkeypatch.setattr(orch, "_run_command", lambda c, **k: "(no output; exit 1)")
        asked = []
        monkeypatch.setattr(orch, "_ask_user", lambda: asked.append(1) or "try sudo")
        notes = []
        for i in range(5):
            _run(orch, f"step {i}")
            notes.append(orch._history[-1]["content"])
        assert all(n.count("[reflect]") + n.count("[step failed]") == 1 for n in notes)
        assert "Then retry" in notes[0]
        assert "different one" in notes[1]
        assert "[user guidance] try sudo" in notes[2] and asked == [1]
        assert "[step failed]" in notes[3]
        assert "Then retry" in notes[4]      # counter reset after marking failed

    def test_success_resets_the_ladder(self, orch, monkeypatch):
        outs = iter(["(no output; exit 1)", "fine", "(no output; exit 1)"])
        monkeypatch.setattr(orch, "_run_command", lambda c, **k: next(outs))
        for i in range(3):
            _run(orch, f"s{i}")
        assert "Then retry" in orch._history[-1]["content"]

    def test_third_identical_command_is_refused(self, orch, monkeypatch):
        ran = []
        monkeypatch.setattr(orch, "_run_command", lambda c, **k: ran.append(c) or "ok")
        for _ in range(3):
            _run(orch, "systemctl restart nginx")
        assert len(ran) == 2 and "refused" in orch._history[-1]["content"]

    def test_third_identical_tool_call_is_refused(self, orch, monkeypatch):
        monkeypatch.setattr(orch, "_confirm_tool", lambda *a: True)
        for _ in range(3):
            orch._handle_tool({"action": "tool", "name": "echo", "args": {"text": "x"}, "explanation": "e"})
        assert orch._commands_run == 2 and "refused" in orch._history[-1]["content"]

    def test_verify_command_is_gated(self, orch, monkeypatch, tmp_path):
        user = tmp_path / "user.toml"
        user.write_text("[[rule]]\nname = 'no-probe'\npattern = '^probe'\ntier = 'deny'\n",
                        encoding="utf-8")
        monkeypatch.setattr(engine.rules, "USER_POLICY_PATH", user)
        engine.rules.load.cache_clear()
        spawned = []
        monkeypatch.setattr(runtime, "run_command", lambda c, **k: spawned.append(c) or "ok")
        try:
            _run(orch, "echo hi", "probe --check")
        finally:
            engine.rules.load.cache_clear()
        assert spawned == ["echo hi"]          # the verify never reached a shell
        assert "blocked" in orch._steps[-1]["output"] and orch._run_failed()

    def test_failure_block_is_printed(self, orch, monkeypatch, capsys):
        monkeypatch.setattr(orch, "_run_command", lambda c, **k: "done")
        _run(orch, "make", {"stdout_contains": "OK"})
        out = capsys.readouterr().out
        assert "↻ step failed: " in out and "(got: done)" in out
        assert "next: retry 1 of 2" in out

    def test_rungs_are_labelled(self, orch, monkeypatch, capsys):
        monkeypatch.setattr(orch, "_run_command", lambda c, **k: "boom\n[exit 2]")
        monkeypatch.setattr(orch, "_ask_user", lambda: "")
        for i in range(4):
            _run(orch, f"s{i}")
        out = capsys.readouterr().out
        assert "step failed: exit 2 (got: boom)" in out
        for label in ("retry 1 of 2", "try an alternative", "asking you", "marked failed, moving on"):
            assert f"next: {label}" in out

    def test_next_action_is_labelled_and_published(self, orch, monkeypatch, capsys):
        from sable.core.events.types import EventKind
        monkeypatch.setattr(orch, "_run_command", lambda c, **k: "(no output; exit 1)")
        published = []
        monkeypatch.setattr(orch._bus, "publish", lambda *a: published.append(a))
        _run(orch, "make")
        orch._show_reflection("make needs a Makefile; create it first")
        orch._show_reflection("not a reflection")       # only once per failure
        out = capsys.readouterr().out
        assert "↻ reflection: make needs a Makefile" in out and "not a reflection" not in out
        (agent, kind, payload), = [p for p in published if p[1] == EventKind.REFLECTION]
        assert payload["rung"] == "retry 1 of 2" and payload["check"] == "exit 1"
        assert payload["reflection"].startswith("make needs") and "step" in payload and "got" in payload

    def test_verify_survives_extract_raw(self):
        from sable.agents.orchestrator import OrchestratorAgent
        raw = json.dumps({"action": "run", "command": "x", "explanation": "e", "verify": {"exit": 0}})
        resp = SimpleNamespace(action="run", command="x", explanation="e", done=False, spawn=None, raw=raw)
        assert json.loads(OrchestratorAgent._extract_raw(None, resp))["verify"] == {"exit": 0}


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

@pytest.fixture
def worker(tmp_path):
    from sable.agents.worker import TaskAgent
    w = TaskAgent.__new__(TaskAgent)
    w._name, w._goal, w._workspace, w._tainted = "t", "g", str(tmp_path), False
    w._db_path = str(tmp_path / "s.db")
    w._config = MagicMock(); w._config.model_for.return_value = "m"
    w._guard, w._failures = runtime.RepeatGuard(), 0
    return w


class TestWorker:
    def test_failed_verify_counts_as_failure(self, worker):
        out = worker._verify({"command": "x", "verify": {"file_exists": "nope"}}, "made it")
        assert runtime.failed_output(out) and '"verify": "failed"' in out

    def test_ladder_queues_on_third_failure_then_marks_failed(self, worker):
        from sable.policy import queue as policy_queue
        notes = [worker._climb_ladder("(no output; exit 1)", "make") for _ in range(4)]
        assert "Then retry" in notes[0] and "different one" in notes[1]
        assert "[queued]" in notes[2] and "[step failed]" in notes[3]
        conn = worker._db_conn()
        try:
            assert [p["command"] for p in policy_queue.pending(conn)] == ["make"]
        finally:
            conn.close()

    def test_block_printed_and_reflection_published(self, worker, monkeypatch, capsys):
        from sable.core.events.types import EventKind
        published = []
        monkeypatch.setattr(worker, "_publish", lambda kind, **p: published.append((kind, p)), raising=False)
        worker._step = 4
        worker._climb_ladder('x\n{"verify": "failed", "check": "test -f a", "got": "(no output; exit 1)"}\n[exit 1]', "k")
        worker._show_reflection("a was never written; write it")
        out = capsys.readouterr().out
        assert "↻ step failed: test -f a" in out and "next: retry 1 of 2" in out
        assert "↻ reflection: a was never written" in out
        assert published == [(EventKind.REFLECTION, {"step": 4, "rung": "retry 1 of 2", "check": "test -f a",
                                                     "got": "(no output; exit 1)",
                                                     "reflection": "a was never written; write it"})]

    def test_verify_command_is_gated(self, worker, monkeypatch):
        monkeypatch.setattr(engine, "gate", lambda cmd, **kw: kw["role"] == "worker" and False)
        monkeypatch.setattr(worker, "_run_command", _no_run, raising=False)
        out = worker._verify({"command": "x", "verify": "curl -f localhost"}, "ok")
        assert "blocked" in out and runtime.failed_output(out)

    def test_parse_keeps_verify(self):
        from sable.agents.worker import TaskAgent
        raw = json.dumps({"command": "x", "explanation": "e", "done": False, "verify": "test -f x"})
        resp = SimpleNamespace(action="", command="x", explanation="e", done=False, raw=raw)
        assert TaskAgent._parse_response(None, resp)["verify"] == "test -f x"

    def test_loop_refuses_third_identical_command(self, worker, monkeypatch):
        from sable.agents import worker as worker_mod
        ran = []
        turns = []
        replies = iter([SimpleNamespace(command="make", explanation="e", done=False, raw="")] * 3
                       + [SimpleNamespace(command="", explanation="e", done=True, raw="")])
        worker._running = True
        worker._breaker = MagicMock(); worker._breaker.wait_while_held.return_value = False
        worker._breaker.check.return_value = None
        worker._memory = MagicMock(); worker._memory.add_turns.side_effect = turns.extend
        worker._memory.build_context.return_value = []
        worker._skill_loader = MagicMock(); worker._skill_loader.load_relevant.return_value = []
        worker._used_skills, worker._skill_validators = [], {}
        worker._guidance_q = __import__("queue").Queue()
        worker._sandbox = MagicMock()
        for name in ("_update_task_status", "_publish", "_record_turn", "_write_live_status",
                     "_write_task_event", "_write_result_summary", "_stdin_reader"):
            monkeypatch.setattr(worker, name, lambda *a, **k: None, raising=False)
        monkeypatch.setattr(worker, "_call_llm", lambda m: next(replies))
        monkeypatch.setattr(worker, "_run_command", lambda c, **k: ran.append(c) or "ok")
        monkeypatch.setattr(engine, "gate", lambda *a, **k: True)
        monkeypatch.setattr(worker_mod.runtime, "run_command", _no_run)
        worker.run()
        assert ran == ["make", "make"]
        assert any("refused" in t["content"] for t in turns if t["role"] == "user")
