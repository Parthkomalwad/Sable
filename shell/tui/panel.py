"""Rich-based telemetry panel renderer.

Renders the right-pane sidebar content:
- Session token total and cost
- Last LLM call stats
- Budget progress bar
- Recent model actions log
- Memory/compression status

Also handles the TUI settings panel (Ctrl+X / /config).
"""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table
from rich.text import Text

console = Console(force_terminal=True)


def render_telemetry_panel(stats: dict) -> None:
    """Render the live telemetry panel to stdout.

    Uses Console(force_terminal=True) for correct ANSI output in tmux pane.

    Args:
        stats: Dict with session/call/budget/memory data from sessions.db.
    """
    daily_cost = stats.get("daily_cost", 0.0)
    session_tokens = stats.get("session_tokens", 0)
    last_call = stats.get("last_call", {})
    budget_pct = stats.get("budget_pct")  # float 0-1 or None

    lines: list = []

    # Daily cost
    lines.append(Text(f"Today: ${daily_cost:.4f}", style="bold green"))

    # Session tokens
    lines.append(Text(f"Session: {session_tokens:,} tokens", style="cyan"))

    # Budget bar
    if budget_pct is not None:
        bar_width = 18
        filled = int(bar_width * min(budget_pct, 1.0))
        bar_color = "green" if budget_pct < 0.8 else ("yellow" if budget_pct < 1.0 else "red")
        bar = "█" * filled + "░" * (bar_width - filled)
        pct_str = f"{int(budget_pct * 100)}%"
        lines.append(Text(f"Budget [{bar}] {pct_str}", style=bar_color))

    # Last call
    if last_call:
        model = last_call.get("model", "?")
        tokens = last_call.get("total_tokens", 0)
        cost = last_call.get("cost_usd", 0.0)
        lines.append(Text())
        lines.append(Text("Last call:", style="dim"))
        lines.append(Text(f"  {model}", style="cyan"))
        lines.append(Text(f"  {tokens} tok  ${cost:.5f}", style="dim"))

    content = Text("\n").join(lines)
    console.print(Panel(content, title="[bold cyan]sable[/bold cyan]", border_style="cyan"))


def render_settings_panel(config: object) -> object | None:
    """Render the full-screen settings overlay (Ctrl+X / /config).

    Allows live editing of config values. Returns updated config or None if cancelled.

    Args:
        config: Current ShellConfig instance.

    Returns:
        Updated ShellConfig if saved, None if escaped without saving.
    """
    from prompt_toolkit import prompt as pt_prompt
    from prompt_toolkit.formatted_text import HTML
    from rich.console import Console as RichConsole

    rc = RichConsole(force_terminal=True, width=120)

    rc.print()
    rc.print(Panel(
        "[bold]Edit settings[/bold]\n[dim]Leave blank to keep current value. Ctrl+C to cancel.[/dim]",
        title="[bold cyan]/config Settings[/bold cyan]",
        border_style="cyan",
    ))
    rc.print()

    fields = [
        ("backend", "Backend [ollama/openai/anthropic]"),
        ("model", "Model name"),
        ("api_base", "API base URL (Ollama only, blank=none)"),
        ("routing_mode", "Routing mode [auto/prefix]"),
        ("daily_token_budget", "Daily token budget (blank=unlimited)"),
        ("session_token_budget", "Session token budget (blank=unlimited)"),
        ("privacy_mode", "Privacy mode [y/n]"),
    ]

    updates: dict = {}

    try:
        for attr, label in fields:
            current = getattr(config, attr, None)
            display = str(current) if current is not None else ""
            rc.print(f"[dim]current {attr}: {display}[/dim]")
            answer = pt_prompt(
                HTML(f"<ansicyan>{label}:</ansicyan> "),
                default=display,
                in_thread=True,
            ).strip()
            if answer != display:
                updates[attr] = answer
    except (EOFError, KeyboardInterrupt):
        rc.print("[dim]cancelled[/dim]")
        return None

    if not updates:
        rc.print("[dim]No changes.[/dim]")
        return None

    # Apply updates to a copy of config
    from shell.config.schema import ShellConfig

    config_dict = config.to_dict()
    for key, val in updates.items():
        if key in ("daily_token_budget", "session_token_budget"):
            config_dict[key] = int(val) if val.isdigit() else None
        elif key == "privacy_mode":
            config_dict[key] = val.lower().startswith("y")
        elif key == "api_base":
            config_dict[key] = val or None
        else:
            config_dict[key] = val

    try:
        new_config = ShellConfig.from_dict(config_dict)
    except ValueError as exc:
        rc.print(f"[red]Invalid config: {exc}[/red]")
        return None

    # Persist to disk
    import json
    import os
    import stat
    from pathlib import Path

    config_path = Path.home() / ".config" / "agentic-shell" / "config.json"
    config_path.write_text(json.dumps(new_config.to_dict(), indent=2))
    os.chmod(config_path, stat.S_IRUSR | stat.S_IWUSR)

    rc.print("[green]✓ Config saved.[/green]")
    return new_config
