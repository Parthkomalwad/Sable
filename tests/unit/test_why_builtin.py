"""`/why` and `/task <name> replay` as the user reaches them (Phase 1, I9).

test_replay.py covers the storage layer. These go through the real dispatch
chain instead, because a builtin that is only ever called directly can be
wired up wrongly and still pass every test of the thing underneath it.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sable.app.builtins.dispatch import handle_builtin
from sable.core.config.schema import ShellConfig


@pytest.fixture
def replay_db(tmp_path, monkeypatch):
    """Point the replay log at a temp database.

    `why.py` constructs ReplayLog with no path, so it resolves DB_PATH at call
    time; patching the module attribute is what redirects it.
    """
    path = tmp_path / "sessions.db"
    monkeypatch.setattr("sable.core.db.DB_PATH", path)
    return path


def _record(path, agent="w1", turn=1, response="{}", **kwargs):
    from sable.core.events.replay import record_turn
    from sable.policy.engine import redact_text

    return record_turn(
        path,
        redact=redact_text,
        agent=agent,
        role=kwargs.pop("role", "worker"),
        turn=turn,
        system_prompt=kwargs.pop("system_prompt", "you are a worker"),
        messages=kwargs.pop("messages", [{"role": "user", "content": "do the thing"}]),
        response=response,
        **kwargs,
    )


def _dispatch(line: str) -> bool:
    return handle_builtin(
        line, db=None, session_id="s1", config=ShellConfig.defaults(), turns=[]
    )


class TestWhyRouting:
    def test_bare_why_is_handled(self, replay_db, capsys):
        assert _dispatch("/why") is True

    def test_why_with_an_agent_is_handled(self, replay_db, capsys):
        assert _dispatch("/why some-agent") is True

    def test_an_unrelated_line_is_not_claimed(self, replay_db):
        """`/whydoesthis` must not be swallowed by a prefix match."""
        assert _dispatch("/whydoesthis") is False

    def test_why_appears_in_the_help_text(self, capsys):
        _dispatch("/help")
        assert "/why" in capsys.readouterr().out


class TestWhyOutput:
    def test_says_so_when_nothing_is_recorded(self, replay_db, capsys):
        _dispatch("/why")
        assert "nothing recorded yet" in capsys.readouterr().out

    def test_renders_the_last_turn(self, replay_db, capsys):
        _record(replay_db, response='{"command": "ls -la"}')

        _dispatch("/why")

        out = capsys.readouterr().out
        assert "what the model saw" in out
        assert "do the thing" in out
        assert "ls -la" in out

    def test_shows_the_system_prompt_in_full(self, replay_db, capsys):
        """The point of /why: the answer is usually in the framing."""
        _record(replay_db, system_prompt="NEVER use sudo")

        _dispatch("/why")

        assert "NEVER use sudo" in capsys.readouterr().out

    def test_shows_the_most_recent_turn(self, replay_db, capsys):
        _record(replay_db, turn=1, response="older")
        _record(replay_db, turn=2, response="newest")

        _dispatch("/why")

        out = capsys.readouterr().out
        assert "newest" in out
        assert "older" not in out

    def test_can_be_scoped_to_one_agent(self, replay_db, capsys):
        _record(replay_db, agent="alpha", response="from-alpha")
        _record(replay_db, agent="beta", response="from-beta")

        _dispatch("/why alpha")

        out = capsys.readouterr().out
        assert "from-alpha" in out
        assert "from-beta" not in out

    def test_an_unknown_agent_lists_the_known_ones(self, replay_db, capsys):
        """A dead end should say where to go instead."""
        _record(replay_db, agent="alpha")

        _dispatch("/why nobody")

        out = capsys.readouterr().out
        assert "no recorded turns for 'nobody'" in out
        assert "alpha" in out

    def test_a_secret_never_appears_in_the_rendering(self, replay_db, capsys):
        """Redaction happens at write time, so this is really asserting that
        nothing un-redacts on the way out."""
        secret = "sk-proj-abc123XYZ789defGHI456jklMNO012"
        _record(replay_db, messages=[{"role": "user", "content": f"export K={secret}"}])

        _dispatch("/why")

        assert secret not in capsys.readouterr().out


class TestTaskReplay:
    @pytest.fixture(autouse=True)
    def _manager(self):
        """`/task` builds a TaskManager before dispatching the subcommand."""
        with patch("sable.agents.manager.TaskManager", return_value=MagicMock()):
            yield

    def test_replay_is_claimed_by_the_task_builtin(self, replay_db):
        assert _dispatch("/task replay w1") is True

    def test_renders_every_turn_in_order(self, replay_db, capsys):
        _record(replay_db, agent="w1", turn=1, response="first-answer")
        _record(replay_db, agent="w1", turn=2, response="second-answer")

        _dispatch("/task replay w1")

        out = capsys.readouterr().out
        assert out.index("first-answer") < out.index("second-answer")

    def test_reports_the_turn_count_and_cost(self, replay_db, capsys):
        _record(replay_db, agent="w1", turn=1, cost_usd=0.01)
        _record(replay_db, agent="w1", turn=2, cost_usd=0.02)

        _dispatch("/task replay w1")

        out = capsys.readouterr().out
        assert "2 turns" in out
        assert "0.0300" in out

    def test_only_the_named_agent_is_shown(self, replay_db, capsys):
        _record(replay_db, agent="mine", response="my-answer")
        _record(replay_db, agent="theirs", response="their-answer")

        _dispatch("/task replay mine")

        out = capsys.readouterr().out
        assert "my-answer" in out
        assert "their-answer" not in out

    def test_an_agent_with_no_turns_says_so(self, replay_db, capsys):
        _dispatch("/task replay nobody")
        assert "no recorded turns for 'nobody'" in capsys.readouterr().out

    def test_replay_appears_in_the_task_usage_line(self, replay_db, capsys):
        _dispatch("/task")
        assert "replay" in capsys.readouterr().out
