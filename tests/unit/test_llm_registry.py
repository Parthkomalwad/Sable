"""Backend construction, extracted from the REPL in Phase 0.5 step 3.

`build_backend` was previously reachable only through `shell.loop`, which is
why `agents/` and `skills/` imported the REPL to call it. It had no direct
tests: the mock-mode switch was covered via test_mock_llm.py, and the
backend-selection branches not at all.
"""
from __future__ import annotations

import pytest

from sable.core.config.schema import ShellConfig
from sable.llm.registry import build_backend, mock_backend_or_none


@pytest.fixture(autouse=True)
def _no_mock(monkeypatch):
    """Most tests here want the real selection path, not the mock short-circuit."""
    monkeypatch.delenv("SABLE_MOCK_LLM", raising=False)


def _config(**overrides) -> ShellConfig:
    config = ShellConfig.defaults()
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


class TestBackendSelection:
    def test_ollama_is_selected_by_name(self):
        from sable.llm.ollama import OllamaBackend

        assert isinstance(build_backend(_config(backend="ollama")), OllamaBackend)

    def test_openai_is_selected_by_name(self, monkeypatch):
        from sable.llm.openai import OpenAIBackend

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert isinstance(build_backend(_config(backend="openai")), OpenAIBackend)

    def test_anthropic_is_selected_by_name(self, monkeypatch):
        from sable.llm.anthropic import AnthropicBackend

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        assert isinstance(build_backend(_config(backend="anthropic")), AnthropicBackend)

    def test_unknown_backend_falls_back_to_ollama(self):
        """Pre-existing behaviour, pinned so the extraction cannot change it.

        A typo in config should leave a usable local shell rather than an
        exception at startup.
        """
        from sable.llm.ollama import OllamaBackend

        assert isinstance(build_backend(_config(backend="nonesuch")), OllamaBackend)

    def test_ollama_uses_the_configured_base_url(self):
        backend = build_backend(_config(backend="ollama", api_base="http://box:1234"))
        assert "box:1234" in backend.base_url

    def test_ollama_falls_back_to_localhost_when_base_url_is_empty(self):
        backend = build_backend(_config(backend="ollama", api_base=""))
        assert "localhost:11434" in backend.base_url


class TestApiKeyResolution:
    def test_env_var_is_used_when_present(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
        backend = build_backend(_config(backend="openai"))
        assert backend.api_key == "sk-from-env"

    def test_config_attribute_is_used_when_env_is_absent(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        config = _config(backend="openai")
        config.api_key = "sk-from-config"
        assert build_backend(config).api_key == "sk-from-config"

    def test_env_var_wins_over_config(self, monkeypatch):
        """`OPENAI_API_KEY=... sable` should override the stored key."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
        config = _config(backend="openai")
        config.api_key = "sk-from-config"
        assert build_backend(config).api_key == "sk-from-env"

    def test_missing_key_yields_empty_string_not_none(self, monkeypatch):
        """The backends format the key into a header, so None would raise."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert build_backend(_config(backend="anthropic")).api_key == ""


class TestMockSwitch:
    def test_returns_none_when_unset(self):
        assert mock_backend_or_none() is None

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_spellings_enable_the_mock(self, monkeypatch, value):
        monkeypatch.setenv("SABLE_MOCK_LLM", value)
        assert mock_backend_or_none() is not None

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_falsy_spellings_do_not(self, monkeypatch, value):
        monkeypatch.setenv("SABLE_MOCK_LLM", value)
        assert mock_backend_or_none() is None

    def test_mock_short_circuits_backend_selection(self, monkeypatch):
        """With the mock on, the configured backend is never constructed.

        This is what lets the playground run with no API key and no httpx.
        """
        monkeypatch.setenv("SABLE_MOCK_LLM", "1")
        backend = build_backend(_config(backend="anthropic"))
        assert type(backend).__name__ == "MockLLMBackend"

    def test_mock_mode_is_passed_through(self, monkeypatch):
        monkeypatch.setenv("SABLE_MOCK_LLM", "1")
        assert build_backend(_config(), mock_mode="worker").mode == "worker"
