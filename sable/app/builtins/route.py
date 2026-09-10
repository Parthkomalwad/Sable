"""The `/route why` builtin and the router-correction log.

Extracted from `app/repl.py` in Phase 0.5 step 3. Pure move, no logic change.
"""
from __future__ import annotations

from sable.core.config.schema import ShellConfig
from sable.ui.console import out as _out


def _record_router_correction(line: str, label: str) -> None:
    """Append one "input<TAB>label" row to the router corrections file.

    Written whenever the user overrides a routing decision: Ctrl+B (this line
    was bash, not a goal) or an answer to the [b/a] prompt. These rows are the
    training data for the router accuracy programme (I3); the corpus in
    tests/fixtures/router_corpus.tsv is the curated version of the same shape.
    """
    from sable.core import paths

    text = line.strip()
    if not text:
        return
    try:
        paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(paths.ROUTER_CORRECTIONS, "a", encoding="utf-8") as handle:
            handle.write(text + "\t" + label + "\n")
    except (PermissionError, OSError):
        pass


def _handle_route_builtin(argument: str, config: ShellConfig) -> bool:
    """Handle `/route why "<line>"`. Returns True if handled."""
    from sable.agents.router import explain

    argument = argument.strip()
    if not argument.startswith("why"):
        _out('usage: /route why "<line>"')
        return True

    target = argument[len("why"):].strip().strip('"').strip("'")
    if not target:
        _out('usage: /route why "<line>"')
        return True

    exp = explain(target, mode=getattr(config, "routing_mode", "auto"))

    PURPLE = "[38;5;141m"
    GREEN = "[38;5;114m"
    YELLOW = "[38;5;179m"
    DIM = "[2;37m"
    RESET = "[0m"
    colour = {"bash": GREEN, "agentic": PURPLE, "ambiguous": YELLOW}[exp.route.value]

    _out("")
    _out(f"  {DIM}line{RESET}   {exp.line}")
    _out(f"  {DIM}route{RESET}  {colour}{exp.route.value}{RESET}")
    _out(f"  {DIM}score{RESET}  bash {exp.bash_score}  vs  nl {exp.nl_score}")
    _out("")
    if exp.reasons:
        _out(f"  {DIM}rules that fired{RESET}")
        for side, reason, points in exp.reasons:
            tag = f"{GREEN}bash{RESET}" if side == "bash" else f"{PURPLE}nl  {RESET}"
            score = f"+{points}" if points else "  "
            _out(f"    {tag} {score}  {reason}")
    else:
        _out(f"  {DIM}no scoring rules fired{RESET}")
    _out("")
    _out(f"  {DIM}decision{RESET}  {exp.decisive}")
    _out("")
    return True
