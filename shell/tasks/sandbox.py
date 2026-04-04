"""Sandbox — command isolation for task agents.

Detects bwrap at runtime. If available: wraps commands with bwrap
(task folder R/W, everything else R/O, unshare-pid).
If not: logs one warning and falls back to intercept_write() which
blocks writes targeting paths outside the task folder.
"""
from __future__ import annotations

import logging
import os
import shutil

logger = logging.getLogger(__name__)
_bwrap_warned = False


class Sandbox:
    def __init__(self, task_dir: str) -> None:
        self._task_dir = os.path.realpath(task_dir)
        self.use_bwrap = shutil.which("bwrap") is not None
        if not self.use_bwrap:
            global _bwrap_warned
            if not _bwrap_warned:
                logger.warning(
                    "bwrap not found — using Python-layer write interception as fallback"
                )
                _bwrap_warned = True

    def wrap_command(self, command: str) -> str:
        """Return the command wrapped in bwrap, or the original if bwrap unavailable."""
        if not self.use_bwrap:
            return command
        return (
            f"bwrap "
            f"--bind {self._task_dir} {self._task_dir} "
            f"--ro-bind / / "
            f"--unshare-pid "
            f"-- /bin/bash -c {shutil.quote(command)}"
        )

    def intercept_write(self, path: str) -> bool:
        """Return True if writing to path is permitted (inside task_dir), False otherwise."""
        real = os.path.realpath(path)
        return real.startswith(self._task_dir)
