"""Integration test: full input → route → LLM → safety → execute → log cycle.

Requires Docker. Uses MockLLMBackend from fixtures/mock_llm.py.
Do not add real LLM calls here.
"""
import pytest

pytestmark = [
    pytest.mark.integration,
    # Every test below is still a `raise NotImplementedError` placeholder
    # scheduled for Phase 2. strict=True means an XPASS fails the build, so
    # when Phase 2 writes a real body this marker has to come off with it and
    # the test cannot quietly stay unenforced.
    pytest.mark.xfail(reason="Phase 2: not implemented yet", strict=True,
                      raises=NotImplementedError),
]


class TestFullLoop:
    def test_bash_command_executes_directly(self):
        """A bash-classified input should execute without an LLM call."""
        raise NotImplementedError("Phase 2: implement test_bash_command_executes_directly")

    def test_nl_input_routes_through_llm(self):
        """An NL input should call the mock LLM and execute its response."""
        raise NotImplementedError("Phase 2: implement test_nl_input_routes_through_llm")

    def test_destructive_command_blocked(self):
        """A destructive LLM response should be blocked by safety.py."""
        raise NotImplementedError("Phase 2: implement test_destructive_command_blocked")

    def test_event_written_to_db_after_nl_route(self):
        """After an NL route, a TokenEvent should be written to sessions.db."""
        raise NotImplementedError("Phase 2: implement test_event_written_to_db_after_nl_route")
