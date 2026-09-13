"""A model that answers with two JSON objects in one turn.

Seen in the wild from gpt-4o-mini on a multi-step goal: it emitted a `run`
object and a `spawn` object back to back. That is valid JSON followed by more
valid JSON, which `json.loads` rejects outright as "Extra data", so the whole
turn was lost.

The contract is one action per turn, so the first complete object IS the
answer and anything after it is the model getting ahead of itself.

The second half of the bug was the label: the parse failure surfaced as
"LLM unreachable", sending a real user to check a backend that was answering
perfectly.
"""
from __future__ import annotations

import pytest

from sable.agents import runtime
from sable.llm.base import parse_llm_json

# Verbatim shape of the response that broke it.
TWO_OBJECTS = (
    '{"action": "run", "command": "python3 -m venv ./proj", '
    '"explanation": "Creating a Python virtual environment in the ./proj directory."}\n'
    '{"action": "spawn", "name": "install_requests", '
    '"goal": "Install the requests package in the Python virtual environment located in ./proj.", '
    '"explanation": "Install"}'
)


class TestParseLlmJson:
    def test_a_single_object_still_parses(self):
        assert parse_llm_json('{"command": "ls"}') == {"command": "ls"}

    def test_fences_are_still_stripped(self):
        assert parse_llm_json('```json\n{"command": "ls"}\n```') == {"command": "ls"}

    def test_two_objects_yield_the_first(self):
        """The regression. Previously raised ValueError and lost the turn."""
        parsed = parse_llm_json(TWO_OBJECTS)

        assert parsed["action"] == "run"
        assert parsed["command"] == "python3 -m venv ./proj"

    def test_two_objects_inside_fences(self):
        assert parse_llm_json(f"```json\n{TWO_OBJECTS}\n```")["action"] == "run"

    def test_trailing_prose_after_an_object_is_ignored(self):
        """Models append commentary despite being told not to."""
        raw = '{"command": "ls"}\n\nLet me know if you want me to continue.'
        assert parse_llm_json(raw) == {"command": "ls"}

    def test_genuinely_broken_json_still_raises(self):
        """The fix must not paper over a real parse failure."""
        with pytest.raises(ValueError):
            parse_llm_json("this is not json at all")

    def test_a_bare_list_still_raises(self):
        """Every caller reads keys off the result, so a list is unusable."""
        with pytest.raises(ValueError):
            parse_llm_json('[{"command": "ls"}]')

    def test_truncated_json_still_raises(self):
        with pytest.raises(ValueError):
            parse_llm_json('{"command": "ls"')


class TestParseJsonAction:
    """`runtime.parse_json_action` had the same weakness and never raises."""

    def test_two_objects_yield_the_first(self):
        parsed = runtime.parse_json_action(TWO_OBJECTS, {"action": "done"})

        assert parsed["action"] == "run"
        assert parsed["command"] == "python3 -m venv ./proj"

    def test_a_single_object_is_unaffected(self):
        assert runtime.parse_json_action('{"action": "done"}') == {"action": "done"}

    def test_broken_json_still_returns_the_default(self):
        default = {"action": "done", "explanation": "unparseable"}
        assert runtime.parse_json_action("not json", default) == default

    def test_a_bare_list_still_returns_the_default(self):
        assert runtime.parse_json_action("[1, 2]", {"action": "done"}) == {"action": "done"}


class TestDegradationLabelling:
    """An unusable answer is not an unreachable backend."""

    def test_unparseable_says_the_backend_is_fine(self):
        from sable.core.health import llm_unparseable

        degradation = llm_unparseable("Extra data: line 2")
        assert "backend itself is fine" in degradation.reduced
        assert "unreachable" not in degradation.summary.lower()

    def test_unparseable_suggests_a_stronger_model(self):
        from sable.core.health import llm_unparseable

        assert "stronger model" in llm_unparseable().hint

    def test_unreachable_still_says_bash_works(self):
        from sable.core.health import llm_unreachable

        assert "bash" in llm_unreachable().reduced

    def test_the_two_are_distinguishable(self):
        from sable.core.health import llm_unparseable, llm_unreachable

        assert llm_unparseable().name != llm_unreachable().name


class TestPromptAsksForOneObject:
    def test_the_prompt_says_exactly_one(self):
        from sable.llm import prompts

        assert "exactly one json object" in prompts.load("orchestrator").lower()
