"""Backend construction: config in, an LLMBackend out.

Extracted from `app/repl.py` in Phase 0.5 step 3 (docs/structure.md §5).
It lived there for historical reasons, and `agents/` and `skills/` both
reached back up into the REPL to call it:

    from shell.loop import _build_backend        # agents/worker.py
    from shell.loop import _build_backend        # agents/orchestrator.py
    from shell.loop import _build_backend        # skills/crystalliser.py

That is the circular import the layering rule exists to forbid: `app` is the
composition root and nothing may import from it. Building a backend needs
config and the backend classes, both of which sit below `agents`, so this
belongs in `llm/` and everything can depend on it.
"""
from __future__ import annotations

import os

from sable.core.config.schema import ShellConfig

_DEFAULT_OLLAMA_URL = "http://localhost:11434"

# Truthy spellings accepted for SABLE_MOCK_LLM.
_TRUTHY = {"1", "true", "yes", "on"}


def mock_backend_or_none(mode: str = "orchestrator"):
    """Return a MockLLMBackend when SABLE_MOCK_LLM is set, else None.

    Lets the whole shell run with zero API calls for demos and the playground.
    The fixture lives under tests/, which is not importable from an installed
    copy, so an ImportError here falls back to the real backend rather than
    breaking startup.
    """
    if os.environ.get("SABLE_MOCK_LLM", "").strip().lower() not in _TRUTHY:
        return None
    try:
        from tests.fixtures.mock_llm import MockLLMBackend
    except ImportError:
        # Deliberately not routed through Rich: this can fire before the
        # console exists, and the caller decides how to surface it.
        return None
    return MockLLMBackend(mode=mode)


def build_backend(
    config: ShellConfig, mock_mode: str = "orchestrator", role: str | None = None
):
    """Return the configured LLM backend, or the mock when SABLE_MOCK_LLM is set.

    mock_mode selects which canned script the mock plays: "orchestrator" for
    the REPL's reasoning loop, "worker" for a task agent.

    `role` selects the model (A5): `models.<role>` from config, falling back to
    `model`. This is the single place backends are constructed, so routing the
    choice through here is what makes "cheap model for summarising, strong model
    for orchestrating" a config change rather than a code change. Omitting
    `role` uses `model`, which is what every caller did before Phase 1.
    """
    # Checked before importing the real backends so the mock path does not
    # need httpx installed.
    mock = mock_backend_or_none(mock_mode)
    if mock is not None:
        return mock

    from sable.llm.anthropic import AnthropicBackend
    from sable.llm.ollama import OllamaBackend
    from sable.llm.openai import OpenAIBackend

    model = config.model_for(role) if role else config.model

    if config.backend == "openai":
        api_key = os.environ.get("OPENAI_API_KEY") or getattr(config, "api_key", "") or ""
        return OpenAIBackend(api_key=api_key, model=model)

    if config.backend == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY") or getattr(config, "api_key", "") or ""
        return AnthropicBackend(api_key=api_key, model=model)

    # ollama is both an explicit choice and the fallback for an unknown
    # backend name, matching the behaviour this replaced.
    return OllamaBackend(
        base_url=config.api_base or _DEFAULT_OLLAMA_URL, model=model
    )
