"""`/why` and `/task <name> replay`: what the model actually saw.

Phase 1 (I9). Both render rows from `agent_turns` (see
`core/events/replay.py`). `/why` shows the most recent turn in full; replay
walks every turn of one agent, compactly.

The rendering follows `/route why`: ANSI constants and `out()`, so a path or a
command containing square brackets is not eaten by Rich's markup parser.
"""
from __future__ import annotations

from sable.ui.console import out as _out

PURPLE = "\033[38;5;141m"
GREEN = "\033[38;5;114m"
YELLOW = "\033[38;5;179m"
DIM = "\033[2;37m"
WHITE = "\033[1;37m"
RESET = "\033[0m"

#: Message bodies are truncated in the per-turn walk. `/why` shows them whole:
#: it is answering "why did it do that", and the answer is usually in the part
#: a preview would cut.
_PREVIEW = 160


def _role_colour(role: str) -> str:
    return {"user": GREEN, "assistant": PURPLE, "system": YELLOW}.get(role, DIM)


def _render_turn(turn, full: bool) -> None:
    """Render one recorded turn."""
    _out("")
    _out(
        f"  {DIM}turn{RESET} {WHITE}{turn.turn}{RESET}"
        f"  {DIM}agent{RESET} {turn.agent}"
        f"  {DIM}role{RESET} {turn.role}"
        f"  {DIM}model{RESET} {turn.model or 'unknown'}"
    )
    _out(
        f"  {DIM}tokens{RESET} {turn.prompt_tokens}p / {turn.completion_tokens}c"
        f"  {DIM}cost{RESET} ${turn.cost_usd:.4f}"
        f"  {DIM}at{RESET} {turn.ts}"
    )

    if full and turn.system_prompt:
        _out("")
        _out(f"  {DIM}system prompt{RESET}")
        for line in turn.system_prompt.splitlines():
            _out(f"    {DIM}{line}{RESET}")

    _out("")
    _out(f"  {DIM}what the model saw ({len(turn.messages)} messages){RESET}")
    for message in turn.messages:
        role = str(message.get("role", "?"))
        content = str(message.get("content", ""))
        if not full and len(content) > _PREVIEW:
            content = content[:_PREVIEW] + "…"
        colour = _role_colour(role)
        first, *rest = content.splitlines() or [""]
        _out(f"    {colour}{role:>9}{RESET}  {first}")
        for line in rest:
            _out(f"    {' ' * 9}  {line}")

    _out("")
    _out(f"  {DIM}what it answered{RESET}")
    for line in (turn.response or "(nothing)").splitlines():
        _out(f"    {WHITE}{line}{RESET}")
    _out("")


#: Colour per event kind, so a stream is skimmable: green for reaching a
#: milestone, red for ending badly, purple for a human stepping in.
_KIND_COLOUR = {
    "spawned": DIM,
    "started": GREEN,
    "status": WHITE,
    "completed": GREEN,
    "failed": YELLOW,
    "lost": YELLOW,
    "guidance": PURPLE,
    "turn": DIM,
    "command": WHITE,
}


def handle_events(agent: str) -> bool:
    """`/task <name> events`: the agent's event stream, oldest first.

    The same rows the sidebar and the orchestrator read, rendered for a human.
    Where `replay` answers "what was it thinking", this answers "what happened
    and when".
    """
    from sable.core.events.bus import EventBus

    with EventBus() as bus:
        events = bus.since(agent=agent)

    if not events:
        _out(f"no events for '{agent}'")
        return True

    _out("")
    _out(f"  {WHITE}events: {agent}{RESET}  {DIM}{len(events)} total{RESET}")
    _out("")
    for event in events:
        colour = _KIND_COLOUR.get(event.kind, DIM)
        stamp = event.ts[11:19] if len(event.ts) > 19 else event.ts
        _out(f"  {DIM}{stamp}{RESET}  {colour}{event.kind:<10}{RESET}  {_payload_summary(event)}")
    _out("")
    return True


def _payload_summary(event) -> str:
    """One line of the payload, chosen by kind so the useful field shows."""
    payload = event.payload
    if event.kind == "status":
        step = payload.get("step", "?")
        command = str(payload.get("command", ""))[:60]
        return f"step {step}  {command}" if command else f"step {step}"
    if event.kind == "completed":
        return str(payload.get("explanation") or payload.get("result", ""))[:70]
    if event.kind in ("failed", "lost"):
        return str(payload.get("reason", ""))[:70]
    if event.kind == "guidance":
        return str(payload.get("text", ""))[:70]
    if event.kind == "started":
        return str(payload.get("goal", ""))[:70]
    return ", ".join(f"{k}={str(v)[:30]}" for k, v in list(payload.items())[:3])


def handle_why(argument: str = "") -> bool:
    """`/why [agent]`: render the most recent turn. Returns True if handled."""
    from sable.core.events.replay import ReplayLog

    agent = argument.strip() or None

    with ReplayLog() as log:
        turn = log.latest(agent=agent)
        known = log.agents()

    if turn is None:
        if agent:
            _out(f"no recorded turns for '{agent}'")
            if known:
                _out(f"  {DIM}agents with turns: {', '.join(known)}{RESET}")
        else:
            _out("nothing recorded yet: /why explains the last thing a model decided")
        return True

    _render_turn(turn, full=True)
    return True


def handle_replay(agent: str) -> bool:
    """`/task <name> replay`: walk every recorded turn for one agent."""
    from sable.core.events.replay import ReplayLog

    with ReplayLog() as log:
        turns = log.turns_for(agent)
        known = log.agents()

    if not turns:
        _out(f"no recorded turns for '{agent}'")
        if known:
            _out(f"  {DIM}agents with turns: {', '.join(known)}{RESET}")
        return True

    total_cost = sum(t.cost_usd for t in turns)
    _out("")
    _out(f"  {WHITE}replay: {agent}{RESET}  {DIM}{len(turns)} turns, ${total_cost:.4f}{RESET}")
    for turn in turns:
        _render_turn(turn, full=False)
    _out(f"  {DIM}/why {agent}  shows the last turn in full{RESET}")
    _out("")
    return True
