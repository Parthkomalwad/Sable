"""The prompt and word-list loaders added in Phase 0.5 step 4.

These files are not decoration. A missing prompt means an agent runs with no
instructions, and a missing word list means the spinner or the pattern
watcher quietly loses its vocabulary. Both loaders raise rather than return
something empty, and these tests pin that along with the shipped contents.
"""
from __future__ import annotations

import pytest

from sable import data
from sable.llm import prompts


class TestShippedPrompts:
    def test_orchestrator_prompt_is_installed(self):
        assert "orchestrator" in prompts.available()

    def test_skill_writer_prompt_is_installed(self):
        assert "skill_writer" in prompts.available()

    def test_orchestrator_prompt_has_its_action_contract(self):
        """The prompt must still describe the JSON actions the code parses.

        `OrchestratorAgent` dispatches on run / spawn / done. A prompt that
        stopped naming them would leave the model guessing and the failure
        would look like a bad model rather than a bad prompt.
        """
        text = prompts.load("orchestrator")
        for action in ("run", "spawn", "done"):
            assert action in text, f"the orchestrator prompt no longer mentions {action!r}"

    def test_prompts_are_not_empty(self):
        for name in prompts.available():
            assert prompts.load(name).strip(), f"{name} is empty"

    def test_missing_prompt_raises_and_names_what_exists(self):
        with pytest.raises(prompts.PromptNotFound) as exc:
            prompts.load("no_such_prompt")
        assert "orchestrator" in str(exc.value), (
            "the error should list the available prompts so the typo is obvious"
        )

    def test_loading_is_cached(self):
        assert prompts.load("orchestrator") is prompts.load("orchestrator")


class TestShippedWordLists:
    def test_spinner_verbs_count(self):
        """187, not the 186 the docs claimed.

        The old figure came from a grep for single-quoted strings, which
        skipped "Beboppin'" because its apostrophe forces double quotes in
        source. docs/vision.md §2.6 now counts with Python instead.
        """
        assert len(data.load_lines("spinner_verbs")) == 187

    def test_spinner_verbs_have_no_duplicates(self):
        verbs = data.load_lines("spinner_verbs")
        assert len(verbs) == len(set(verbs))

    def test_the_apostrophe_verb_survived_the_move(self):
        """The one entry a naive extraction would mangle."""
        assert "Beboppin'" in data.load_lines("spinner_verbs")

    def test_stopwords_are_loaded_as_a_set(self):
        stopwords = data.load_set("intent_stopwords")
        assert isinstance(stopwords, frozenset)
        assert "the" in stopwords

    def test_missing_file_raises_and_names_what_exists(self):
        with pytest.raises(FileNotFoundError) as exc:
            data.load_lines("no_such_list")
        assert "spinner_verbs" in str(exc.value)

    def test_loading_is_cached(self):
        assert data.load_lines("spinner_verbs") is data.load_lines("spinner_verbs")

    def test_returns_an_immutable_tuple(self):
        """Cached, so a caller must not be able to mutate the shared value."""
        assert isinstance(data.load_lines("spinner_verbs"), tuple)


class TestConsumersUseTheFiles:
    """The modules that used to hold these literals now read the files."""

    def test_orchestrator_uses_the_loaded_verbs_and_prompt(self):
        from sable.agents import orchestrator

        assert len(orchestrator._SPINNER_VERBS) == 187
        assert orchestrator._SYSTEM_PROMPT == prompts.load("orchestrator")

    def test_crystalliser_uses_the_loaded_prompt(self):
        from sable.skills import crystalliser

        assert crystalliser._SKILL_SYSTEM_PROMPT == prompts.load("skill_writer")

    def test_watcher_uses_the_loaded_stopwords(self):
        from sable.skills import watcher

        assert watcher.STOPWORDS == data.load_set("intent_stopwords")


class TestFileFormat:
    def test_comments_and_blank_lines_are_ignored(self, tmp_path, monkeypatch):
        sample = tmp_path / "sample.txt"
        sample.write_text(
            "# a comment\n\nfirst\n  \nsecond\n  # indented comment\nthird\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(data, "DATA_DIR", tmp_path)
        data.load_lines.cache_clear()
        try:
            assert data.load_lines("sample") == ("first", "second", "third")
        finally:
            data.load_lines.cache_clear()
