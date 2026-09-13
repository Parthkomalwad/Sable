"""Integration tests: LLM backend SSE parsing and JSON fallback chain.

Requires Docker. Tests each backend against a mock HTTP server.
Do not call real LLM APIs here.
"""
import pytest

pytestmark = [
    pytest.mark.integration,
    # Every test below is still a `raise NotImplementedError` placeholder.
    # Re-labelled from "Phase 2" to Phase 3.5: backend SSE parsing and native tool-use land with the tool registry (J1), which is where a second transport path first has to be proven.
    # Phase 2 (self-learning skills) never owed these, and the old label
    # claimed a debt against the wrong phase.
    # strict=True means an XPASS fails the build, so when a real body is
    # written this marker has to come off with it and the test cannot
    # quietly stay unenforced.
    pytest.mark.xfail(reason="Phase 3.5: not implemented yet", strict=True,
                      raises=NotImplementedError),
]


class TestOllamaBackend:
    def test_parses_valid_json_response(self):
        raise NotImplementedError("Phase 3.5: implement test_parses_valid_json_response")

    def test_fallback_strips_markdown_fences(self):
        raise NotImplementedError("Phase 3.5: implement test_fallback_strips_markdown_fences")

    def test_fallback_handles_malformed_json(self):
        raise NotImplementedError("Phase 3.5: implement test_fallback_handles_malformed_json")


class TestOpenAIBackend:
    def test_parses_valid_json_response(self):
        raise NotImplementedError("Phase 3.5: implement test_parses_valid_json_response")

    def test_includes_correct_auth_header(self):
        raise NotImplementedError("Phase 3.5: implement test_includes_correct_auth_header")


class TestAnthropicBackend:
    def test_parses_valid_json_response(self):
        raise NotImplementedError("Phase 3.5: implement test_parses_valid_json_response")

    def test_includes_anthropic_version_header(self):
        """anthropic-version: 2023-06-01 must be present in every request."""
        raise NotImplementedError("Phase 3.5: implement test_includes_anthropic_version_header")
