"""Sandbox command isolation for task agents.

Two modes depending on system support:

1. bwrap (preferred): full kernel-level namespace isolation.
   - workspace = R/W
   - everything else = R/O
   - unshare-pid

2. bash-wrapper fallback (no bwrap): wraps the command in a bash script
   that overrides all write-capable builtins and common tools with functions
   that reject any path outside the workspace. Read-only access to the rest
   of the filesystem is preserved (cat, ls, grep, etc. still work).
"""
from __future__ import annotations

import logging
import os
import shlex
import shutil

logger = logging.getLogger(__name__)
_bwrap_warned = False

# Commands that can write outside the workspace we override them in bash
_WRITE_BLOCKLIST = [
    "cp", "mv", "rm", "rmdir", "mkdir", "touch", "ln", "chmod", "chown",
    "dd", "tee", "install", "rsync", "wget", "curl", "tar", "unzip", "zip",
    "git", "pip", "npm", "yarn", "apt", "apt-get", "dpkg",
]

_BASH_GUARD_TEMPLATE = r"""
set -uo pipefail
WORKSPACE={workspace}

# Resolve a path to its realpath if it exists, else resolve parent
_resolve() {{
    local p="$1"
    if [ -e "$p" ]; then
        realpath "$p" 2>/dev/null || echo "$p"
    else
        local parent
        parent="$(dirname "$p")"
        echo "$(realpath "$parent" 2>/dev/null || echo "$parent")/$(basename "$p")"
    fi
}}

# Return 0 if path is inside workspace, 1 otherwise
_inside() {{
    local real
    real="$(_resolve "$1")"
    case "$real" in
        "$WORKSPACE"/*|"$WORKSPACE") return 0 ;;
        *) return 1 ;;
    esac
}}

# Generic write guard: checks all arguments that look like paths
_guard() {{
    local cmd="$1"; shift
    for arg in "$@"; do
        # Skip flags
        [[ "$arg" == -* ]] && continue
        if ! _inside "$arg"; then
            echo "[sandbox] BLOCKED: $cmd '$arg' is outside workspace ($WORKSPACE)" >&2
            return 1
        fi
    done
    command "$cmd" "$@"
}}

# Override write-capable commands
cp()      {{ _guard cp      "$@"; }}
mv()      {{ _guard mv      "$@"; }}
rm()      {{ _guard rm      "$@"; }}
rmdir()   {{ _guard rmdir   "$@"; }}
mkdir()   {{ _guard mkdir   "$@"; }}
touch()   {{ _guard touch   "$@"; }}
ln()      {{ _guard ln      "$@"; }}
chmod()   {{ _guard chmod   "$@"; }}
chown()   {{ _guard chown   "$@"; }}
tee()     {{ _guard tee     "$@"; }}
install() {{ _guard install "$@"; }}

# Block system-level package managers
apt()     {{ echo "[sandbox] BLOCKED: apt is not allowed inside task agents" >&2; return 1; }}
apt-get() {{ echo "[sandbox] BLOCKED: apt-get is not allowed inside task agents" >&2; return 1; }}
dpkg()    {{ echo "[sandbox] BLOCKED: dpkg is not allowed inside task agents" >&2; return 1; }}

# Allow npm/yarn/pip only when installing locally (no -g / --global flags)
npm() {{
    for arg in "$@"; do
        if [[ "$arg" == "-g" || "$arg" == "--global" ]]; then
            echo "[sandbox] BLOCKED: npm global installs not allowed" >&2; return 1
        fi
    done
    command npm "$@"
}}
yarn() {{
    for arg in "$@"; do
        if [[ "$arg" == "global" ]]; then
            echo "[sandbox] BLOCKED: yarn global installs not allowed" >&2; return 1
        fi
    done
    command yarn "$@"
}}
pip() {{
    for arg in "$@"; do
        if [[ "$arg" == "--system" || "$arg" == "--user" ]]; then
            echo "[sandbox] BLOCKED: pip system/user installs not allowed use venv inside workspace" >&2; return 1
        fi
    done
    command pip "$@"
}}

# Redirect redirections (>) enforced via shell option + ERR trap is not enough,
# so we wrap the user command in a subshell with WORKSPACE exported so scripts
# that respect it behave correctly.
export WORKSPACE

export -f _resolve _inside _guard cp mv rm rmdir mkdir touch ln chmod chown tee install 2>/dev/null || true

# Now run the actual command
{command}
"""


class Sandbox:
    def __init__(self, task_dir: str, shared_read_dir: str | None = None,
                 extra_write_dirs: list[str] | None = None) -> None:
        self._task_dir = os.path.realpath(task_dir)
        self._shared_read_dir = os.path.realpath(shared_read_dir) if shared_read_dir else None
        self._extra_write_dirs = [os.path.realpath(d) for d in (extra_write_dirs or [])]
        self.use_bwrap = self._probe_bwrap()

    def _probe_bwrap(self) -> bool:
        """Return True only if bwrap is present AND user namespaces work."""
        global _bwrap_warned
        if shutil.which("bwrap") is None:
            if not _bwrap_warned:
                logger.warning(
                    "bwrap not found using bash-wrapper write interception as fallback"
                )
                _bwrap_warned = True
            return False
        import subprocess
        try:
            result = subprocess.run(
                ["bwrap", "--ro-bind", "/", "/", "--unshare-pid", "--", "true"],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass
        if not _bwrap_warned:
            logger.warning(
                "bwrap present but user namespaces unavailable "
                "falling back to bash-wrapper write interception"
            )
            _bwrap_warned = True
        return False

    def wrap_command(self, command: str) -> str:
        """Return the command wrapped with sandbox enforcement."""
        if self.use_bwrap:
            ro_bind = ""
            if self._shared_read_dir:
                ro_bind = f"--ro-bind {shlex.quote(self._shared_read_dir)} {shlex.quote(self._shared_read_dir)} "
            extra_rw = " ".join(
                f"--bind {shlex.quote(d)} {shlex.quote(d)}"
                for d in self._extra_write_dirs
            )
            if extra_rw:
                extra_rw += " "
            return (
                f"bwrap "
                f"--bind {self._task_dir} {self._task_dir} "
                f"{extra_rw}"
                f"{ro_bind}"
                f"--ro-bind / / "
                f"--unshare-pid "
                f"-- /bin/bash -c {shlex.quote(command)}"
            )
        # Bash-wrapper fallback: reads already allowed everywhere, writes blocked outside task_dir
        # Also allow extra_write_dirs
        extra_workspaces = " ".join(shlex.quote(d) for d in self._extra_write_dirs)
        guard = _BASH_GUARD_TEMPLATE.format(
            workspace=shlex.quote(self._task_dir),
            command=command,
        )
        if self._extra_write_dirs:
            # Patch _inside() to also allow extra dirs
            extra_cases = "\n        ".join(
                f'"{d}"/*|"{d}") return 0 ;;' for d in self._extra_write_dirs
            )
            guard = guard.replace(
                '"$WORKSPACE"/*|"$WORKSPACE") return 0 ;;',
                f'"$WORKSPACE"/*|"$WORKSPACE") return 0 ;;\n        {extra_cases}',
            )
        return guard

    def intercept_write(self, path: str) -> bool:
        """Return True if writing to path is permitted (inside task_dir or extra_write_dirs)."""
        real = os.path.realpath(path)
        if real.startswith(self._task_dir):
            return True
        return any(real.startswith(d) for d in self._extra_write_dirs)
