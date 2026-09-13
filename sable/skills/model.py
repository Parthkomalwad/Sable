"""The `Skill` dataclass and the `SKILL.md` format it round-trips.

B2 turns a skill from a flat markdown file into a folder with a contract.
`crystalliser.py` writes one, `loader.py` injects one into an agent's
context, `/skill approve` flips one field of one, and B7 will import one
written by a stranger. That is four readers, so the format is a contract and
this module is the only place that knows it.

**Frontmatter is TOML in a `+++` fence.** CLAUDE.md's approved dependency
list has no YAML parser, and `tomllib` has been stdlib since 3.11, which this
project already requires; `policy/defaults/policy.toml` is written up with
the same reasoning. The fence marker is `+++` rather than `---` on purpose,
so a reader never mistakes it for YAML frontmatter that happens to parse:
`triggers = ["a", "b"]` is valid in both languages and means the same thing,
which is exactly how a format drifts into being half-YAML by accident.

**It raises rather than guessing.** A malformed skill yields a
`SkillFormatError` naming the file, not a half-built `Skill` that injects an
empty body into a model's context. Unlike the policy file, though, one bad
skill is not a reason to refuse to start: a missing blocklist means running
`rm -rf /` unasked, while a missing skill just means doing the work by hand.
So this raises a typed error and lets the caller decide, rather than exiting.

**Unknown keys survive.** Anything this version does not recognise is kept in
`extra` and rendered back out. B7 imports skills written against a later
contract and B8 adds per-model notes; a parser that dropped what it did not
understand would corrupt those files on the first `/skill edit`.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field

#: The fence that opens and closes the frontmatter block.
FENCE = "+++"

#: `status` gates injection, so the set is closed and a typo is an error.
#: `pending` is the default: Phase 2's gate says a skill is never enabled
#: without a human, so the value that stays inert is the safe one to assume.
STATUSES = frozenset({"pending", "enabled", "disabled"})

#: Where the skill came from. Phase 8's K8 sets a policy floor by source,
#: which is why `crystallised` is distinguished from `user` from the start.
SOURCES = frozenset({"user", "crystallised", "imported"})

#: Fields this version understands. Everything else lands in `extra`.
_KNOWN = frozenset({
    "name", "description", "triggers", "preconditions",
    "validate", "status", "source",
})


class SkillFormatError(ValueError):
    """A skill file is malformed, or is missing a field the contract requires."""


@dataclass
class Skill:
    """One skill: the contract fields, the markdown body, and anything extra."""

    name: str
    description: str = ""
    body: str = ""
    triggers: list[str] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    validate: str = ""
    status: str = "pending"
    source: str = "user"
    #: Frontmatter keys this version does not know. Rendered back verbatim.
    extra: dict = field(default_factory=dict)
    #: True when parsed from a flat pre-B2 file that had no frontmatter.
    is_legacy: bool = False

    @property
    def is_enabled(self) -> bool:
        """May this skill be injected into an agent's context?

        The loader, the ranker and the announcement all ask exactly this.
        Spelling it once, here, is what stops a future `status` value being
        handled at three call sites and missed at the fourth.
        """
        return self.status == "enabled"


def _require(condition: bool, message: str, name: str | None = None) -> None:
    """Raise a `SkillFormatError` naming the skill, when one is known.

    An error on load is close to useless if it does not say which file, since
    skills are loaded in a batch by the loader.
    """
    if not condition:
        where = f"{name}: " if name else ""
        raise SkillFormatError(f"{where}{message}")


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return `(frontmatter, body)`, with frontmatter None when there is none.

    Tolerates CRLF, so a skill edited on Windows or imported from a repo
    still loads, and leading blank lines before the fence.
    """
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    stripped = normalised.lstrip("\n")

    if not stripped.startswith(FENCE):
        return None, normalised.strip("\n")

    rest = stripped[len(FENCE):].lstrip("\n")
    closing = rest.find(f"\n{FENCE}")
    if closing == -1:
        raise SkillFormatError(
            f"unclosed {FENCE} frontmatter fence: the block is opened but never closed"
        )

    frontmatter = rest[:closing]
    body = rest[closing + len(FENCE) + 1:]
    return frontmatter, body.strip("\n")


def _as_str_list(raw: dict, key: str, name: str | None) -> list[str]:
    value = raw.get(key, [])
    _require(
        isinstance(value, list) and all(isinstance(v, str) for v in value),
        f"'{key}' must be a list of strings",
        name,
    )
    return list(value)


def _first_heading(body: str) -> str:
    """The description a pre-B2 flat file implies.

    `loader.py` already infers a description from the first heading line;
    keeping that inference here means the migration and the loader cannot
    disagree about what an old file means.
    """
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        if stripped:
            return ""
    return ""


def parse_skill(text: str, name: str | None = None) -> Skill:
    """Parse `SKILL.md` text into a `Skill`.

    `name` is the fallback identity for a legacy flat file, which has no
    frontmatter to carry one; callers pass the filename stem. It is also used
    to name the file in any error raised here.
    """
    frontmatter, body = _split_frontmatter(text)

    if frontmatter is None:
        # A pre-B2 flat file. It must still load: every skill a user already
        # has is in this shape, and refusing them would look exactly like the
        # migration having lost them.
        _require(
            bool(name),
            "a skill file without frontmatter needs a name from its filename",
        )
        return Skill(
            name=name or "",
            description=_first_heading(body),
            body=body,
            # A skill that already worked stays working. Task 2's migration
            # relies on this: silently sending every existing skill to
            # pending would be indistinguishable from losing them.
            status="enabled",
            is_legacy=True,
        )

    try:
        raw = tomllib.loads(frontmatter)
    except tomllib.TOMLDecodeError as exc:
        raise SkillFormatError(
            f"{name + ': ' if name else ''}frontmatter is not valid TOML: {exc}"
        ) from exc

    skill_name = raw.get("name", "")
    _require(isinstance(skill_name, str) and bool(skill_name),
             "frontmatter has no 'name'", name)

    description = raw.get("description", "")
    _require(
        isinstance(description, str) and bool(description),
        "frontmatter has no 'description'; it is what /skill list and ranking show",
        name or skill_name,
    )

    status = raw.get("status", "pending")
    _require(status in STATUSES,
             f"unknown 'status' {status!r}; expected one of {sorted(STATUSES)}",
             name or skill_name)

    source = raw.get("source", "user")
    _require(source in SOURCES,
             f"unknown 'source' {source!r}; expected one of {sorted(SOURCES)}",
             name or skill_name)

    validate = raw.get("validate", "")
    # B5 runs this as a shell command, so the wrong type is a real hazard
    # rather than a style nit.
    _require(isinstance(validate, str), "'validate' must be a string (a command to run)",
             name or skill_name)

    return Skill(
        name=skill_name,
        description=description,
        body=body,
        triggers=_as_str_list(raw, "triggers", name or skill_name),
        preconditions=_as_str_list(raw, "preconditions", name or skill_name),
        validate=validate,
        status=status,
        source=source,
        extra={k: v for k, v in raw.items() if k not in _KNOWN},
    )


def _toml_value(value) -> str:
    """Render one frontmatter value as TOML.

    Hand-rolled because `tomllib` is read-only and `tomli-w` is not on the
    approved dependency list. The value space here is small and closed:
    strings, lists of strings, and whatever `extra` carried in, which came
    from `tomllib` and so is already a TOML-representable type.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_skill(skill: Skill) -> str:
    """Render a `Skill` back to `SKILL.md` text.

    The inverse of `parse_skill` for everything the contract covers, plus
    `extra`. A legacy skill renders with frontmatter, which is what Task 2's
    migration writes to disk.
    """
    lines = [FENCE]
    lines.append(f"name = {_toml_value(skill.name)}")
    lines.append(f"description = {_toml_value(skill.description)}")
    if skill.triggers:
        lines.append(f"triggers = {_toml_value(skill.triggers)}")
    if skill.preconditions:
        lines.append(f"preconditions = {_toml_value(skill.preconditions)}")
    if skill.validate:
        lines.append(f"validate = {_toml_value(skill.validate)}")
    lines.append(f"status = {_toml_value(skill.status)}")
    lines.append(f"source = {_toml_value(skill.source)}")
    for key, value in skill.extra.items():
        lines.append(f"{key} = {_toml_value(value)}")
    lines.append(FENCE)
    return "\n".join(lines) + "\n\n" + skill.body.strip("\n") + "\n"
