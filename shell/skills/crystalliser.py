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


_SKILL_SYSTEM_PROMPT = """You are a skill documentation writer.
Given a list of shell commands a developer runs repeatedly, write a concise
markdown skill file in this format:

# <skill name>

## When to use
<1-2 sentences>

## Steps
<numbered steps>

## Commands
```bash
<commands>
```

Be concise. The skill name should be a short slug like "git-deploy" or "docker-rebuild".
"""

SKILLS_DIR = Path.home() / "skills" / "instructions"


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


class SkillCrystalliser:
    def __init__(self, config, db_path: str | None = None) -> None:
        from shell.telemetry.db import DB_PATH
        self._config = config
        self._db_path = db_path or str(DB_PATH)

    def crystallise(self, pattern: dict) -> Path:
        """Generate skill file from pattern. Returns path to written file."""
        from shell.skills.index import SkillIndex

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
        from shell.loop import _build_backend
        backend = _build_backend(self._config)
        messages = [{"role": "user", "content": prompt}]
        try:
            response = asyncio.run(backend.complete(messages, _SKILL_SYSTEM_PROMPT))
            # LLMResponse has no .content the markdown skill text comes back
            # in .explanation (backends put free-form text there when no JSON found)
            content = response.explanation or response.command or str(response)
        except Exception as exc:
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
