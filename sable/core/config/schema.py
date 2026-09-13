"""ShellConfig dataclass and validation.

Config is stored at ~/.config/agentic-shell/config.json with 600 permissions.
API keys are stored in the Linux keyring (Phase 2); in Phase 1 they go in config.
"""
from __future__ import annotations
from dataclasses import dataclass, field


VALID_BACKENDS = {"ollama", "openai", "anthropic", "custom"}
VALID_ROUTING_MODES = {"auto", "prefix"}

#: Roles that may carry their own model (A5, roadmap §3.3). Routing, summarising
#: and skill matching are cheap, high-volume jobs that a small local model does
#: well; orchestration is the reasoning core and wants the strong model. Any
#: role left unset falls back to `model`, so an existing config keeps behaving
#: exactly as it did.
#:
#: `router` is accepted and stored but nothing reads it yet: `agents/router.py`
#: is pure heuristics and makes no LLM call. It is listed here because the
#: roadmap names it and a model-backed router is a later phase; documenting it
#: as unread is better than silently dropping a key a user set.
MODEL_ROLES = ("router", "orchestrator", "worker", "summariser")


@dataclass
class ShellConfig:
    """Runtime configuration for Sable."""
    backend: str                        # "ollama" | "openai" | "anthropic" | "custom"
    model: str                          # e.g. "llama3.1", "gpt-4o", "claude-3-5-haiku-20241022"
    api_base: str | None                # None for cloud backends, URL for Ollama/custom
    routing_mode: str                   # "auto" | "prefix"
    daily_token_budget: int | None      # None = no limit
    session_token_budget: int | None
    privacy_mode: bool                  # strip secrets before sending to model
    setup_complete: bool
    tasks_base_dir: str = "~/tasks"
    # Per-role model overrides (A5). Empty means "use `model` for everything",
    # which is what every config written before Phase 1 says.
    models: dict[str, str] = field(default_factory=dict)

    def model_for(self, role: str) -> str:
        """The model this role should use, falling back to `model`.

        One place decides, so a role nobody configured and a role that does not
        exist both resolve to the single configured model rather than to an
        empty string that a backend would send as its model name.
        """
        return self.models.get(role) or self.model

    @staticmethod
    def defaults() -> "ShellConfig":
        """Return a ShellConfig with sensible defaults."""
        return ShellConfig(
            backend="ollama",
            model="llama3.1",
            api_base="http://localhost:11434",
            routing_mode="auto",
            daily_token_budget=None,
            session_token_budget=None,
            privacy_mode=False,
            setup_complete=False,
            tasks_base_dir="~/tasks",
            models={},
        )

    @staticmethod
    def from_dict(data: dict) -> "ShellConfig":
        """Parse and validate a config dict. Raises ValueError on invalid input."""
        backend = data.get("backend", "ollama")
        if backend not in VALID_BACKENDS:
            raise ValueError(f"Invalid backend: {backend!r}. Must be one of {VALID_BACKENDS}")

        routing_mode = data.get("routing_mode", "auto")
        if routing_mode not in VALID_ROUTING_MODES:
            raise ValueError(f"Invalid routing_mode: {routing_mode!r}. Must be one of {VALID_ROUTING_MODES}")

        daily_budget = data.get("daily_token_budget")
        if daily_budget is not None and (not isinstance(daily_budget, int) or daily_budget <= 0):
            raise ValueError(f"daily_token_budget must be a positive int or null, got {daily_budget!r}")

        session_budget = data.get("session_token_budget")
        if session_budget is not None and (not isinstance(session_budget, int) or session_budget <= 0):
            raise ValueError(f"session_token_budget must be a positive int or null, got {session_budget!r}")

        model = data.get("model", "")
        if not model:
            raise ValueError("model must be a non-empty string")

        raw_models = data.get("models") or {}
        if not isinstance(raw_models, dict):
            raise ValueError(f"models must be an object, got {raw_models!r}")
        models: dict[str, str] = {}
        for role, role_model in raw_models.items():
            if role not in MODEL_ROLES:
                raise ValueError(
                    f"Unknown model role: {role!r}. Must be one of {MODEL_ROLES}"
                )
            if not isinstance(role_model, str):
                raise ValueError(
                    f"models.{role} must be a string, got {role_model!r}"
                )
            # An empty value means "not set": store nothing so model_for falls
            # back rather than sending "" as the model name.
            if role_model:
                models[role] = role_model

        cfg = ShellConfig(
            backend=backend,
            model=model,
            api_base=data.get("api_base") or None,
            routing_mode=routing_mode,
            daily_token_budget=daily_budget,
            session_token_budget=session_budget,
            privacy_mode=bool(data.get("privacy_mode", False)),
            setup_complete=bool(data.get("setup_complete", False)),
            tasks_base_dir=data.get("tasks_base_dir", "~/tasks"),
            models=models,
        )
        if data.get("api_key"):
            cfg.api_key = data["api_key"]  # type: ignore[attr-defined]
        return cfg

    def to_dict(self) -> dict:
        """Serialize config to a JSON-safe dict."""
        return {
            "backend": self.backend,
            "model": self.model,
            "api_base": self.api_base,
            "routing_mode": self.routing_mode,
            "daily_token_budget": self.daily_token_budget,
            "session_token_budget": self.session_token_budget,
            "privacy_mode": self.privacy_mode,
            "setup_complete": self.setup_complete,
            "tasks_base_dir": self.tasks_base_dir,
            "models": dict(self.models),
            "api_key": getattr(self, "api_key", ""),
        }
