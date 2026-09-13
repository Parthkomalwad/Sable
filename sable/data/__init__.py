"""Plain word lists, one entry per line.

Phase 0.5 step 4 (docs/structure.md §5). These are data that happened to be
written as Python literals: a 187-entry list of spinner verbs and a set of
stopwords. Neither needs code to express, both are things someone might
reasonably want to edit, and a 40-line literal in the middle of
`orchestrator.py` made that file harder to read for no benefit.

Blank lines and `#` comments are ignored, so a list can be annotated.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def load_lines(name: str) -> tuple[str, ...]:
    """Return the non-empty, non-comment lines of `name`.txt, in file order.

    A tuple so the cached value cannot be mutated by a caller.
    """
    path = DATA_DIR / f"{name}.txt"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        available = sorted(p.stem for p in DATA_DIR.glob("*.txt"))
        raise FileNotFoundError(
            f"no data file named {name!r} at {path}. Available: {available}"
        ) from exc

    return tuple(
        line.strip()
        for line in raw.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def load_set(name: str) -> frozenset[str]:
    """Same, as a frozenset, for membership tests."""
    return frozenset(load_lines(name))
