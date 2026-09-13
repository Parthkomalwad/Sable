"""Per-role model selection (Phase 1, A5).

Roadmap §3.3: routing and summarising are cheap, high-volume jobs a small
local model does well; orchestration is the reasoning core and wants the
strong one. `models: {router, orchestrator, worker, summariser}` carries that,
with any unset role falling back to `model`.

The fallback is the load-bearing property: every config written before Phase 1
has no `models` key at all, and must keep behaving exactly as it did.
"""
from __future__ import annotations

import pytest

from sable.core.config.schema import MODEL_ROLES, ShellConfig
from sable.llm.registry import build_backend


@pytest.fixture(autouse=True)
def _no_mock(monkeypatch):
    """These exercise real backend selection, not the mock short-circuit."""
    monkeypatch.delenv("SABLE_MOCK_LLM", raising=False)


def _config(**overrides) -> ShellConfig:
    config = ShellConfig.defaults()
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


class TestModelFor:
    def test_a_configured_role_wins(self):
        config = _config(model="llama3.1", models={"orchestrator": "claude-sonnet-5"})
        assert config.model_for("orchestrator") == "claude-sonnet-5"

    def test_an_unset_role_falls_back(self):
        config = _config(model="llama3.1", models={"orchestrator": "claude-sonnet-5"})
        assert config.model_for("worker") == "llama3.1"

    def test_no_models_at_all_falls_back(self):
        """The shape of every config file written before Phase 1."""
        config = _config(model="llama3.1", models={})
        assert all(config.model_for(role) == "llama3.1" for role in MODEL_ROLES)

    def test_an_unknown_role_falls_back_rather_than_returning_nothing(self):
        """A backend would send an empty string as its model name."""
        assert _config(model="llama3.1").model_for("nonesuch") == "llama3.1"

    def test_roles_are_independent(self):
        config = _config(
            model="llama3.1",
            models={"orchestrator": "strong-model", "summariser": "cheap-model"},
        )
        assert config.model_for("orchestrator") == "strong-model"
        assert config.model_for("summariser") == "cheap-model"
        assert config.model_for("worker") == "llama3.1"


class TestConfigParsing:
    def test_models_round_trips(self):
        models = {"orchestrator": "claude-sonnet-5", "summariser": "claude-haiku-4-5"}
        config = ShellConfig.from_dict(
            {"backend": "ollama", "model": "llama3.1", "models": models}
        )
        assert config.models == models
        assert ShellConfig.from_dict(config.to_dict()).models == models

    def test_a_config_without_models_parses(self):
        """Backwards compatibility: this is what is on disk today."""
        config = ShellConfig.from_dict({"backend": "ollama", "model": "llama3.1"})
        assert config.models == {}

    def test_to_dict_always_emits_models(self):
        assert "models" in ShellConfig.defaults().to_dict()

    @pytest.mark.parametrize("role", MODEL_ROLES)
    def test_every_documented_role_is_accepted(self, role):
        config = ShellConfig.from_dict(
            {"backend": "ollama", "model": "llama3.1", "models": {role: "some-model"}}
        )
        assert config.models[role] == "some-model"

    def test_an_unknown_role_raises(self):
        """A typo should be caught at startup, not silently fall back forever."""
        with pytest.raises(ValueError, match="Unknown model role"):
            ShellConfig.from_dict(
                {"backend": "ollama", "model": "llama3.1", "models": {"orchestraor": "x"}}
            )

    def test_a_non_string_model_raises(self):
        with pytest.raises(ValueError, match="must be a string"):
            ShellConfig.from_dict(
                {"backend": "ollama", "model": "llama3.1", "models": {"worker": 42}}
            )

    def test_models_must_be_an_object(self):
        with pytest.raises(ValueError, match="must be an object"):
            ShellConfig.from_dict(
                {"backend": "ollama", "model": "llama3.1", "models": ["worker"]}
            )

    def test_an_empty_value_is_treated_as_unset(self):
        """Rather than stored and later sent as the model name."""
        config = ShellConfig.from_dict(
            {"backend": "ollama", "model": "llama3.1", "models": {"worker": ""}}
        )
        assert config.models == {}
        assert config.model_for("worker") == "llama3.1"

    def test_null_models_is_treated_as_absent(self):
        config = ShellConfig.from_dict(
            {"backend": "ollama", "model": "llama3.1", "models": None}
        )
        assert config.models == {}


class TestBuildBackendRouting:
    """build_backend is the single place backends are constructed, so routing
    the model choice through it is what makes this a config change rather than
    a code change (structure.md §3.3)."""

    def test_role_selects_the_models_entry(self):
        config = _config(backend="ollama", models={"worker": "worker-model"})
        assert build_backend(config, role="worker").model == "worker-model"

    def test_role_falls_back_to_the_default_model(self):
        config = _config(backend="ollama", model="llama3.1", models={"worker": "w"})
        assert build_backend(config, role="orchestrator").model == "llama3.1"

    def test_no_role_uses_the_default_model(self):
        """Every caller before Phase 1 passed no role; they must be unaffected
        even when per-role models are configured."""
        config = _config(backend="ollama", model="llama3.1", models={"worker": "w"})
        assert build_backend(config).model == "llama3.1"

    @pytest.mark.parametrize(
        "backend,env",
        [("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")],
    )
    def test_cloud_backends_honour_the_role_too(self, monkeypatch, backend, env):
        monkeypatch.setenv(env, "sk-test")
        config = _config(backend=backend, models={"summariser": "cheap-model"})
        assert build_backend(config, role="summariser").model == "cheap-model"

    def test_role_and_mock_mode_are_independent(self, monkeypatch):
        """mock_mode picks the canned script, role picks the model. Setting
        one must not disturb the other."""
        monkeypatch.setenv("SABLE_MOCK_LLM", "1")
        backend = build_backend(_config(), mock_mode="worker", role="worker")
        assert backend.mode == "worker"

    def test_the_router_role_resolves_even_though_nothing_reads_it(self):
        """`agents/router.py` is pure heuristics and makes no LLM call. The
        role is accepted so a user who configures it early does not lose the
        value; this pins that it resolves rather than raising."""
        config = _config(backend="ollama", models={"router": "tiny-model"})
        assert build_backend(config, role="router").model == "tiny-model"


class TestCallSites:
    """Each agent asks for its own role. These pin the wiring, since a missing
    role argument fails silently by falling back to the default model."""

    def test_orchestrator_asks_for_the_orchestrator_model(self, tmp_path):
        import inspect

        from sable.agents import orchestrator

        source = inspect.getsource(orchestrator.OrchestratorAgent._call_llm)
        assert 'role="orchestrator"' in source

    def test_worker_asks_for_the_worker_model(self):
        import inspect

        from sable.agents import worker

        source = inspect.getsource(worker.TaskAgent._call_llm)
        assert 'role="worker"' in source

    def test_crystalliser_asks_for_the_summariser_model(self):
        import inspect

        from sable.skills import crystalliser

        source = inspect.getsource(crystalliser.SkillCrystalliser.crystallise)
        assert 'role="summariser"' in source
