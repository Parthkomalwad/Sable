"""First-run interactive setup wizard.

Uses prompt_toolkit for interactive input. API key field uses is_password=True.
Writes config.json with os.chmod 600 permissions.
In Phase 1, API keys are stored in config.json (with a warning).
In Phase 2, API keys move to the Linux keyring.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.formatted_text import HTML
from rich.console import Console
from rich.panel import Panel

from shell.config.schema import ShellConfig

console = Console(force_terminal=True, width=120)

CONFIG_PATH = Path.home() / ".config" / "agentic-shell" / "config.json"


def _ask(question: str, default: str = "", is_password: bool = False) -> str:
    """Prompt the user for input, returning stripped answer or default."""
    try:
        answer = pt_prompt(
            HTML(f"<ansicyan>{question}</ansicyan> "),
            default=default,
            is_password=is_password,
            in_thread=True,
        ).strip()
        return answer if answer else default
    except (EOFError, KeyboardInterrupt):
        return default


def run_wizard() -> None:
    """Run the first-run configuration wizard.

    Prompts the user for backend, model, API key, routing mode, and budgets.
    Writes config to ~/.config/agentic-shell/config.json with 600 permissions.
    """
    console.print()
    console.print(Panel(
        "[bold]Welcome to Agentic Shell![/bold]\n\n"
        "Let's configure your AI backend. This takes about 30 seconds.\n"
        "[dim]Config will be saved to ~/.config/agentic-shell/config.json[/dim]",
        title="[bold cyan]First-Run Setup[/bold cyan]",
        border_style="cyan",
    ))
    console.print()

    # Backend selection
    console.print("[bold]Available backends:[/bold]")
    console.print("  1. ollama  local model (free, no API key)")
    console.print("  2. openai  OpenAI API (gpt-4o, gpt-4o-mini, ...)")
    console.print("  3. anthropic Anthropic API (claude-...)")
    console.print()

    backend_choice = _ask("Backend [ollama/openai/anthropic]:", default="ollama")
    backend_map = {"1": "ollama", "2": "openai", "3": "anthropic"}
    backend = backend_map.get(backend_choice, backend_choice)
    if backend not in {"ollama", "openai", "anthropic"}:
        backend = "ollama"

    # Model
    default_models = {
        "ollama": "llama3.1",
        "openai": "gpt-4o-mini",
        "anthropic": "claude-3-5-haiku-20241022",
    }
    default_model = default_models.get(backend, "llama3.1")
    model = _ask(f"Model name [{default_model}]:", default=default_model)

    # API base (Ollama only)
    api_base: str | None = None
    if backend == "ollama":
        api_base = _ask("Ollama URL [http://localhost:11434]:", default="http://localhost:11434")

    # API key (cloud backends)
    api_key = ""
    if backend in {"openai", "anthropic"}:
        console.print()
        console.print("[yellow]API key will be stored in config.json. "
                      "Ensure file permissions are 600.[/yellow]")
        env_var = "OPENAI_API_KEY" if backend == "openai" else "ANTHROPIC_API_KEY"
        api_key = _ask(f"{env_var}:", is_password=True)

    # Routing mode
    console.print()
    console.print("[bold]Routing modes:[/bold]")
    console.print("  auto   shell heuristic decides bash vs AI automatically")
    console.print("  prefix use '>>' prefix to invoke AI explicitly")
    routing_mode = _ask("Routing mode [auto/prefix]:", default="auto")
    if routing_mode not in {"auto", "prefix"}:
        routing_mode = "auto"

    # Budgets
    console.print()
    daily_str = _ask("Daily token budget (blank = unlimited):", default="")
    daily_budget: int | None = None
    if daily_str.strip().isdigit():
        daily_budget = int(daily_str.strip())

    session_str = _ask("Session token budget (blank = unlimited):", default="")
    session_budget: int | None = None
    if session_str.strip().isdigit():
        session_budget = int(session_str.strip())

    # Privacy mode
    privacy_str = _ask("Enable privacy mode? Redacts secrets before sending to model [y/N]:", default="n")
    privacy_mode = privacy_str.lower().startswith("y")

    # Build config
    config = ShellConfig(
        backend=backend,
        model=model,
        api_base=api_base,
        routing_mode=routing_mode,
        daily_token_budget=daily_budget,
        session_token_budget=session_budget,
        privacy_mode=privacy_mode,
        setup_complete=True,
    )

    config_dict = config.to_dict()
    if api_key:
        config_dict["api_key"] = api_key  # Phase 1 storage; migrated to keyring in Phase 2+

    # Write config file
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config_dict, indent=2))
    os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 600

    console.print()
    console.print(f"[green]✓ Config saved to {CONFIG_PATH}[/green]")
    console.print("[dim]Run 'sable' again to start.[/dim]")
    console.print()
