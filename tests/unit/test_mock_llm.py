"""Unit tests for the mock LLM backend and the SABLE_MOCK_LLM switch."""
from __future__ import annotations

import asyncio

import pytest

from tests.fixtures.mock_llm import (
    DEFAULT_ORCHESTRATOR_SCRIPT,
    MockLLMBackend,
    mock_llm_enabled,
)


def _complete(backend, text: str):
    return asyncio.run(backend.complete([{"role": "user", "content": text}], "system"))


class TestSingleMode:
    def test_known_input_returns_canned_command(self):
        assert _complete(MockLLMBackend(), "list files").command == "ls -la"

    def test_multi_step_input_returns_a_plan(self):
        response = _complete(MockLLMBackend(), "set up nginx")
        assert response.plan == [
            "apt install nginx -y",
            "systemctl enable nginx",
            "systemctl start nginx",
        ]

    def test_destructive_input_is_flagged_unsafe(self):
        assert _complete(MockLLMBackend(), "delete everything").safe is False

    def test_unknown_input_falls_back(self):
        assert _complete(MockLLMBackend(), "something else").command == "echo 'unknown input'"


class TestOrchestratorMode:
    def test_plays_run_run_spawn_run_done_in_order(self):
        backend = MockLLMBackend(mode="orchestrator")
        messages = [{"role": "user", "content": "create a hello file"}]

        actions = [
            asyncio.run(backend.complete(messages, "system")).action for _ in range(5)
        ]

        assert actions == ["run", "run", "spawn", "run", "done"]

    def test_spawn_step_carries_a_name_and_goal(self):
        backend = MockLLMBackend(mode="orchestrator")
        messages = [{"role": "user", "content": "create a hello file"}]

        for _ in range(2):
            asyncio.run(backend.complete(messages, "system"))
        spawn = asyncio.run(backend.complete(messages, "system"))

        assert spawn.action == "spawn"
        assert spawn.spawn["name"] == "verify-hello"
        assert spawn.spawn["goal"]

    def test_done_step_sets_the_done_flag(self):
        backend = MockLLMBackend(mode="orchestrator", script=DEFAULT_ORCHESTRATOR_SCRIPT)
        messages = [{"role": "user", "content": "anything"}]

        asyncio.run(backend.complete(messages, "system"))
        final = asyncio.run(backend.complete(messages, "system"))

        assert final.action == "done"
        assert final.done is True

    def test_unscripted_goal_still_terminates(self):
        backend = MockLLMBackend(mode="orchestrator")
        messages = [{"role": "user", "content": "no script for this goal"}]

        actions = [
            asyncio.run(backend.complete(messages, "system")).action for _ in range(2)
        ]

        assert actions == ["run", "done"]

    def test_script_holds_on_the_final_action_when_over_run(self):
        backend = MockLLMBackend(mode="orchestrator")
        messages = [{"role": "user", "content": "create a hello file"}]

        for _ in range(5):
            asyncio.run(backend.complete(messages, "system"))
        extra = asyncio.run(backend.complete(messages, "system"))

        assert extra.action == "done"

    def test_script_advances_across_freshly_built_backends(self):
        """OrchestratorAgent builds a new backend every turn, so the script
        position has to come from the conversation, not from instance state.
        A counter on the object would replay step one forever."""
        conversation = [{"role": "user", "content": "create a hello file"}]
        actions = []

        for _ in range(5):
            backend = MockLLMBackend(mode="orchestrator")  # new instance each turn
            response = asyncio.run(backend.complete(conversation, "system"))
            actions.append(response.action)
            # The agent records its action, then the command output.
            conversation.append({"role": "assistant", "content": '{"action": "x"}'})
            conversation.append({"role": "user", "content": "(output)"})

        assert actions == ["run", "run", "spawn", "run", "done"]

    def test_a_cancelled_command_still_advances_the_script(self):
        """A cancelled confirm appends only a user message, so counting
        assistant JSON alone would stall. That happens for real whenever stdin
        is a pipe: input() raises EOF and every command is cancelled."""
        backend = MockLLMBackend(mode="orchestrator")
        conversation = [
            {"role": "user", "content": "create a hello file"},
            {"role": "user", "content": "[user cancelled command: echo hello > hello.txt]"},
        ]

        assert asyncio.run(backend.complete(conversation, "system")).command == "cat hello.txt"

    def test_priming_messages_do_not_advance_the_script(self):
        """The orchestrator seeds two fixed messages plus a "Noted." per folded
        sub-agent status. None of those are actions."""
        backend = MockLLMBackend(mode="orchestrator")
        primed = [
            {"role": "user", "content": "<goal>create a hello file</goal>"},
            {"role": "assistant", "content": "Understood. I will accomplish this goal step by step."},
            {"role": "user", "content": "[agent 'w' COMPLETED]"},
            {"role": "assistant", "content": "Noted."},
        ]

        assert asyncio.run(backend.complete(primed, "system")).action == "run"

    def test_each_instance_starts_at_the_beginning(self):
        messages = [{"role": "user", "content": "create a hello file"}]
        first = MockLLMBackend(mode="orchestrator")
        asyncio.run(first.complete(messages, "system"))

        second = MockLLMBackend(mode="orchestrator")

        assert asyncio.run(second.complete(messages, "system")).action == "run"


class TestWorkerMode:
    def test_plays_commands_then_done(self):
        backend = MockLLMBackend(mode="worker")
        messages = [{"role": "user", "content": "verify-hello"}]

        steps = [asyncio.run(backend.complete(messages, "system")) for _ in range(3)]

        assert [s.command for s in steps[:2]] == ["ls hello.txt", "cat hello.txt"]
        assert steps[2].done is True
        assert steps[2].command == ""

    def test_intermediate_steps_are_not_done(self):
        backend = MockLLMBackend(mode="worker")
        first = asyncio.run(backend.complete([{"role": "user", "content": "x"}], "system"))
        assert first.done is False


class TestMockLlmEnabled:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_values_enable(self, monkeypatch, value):
        monkeypatch.setenv("SABLE_MOCK_LLM", value)
        assert mock_llm_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "", "  "])
    def test_other_values_do_not_enable(self, monkeypatch, value):
        monkeypatch.setenv("SABLE_MOCK_LLM", value)
        assert mock_llm_enabled() is False

    def test_unset_does_not_enable(self, monkeypatch):
        monkeypatch.delenv("SABLE_MOCK_LLM", raising=False)
        assert mock_llm_enabled() is False


class TestBuildBackendWiring:
    def test_build_backend_returns_mock_when_enabled(self, monkeypatch):
        from sable.core.config.schema import ShellConfig
        from sable.llm.registry import build_backend as _build_backend

        monkeypatch.setenv("SABLE_MOCK_LLM", "1")

        backend = _build_backend(ShellConfig.defaults())

        assert isinstance(backend, MockLLMBackend)
        assert backend.mode == "orchestrator"

    def test_worker_mode_is_passed_through(self, monkeypatch):
        from sable.core.config.schema import ShellConfig
        from sable.llm.registry import build_backend as _build_backend

        monkeypatch.setenv("SABLE_MOCK_LLM", "1")

        assert _build_backend(ShellConfig.defaults(), mock_mode="worker").mode == "worker"

    def test_real_backend_when_disabled(self, monkeypatch):
        from sable.core.config.schema import ShellConfig
        from sable.llm.ollama import OllamaBackend
        from sable.llm.registry import build_backend as _build_backend

        monkeypatch.delenv("SABLE_MOCK_LLM", raising=False)

        assert isinstance(_build_backend(ShellConfig.defaults()), OllamaBackend)
