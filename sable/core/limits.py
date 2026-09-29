"""Sub-agent limit values and their validation (F5).

In `core` so the config schema can validate them; enforcement is
`sable/agents/limits.py`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

DEFAULTS = {"mem_mb": 2048, "cpu_s": None, "procs": 256, "network": True}
INT_KEYS = ("mem_mb", "cpu_s", "procs")


@dataclass(frozen=True)
class Limits:
    mem_mb: int | None = None
    cpu_s: int | None = None
    procs: int | None = None
    network: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def parse(raw: dict | None, base: dict | None = None) -> Limits:
    """Validate `raw` over `base` (config defaults). Raises ValueError."""
    if raw is not None and not isinstance(raw, dict):
        raise ValueError(f"limits must be an object, got {raw!r}")
    merged = {**DEFAULTS, **(base or {}), **(raw or {})}
    for key in merged:
        if key not in DEFAULTS:
            raise ValueError(f"Unknown limits key: {key!r}")
    for key in INT_KEYS:
        v = merged[key]
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v <= 0):
            raise ValueError(f"limits.{key} must be a positive int or null, got {v!r}")
    if not isinstance(merged["network"], bool):
        raise ValueError(f"limits.network must be true or false, got {merged['network']!r}")
    return Limits(**merged)
