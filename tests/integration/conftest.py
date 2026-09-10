"""Shared helpers for the integration suite."""
from __future__ import annotations

import json
import pathlib
import stat

# Distinctive so a test can tell "the config I wrote" from "the user's own
# config" when the suite runs inside a live playground.
TEST_MODEL = "layout-test-model"


def ensure_config() -> None:
    """Write a Sable config file if there is none.

    `docker/playground-entry.sh` writes this before starting tmux, but a bare
    `pytest` run against the image never reaches that code. Without a config,
    anything that starts the shell or a telemetry pane renders the first-run
    wizard instead, and tests that assert on panel content fail for a reason
    that has nothing to do with what they are testing.

    An existing config is left untouched, so running the suite inside a live
    playground does not clobber the user's settings.
    """
    config = pathlib.Path.home() / ".config" / "agentic-shell" / "config.json"
    if config.exists():
        return
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({
        "backend": "ollama",
        "model": TEST_MODEL,
        "api_base": "http://127.0.0.1:11434",
        "routing_mode": "auto",
        "daily_token_budget": None,
        "session_token_budget": None,
        "privacy_mode": False,
        "setup_complete": True,
        "tasks_base_dir": "~/tasks",
    }, indent=2))
    config.chmod(stat.S_IRUSR | stat.S_IWUSR)
