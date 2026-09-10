"""Integration tests: session context resume on login.

Requires Docker. Tests that compressed context is loaded and injected
into LLM calls correctly on re-login.
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


class TestSessionResume:
    def test_context_loaded_on_login(self):
        """Compressed context from previous session is loaded at startup."""
        raise NotImplementedError("Phase 2: implement test_context_loaded_on_login")

    def test_context_not_loaded_if_too_large(self):
        """Context over 500 tokens is not loaded (would bloat prompts)."""
        raise NotImplementedError("Phase 2: implement test_context_not_loaded_if_too_large")

    def test_resume_banner_displayed(self):
        """Login banner shows compressed turn count and token size."""
        raise NotImplementedError("Phase 2: implement test_resume_banner_displayed")

    def test_context_injected_into_llm_system_prompt(self):
        """Loaded context appears in the system prompt for LLM calls."""
        raise NotImplementedError("Phase 2: implement test_context_injected_into_llm_system_prompt")
