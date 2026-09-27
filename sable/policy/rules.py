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
import sys
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sable.core.paths import SABLE_HOME
from sable.policy.tiers import Tier

POLICY_PATH = Path(__file__).parent / "defaults" / "policy.toml"
# Phase 3 Task 3. The admin file is a floor: a user rule may make a command
# stricter than it and never looser. Both are optional; a missing file is no
# rules, not an error.
ADMIN_POLICY_PATH = Path("/etc/sable/policy.toml")
USER_POLICY_PATH = SABLE_HOME / "policy.toml"


class PolicyError(RuntimeError):
    """The policy file is missing, unreadable, or does not make sense."""


@dataclass(frozen=True)
class Rule:
    """One policy rule, with the text that explains it to a human."""

    name: str
    pattern: str
    category: str = ""
    why: str = ""
    tier: Tier = Tier.CONFIRM
    source: str = ""   # the file this rule was read from

    @property
    def compiled(self) -> re.Pattern[str]:
        return _compile(self.pattern)


@lru_cache(maxsize=None)
def _compile(pattern: str) -> re.Pattern[str]:
    """Compile once and reuse; this runs on every command."""
    return re.compile(pattern)


def _require(condition: bool, message: str, path: Path | None = None) -> None:
    if not condition:
        raise PolicyError(f"{path or POLICY_PATH}: {message}")


def _parse_rules(
    raw: dict, key: str, *, need_metadata: bool, path: Path | None = None, required: bool = True,
) -> tuple[Rule, ...]:
    path = path or POLICY_PATH
    entries = raw.get(key, [])
    _require(isinstance(entries, list), f"[[{key}]] must be a list of tables", path)
    if required:
        _require(bool(entries), f"no [[{key}]] rules found; refusing to run unguarded", path)

    rules: list[Rule] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"[[{key}]] #{index + 1}"
        name = entry.get("name", "")
        pattern = entry.get("pattern", "")
        _require(bool(name), f"{where} has no name", path)
        _require(bool(pattern), f"{where} ({name}) has no pattern", path)
        _require(name not in seen, f"duplicate rule name {name!r}", path)
        seen.add(name)

        try:
            _compile(pattern)
        except re.error as exc:
            raise PolicyError(f"{path}: {where} ({name}) has an invalid regex: {exc}") from exc

        if need_metadata:
            # Only enforced for destructive rules: their `why` is what the
            # user is shown when a command is held for confirmation, so a
            # blank one is a real gap rather than a style nit.
            _require(bool(entry.get("why")), f"{where} ({name}) has no 'why'", path)

        # A missing tier is `confirm`, never `allow`: a forgotten field must
        # fail safe rather than quietly wave the command through.
        try:
            tier = Tier(entry.get("tier", Tier.CONFIRM))
        except ValueError as exc:
            raise PolicyError(
                f"{path}: {where} ({name}) has an unknown tier "
                f"{entry['tier']!r}; expected one of {[t.value for t in Tier]}"
            ) from exc

        rules.append(Rule(
            name=name,
            pattern=pattern,
            category=entry.get("category", ""),
            # User and admin rules may omit `why`; the prompt still says
            # which rule in which file fired, never a blank line.
            why=entry.get("why") or f"{tier.value} by rule {name} in {path}",
            tier=tier,
            source=str(path),
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
        _load_layer(ADMIN_POLICY_PATH, strict=True),
        _load_layer(USER_POLICY_PATH, strict=False),
    )


def _load_layer(path: Path, *, strict: bool) -> tuple[Rule, ...]:
    """Optional `[[rule]]` entries from an admin or user file.

    Missing is fine. Malformed is where the two differ: a broken admin file
    raises, because a typo must not silently remove the floor users cannot
    loosen. A broken user file only warns, because the shipped rules and the
    floor still stand without it and the user can fix it from the shell.
    """
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        return _parse_rules(raw, "rule", need_metadata=False, path=path, required=False)
    except FileNotFoundError:
        return ()
    except (tomllib.TOMLDecodeError, PolicyError, OSError) as exc:
        if strict:
            raise PolicyError(f"{path}: cannot be read ({exc}); refusing to run without the admin floor") from exc
        sys.stderr.write(f"sable: ignoring {path}: {exc}\n")
        return ()


def destructive_rules() -> tuple[Rule, ...]:
    return load()[0]


def secret_rules() -> tuple[Rule, ...]:
    return load()[1]


def _first(layer: tuple[Rule, ...], command: str) -> Rule | None:
    return next((r for r in layer if r.compiled.search(command)), None)


def match(command: str) -> Rule | None:
    """The rule that decides this command, across all three files.

    The floor is the admin file's first match, else the shipped defaults'.
    A user rule replaces it only when it is strictly more severe, so a user
    `allow` never beats a floor `confirm`, while a user `deny` beats anything.
    """
    defaults, _, admin, user = load()
    floor = _first(admin, command) or _first(defaults, command)
    mine = _first(user, command)
    if mine and (floor is None or mine.tier.severity > floor.tier.severity):
        return mine
    return floor


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
