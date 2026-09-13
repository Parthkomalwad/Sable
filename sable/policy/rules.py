"""Loading the shipped policy from `defaults/policy.toml`.

Phase 0.5 step 4. The patterns used to be Python lists in `policy/engine.py`;
they are data now, so a rule can be read and audited without reading code,
and so Phase 3 can explain which rule fired and why.

Two things this module is deliberate about.

**It fails loudly.** A missing, malformed or empty policy file raises rather
than falling back to an empty list. An empty blocklist is not a degraded
shell, it is a shell that silently runs `rm -rf /` without asking, so the
only safe response to "I cannot read my own safety rules" is to refuse to
start.

**Order is preserved.** `engine.py` reports the first pattern that matches,
and the tests name specific rules, so the file's order is part of the
contract rather than an accident.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

POLICY_PATH = Path(__file__).parent / "defaults" / "policy.toml"


class PolicyError(RuntimeError):
    """The policy file is missing, unreadable, or does not make sense."""


@dataclass(frozen=True)
class Rule:
    """One policy rule, with the text that explains it to a human."""

    name: str
    pattern: str
    category: str = ""
    why: str = ""

    @property
    def compiled(self) -> re.Pattern[str]:
        return _compile(self.pattern)


@lru_cache(maxsize=None)
def _compile(pattern: str) -> re.Pattern[str]:
    """Compile once and reuse; this runs on every command."""
    return re.compile(pattern)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PolicyError(f"{POLICY_PATH}: {message}")


def _parse_rules(raw: dict, key: str, *, need_metadata: bool) -> tuple[Rule, ...]:
    entries = raw.get(key, [])
    _require(isinstance(entries, list), f"[[{key}]] must be a list of tables")
    _require(bool(entries), f"no [[{key}]] rules found; refusing to run unguarded")

    rules: list[Rule] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"[[{key}]] #{index + 1}"
        name = entry.get("name", "")
        pattern = entry.get("pattern", "")
        _require(bool(name), f"{where} has no name")
        _require(bool(pattern), f"{where} ({name}) has no pattern")
        _require(name not in seen, f"duplicate rule name {name!r}")
        seen.add(name)

        try:
            _compile(pattern)
        except re.error as exc:
            raise PolicyError(f"{POLICY_PATH}: {where} ({name}) has an invalid regex: {exc}") from exc

        if need_metadata:
            # Only enforced for destructive rules: their `why` is what the
            # user is shown when a command is held for confirmation, so a
            # blank one is a real gap rather than a style nit.
            _require(bool(entry.get("why")), f"{where} ({name}) has no 'why'")

        rules.append(Rule(
            name=name,
            pattern=pattern,
            category=entry.get("category", ""),
            why=entry.get("why", ""),
        ))
    return tuple(rules)


@lru_cache(maxsize=1)
def load() -> tuple[tuple[Rule, ...], tuple[Rule, ...]]:
    """Return (destructive_rules, secret_rules) from the policy file.

    Cached: this is read once per process and consulted on every command.
    """
    try:
        raw = tomllib.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PolicyError(
            f"{POLICY_PATH} is missing. Sable will not run without its safety "
            f"rules, because an empty blocklist means destructive commands "
            f"execute without confirmation."
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise PolicyError(f"{POLICY_PATH} is not valid TOML: {exc}") from exc

    return (
        _parse_rules(raw, "destructive", need_metadata=True),
        _parse_rules(raw, "secret", need_metadata=False),
    )


def destructive_rules() -> tuple[Rule, ...]:
    return load()[0]


def secret_rules() -> tuple[Rule, ...]:
    return load()[1]


def match_destructive(command: str) -> Rule | None:
    """The first destructive rule this command trips, if any.

    Returning the rule rather than a bool is what lets the caller say which
    rule fired and why, instead of just refusing.
    """
    for rule in destructive_rules():
        if rule.compiled.search(command):
            return rule
    return None


def match_secret(text: str) -> Rule | None:
    """The first structured-credential format found in `text`, if any."""
    for rule in secret_rules():
        if rule.compiled.search(text):
            return rule
    return None
