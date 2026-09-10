"""Compatibility shim: `shell.*` now lives at `sable.*`.

Phase 0.5 moved the package (docs/structure.md §5 step 2). This shim keeps
the old import paths working for one release, because several things outside
this repo's Python code hard-code them and would otherwise break the moment
the move lands:

  - `install.sh` writes tmux send-keys commands running
    `python -m shell.telemetry.watch` and `python -m shell.tasks.panel`, and
    those strings are baked into any tmux session a user already has running.
  - `docker/Dockerfile` and `docker/Dockerfile.playground` build a `sable`
    wrapper around `python -m shell.main`.
  - `/etc/shells` and the chsh'd login shell on an installed machine point at
    the same wrapper.

An installed user upgrading mid-session keeps a working shell, and the
release after next deletes this file (§5 step 7).

Importing anything through `shell.` raises a DeprecationWarning naming the
new path. Warnings are off by default in Python, so this is silent for users
and visible under `python -W error` or pytest, which is what the Phase 0.5
gate checks.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import warnings
from types import ModuleType

from sable import __version__ as __version__  # re-exported, see _ATTRS below

# Old dotted path -> new dotted path. Mirrors the docs/structure.md §2 table.
# Submodules of a mapped package are redirected too, so `shell.llm.base`
# resolves via the `shell.llm` entry without needing its own row.
_MOVED: dict[str, str] = {
    "shell.main": "sable.app.main",
    "shell.loop": "sable.app.repl",
    "shell.mode": "sable.app.mode",
    "shell.tour": "sable.app.tour",
    "shell.paths": "sable.core.paths",
    "shell.executor": "sable.core.executor",
    "shell.safety": "sable.policy.engine",
    "shell.planner": "sable.agents.planner",
    "shell.router": "sable.agents.router",
    "shell.llm": "sable.llm",
    "shell.config": "sable.core.config",
    "shell.config.schema": "sable.core.config.schema",
    "shell.config.wizard": "sable.core.config.wizard",
    "shell.config.keyring": "sable.core.config.keyring",
    "shell.telemetry": "sable.core",
    "shell.telemetry.db": "sable.core.db",
    "shell.telemetry.events": "sable.core.events.types",
    "shell.telemetry.watch": "sable.ui.sidebar.watch",
    "shell.memory": "sable.memory",
    "shell.memory.store": "sable.memory.session",
    "shell.memory.compressor": "sable.memory.compressor",
    "shell.clipboard": "sable.ui.clipboard",
    "shell.clipboard.manager": "sable.ui.clipboard.manager",
    "shell.tui": "sable.ui.tmux",
    "shell.tui.layout": "sable.ui.tmux.layout",
    "shell.tui.panel": "sable.ui.settings_panel",
    "shell.tasks": "sable.agents",
    "shell.tasks.orchestrator": "sable.agents.orchestrator",
    "shell.tasks.agent": "sable.agents.worker",
    "shell.tasks.manager": "sable.agents.manager",
    "shell.tasks.reconcile": "sable.agents.reconcile",
    "shell.tasks.sandbox": "sable.agents.sandbox",
    "shell.tasks.memory": "sable.memory.task",
    "shell.tasks.skills": "sable.skills.loader",
    "shell.tasks.panel": "sable.ui.sidebar.agents_panel",
    "shell.skills": "sable.skills",
    "shell.skills.index": "sable.skills.index",
    "shell.skills.pattern_watcher": "sable.skills.watcher",
    "shell.skills.crystalliser": "sable.skills.crystalliser",
}

# `from shell import loop` reaches the package attribute rather than the
# import system, so the finder below never sees it. These cover that form.
_ATTRS: dict[str, str] = {
    old.split(".", 1)[1]: new
    for old, new in _MOVED.items()
    if old.count(".") == 1
}


def _warn(old: str, new: str) -> None:
    warnings.warn(
        f"{old} has moved to {new}. The shell.* names are a compatibility "
        f"shim and will be removed; import from sable.* instead.",
        DeprecationWarning,
        stacklevel=3,
    )


def _resolve(name: str) -> str | None:
    """Map an old dotted name onto its new one, longest prefix first.

    Longest-first matters: `shell.tasks.memory` must resolve to
    `sable.memory.task` and not be caught by the `shell.tasks` prefix and
    turned into `sable.agents.memory`, which does not exist.
    """
    if name in _MOVED:
        return _MOVED[name]
    for old in sorted(_MOVED, key=len, reverse=True):
        if name.startswith(old + "."):
            return _MOVED[old] + name[len(old):]
    return None


class _AliasLoader(importlib.abc.Loader):
    """Loader that hands back the already-imported new module unchanged.

    Returning the same object rather than executing a copy is the whole
    point: `sys.modules["shell.x"] is sys.modules["sable.y"]`, so module
    level singletons (the Rich Console, the SQLite connection, patched
    attributes in tests) are shared rather than duplicated.
    """

    def __init__(self, new_name: str) -> None:
        self._new_name = new_name

    def create_module(self, spec):
        return importlib.import_module(self._new_name)

    def exec_module(self, module) -> None:
        return None  # already executed under its new name

    def get_code(self, fullname: str):
        """Source of the module under its NEW name, for `python -m`.

        runpy asks the loader for code rather than importing, so without
        this `python -m shell.main` fails outright. install.sh, both
        Dockerfiles and the tmux panes all run `python -m shell.*`, so this
        is the path an installed machine actually uses.
        """
        spec = importlib.util.find_spec(self._new_name)
        if spec is None or spec.loader is None:
            return None
        return spec.loader.get_code(self._new_name)

    def get_source(self, fullname: str):
        spec = importlib.util.find_spec(self._new_name)
        if spec is None or spec.loader is None:
            return None
        return spec.loader.get_source(self._new_name)

    def is_package(self, fullname: str) -> bool:
        return hasattr(importlib.import_module(self._new_name), "__path__")


class _ShimFinder(importlib.abc.MetaPathFinder):
    """Meta-path hook mapping `shell.*` onto the new `sable.*` module."""

    def find_spec(self, fullname: str, path=None, target=None):
        if not fullname.startswith("shell."):
            return None
        new_name = _resolve(fullname)
        if new_name is None:
            return None
        _warn(fullname, new_name)
        target = importlib.import_module(new_name)

        # Only mirror package-ness when the target really is a package.
        # Marking a plain module as one breaks `python -m shell.main`, which
        # then looks for a `shell.main.__main__` submodule and fails. That
        # command is the installed login shell, so getting this wrong takes
        # the shell down on upgrade.
        search_locations = getattr(target, "__path__", None)
        spec = importlib.machinery.ModuleSpec(
            fullname,
            _AliasLoader(new_name),
            is_package=search_locations is not None,
        )
        if search_locations is not None:
            spec.submodule_search_locations = search_locations
        return spec


sys.meta_path.insert(0, _ShimFinder())


def __getattr__(name: str) -> ModuleType:
    """Handle `from shell import loop` and `shell.loop` attribute access."""
    target = _ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module 'shell' has no attribute {name!r}")
    _warn(f"shell.{name}", target)
    module = importlib.import_module(target)
    sys.modules[f"shell.{name}"] = module
    return module


__all__ = ["__version__", *sorted(_ATTRS)]
