"""Orchestrator turns are recorded in `token_events`.

Found while running a real goal against a paid key: the sidebar showed
`Cost $0.0000 · Calls 0` after a call that had genuinely been billed. Nothing
on the natural-language path wrote a `token_events` row, and that is the table
the sidebar, `/stats`, `/history` and `app/budget.py` all read. So NL spend was
invisible *and* the daily and session budgets could never trip on it.

The numbers already existed in `agent_turns`; they simply never reached the
table everything else reads.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from sable.llm.base import LLMResponse


def _orchestrator(tmp_path, session_id="session-1", goal="list the files"):
    from sable.agents.orchestrator import OrchestratorAgent
    from sable.core.config.schema import ShellConfig

    config = ShellConfig.defaults()
    config.tasks_base_dir = str(tmp_path / "tasks")
    return OrchestratorAgent(
        goal=goal,
        cwd=str(tmp_path),
        config=config,
        db_path=str(tmp_path / "sessions.db"),
        task_manager=MagicMock(),
        session_id=session_id,
    )


def _response(**overrides) -> LLMResponse:
    fields = dict(
        command="ls -la", explanation="list files", safe=True, plan=None,
        prompt_tokens=120, completion_tokens=30, cost_usd=0.0042,
        model="gpt-4o-mini", action="run",
    )
    fields.update(overrides)
    return LLMResponse(**fields)


def _rows(path):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(
            "SELECT session_id, action_type, nl_input, prompt_tokens, "
            "completion_tokens, total_tokens, cost_usd, model FROM token_events"
        ).fetchall()
    finally:
        conn.close()


class TestCostIsRecorded:
    def test_a_turn_writes_one_row(self, tmp_path):
        orchestrator = _orchestrator(tmp_path)

        orchestrator._record_turn(1, [{"role": "user", "content": "hi"}], "{}", _response())

        assert len(_rows(tmp_path / "sessions.db")) == 1

    def test_the_row_carries_the_tokens_and_cost(self, tmp_path):
        orchestrator = _orchestrator(tmp_path)

        orchestrator._record_turn(1, [], "{}", _response())

        (row,) = _rows(tmp_path / "sessions.db")
        _, _, _, prompt_tokens, completion_tokens, total, cost, model = row
        assert (prompt_tokens, completion_tokens) == (120, 30)
        assert total == 150, "total_tokens must be the sum, it is what /stats sums"
        assert cost == pytest.approx(0.0042)
        assert model == "gpt-4o-mini"

    def test_it_is_attributed_to_the_session(self, tmp_path):
        """budget.check_and_enforce scopes the session limit by session_id, so
        a row without one cannot be counted against it."""
        orchestrator = _orchestrator(tmp_path, session_id="abc-123")

        orchestrator._record_turn(1, [], "{}", _response())

        assert _rows(tmp_path / "sessions.db")[0][0] == "abc-123"

    def test_it_is_tagged_nl_route(self, tmp_path):
        """The action_type /history and /stats already render for this path."""
        orchestrator = _orchestrator(tmp_path)

        orchestrator._record_turn(1, [], "{}", _response())

        assert _rows(tmp_path / "sessions.db")[0][1] == "nl_route"

    def test_the_goal_is_stored_as_the_input(self, tmp_path):
        orchestrator = _orchestrator(tmp_path, goal="build a venv")

        orchestrator._record_turn(1, [], "{}", _response())

        assert _rows(tmp_path / "sessions.db")[0][2] == "build a venv"

    def test_every_turn_is_recorded(self, tmp_path):
        """A multi-turn goal costs multiple calls; one row each."""
        orchestrator = _orchestrator(tmp_path)

        for turn in (1, 2, 3):
            orchestrator._record_turn(turn, [], "{}", _response())

        assert len(_rows(tmp_path / "sessions.db")) == 3

    def test_a_zero_cost_turn_is_still_recorded(self, tmp_path):
        """Ollama is free but the call still happened, and `Calls` must count
        it: a silent zero is what made this bug hard to see."""
        orchestrator = _orchestrator(tmp_path)

        orchestrator._record_turn(1, [], "{}", _response(cost_usd=0.0, model="llama3.1"))

        assert len(_rows(tmp_path / "sessions.db")) == 1

    def test_a_broken_database_does_not_break_the_turn(self, tmp_path):
        """Telemetry is worth less than the work it describes."""
        orchestrator = _orchestrator(tmp_path)
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        orchestrator._db_path = str(blocker / "nested.db")

        orchestrator._record_turn(1, [], "{}", _response())  # must not raise


class TestBudgetCanSeeIt:
    """The point of the fix: the limits read `token_events`."""

    def test_spend_reaches_the_table_the_budget_reads(self, tmp_path):
        from unittest.mock import patch

        from sable.core.db import Database

        orchestrator = _orchestrator(tmp_path)
        for _ in range(3):
            orchestrator._record_turn(1, [], "{}", _response(cost_usd=0.01))

        with patch("sable.core.db.DB_PATH", tmp_path / "sessions.db"):
            db = Database()
            try:
                assert db.get_today_stats()["calls"] == 3
                assert db.get_today_stats()["cost"] == pytest.approx(0.03)
                assert db.get_session_spend("session-1") == pytest.approx(0.03)
            finally:
                db.close()


class TestBackwardsCompatibility:
    def test_session_id_is_optional(self, tmp_path):
        """Existing callers and tests construct the orchestrator without one."""
        from sable.agents.orchestrator import OrchestratorAgent
        from sable.core.config.schema import ShellConfig

        config = ShellConfig.defaults()
        config.tasks_base_dir = str(tmp_path / "tasks")

        agent = OrchestratorAgent(
            goal="g", cwd=str(tmp_path), config=config,
            db_path=str(tmp_path / "sessions.db"), task_manager=MagicMock(),
        )

        agent._record_turn(1, [], "{}", _response())
        assert _rows(tmp_path / "sessions.db")[0][0] == ""


class TestPromptGuardsAgainstNarratedDelegation:
    """The other half of the bug: gpt-4o-mini emitted
    `{"action": "done", "explanation": "...I'm delegating to a sub-agent"}`,
    so the spawn was described and never performed."""

    def test_the_prompt_says_done_ends_the_run(self):
        from sable.llm import prompts

        text = prompts.load("orchestrator").lower()
        assert "done ends the run" in text

    def test_the_prompt_forbids_describing_delegation_in_a_done(self):
        from sable.llm import prompts

        text = prompts.load("orchestrator").lower()
        assert "never describe work you" in text
