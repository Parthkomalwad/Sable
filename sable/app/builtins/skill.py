"""The `/skill` builtin: list, new and edit skill files.

Extracted from `app/repl.py` in Phase 0.5 step 3. Pure move, no logic change.
"""
from __future__ import annotations

from sable.ui.console import out as _out


def _handle_skill_builtin(parts: list[str]) -> bool:
    """Handle /skill subcommands. Return True if handled."""
    import subprocess
    from pathlib import Path
    skills_dir = Path.home() / "skills" / "instructions"
    skills_dir.mkdir(parents=True, exist_ok=True)

    if not parts or parts[0] == "list":
        files = list(skills_dir.glob("*.md"))
        if not files:
            _out("no skills found")
        for f in files:
            _out(f"  {f.stem}")
        return True

    if parts[0] == "new" and len(parts) >= 2:
        name = parts[1]
        path = skills_dir / f"{name}.md"
        path.write_text(f"# {name}\n\n<!-- describe when to use this skill -->\n")
        _out(f"created {path}")
        return True

    if parts[0] == "edit" and len(parts) >= 2:
        name = parts[1]
        path = skills_dir / f"{name}.md"
        editor = os.environ.get("EDITOR", "nano")
        subprocess.run([editor, str(path)])
        return True

    _out("usage: /skill <list|new <name>|edit <name>>")
    return True
