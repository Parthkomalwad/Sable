"""System prompts, held as editable markdown rather than Python strings.

Phase 0.5 step 4 (docs/structure.md §5). A prompt is the thing most likely to
be tuned and least likely to need a code change to tune, so it lives in a
file: `sable/llm/prompts/<name>.md`, loaded by name.

Loading is cached, because a prompt is read on every agent turn and the file
does not change under a running process.

A missing prompt raises. Falling back to an empty string would leave an agent
running with no instructions at all, which fails much later and much more
confusingly than an import-time error naming the missing file.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


class PromptNotFound(FileNotFoundError):
    """No prompt file of that name is installed."""


@lru_cache(maxsize=None)
def load(name: str) -> str:
    """Return the prompt text for `name` (without the .md extension)."""
    path = PROMPTS_DIR / f"{name}.md"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        available = sorted(p.stem for p in PROMPTS_DIR.glob("*.md"))
        raise PromptNotFound(
            f"no prompt named {name!r} at {path}. Available: {available}"
        ) from exc


def available() -> list[str]:
    """Every installed prompt name. Backs a future `/prompt show <role>`."""
    return sorted(p.stem for p in PROMPTS_DIR.glob("*.md"))
