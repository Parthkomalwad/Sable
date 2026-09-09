"""Unit tests for shell/config/schema.py.

Tests:
- ShellConfig.from_dict() accepts valid configs
- ShellConfig.from_dict() rejects invalid configs
- ShellConfig.to_dict() round-trips correctly

No LLM calls, no subprocess, no file I/O.
"""
import pytest
from shell.config.schema import ShellConfig
from tests.fixtures.sample_configs import (
    OLLAMA_CONFIG,
    OPENAI_CONFIG,
    ANTHROPIC_CONFIG,
    INVALID_CONFIG_MISSING_BACKEND,
    INVALID_CONFIG_BAD_ROUTING_MODE,
)


class TestFromDict:
    def test_valid_ollama_config(self):
        config = ShellConfig.from_dict(OLLAMA_CONFIG)
        assert config.backend == "ollama"
        assert config.model == "llama3.1"
        assert config.routing_mode == "auto"
        assert config.setup_complete is True

    def test_valid_openai_config(self):
        config = ShellConfig.from_dict(OPENAI_CONFIG)
        assert config.backend == "openai"
        assert config.privacy_mode is True
        assert config.daily_token_budget == 100000

    def test_valid_anthropic_config(self):
        config = ShellConfig.from_dict(ANTHROPIC_CONFIG)
        assert config.backend == "anthropic"
        assert config.routing_mode == "prefix"

    def test_missing_backend_defaults_to_ollama(self):
        """A config without a backend is not fatal: it defaults to ollama so a
        hand-edited file keeps working. Only an unrecognised backend raises."""
        config = ShellConfig.from_dict(INVALID_CONFIG_MISSING_BACKEND)
        assert config.backend == "ollama"

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError):
            ShellConfig.from_dict({**INVALID_CONFIG_MISSING_BACKEND, "backend": "nope"})

    def test_invalid_routing_mode_raises(self):
        with pytest.raises(ValueError):
            ShellConfig.from_dict(INVALID_CONFIG_BAD_ROUTING_MODE)


class TestToDict:
    def test_round_trip(self):
        """to_dict() is a superset of the stored config: it always emits
        tasks_base_dir and api_key, which older config files omit."""
        config = ShellConfig.from_dict(OLLAMA_CONFIG)
        result = config.to_dict()

        for key, value in OLLAMA_CONFIG.items():
            assert result[key] == value
        assert result["tasks_base_dir"] == "~/tasks"
        assert result["api_key"] == ""

    def test_round_trip_through_from_dict_is_stable(self):
        config = ShellConfig.from_dict(OLLAMA_CONFIG)
        assert ShellConfig.from_dict(config.to_dict()).to_dict() == config.to_dict()
