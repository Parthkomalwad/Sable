"""Integration test: full input → route → LLM → safety → execute → log cycle.

Requires Docker. Uses MockLLMBackend from fixtures/mock_llm.py.
Do not add real LLM calls here.
"""
import pytest

pytestmark = [
    pytest.mark.integration,
    # Every test below is still a `raise NotImplementedError` placeholder.
    # Re-labelled from "Phase 2" to Phase 4: the full input loop is Phase 4's blocks work (G2): these assert on rendered input/output pairs, which is the surface that phase builds.
    # Phase 2 (self-learning skills) never owed these, and the old label
    # claimed a debt against the wrong phase.
    # strict=True means an XPASS fails the build, so when a real body is
    # written this marker has to come off with it and the test cannot
    # quietly stay unenforced.
    pytest.mark.xfail(reason="Phase 4: not implemented yet", strict=True,
                      raises=NotImplementedError),
]


class TestFullLoop:
    def test_bash_command_executes_directly(self):
        """A bash-classified input should execute without an LLM call."""
        raise NotImplementedError("Phase 4: implement test_bash_command_executes_directly")

    def test_nl_input_routes_through_llm(self):
        """An NL input should call the mock LLM and execute its response."""
        raise NotImplementedError("Phase 4: implement test_nl_input_routes_through_llm")

    def test_destructive_command_blocked(self):
        """A destructive LLM response should be blocked by safety.py."""
        raise NotImplementedError("Phase 4: implement test_destructive_command_blocked")

    def test_event_written_to_db_after_nl_route(self):
        """After an NL route, a TokenEvent should be written to sessions.db."""
        raise NotImplementedError("Phase 4: implement test_event_written_to_db_after_nl_route")
