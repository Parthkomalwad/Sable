"""`/tour`: a short guided walkthrough of what Sable does (I10).

Seven steps, each one screen: routing, the confirm block, the YES word for
destructive commands, tasks, skills, the plain-bash escape hatch, and leaving.
Nothing is executed and nothing is written; the tour only explains, so it is
safe to run at any moment and safe to abandon half way.

Runs identically with SABLE_MOCK_LLM=1, which is the point: someone with no
API key can still see how the whole thing behaves.
"""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()

_ACCENT = "color(141)"
_BORDER = "color(55)"


class _Step:
    def __init__(self, title: str, body, hint: str = "") -> None:
        self.title = title
        self.body = body
        self.hint = hint


def _routing_body() -> Table:
    table = Table(box=box.SIMPLE, show_header=True, header_style=f"bold {_ACCENT}",
                  padding=(0, 2))
    table.add_column("you type")
    table.add_column("goes to")
    table.add_column("why")
    table.add_row("ls -la", "bash", "a real command with a flag")
    table.add_row("git push origin main", "bash", "argv, not prose")
    table.add_row("what is using port 8080", "the agent", "a question")
    table.add_row("find large log files", "the agent", "plain English after a verb")
    table.add_row("restart", "it asks", "genuinely ambiguous")
    return table


_STEPS: list[_Step] = [
    _Step(
        "1 of 7 - every line is routed",
        _routing_body,
        "Not sure why a line went where it did? Run: /route why \"<line>\"",
    ),
    _Step(
        "2 of 7 - you confirm every command before it runs",
        "When the agent decides to run something, it shows you the command and\n"
        "one line saying why, then waits for you to confirm:\n\n"
        "  [bold white]$ docker compose restart api[/bold white]\n\n"
        "  [dim]enter[/dim] run it   [dim]e[/dim] edit it first   [dim]q[/dim] cancel\n\n"
        "Nothing runs without you pressing a key. Editing is useful: your\n"
        "correction is how the shell learns your preferences.",
        "The agent never runs a command you have not seen.",
    ),
    _Step(
        "3 of 7 - destructive commands need the word YES",
        "A short list of patterns is treated as destructive: rm -rf, mkfs, dd to\n"
        "a device, curl piped into a shell, shutdown, and a few more.\n\n"
        "Those do not accept a keypress. You have to type [bold]YES[/bold] in capitals.\n"
        "It is deliberately awkward, because the alternative is worse.",
        "This applies to commands you type yourself, not just the agent's.",
    ),
    _Step(
        "4 of 7 - long work goes to background agents",
        "Anything slow gets handed to a sub-agent in its own tmux window, with\n"
        "its own sandboxed workspace under ~/tasks/<name>/.\n\n"
        "  [bold]/task list[/bold]            what is running\n"
        "  [bold]/task attach <name>[/bold]   watch one work\n"
        "  [bold]/task kill <name>[/bold]     stop it\n\n"
        "You keep typing in this pane while they work.",
        "The tasks bar at the bottom shows their status live.",
    ),
    _Step(
        "5 of 7 - it learns things you do repeatedly",
        "Do the same sequence of commands three times and Sable writes it up as\n"
        "a skill: plain markdown in ~/skills/instructions/ that you can read and\n"
        "edit. Skills carry a confidence score that moves as they succeed or fail.\n\n"
        "  [bold]/skill list[/bold]     what it has learned\n"
        "  [bold]/skill edit <name>[/bold]  fix one by hand",
        "Nothing is a black box: every skill is a file you can open.",
    ),
    _Step(
        "6 of 7 - the way out is always one key",
        "  [bold]/bash[/bold] or [bold]Ctrl+\\[/bold]   a plain bash subshell, sidebar hidden.\n"
        "                    Type [bold]exit[/bold] and you are back here, context intact.\n\n"
        "  [bold]Ctrl+B[/bold]            run just the next line as raw bash\n"
        "  [bold]sable off[/bold]         log straight into bash from now on\n"
        "  [bold]sable on[/bold]          undo that",
        "If Sable is ever in your way, it is one keystroke to step around it.",
    ),
    _Step(
        "7 of 7 - the rest",
        "  [bold]/help[/bold]     every builtin\n"
        "  [bold]/stats[/bold]    what you have spent\n"
        "  [bold]/memory[/bold]   what the shell remembers of this session\n"
        "  [bold]/config[/bold]   change backend, model, budgets (Ctrl+X)\n"
        "  [bold]/exit[/bold]     leave\n\n"
        "That is the whole tour. Type a goal in plain English and try it.",
        "Run /tour again whenever you want.",
    ),
]


def _render(step: _Step) -> None:
    body = step.body() if callable(step.body) else step.body
    console.print()
    console.print(Panel(
        body,
        title=f"[bold {_ACCENT}]{step.title}[/bold {_ACCENT}]",
        border_style=_BORDER,
        padding=(1, 2),
    ))
    if step.hint:
        console.print(f"  [dim]{step.hint}[/dim]")


def run_tour(interactive: bool = True, prompt=input) -> None:
    """Walk through the tour.

    Args:
        interactive: wait for a keypress between steps. False prints it all at
            once, which is what the tests and a piped terminal want.
        prompt: injected so tests do not need a tty.
    """
    console.print()
    console.print(
        f"[bold {_ACCENT}]  Sable in seven screens[/bold {_ACCENT}]"
    )
    console.print("  [dim]enter for the next one, q to stop[/dim]")

    for index, step in enumerate(_STEPS):
        _render(step)
        if not interactive or index == len(_STEPS) - 1:
            continue
        try:
            answer = prompt("")
        except (EOFError, KeyboardInterrupt):
            console.print("\n  [dim]tour ended[/dim]\n")
            return
        if answer.strip().lower() in {"q", "quit", "exit"}:
            console.print("\n  [dim]tour ended, run /tour to pick it up again[/dim]\n")
            return

    console.print()
