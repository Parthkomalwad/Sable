"""A4: the reviewer agent and its place in the orchestrator loop."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from sable.agents import reviewer
from sable.agents.reviewer import Verdict


def _reply(raw):
    return SimpleNamespace(raw=raw)


# --------------------------------------------------------------------------
# reviewer.py
# --------------------------------------------------------------------------

def test_prompt_frames_steps_as_untrusted_and_redacts():
    text = reviewer.build_input("deploy", [{"command": "echo hi",
                                            "output": "key AKIA" + "A" * 16}])
    assert text.startswith("Goal: deploy")
    assert "<untrusted-steps>" in text and "</untrusted-steps>" in text
    assert "AKIA" + "A" * 16 not in text


def test_input_is_capped_and_keeps_the_latest_steps():
    steps = [{"command": f"cmd{i}", "output": "x" * 2000} for i in range(20)]
    text = reviewer.build_input("g", steps)
    assert len(text) < reviewer.INPUT_CAP + 200
    assert "cmd19" in text and "earlier steps cut" in text


def test_output_tail_is_kept():
    text = reviewer.build_input("g", [{"command": "c", "output": "HEAD" + "y" * 5000 + "TAIL"}])
    assert "TAIL" in text and "HEAD" not in text


def test_verdict_parsed_through_fences():
    with patch.object(reviewer.runtime, "call_llm",
                      return_value=_reply('```json\n{"verdict": "pass", "why": "ok"}\n```')):
        assert reviewer.review(None, "g", []) == Verdict("pass", "ok")


def test_unparseable_reasks_once_then_concerns():
    call = MagicMock(return_value=_reply("I think it went fine"))
    with patch.object(reviewer.runtime, "call_llm", call):
        v = reviewer.review(None, "g", [])
    assert v == Verdict("concerns", "reviewer reply unreadable")
    assert call.call_count == 2


def test_reask_can_recover():
    call = MagicMock(side_effect=[_reply("hmm"), _reply('{"verdict": "fail", "why": "no"}')])
    with patch.object(reviewer.runtime, "call_llm", call):
        assert reviewer.review(None, "g", []) == Verdict("fail", "no")


def test_unknown_verdict_is_not_accepted():
    with patch.object(reviewer.runtime, "call_llm", return_value=_reply('{"verdict": "great"}')):
        assert reviewer.review(None, "g", []).verdict == "concerns"


def test_backend_error_is_concerns():
    with patch.object(reviewer.runtime, "call_llm", side_effect=reviewer.runtime.LLMUnavailable("t")):
        assert reviewer.review(None, "g", []).verdict == "concerns"


def test_reviewer_model_defaults_to_orchestrator():
    from sable.core.config.schema import ShellConfig
    cfg = ShellConfig.from_dict({"model": "base", "models": {"orchestrator": "strong"}})
    assert cfg.model_for("reviewer") == "strong"
    cfg = ShellConfig.from_dict({"model": "base", "models": {"reviewer": "judge"}})
    assert cfg.model_for("reviewer") == "judge"
    assert cfg.review == "on"
    assert ShellConfig.from_dict({"model": "m", "review": "off"}).review == "off"
    with pytest.raises(ValueError):
        ShellConfig.from_dict({"model": "m", "review": "maybe"})


# --------------------------------------------------------------------------
# orchestrator flows
# --------------------------------------------------------------------------

def _orch(tmp_path, review="on"):
    from sable.agents.orchestrator import OrchestratorAgent
    config = MagicMock()
    config.tasks_base_dir = str(tmp_path / "tasks")
    config.review = review
    return OrchestratorAgent(goal="make a file", cwd=str(tmp_path), config=config,
                             db_path=str(tmp_path / "t.db"), task_manager=MagicMock())


def _ran(orch, command):
    orch._commands_run += 1
    orch._record_step(command, "(no output; exit 0)")


def _run_review(orch, verdicts, tty=False, answer="n"):
    review = MagicMock(side_effect=[Verdict(*v) for v in verdicts])
    with patch("sable.agents.reviewer.review", review), \
         patch("sable.llm.registry.build_backend"), \
         patch("sable.core.audit.write_action") as audit, \
         patch("sys.stdin.isatty", return_value=tty), \
         patch("builtins.input", return_value=answer):
        outcomes = [orch._review({"action": "done"}) for _ in verdicts]
    return outcomes, review, audit


def test_pass_completes_and_is_recorded(tmp_path, capsys):
    orch = _orch(tmp_path)
    _ran(orch, "touch a.txt")
    outcomes, _, audit = _run_review(orch, [("pass", "file exists")])
    assert outcomes == ["done"]
    assert "reviewer: pass  file exists" in capsys.readouterr().out
    audit.assert_called_once_with("review", "pass: file exists")
    kinds = [e.kind for e in orch._bus.since(0)]
    assert "review" in kinds


def test_concerns_completes(tmp_path, capsys):
    orch = _orch(tmp_path)
    _ran(orch, "touch a.txt")
    assert _run_review(orch, [("concerns", "unchecked")])[0] == ["done"]
    assert "reviewer: concerns" in capsys.readouterr().out


def test_first_fail_goes_back_to_the_model(tmp_path):
    orch = _orch(tmp_path)
    _ran(orch, "touch a.txt")
    assert _run_review(orch, [("fail", "wrong dir")])[0] == ["retry"]
    assert "wrong dir" in orch._history[-1]["content"]
    assert orch._history[-1]["role"] == "user"


def test_second_fail_asks_in_a_tty(tmp_path):
    orch = _orch(tmp_path)
    _ran(orch, "touch a.txt")
    outcomes, _, _ = _run_review(orch, [("fail", "a"), ("fail", "b")], tty=True, answer="y")
    assert outcomes == ["retry", "done"]
    orch = _orch(tmp_path)
    _ran(orch, "touch a.txt")
    outcomes, _, _ = _run_review(orch, [("fail", "a"), ("fail", "b")], tty=True, answer="n")
    assert outcomes == ["retry", "failed"]


def test_second_fail_without_tty_fails(tmp_path):
    orch = _orch(tmp_path)
    _ran(orch, "touch a.txt")
    assert _run_review(orch, [("fail", "a"), ("fail", "b")])[0] == ["retry", "failed"]


def test_read_only_goal_skips_review(tmp_path):
    orch = _orch(tmp_path)
    _ran(orch, "ls -la")
    review = MagicMock()
    with patch("sable.agents.reviewer.review", review):
        assert orch._review({}) == "done"
    review.assert_not_called()


def test_graph_join_is_reviewed(tmp_path):
    orch = _orch(tmp_path)
    orch._commands_run = 1
    orch._graph_reports.append("a: done")
    _, review, _ = _run_review(orch, [("pass", "ok")])
    assert review.call_args[0][2][-1]["output"] == "a: done"


def test_config_off_skips_review(tmp_path):
    orch = _orch(tmp_path, review="off")
    _ran(orch, "touch a.txt")
    review = MagicMock()
    with patch("sable.agents.reviewer.review", review):
        assert orch._review({}) == "done"
    review.assert_not_called()


def test_loop_continues_after_first_fail(tmp_path):
    orch = _orch(tmp_path)
    _ran(orch, "touch a.txt")
    orch._handle_done = MagicMock()
    orch._breaker = MagicMock(check=MagicMock(return_value=None))
    orch._call_llm = MagicMock(return_value=SimpleNamespace())
    orch._extract_raw = MagicMock(return_value="")
    orch._record_turn = MagicMock()
    orch._parse_action = MagicMock(return_value={"action": "done", "explanation": "ok"})
    orch._review = MagicMock(side_effect=["retry", "done"])
    orch.run()
    assert orch._review.call_count == 2
    orch._handle_done.assert_called_once()
