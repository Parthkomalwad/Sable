"""Unit tests for shell/memory/compressor.py.

Tests:
- should_compress() fires at correct thresholds
- should_compress() does not fire below thresholds
- Last 2 turns are always preserved by compress()

No LLM calls, no subprocess, no file I/O.
"""
import pytest
from sable.memory.compressor import should_compress


SAMPLE_TURNS = [
    {"role": "user", "content": "list files"},
    {"role": "assistant", "content": "ls -la"},
    {"role": "user", "content": "show disk usage"},
    {"role": "assistant", "content": "df -h"},
    {"role": "user", "content": "what processes are running"},
    {"role": "assistant", "content": "ps aux"},
]


class TestShouldCompress:
    def test_fires_above_token_and_turn_threshold(self):
        config = {"compress_after_tokens": 2000, "compress_after_turns": 5}
        assert should_compress(SAMPLE_TURNS, token_count=2100, config=config) is True

    def test_does_not_fire_below_token_threshold(self):
        config = {"compress_after_tokens": 2000, "compress_after_turns": 5}
        assert should_compress(SAMPLE_TURNS, token_count=1500, config=config) is False

    def test_does_not_fire_below_turn_threshold(self):
        config = {"compress_after_tokens": 2000, "compress_after_turns": 5}
        short_turns = SAMPLE_TURNS[:4]  # only 4 turns
        assert should_compress(short_turns, token_count=2100, config=config) is False

    def test_does_not_fire_at_exact_threshold(self):
        # strictly greater than, not >=
        config = {"compress_after_tokens": 2000, "compress_after_turns": 5}
        assert should_compress(SAMPLE_TURNS, token_count=2000, config=config) is False

    def test_uses_default_thresholds_when_not_in_config(self):
        config = {}
        assert should_compress(SAMPLE_TURNS, token_count=2100, config=config) is True

    def test_custom_thresholds_respected(self):
        config = {"compress_after_tokens": 500, "compress_after_turns": 2}
        short_turns = SAMPLE_TURNS[:4]
        assert should_compress(short_turns, token_count=600, config=config) is True
