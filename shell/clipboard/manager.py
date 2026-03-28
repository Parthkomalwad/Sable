"""Clipboard/snippet manager — interactive TUI picker and CLI parsing."""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shell.telemetry.db import Database

PURPLE = '\033[38;5;141m'
GREEN  = '\033[38;5;114m'
RED    = '\033[38;5;203m'
DIM    = '\033[2;37m'
RESET  = '\033[0m'


def _get_tmux_session() -> str:
    """Return the current tmux session name, or empty string if not in tmux."""
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#S"],
            capture_output=True, text=True, timeout=1
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _send_to_main_pane(command: str, session: str) -> None:
    """Send command string to pane 0.0 of the given tmux session."""
    subprocess.run(
        ["tmux", "send-keys", "-t", f"{session}:0.0", command, "Enter"],
        capture_output=True
    )
