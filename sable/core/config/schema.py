"""ShellConfig dataclass and validation.

Config is stored at ~/.config/agentic-shell/config.json with 600 permissions.
API keys are stored in the Linux keyring (Phase 2); in Phase 1 they go in config.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

from sable.core.config.migrate import CURRENT


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
MODEL_ROLES = ("router", "orchestrator", "worker", "summariser", "reviewer")


#: G6 palettes; the colours live in ui/theme.py, which core may not import.
THEME_NAMES = ("default", "mono", "high-contrast")


def _theme_or_default(name) -> str:
    return name if name in THEME_NAMES else "default"


OTEL_DEFAULTS = {"endpoint": None, "headers": {}, "service_name": "sable"}


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
    theme: str = "default"  # G6; an unknown name loads as "default"
    # Per-role model overrides (A5). Empty means "use `model` for everything",
    # which is what every config written before Phase 1 says.
    models: dict[str, str] = field(default_factory=dict)
    # Per-job circuit breaker (I2, sable/policy/breaker.py). Keys are
    # tokens, usd, turns, wall_s; a missing key is unlimited, so an empty
    # dict is today's behaviour. breaker_consecutive_failures: None = off.
    per_job_budget: dict[str, float] = field(default_factory=dict)
    breaker_consecutive_failures: int | None = None
    # Per-tool settings (Phase 3.5), e.g. {"web": {"search_provider": "brave"}}.
    # Empty means every tool's defaults, so DuckDuckGo search.
    tools: dict = field(default_factory=dict)
    # Per-tool budgets (J12). A tool name or prefix (`web` covers web.search)
    # to {max_calls_per_goal, max_bytes, max_cost}; empty is unlimited.
    tool_budgets: dict[str, dict[str, float]] = field(default_factory=dict)
    # ntfy (E5): server, topic, reply_topic. The access token is keyring-only.
    notify: dict[str, str] = field(default_factory=dict)
    # MCP servers and trusted tools (Phase 6, sable/mcp/servers.py). Kept
    # here so a settings-panel save does not drop it.
    mcp: dict = field(default_factory=dict)
    # Other hosts for `@NAME` (Phase 9, H5; sable/app/builtins/hosts.py
    # validates them). Kept here so a settings-panel save does not drop it.
    hosts: dict = field(default_factory=dict)
    # Local HH:MM the daemon runs palace consolidation (Phase 7, C3).
    maintenance_time: str = "02:30"
    # Sub-agent resource limits (Phase 8, F5, sable/agents/limits.py).
    # Empty means limits.DEFAULTS; a spawn action may override per task.
    limits: dict = field(default_factory=dict)
    # A4: a reviewer model checks a goal's work before done. "on" | "off".
    review: str = "on"
    # Rehearse a plan on a copy first (Phase 8, F2 K5): auto (2+ changing
    # steps, and every daemon job), always, or off.
    rehearse: str = "auto"
    # OpenTelemetry export (Phase 9, H4, sable/core/otel.py). Off while
    # endpoint is None; header values may be `$SECRET:name`.
    otel: dict = field(default_factory=lambda: {**OTEL_DEFAULTS, "headers": {}})

    def model_for(self, role: str) -> str:
        """The model this role should use, falling back to `model`.

        One place decides, so a role nobody configured and a role that does not
        exist both resolve to the single configured model rather than to an
        empty string that a backend would send as its model name.
        """
        if role == "reviewer":
            # A4: the reviewer defaults to the orchestrator's model, not `model`.
            return self.models.get("reviewer") or self.model_for("orchestrator")
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

        per_job = data.get("per_job_budget") or {}
        if not isinstance(per_job, dict):
            raise ValueError(f"per_job_budget must be an object, got {per_job!r}")
        for key, limit in per_job.items():
            if key not in ("tokens", "usd", "turns", "wall_s"):
                raise ValueError(f"Unknown per_job_budget key: {key!r}")
            if limit is not None and (isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit <= 0):
                raise ValueError(f"per_job_budget.{key} must be a positive number or null, got {limit!r}")
        failures = data.get("breaker_consecutive_failures")
        if failures is not None and (isinstance(failures, bool) or not isinstance(failures, int) or failures <= 0):
            raise ValueError(f"breaker_consecutive_failures must be a positive int or null, got {failures!r}")

        tools = data.get("tools") or {}
        if not isinstance(tools, dict) or not isinstance(tools.get("web", {}), dict):
            raise ValueError(f"tools must be an object of objects, got {tools!r}")
        provider = tools.get("web", {}).get("search_provider")
        if provider is not None and provider not in ("duckduckgo", "searxng", "brave", "tavily"):
            raise ValueError(f"Unknown tools.web.search_provider: {provider!r}")
        tool_budgets = data.get("tool_budgets") or {}
        if not isinstance(tool_budgets, dict):
            raise ValueError(f"tool_budgets must be an object, got {tool_budgets!r}")
        for tool, spec in tool_budgets.items():
            if not isinstance(tool, str) or not tool.strip() or not isinstance(spec, dict):
                raise ValueError(f"tool_budgets.{tool} must map a tool name to an object, got {spec!r}")
            for key, limit in spec.items():
                if key not in ("max_calls_per_goal", "max_bytes", "max_cost"):
                    raise ValueError(f"Unknown tool_budgets.{tool} key: {key!r}")
                integer = key != "max_cost"
                if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int if integer else (int, float)) or limit <= 0):
                    kind = "positive int" if integer else "positive number"
                    raise ValueError(f"tool_budgets.{tool}.{key} must be a {kind} or null, got {limit!r}")

        notify = data.get("notify") or {}
        if not isinstance(notify, dict) or any(
                k not in ("server", "topic", "reply_topic") or not isinstance(v, str)
                for k, v in notify.items()):
            raise ValueError(f"notify must map server/topic/reply_topic to strings, got {notify!r}")

        mcp = data.get("mcp") or {}
        if not isinstance(mcp, dict):
            raise ValueError(f"mcp must be an object, got {mcp!r}")

        hosts = data.get("hosts") or {}
        if not isinstance(hosts, dict):
            raise ValueError(f"hosts must be an object, got {hosts!r}")

        maintenance_time = data.get("maintenance_time") or "02:30"
        if not isinstance(maintenance_time, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", maintenance_time):
            raise ValueError(f"maintenance_time must be HH:MM, got {maintenance_time!r}")

        raw_limits = data.get("limits") or {}
        from sable.core.limits import parse as _parse_limits
        _parse_limits(raw_limits)  # raises ValueError
        review = data.get("review", "on")
        if isinstance(review, bool):
            review = "on" if review else "off"
        if review not in ("on", "off"):
            raise ValueError(f"review must be on or off, got {review!r}")

        rehearse = data.get("rehearse") or "auto"
        if rehearse not in ("auto", "always", "off"):
            raise ValueError(f"rehearse must be auto, always or off, got {rehearse!r}")

        otel = {**OTEL_DEFAULTS, **(data.get("otel") or {})}
        if (otel["endpoint"] is not None and not isinstance(otel["endpoint"], str)
                or not isinstance(otel["headers"], dict)
                or not all(isinstance(v, str) for v in otel["headers"].values())
                or not isinstance(otel["service_name"], str)):
            raise ValueError(f"otel must be {{endpoint: str|null, headers: {{str: str}}, service_name: str}}, got {otel!r}")

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
            theme=_theme_or_default(data.get("theme")),
            models=models,
            per_job_budget={k: v for k, v in per_job.items() if v is not None},
            breaker_consecutive_failures=failures,
            tools=tools,
            tool_budgets={t: {k: v for k, v in spec.items() if v is not None}
                          for t, spec in tool_budgets.items()},
            notify=dict(notify),
            mcp=dict(mcp),
            hosts=dict(hosts),
            maintenance_time=maintenance_time,
            limits=dict(raw_limits),
            review=review,
            rehearse=rehearse,
            otel={**otel, "headers": dict(otel["headers"])},
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
            "theme": self.theme,
            "models": dict(self.models),
            "per_job_budget": dict(self.per_job_budget),
            "breaker_consecutive_failures": self.breaker_consecutive_failures,
            "tools": dict(self.tools),
            "tool_budgets": {t: dict(spec) for t, spec in self.tool_budgets.items()},
            "notify": dict(self.notify),
            "mcp": dict(self.mcp),
            "hosts": dict(self.hosts),
            "schema_version": CURRENT,
            "maintenance_time": self.maintenance_time,
            "limits": dict(self.limits),
            "review": self.review,
            "rehearse": self.rehearse,
            "otel": {**self.otel, "headers": dict(self.otel.get("headers") or {})},
            "api_key": getattr(self, "api_key", ""),
        }
