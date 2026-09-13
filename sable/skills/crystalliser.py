"""SkillCrystalliser LLM-driven skill file generator.

Pulls raw command history for a pattern cluster from audit.log.
Sends to LLM with a skill-writing system prompt.
Writes output to skills/instructions/<slug>.md.
Updates skills_index.json.
Marks skill_patterns row crystallised.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from sable.llm import prompts


# Editable markdown at sable/llm/prompts/skill_writer.md.
_SKILL_SYSTEM_PROMPT = prompts.load("skill_writer")

# Editable markdown at sable/llm/prompts/crystallise_check.md. Asks whether a
# finished run is a reusable procedure at all, which `skill_writer` assumes.
_CHECK_SYSTEM_PROMPT = prompts.load("crystallise_check")

#: The pre-B2 flat layout `crystallise()` still writes to. Kept as-is: the
#: `/exit` path is Task 7's to change, and existing tests patch this name.
SKILLS_DIR = Path.home() / "skills" / "instructions"

#: The B2 folder layout `from_run()` writes to.
SKILLS_ROOT = Path.home() / "skills"

#: A run shorter than this is not a procedure, and asking about one would
#: spend a summariser call per trivial goal in the system.
MIN_STEPS_TO_CRYSTALLISE = 3


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


class SkillCrystalliser:
    def __init__(self, config, db_path: str | None = None) -> None:
        from sable.core.db import DB_PATH
        self._config = config
        self._db_path = db_path or str(DB_PATH)

    def crystallise(self, pattern: dict, status: str = "enabled") -> Path:
        """Generate skill file from pattern. Returns path to written file.

        `status` defaults to "enabled" so every existing caller and test is
        unchanged. The `/exit` path passes "pending" (Task 7): a pattern
        crossing the 3x threshold is evidence worth keeping, not a decision
        to start feeding a model text nobody approved.
        """
        from sable.skills.index import SkillIndex

        commands = pattern.get("command_sequence", "").split("|")
        keywords = pattern.get("intent_keywords", [])
        repo = pattern.get("repo_path", "")

        prompt = (
            f"Repository: {repo}\n"
            f"Repeated commands:\n" +
            "\n".join(f"  {c}" for c in commands) +
            f"\nKeywords: {', '.join(keywords)}\n"
            "Write a skill file for this workflow."
        )

        import asyncio
        from sable.llm.registry import build_backend
        # Writing up a skill from a command cluster is summarising, not
        # reasoning: it gets the cheap model when one is configured (A5).
        backend = build_backend(self._config, role="summariser")
        messages = [{"role": "user", "content": prompt}]
        try:
            response = asyncio.run(backend.complete(messages, _SKILL_SYSTEM_PROMPT))
            # LLMResponse has no .content the markdown skill text comes back
            # in .explanation (backends put free-form text there when no JSON found)
            content = response.explanation or response.command or str(response)
        except (OSError, ValueError, RuntimeError, AttributeError) as exc:
            # The skill file is still written, carrying the reason it is empty,
            # so a failed crystallisation is visible on disk rather than absent.
            content = f"# auto-skill\n\n<!-- generation failed: {exc} -->\n"

        slug = _slugify(keywords[0] if keywords else "skill")
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = SKILLS_DIR / f"{slug}.md"
        out_path.write_text(content)

        index = SkillIndex()
        index.add(
            name=slug,
            file=str(out_path),
            keywords=keywords,
            auto_generated=True,
            status=status,
        )

        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "UPDATE skill_patterns SET crystallised=1 WHERE pattern_hash=?",
            (pattern["pattern_hash"],),
        )
        conn.commit()
        conn.close()

        return out_path

    def from_run(self, goal: str, steps: list[dict], succeeded: bool) -> Path | None:
        """Draft a skill from a run that just finished (B3).

        Returns the path written, or None when nothing was drafted.

        `PatternWatcher` only notices a procedure after the same commands
        appear three times across sessions, at `/exit`. This catches the case
        that misses: one multi-step goal that worked, whose procedure is
        worth keeping before it is ever repeated.

        The draft lands **pending**. This is the path that would otherwise
        enable a skill silently, moments after a goal finished, while the
        user is still reading the output, which is exactly what Phase 2's
        gate forbids.

        Never raises. Drafting runs after the work is done and must not turn
        a finished goal into a failure.
        """
        from sable.skills.index import SkillIndex
        from sable.skills.model import Skill, render_skill

        # Checked before the model is asked, so a run that does not qualify
        # costs nothing. Asking first and discarding the answer would spend a
        # summariser call on every short or failed goal in the system.
        if not succeeded or len(steps) < MIN_STEPS_TO_CRYSTALLISE:
            return None

        answer = self._ask_if_reusable(goal, steps)
        if answer is None or not answer.get("reusable"):
            # A refusal is honoured. The model was asked a yes/no question,
            # and overriding its no would make the question theatre.
            return None

        name = _slugify(answer.get("name", "") or goal)
        description = answer.get("description", "")
        body = answer.get("body", "")
        if not name or not description or not body:
            # A yes without the fields to act on is not a usable answer.
            return None

        folder = SKILLS_ROOT / name
        out_path = folder / "SKILL.md"
        if out_path.exists():
            # A skill the user approved, and possibly edited, outranks a new
            # draft. Replacing it would discard their edits and reset a
            # confidence it had earned.
            return None

        triggers = answer.get("triggers", [])
        skill = Skill(
            name=name,
            description=description,
            body=body,
            triggers=[t for t in triggers if isinstance(t, str)],
            validate=answer.get("validate", "") or "",
            status="pending",
            source="crystallised",
        )

        try:
            folder.mkdir(parents=True, exist_ok=True)
            out_path.write_text(render_skill(skill), encoding="utf-8")
        except OSError:
            # Nothing was drafted, and the run it came from is unaffected.
            return None

        SkillIndex().add(
            name=name,
            file=str(out_path),
            keywords=self._keywords(goal, skill.triggers),
            auto_generated=True,
            status="pending",
        )
        return out_path

    @staticmethod
    def _keywords(goal: str, triggers: list[str]) -> list[str]:
        """Index keywords: the goal's words plus the model's trigger phrases.

        Both, because the goal is how this run was asked for and the triggers
        are how the model expects it to be asked for next time.
        """
        words = {w.lower().strip(".,?!") for w in goal.split() if len(w) > 2}
        for phrase in triggers:
            words.update(w.lower() for w in phrase.split() if len(w) > 2)
        return sorted(words)

    def _ask_if_reusable(self, goal: str, steps: list[dict]) -> dict | None:
        """Ask the summariser whether this run is worth keeping. None on failure."""
        import asyncio

        from sable.agents import runtime
        from sable.llm.registry import build_backend

        lines = [f"Goal: {goal}", "", "Commands that achieved it:"]
        for i, step in enumerate(steps, 1):
            command = str(step.get("command", "")).strip()
            explanation = str(step.get("explanation", "")).strip()
            lines.append(f"  {i}. {command}" + (f"   # {explanation}" if explanation else ""))

        # A5: judging reusability is summarising, not reasoning, so it gets
        # the cheap model when one is configured.
        backend = build_backend(self._config, role="summariser")
        messages = [{"role": "user", "content": "\n".join(lines)}]

        try:
            response = asyncio.run(backend.complete(messages, _CHECK_SYSTEM_PROMPT))
        except (OSError, ValueError, RuntimeError, AttributeError):
            # Unlike `crystallise()`, there is no placeholder to write: no
            # threshold was crossed and no evidence is lost by drafting
            # nothing. The run itself already succeeded.
            return None

        # `raw` first: this question's answer carries `reusable`, `name`,
        # `triggers`, `validate` and `body`, none of which is in the base
        # schema, so the typed fields come back empty and reading them lost
        # the entire answer. A live run billed 166 completion tokens and
        # drafted nothing, silently. The other two are kept as a fallback
        # for any backend or fixture that predates the field.
        raw = (
            getattr(response, "raw", "")
            or getattr(response, "explanation", "")
            or getattr(response, "command", "")
        )
        parsed = runtime.parse_json_action(raw, {})
        return parsed or None

    def update(self, pattern: dict) -> Path:
        """Re-crystallise existing skill when usage has diverged."""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "UPDATE skill_patterns SET crystallised=0 WHERE pattern_hash=?",
            (pattern["pattern_hash"],),
        )
        conn.commit()
        conn.close()
        return self.crystallise(pattern)
