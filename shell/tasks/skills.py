"""TaskSkillLoader keyword-match goal text to skill files.

Local skills (~/tasks/<name>/.agentic/skills/) override global skills
on name collision. Returns list of dicts: {name, content, hash, source}.

Note: load_relevant() is a keyword-only stub in Phase 3.
Phase 5 will upgrade it to consult SkillIndex.get_ranked().
"""
from __future__ import annotations

import hashlib
from pathlib import Path


GLOBAL_SKILLS_DIR = Path.home() / "skills" / "instructions"


class TaskSkillLoader:
    def __init__(self, task_name: str, tasks_base_dir: str) -> None:
        self._local_dir = (
            Path(tasks_base_dir).expanduser() / task_name / ".agentic" / "skills"
        )
        self._global_dir = GLOBAL_SKILLS_DIR

    def load_relevant(self, goal: str) -> list[dict]:
        """Return skills whose filename or first-line description matches goal keywords."""
        keywords = {w.lower() for w in goal.split() if len(w) > 2}
        candidates: dict[str, dict] = {}

        for source, directory in [("global", self._global_dir), ("local", self._local_dir)]:
            if not directory.exists():
                continue
            for path in directory.glob("*.md"):
                name = path.stem
                try:
                    content = path.read_text()
                except OSError:
                    continue
                first_line = content.splitlines()[0].lstrip("#").strip().lower() if content else ""
                matched = any(kw in name.lower() or kw in first_line for kw in keywords)
                if matched:
                    h = hashlib.sha256(content.encode()).hexdigest()
                    candidates[name] = {
                        "name": name,
                        "content": content,
                        "hash": h,
                        "source": source,
                    }

        return list(candidates.values())
