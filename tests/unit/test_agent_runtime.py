"""The shared agent runtime (Phase 1, A2).

`agents/runtime.py` holds the three things the orchestrator and the worker
genuinely duplicated: a pty runner with a deadline, an asyncio wrapper around
`backend.complete()`, and a fence-stripping JSON parse. Each had drifted
between the two copies, so these tests pin the behaviour the single version
has to keep.

The pty tests are Unix-only by nature (ptyprocess), which is why the suite runs
in the playground image.
"""
from __future__ import annotations

import asyncio
import sys

import pytest

from sable.agents import runtime

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="ptyprocess is Unix-only"
)

#: Timeout used by the tests that deliberately time out. The unit suite has a
#: 10 second budget and three of these run, so they wait as briefly as the
#: deadline check allows rather than a full second each.
_TINY = 0.25


class TestEscalatedTimeout:
    """Pre-existing worker behaviour, now stated once.

    Killing a half-finished `docker pull` at 120s leaves the agent with no way
    to make progress, so these get ten minutes instead.
    """

    @pytest.mark.parametrize(
        "command",
        [
            "docker compose up -d",
            "npm install",
            "pip install -r requirements.txt",
            "yarn add react",
            "git clone https://example.com/repo",
        ],
    )
    def test_long_running_commands_get_the_longer_ceiling(self, command):
        assert runtime.escalated_timeout(command) == runtime.LONG_COMMAND_TIMEOUT

    @pytest.mark.parametrize("command", ["ls -la", "echo hello", "cat file.txt"])
    def test_ordinary_commands_keep_the_default(self, command):
        assert runtime.escalated_timeout(command) == runtime.COMMAND_TIMEOUT

    def test_the_default_is_overridable(self):
        assert runtime.escalated_timeout("ls", default=30) == 30

    def test_a_marker_anywhere_in_the_line_counts(self):
        """The marker is matched as a substring, as the worker always did:
        `cd /srv && docker compose up` has to get the longer ceiling too."""
        assert runtime.escalated_timeout("cd /srv && docker compose up") == 600


class TestRunCommand:
    def test_returns_what_the_command_printed(self, tmp_path):
        assert "hello" in runtime.run_command("echo hello", cwd=str(tmp_path))

    def test_empty_command_is_reported_not_run(self, tmp_path):
        assert runtime.run_command("   ", cwd=str(tmp_path)) == "(empty command)"

    def test_runs_in_the_given_directory(self, tmp_path):
        (tmp_path / "marker.txt").write_text("x")
        assert "marker.txt" in runtime.run_command("ls", cwd=str(tmp_path))

    def test_stderr_is_captured_too(self, tmp_path):
        """A pty merges the two streams, and the model needs to see errors."""
        output = runtime.run_command("echo oops >&2", cwd=str(tmp_path))
        assert "oops" in output

    def test_a_failing_command_returns_its_output_not_an_exception(self, tmp_path):
        output = runtime.run_command("ls /nonexistent-path-xyz", cwd=str(tmp_path))
        assert "No such file" in output or "cannot access" in output

    def test_timeout_appends_the_marker_the_orchestrator_greps_for(self, tmp_path):
        """`orchestrator._handle_run` looks for "[timeout after" to decide
        whether to delegate the goal to a sub-agent, so the exact text is a
        contract between the runner and its caller."""
        output = runtime.run_command("sleep 30", cwd=str(tmp_path), timeout=_TINY)
        assert f"[timeout after {_TINY}s]" in output

    def test_timeout_calls_the_callback_with_the_limit(self, tmp_path):
        seen: list[float] = []
        runtime.run_command(
            "sleep 30", cwd=str(tmp_path), timeout=_TINY, on_timeout=seen.append
        )
        assert seen == [_TINY]

    def test_no_callback_is_fine(self, tmp_path):
        """The orchestrator passes none; it prints its own line afterwards."""
        assert f"[timeout after {_TINY}s]" in runtime.run_command(
            "sleep 30", cwd=str(tmp_path), timeout=_TINY
        )

    def test_wrap_rewrites_the_command_before_it_runs(self, tmp_path):
        """How the worker applies its Sandbox without the runner knowing."""
        output = runtime.run_command(
            "irrelevant",
            cwd=str(tmp_path),
            wrap=lambda _: "echo wrapped-instead",
        )
        assert "wrapped-instead" in output

    def test_multi_line_scripts_survive(self, tmp_path):
        """Why the command goes through a temp file rather than `bash -c`:
        the sandbox wrapper is a multi-line script with function definitions."""
        script = "greet() { echo from-a-function; }\ngreet\n"
        assert "from-a-function" in runtime.run_command(script, cwd=str(tmp_path))

    def test_the_temp_script_is_cleaned_up(self, tmp_path, monkeypatch):
        created: list[str] = []
        real_mkstemp = runtime.tempfile.mkstemp

        def spy(*args, **kwargs):
            handle, path = real_mkstemp(*args, **kwargs)
            created.append(path)
            return handle, path

        monkeypatch.setattr(runtime.tempfile, "mkstemp", spy)
        runtime.run_command("echo hi", cwd=str(tmp_path))

        import os

        assert created and not any(os.path.exists(p) for p in created)


class _FakeBackend:
    def __init__(self, result="ok", delay=0.0, error=None):
        self._result = result
        self._delay = delay
        self._error = error
        self.seen: list[tuple[list[dict], str]] = []

    async def complete(self, messages, system):
        self.seen.append((messages, system))
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._result


class TestCallLlm:
    def test_returns_the_backend_result(self):
        assert runtime.call_llm(_FakeBackend("answer"), [], "system") == "answer"

    def test_passes_messages_and_system_through(self):
        backend = _FakeBackend()
        messages = [{"role": "user", "content": "hi"}]

        runtime.call_llm(backend, messages, "be helpful")

        assert backend.seen == [(messages, "be helpful")]

    def test_timeout_raises_llm_unavailable(self):
        """A typed exception rather than asyncio.TimeoutError, so a caller can
        distinguish "the model did not answer" from any other failure."""
        with pytest.raises(runtime.LLMUnavailable):
            runtime.call_llm(_FakeBackend(delay=5), [], "system", timeout=0.1)

    def test_llm_unavailable_is_an_agent_error(self):
        assert issubclass(runtime.LLMUnavailable, runtime.AgentError)

    def test_backend_errors_propagate_unchanged(self):
        """Only timeouts are translated. A ConnectionError still reads as one,
        so a caller retrying on connectivity can tell the difference."""
        with pytest.raises(ConnectionError):
            runtime.call_llm(_FakeBackend(error=ConnectionError("refused")), [], "s")

    def test_consecutive_calls_each_get_a_working_loop(self):
        """Both agents call this once per turn, so a closed loop leaking into
        the next call would break every run after the first."""
        backend = _FakeBackend("fine")
        assert [runtime.call_llm(backend, [], "s") for _ in range(3)] == ["fine"] * 3

    def test_leaves_no_running_loop_behind(self):
        runtime.call_llm(_FakeBackend(), [], "s")
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()


class TestStripFences:
    def test_removes_a_json_fence(self):
        assert runtime.strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_removes_a_bare_fence(self):
        assert runtime.strip_fences('```\n{"a": 1}\n```') == '{"a": 1}'

    def test_leaves_unfenced_text_alone(self):
        assert runtime.strip_fences('{"a": 1}') == '{"a": 1}'

    def test_strips_surrounding_whitespace(self):
        assert runtime.strip_fences('\n  {"a": 1}  \n') == '{"a": 1}'


class TestParseJsonAction:
    def test_parses_a_plain_object(self):
        assert runtime.parse_json_action('{"action": "run"}') == {"action": "run"}

    def test_parses_through_fences(self):
        parsed = runtime.parse_json_action('```json\n{"action": "done"}\n```')
        assert parsed == {"action": "done"}

    def test_unparseable_returns_the_default(self):
        """CLAUDE.md: never raise an unhandled parse exception. The agent has
        to be able to report the failure and carry on."""
        default = {"action": "done", "explanation": "could not parse"}
        assert runtime.parse_json_action("not json at all", default) == default

    def test_unparseable_without_a_default_returns_an_empty_dict(self):
        assert runtime.parse_json_action("not json") == {}

    @pytest.mark.parametrize("raw", ["[1, 2, 3]", '"a string"', "42", "null"])
    def test_non_objects_count_as_unparseable(self, raw):
        """Every caller reads keys off the result, so a bare list or string is
        no more usable than a syntax error."""
        assert runtime.parse_json_action(raw, {"action": "done"}) == {"action": "done"}

    def test_the_default_is_copied_not_shared(self):
        """A caller that mutates what it gets back must not corrupt the
        default for the next call."""
        default = {"action": "done"}
        returned = runtime.parse_json_action("bad", default)
        returned["action"] = "mutated"
        assert default == {"action": "done"}
