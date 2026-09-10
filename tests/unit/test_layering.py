"""The layering rule from docs/structure.md §2, enforced.

    core -> llm -> policy -> agents -> skills / memory / mcp / daemon -> ui -> app

A package may import from packages BELOW it in that list (its dependencies)
and never from packages above. `agents` in particular never imports `ui`: it
publishes events and `ui` renders them. That single rule is what removes the
current `tasks -> loop` circular import and what lets the daemon run agents
with no terminal attached.

This test is the finish line for the Phase 0.5 migration (structure.md §5
step 1). Step 2 moved the tree, so the rule is now live and reporting the
violations that steps 3 and 4 are there to remove:

  - `agents` and `skills` import `app` for `_build_backend` and
    `_write_audit_log`. Step 3 extracts those to `llm/registry.py` and
    `core/audit.py`, both below `agents`.
  - `agents` imports `memory` and `skills`, which sit one layer above it.
    Step 3 inverts those through the events bus.

Until then `test_no_package_imports_its_own_layer_or_higher` fails, on
purpose: it is the migration's progress bar, and it turns green when the
work is done.
"""
from __future__ import annotations

import ast
import os
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Lowest first. Everything on a line may import anything on an earlier line,
# and nothing on a later one. Packages sharing a line are peers and may not
# import each other either, since none of them is below the others.
LAYERS: list[tuple[str, ...]] = [
    ("core",),
    ("llm",),
    ("policy",),
    ("agents",),
    ("skills", "memory", "mcp", "daemon"),
    ("ui",),
    ("app",),
]

# Depth of each package, its index in LAYERS. Lower may not import higher.
DEPTH: dict[str, int] = {
    pkg: i for i, line in enumerate(LAYERS) for pkg in line
}

# A package importing its own siblings (llm/openai.py importing llm.base) is
# ordinary cohesion, not a layering violation. The rule is about crossing
# package boundaries, so same-package imports are skipped entirely. Peers on
# the same LAYERS line are still forbidden from importing each other: nothing
# on that line sits below the others, so an edge between them has no defined
# direction and is exactly the kind of tangle the rule exists to prevent.

# `data/` is inert files and `__init__` / `__main__` sit outside the layering.
EXEMPT_TOP_LEVEL = {"data", "__init__.py", "__main__.py"}


def _iter_python_files(package_root: pathlib.Path):
    for path in sorted(package_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _imports_of(path: pathlib.Path, namespace: str) -> set[str]:
    """Top-level `namespace` subpackages that `path` imports.

    Function-level imports count. Deferring an import inside a function is
    how the current tree hides its circular dependency, so a rule that only
    looked at module scope would call the cycle clean.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError) as exc:
        pytest.fail(f"could not parse {path}: {exc}")

    found: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            # Relative imports stay inside the package, so they cannot cross
            # a layer boundary and are not interesting here.
            if node.level == 0 and node.module:
                names = [node.module]
        for name in names:
            parts = name.split(".")
            if parts[0] == namespace and len(parts) > 1:
                found.add(parts[1])
    return found


def _package_of(path: pathlib.Path, root: pathlib.Path) -> str:
    return path.relative_to(root).parts[0]


def _violations(root: pathlib.Path, namespace: str) -> list[str]:
    """Every import that points at the same layer or higher."""
    problems: list[str] = []
    for path in _iter_python_files(root):
        package = _package_of(path, root)
        if package in EXEMPT_TOP_LEVEL or package not in DEPTH:
            continue
        for imported in sorted(_imports_of(path, namespace)):
            if imported not in DEPTH or imported == package:
                continue
            if DEPTH[imported] >= DEPTH[package]:
                relation = "its own layer" if DEPTH[imported] == DEPTH[package] else "a higher layer"
                problems.append(
                    f"{path.relative_to(REPO_ROOT).as_posix()}: "
                    f"{package} imports {imported}, which is {relation} "
                    f"({package}=L{DEPTH[package]}, {imported}=L{DEPTH[imported]})"
                )
    return problems


sable_root = REPO_ROOT / "sable"
requires_sable = pytest.mark.skipif(
    not sable_root.is_dir(),
    reason="the sable/ tree does not exist yet, Phase 0.5 migration in progress",
)


@requires_sable
class TestSableLayering:
    # Expected to fail until migration steps 3 and 4 land, and marked strict
    # so that the moment they do, this XPASSes and fails the build until the
    # marker comes off. That is the point: the finish line has to announce
    # itself rather than sit here quietly passing as an xfail forever.
    @pytest.mark.xfail(
        reason="steps 3 and 4 remove the remaining agents/skills -> app edges",
        strict=True,
    )
    def test_no_package_imports_its_own_layer_or_higher(self):
        problems = _violations(sable_root, "sable")
        assert not problems, (
            "layering rule violated (docs/structure.md §2):\n  "
            + "\n  ".join(problems)
        )

    def test_agents_never_imports_ui(self):
        """Called out separately in structure.md §2 because it is the one
        that matters most: agents publish events, ui renders them. Break it
        and the daemon can no longer run an agent with no terminal attached.
        """
        agents = sable_root / "agents"
        if not agents.is_dir():
            pytest.skip("sable/agents does not exist yet")
        offenders = [
            path.relative_to(REPO_ROOT).as_posix()
            for path in _iter_python_files(agents)
            if "ui" in _imports_of(path, "sable")
        ]
        assert not offenders, (
            "agents/ must not import ui/, it publishes events instead:\n  "
            + "\n  ".join(offenders)
        )

    def test_every_top_level_package_is_placed_in_the_layering(self):
        """A new package added without a layer assignment is invisible to
        the rule above, which would silently stop enforcing anything for it.
        """
        unplaced = sorted(
            entry.name
            for entry in sable_root.iterdir()
            if entry.is_dir()
            and entry.name != "__pycache__"
            and entry.name not in EXEMPT_TOP_LEVEL
            and entry.name not in DEPTH
        )
        assert not unplaced, (
            f"packages with no layer in LAYERS: {unplaced}. Add them to "
            f"docs/structure.md §2 and to LAYERS in this file."
        )
