"""G6 themes: the colours the prompt and the shell's own lines use.

Values are ANSI SGR parameter strings, so they drop straight into the
escape codes the prompt already writes. `current()` is the active palette;
`/theme <name>` switches it and persists the name in config.json.
"""
from __future__ import annotations

import json
import os
import stat

THEMES: dict[str, dict[str, str]] = {
    # Roles: path (cwd), git (branch), ok (the check after a clean exit),
    # err (the cross and exit code after a failure), dim (hints), accent
    # (the cursor and Sable's own marks). Foreground only: the prompt is plain
    # text on the terminal's background, like the README mockup.
    "default": {
        "path": "1;38;5;153", "git": "38;5;114",
        "ok": "38;5;114", "err": "38;5;203", "dim": "2;37", "accent": "38;5;141",
    },
    "mono": {
        "path": "1;37", "git": "37",
        "ok": "37", "err": "1;37", "dim": "2;37", "accent": "1;37",
    },
    "high-contrast": {
        "path": "1;97", "git": "1;93",
        "ok": "1;92", "err": "1;91", "dim": "0;97", "accent": "1;93",
    },
}

_current = ["default"]


def current() -> dict[str, str]:
    return THEMES[_current[0]]


def set_theme(name: str) -> bool:
    if name not in THEMES:
        return False
    _current[0] = name
    return True


def sgr(role: str) -> str:
    """The escape sequence for one role of the active theme."""
    return f"\033[{current()[role]}m"


def persist(name: str, path=None) -> bool:
    """Write `theme` into config.json, keeping every other key. Never raises."""
    from sable.core.config.wizard import CONFIG_PATH

    path = path or CONFIG_PATH
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
        data["theme"] = name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        return True
    except (OSError, ValueError):
        return False
