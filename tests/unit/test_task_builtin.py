"""The `/task` builtin, focused on the spawn-context path.

`/task new` builds a context handoff from the recent orchestrator turns.
That path had no coverage, and the Phase 0.5 split briefly broke it: the
helper that fetched the turns read a module global that did not travel with
it, so `/task new` would have raised NameError at runtime. Nothing caught it
because nothing called it.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sable.app.builtins.task import _build_spawn_context, _handle_task_builtin
from sable.core.config.schema import ShellConfig


@pytest.fixture
def manager():
    """Patch TaskManager where the builtin imports it, inside the function."""
    fake = MagicMock()
    with patch("sable.agents.manager.TaskManager", return_value=fake):
        yield fake


def _turns(count: int) -> list[dict]:
    """Turns in the shape _build_spawn_context's fallback reads."""
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i}"}
        for i in range(count)
    ]


class TestSpawnContext:
    def test_empty_turns_give_no_context(self):
        """No history means nothing worth handing off."""
        assert _build_spawn_context(turns=[], goal="do a thing") == ""

    def test_goal_is_always_present(self):
        context = _build_spawn_context(turns=_turns(2), goal="deploy the app")
        assert "deploy the app" in context

    def test_recent_turns_are_included(self):
        context = _build_spawn_context(turns=_turns(3), goal="g")
        assert "message 2" in context

    def test_only_the_last_eight_turns_are_carried(self):
        """A long session must not blow the sub-agent's context window."""
        context = _build_spawn_context(turns=_turns(20), goal="g")
        assert "message 19" in context
        assert "message 0" not in context


class TestTaskNew:
    def test_spawns_with_the_given_name_and_goal(self, manager):
        handled = _handle_task_builtin(
            ["new", "mytask", "build", "the", "thing"],
            config=ShellConfig.defaults(), db=None, turns=_turns(2),
        )
        assert handled is True
        manager.spawn.assert_called_once()
        args, kwargs = manager.spawn.call_args
        assert args[0] == "mytask"
        assert "build the thing" in args[1]

    def test_context_is_built_from_the_turns_passed_in(self, manager):
        """The regression guard.

        The turns list must reach the context builder through the argument,
        not through a module global. When it did not, this raised NameError.
        """
        _handle_task_builtin(
            ["new", "t", "goal", "here"],
            config=ShellConfig.defaults(), db=None, turns=_turns(3),
        )
        context = manager.spawn.call_args.kwargs.get("context", "")
        assert "goal here" in context

    def test_no_turns_is_not_an_error(self, manager):
        """`/task new` as the very first thing typed in a session."""
        handled = _handle_task_builtin(
            ["new", "t", "goal"],
            config=ShellConfig.defaults(), db=None, turns=None,
        )
        assert handled is True
        manager.spawn.assert_called_once()


class TestUsage:
    def test_bare_task_prints_usage_and_is_handled(self, capsys):
        handled = _handle_task_builtin([], config=ShellConfig.defaults(), db=None)
        assert handled is True
        assert "usage:" in capsys.readouterr().out

    def test_task_list_is_handled(self, manager):
        manager.list_tasks.return_value = []
        assert _handle_task_builtin(
            ["list"], config=ShellConfig.defaults(), db=None
        ) is True
