"""Multi-step plan execution.

When the LLM returns a plan array, this module executes steps sequentially,
displaying progress and pausing on failures.
"""
from __future__ import annotations

import os
import sys

from sable.core.executor import execute_bash
from sable.policy.engine import is_destructive, confirm_destructive

# Rich imports for styled plan output
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.rule import Rule

_console = Console(highlight=False)

PURPLE = "color(141)"
GREEN  = "color(114)"
RED    = "color(203)"
DIM    = "color(238)"
WHITE  = "color(253)"


def _render_plan_header(description: str, steps: list[str]) -> None:
    """Render the plan overview panel with all steps listed."""
    t = Text()
    for i, cmd in enumerate(steps, 1):
        t.append(f"  {i}. ", style=DIM)
        t.append(cmd + "\n", style=WHITE)
    label = description[:60] + "…" if len(description) > 60 else description
    _console.print(
        Panel(t, title=f"[{PURPLE}]✦ plan[/{PURPLE}]  [{DIM}]{label}[/{DIM}]",
              border_style="color(55)", padding=(0, 1))
    )


def _step_line(i: int, total: int, cmd: str, state: str) -> None:
    """Print a single step status line."""
    if state == "pending":
        marker, style = "○", DIM
    elif state == "ok":
        marker, style = "✓", GREEN
    elif state == "fail":
        marker, style = "✗", RED
    elif state == "skip":
        marker, style = "–", DIM
    else:
        marker, style = " ", WHITE

    t = Text()
    t.append(f"  {marker} ", style=style)
    t.append(f"step {i}/{total}  ", style=DIM)
    t.append(cmd, style=WHITE)
    _console.print(t)


def execute_plan(plan: list[str], cwd: str, description: str = "") -> int:
    """Execute a list of shell commands sequentially as a plan.

    Displays all steps upfront in a styled panel.
    Updates step markers: ○ → ✓ (success) or ✗ (failure).
    On failure, prompts: [c]ontinue [r]etry [a]bort.

    Args:
        plan: Ordered list of shell command strings.
        cwd: Starting working directory.
        description: Optional human-readable description of the plan.

    Returns:
        Exit code of the last executed step.
    """
    if not plan:
        return 0

    sys.stdout.write("\n")
    _render_plan_header(description or "multi-step plan", plan)
    sys.stdout.write("\n")

    # Confirm before executing
    try:
        sys.stdout.write("\033[2;37m  confirm all steps? [Enter] run   q cancel:  \033[0m")
        sys.stdout.flush()
        answer = input("").strip().lower()
    except (EOFError, KeyboardInterrupt):
        _console.print(f"[{DIM}]  cancelled[/{DIM}]")
        return 1
    if answer == "q":
        _console.print(f"[{DIM}]  cancelled[/{DIM}]")
        return 1

    sys.stdout.write("\n")
    last_exit = 0
    current_cwd = cwd
    total = len(plan)

    for i, cmd in enumerate(plan):
        step_num = i + 1

        # Safety check per step
        if is_destructive(cmd):
            if not confirm_destructive(cmd):
                _step_line(step_num, total, cmd, "skip")
                continue

        exit_code, _ = execute_bash(cmd, current_cwd)
        current_cwd = os.getcwd()
        last_exit = exit_code

        if exit_code == 0:
            _step_line(step_num, total, cmd, "ok")
        else:
            _step_line(step_num, total, cmd, "fail")
            sys.stdout.write("\n")
            try:
                sys.stdout.write(f"\033[38;5;203m  step {step_num} failed (exit {exit_code})\033[0m\n")
                sys.stdout.write("\033[2;37m  [c]ontinue  [r]etry  [a]bort:  \033[0m")
                sys.stdout.flush()
                choice = input("").strip().lower()
            except (EOFError, KeyboardInterrupt):
                choice = "a"

            if choice == "a":
                _console.print(f"\n[{RED}]  plan aborted[/{RED}]")
                return exit_code
            elif choice == "r":
                exit_code2, _ = execute_bash(cmd, current_cwd)
                current_cwd = os.getcwd()
                last_exit = exit_code2
                state2 = "ok" if exit_code2 == 0 else "fail"
                _step_line(step_num, total, cmd + "  (retry)", state2)
            sys.stdout.write("\n")

    _console.print(Rule(style="color(55)"))
    return last_exit
