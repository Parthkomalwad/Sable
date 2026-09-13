"""The `SKILL.md` format and its parser.

B2 turns a skill from a flat markdown file into a folder with a contract:
`name`, `description`, `triggers`, `preconditions`, `validate`, plus the
`status` that Phase 2's approval gate reads and the `source` that Phase 8's
K8 trust floor will read.

These tests treat the format as a contract rather than as a convenience,
because three things already on the roadmap parse it: the loader injects the
body into an agent's context, `/skill approve` writes `status`, and B7's
import/publish has to round-trip a file written by someone else. So a round
trip must not lose an unknown key, and a malformed file must say what is
wrong rather than yielding a half-built Skill.

Frontmatter is TOML inside a `+++` fence, not YAML. CLAUDE.md's approved
dependency list has no YAML parser and `tomllib` is stdlib from 3.11, the
same reasoning `policy/defaults/policy.toml` is written up with. The fence
marker is `+++` rather than `---` precisely so the format never looks like
YAML frontmatter that happens to parse.
"""
from __future__ import annotations

import pytest

from sable.skills.model import (
    Skill,
    SkillFormatError,
    parse_skill,
    render_skill,
)


SAMPLE = """\
+++
name = "deploy-api"
description = "Build and restart the API container"
triggers = ["deploy the api", "restart the api"]
preconditions = ["docker compose is installed"]
validate = "docker compose ps api | grep -q running"
status = "enabled"
source = "user"
+++

# Deploy the API

## Steps
1. Build the image.
2. Restart the service.
"""


class TestParsing:
    def test_reads_every_contract_field(self):
        skill = parse_skill(SAMPLE)
        assert skill.name == "deploy-api"
        assert skill.description == "Build and restart the API container"
        assert skill.triggers == ["deploy the api", "restart the api"]
        assert skill.preconditions == ["docker compose is installed"]
        assert skill.validate == "docker compose ps api | grep -q running"
        assert skill.status == "enabled"
        assert skill.source == "user"

    def test_body_excludes_the_frontmatter(self):
        """The body is what gets injected into a model's context.

        If the fence leaked through, every agent prompt would carry TOML the
        model has no use for, at the top of the most expensive context there is.
        """
        body = parse_skill(SAMPLE).body
        assert body.startswith("# Deploy the API")
        assert "+++" not in body
        assert "description =" not in body

    def test_optional_fields_have_usable_defaults(self):
        skill = parse_skill(
            '+++\nname = "minimal"\ndescription = "d"\n+++\n\nbody\n'
        )
        assert skill.triggers == []
        assert skill.preconditions == []
        assert skill.validate == ""
        # An unreviewed skill is pending: Phase 2's gate says a skill is never
        # enabled without a human, so the safe default is the one that stays inert.
        assert skill.status == "pending"
        assert skill.source == "user"

    def test_crlf_frontmatter_parses(self):
        """A skill edited on Windows, or imported from a repo, still loads."""
        skill = parse_skill(SAMPLE.replace("\n", "\r\n"))
        assert skill.name == "deploy-api"
        assert skill.triggers == ["deploy the api", "restart the api"]

    def test_leading_blank_lines_before_the_fence_are_tolerated(self):
        skill = parse_skill("\n\n" + SAMPLE)
        assert skill.name == "deploy-api"


class TestLegacyFlatFiles:
    """The format that exists on disk today, which Task 2 migrates.

    `crystalliser.py` writes `~/skills/instructions/<slug>.md` with no
    frontmatter at all, and `loader.py` infers a description from the first
    heading. Parsing has to keep working on those files, or every skill a
    user already has becomes unreadable the moment this lands.
    """

    def test_file_without_frontmatter_parses_as_legacy(self):
        skill = parse_skill("# Restart nginx\n\nSteps here.\n", name="restart-nginx")
        assert skill.name == "restart-nginx"
        assert skill.is_legacy is True
        assert skill.triggers == []
        assert skill.body.startswith("# Restart nginx")

    def test_legacy_description_comes_from_the_first_heading(self):
        """The same inference `loader.py` already does, kept in one place."""
        skill = parse_skill("# Restart nginx\n\nSteps.\n", name="restart-nginx")
        assert skill.description == "Restart nginx"

    def test_legacy_without_a_heading_has_an_empty_description(self):
        skill = parse_skill("just prose, no heading\n", name="x")
        assert skill.description == ""
        assert skill.is_legacy is True

    def test_legacy_needs_a_name_from_the_caller(self):
        """There is no frontmatter to take it from, so the filename is it."""
        with pytest.raises(SkillFormatError, match="name"):
            parse_skill("# No frontmatter\n")

    def test_parsed_frontmatter_is_not_legacy(self):
        assert parse_skill(SAMPLE).is_legacy is False


class TestRoundTrip:
    def test_render_then_parse_preserves_every_field(self):
        original = parse_skill(SAMPLE)
        reparsed = parse_skill(render_skill(original))
        assert reparsed == original

    def test_unknown_keys_survive_a_round_trip(self):
        """Forward compatibility, and it is why `extra` exists.

        B7 imports skills written against a later version of this contract,
        and B8 adds per-model notes. A parser that dropped keys it did not
        recognise would silently corrupt those files on the first `/skill edit`.
        """
        source = (
            '+++\nname = "x"\ndescription = "d"\n'
            'model_notes = "needs a tool-use backend"\n+++\n\nbody\n'
        )
        skill = parse_skill(source)
        assert skill.extra["model_notes"] == "needs a tool-use backend"
        assert 'model_notes = "needs a tool-use backend"' in render_skill(skill)

    def test_rendered_frontmatter_is_valid_toml_in_a_fence(self):
        rendered = render_skill(parse_skill(SAMPLE))
        assert rendered.startswith("+++\n")
        assert "\n+++\n" in rendered

    def test_a_legacy_skill_renders_with_frontmatter(self):
        """This is what Task 2's migration writes out."""
        skill = parse_skill("# Restart nginx\n\nSteps.\n", name="restart-nginx")
        rendered = render_skill(skill)
        assert rendered.startswith("+++\n")
        assert parse_skill(rendered).name == "restart-nginx"
        assert parse_skill(rendered).is_legacy is False


class TestMalformedInputRaises:
    """A skill file it cannot trust must say so, not yield a half-built Skill.

    Unlike the policy file, a bad skill is not a reason to refuse to start:
    one unreadable skill must not take the shell down. The caller decides
    that, which is why this raises a typed error instead of exiting.
    """

    def test_unclosed_fence_raises(self):
        with pytest.raises(SkillFormatError, match="unclosed"):
            parse_skill('+++\nname = "x"\ndescription = "d"\n\n# body\n')

    def test_malformed_toml_raises(self):
        with pytest.raises(SkillFormatError, match="not valid TOML"):
            parse_skill('+++\nname = "x\n+++\n\nbody\n')

    def test_missing_name_raises(self):
        with pytest.raises(SkillFormatError, match="name"):
            parse_skill('+++\ndescription = "d"\n+++\n\nbody\n')

    def test_missing_description_raises(self):
        """`description` is what ranking and `/skill list` both show."""
        with pytest.raises(SkillFormatError, match="description"):
            parse_skill('+++\nname = "x"\n+++\n\nbody\n')

    def test_unknown_status_raises(self):
        """Status gates whether a skill is injected, so a typo must not pass."""
        with pytest.raises(SkillFormatError, match="status"):
            parse_skill(
                '+++\nname = "x"\ndescription = "d"\nstatus = "aproved"\n+++\n\nbody\n'
            )

    def test_unknown_source_raises(self):
        with pytest.raises(SkillFormatError, match="source"):
            parse_skill(
                '+++\nname = "x"\ndescription = "d"\nsource = "nowhere"\n+++\n\nbody\n'
            )

    def test_triggers_must_be_a_list_of_strings(self):
        with pytest.raises(SkillFormatError, match="triggers"):
            parse_skill(
                '+++\nname = "x"\ndescription = "d"\ntriggers = "deploy"\n+++\n\nbody\n'
            )

    def test_validate_must_be_a_string(self):
        """B5 runs this as a command, so the wrong type is a real hazard."""
        with pytest.raises(SkillFormatError, match="validate"):
            parse_skill(
                '+++\nname = "x"\ndescription = "d"\nvalidate = ["a", "b"]\n+++\n\nbody\n'
            )

    def test_the_error_names_the_skill_when_one_is_known(self):
        """An error on load is useless if it does not say which file."""
        with pytest.raises(SkillFormatError, match="broken-skill"):
            parse_skill('+++\nname = "x\n+++\n', name="broken-skill")


class TestStatusHelpers:
    """`is_enabled` is the single question every read site asks.

    The loader, the ranker and the announcement all need "may this be
    injected?". Spelling that as one property rather than comparing strings
    at four call sites is what stops a future `status` value from being
    handled in three places and missed in the fourth.
    """

    @pytest.mark.parametrize("status,enabled", [
        ("enabled", True),
        ("pending", False),
        ("disabled", False),
    ])
    def test_only_enabled_skills_may_be_injected(self, status, enabled):
        skill = parse_skill(
            f'+++\nname = "x"\ndescription = "d"\nstatus = "{status}"\n+++\n\nbody\n'
        )
        assert skill.is_enabled is enabled

    def test_a_legacy_skill_is_enabled(self):
        """A skill the user already had working must not go pending.

        Task 2's migration relies on this: silently disabling every existing
        skill would look exactly like the migration having lost them.
        """
        assert parse_skill("# x\n", name="x").is_enabled is True
