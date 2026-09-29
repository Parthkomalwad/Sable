"""Resource limits for sub-agents (Phase 8, F5).

Memory, CPU time and process count are set in the child with bash's own
`ulimit` builtin, prepended to the command script `runtime.run_command` runs.
`ulimit -v/-t/-u` is setrlimit(RLIMIT_AS/CPU/NPROC) in the bash process, and
every child (bwrap included) inherits it. That is simpler than a preexec hook,
which ptyprocess.spawn does not take, or a `python3 -c` launcher, and needs no
`prlimit`. Network off is bwrap's `--unshare-net`; without bwrap it cannot be
enforced, and `enforced()` says so rather than dropping it silently.

Known ceilings: RLIMIT_NPROC counts every process of the user, not just this
agent's, and root ignores it. RLIMIT_AS counts virtual memory, so a runtime
that reserves large address space (a JVM, Go) may need a higher mem_mb.
"""
from __future__ import annotations

import sys

from sable.core.limits import DEFAULTS, Limits, parse, INT_KEYS  # noqa: F401  (validation lives in core so config can use it)


def supported(bwrap: bool | None = None) -> dict[str, bool]:
    """Which limits this host can enforce."""
    try:
        import resource
    except ImportError:  # Windows
        resource = None
    if bwrap is None:
        from sable.core.health import bwrap_available
        bwrap = sys.platform != "win32" and bwrap_available()
    return {
        "mem_mb": hasattr(resource, "RLIMIT_AS"),
        "cpu_s": hasattr(resource, "RLIMIT_CPU"),
        "procs": hasattr(resource, "RLIMIT_NPROC"),
        "network": bool(bwrap),
    }


def enforced(limits: Limits, support: dict[str, bool]) -> dict[str, str]:
    """Each limit as it will actually apply: a value, 'off', or 'NOT enforced'."""
    out = {}
    for key in INT_KEYS:
        value = getattr(limits, key)
        if value is None:
            out[key] = "unlimited"
        else:
            out[key] = str(value) if support[key] else f"{value} (NOT enforced on this host)"
    if limits.network:
        out["network"] = "on"
    else:
        out["network"] = "off" if support["network"] else "off requested, NOT enforced (no bwrap)"
    return out


def ulimit_prefix(limits: Limits) -> str:
    """The script line that applies the rlimits, or '' when none are set."""
    parts = []
    if limits.mem_mb:
        parts.append(f"-v {limits.mem_mb * 1024}")  # ulimit -v takes KiB
    if limits.cpu_s:
        parts.append(f"-t {limits.cpu_s}")
    if limits.procs:
        parts.append(f"-u {limits.procs}")
    # A limit the host refuses (above the hard limit) is said, not fatal.
    return "".join(f"ulimit {p} 2>/dev/null || echo '[limits] could not set ulimit {p}' >&2\n"
                   for p in parts)
