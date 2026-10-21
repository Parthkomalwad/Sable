"""The builtin command catalog: one list behind /help, /help <cmd>, Tab and Ctrl+P.

Each command has a group, a one-line summary, its usage forms, and an
example. `/help` lays the groups out in two columns; `/help <command>` shows
one command's forms as a card; the prompt's completer offers the names and
their subcommands from the same data; and `plain_help()` renders the old flat
text the Ctrl+P palette parses. Adding a command here reaches all four.

Pure data and rendering: nothing here dispatches a command.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from rich.console import Console
from rich.text import Text

ACCENT = "color(141)"
TEXT = "color(252)"
DIM = "color(245)"
FAINT = "color(240)"


@dataclass(frozen=True)
class Command:
    name: str
    group: str
    summary: str
    usage: tuple = ()          # (form, what it does) pairs
    example: str = ""
    also: str = ""             # other ways in: aliases, keys
    hidden_aliases: tuple = field(default=())


GROUPS = ["everyday", "agents & tasks", "memory & skills", "automation", "safety", "connect", "settings"]

C = Command
COMMANDS: list[Command] = [
    # ── everyday ──────────────────────────────────────────────────────────
    C("/help", "everyday", "every command, or one in detail",
      (("/help", "this list"), ("/help <command>", "forms and an example for one command")),
      "/help task", "/?", ("/?",)),
    C("/tour", "everyday", "2-minute walkthrough",
      (("/tour", "routing, confirmations, tasks and skills, step by step"),)),
    C("/history", "everyday", "recent commands, with AI cost",
      (("/history", "your last commands and what each cost"),), also="/hist", hidden_aliases=("/hist",)),
    C("/stats", "everyday", "7-day token usage",
      (("/stats", "tokens and cost per day"), ("/stats --csv", "the same as CSV"))),
    C("/block", "everyday", "scrollback: show, copy, rerun",
      (("/block", "numbered list of recent output blocks"),
       ("/block <N> show", "print block N again"),
       ("/block <N> copy", "copy block N's output"),
       ("/block <N> rerun", "run block N's command again")),
      "/block 3 rerun"),
    C("/clip", "everyday", "saved snippets",
      (("/clip", "open the picker"),
       ('/clip add "<command>"', "save a snippet"),
       ("/clip run <N>", "run snippet N"),
       ("/clip del <N>", "delete snippet N")),
      '/clip add "docker ps -a"'),
    C("/alias", "everyday", "name a command in your words",
      (('/alias "<phrase>" = <command>', "saying the phrase runs the command, no model call"),
       ('/alias delete "<phrase>"', "remove it")),
      '/alias "show ports" = ss -tlnp'),
    C("/bash", "everyday", "plain bash; exit comes back",
      (("/bash", "a normal bash subshell with the agentic layer paused"),),
      also="/plain, Ctrl+\\", hidden_aliases=("/plain",)),
    C("/clear", "everyday", "clear the screen", (("/clear", "clear the terminal"),)),
    C("/exit", "everyday", "leave Sable",
      (("/exit", "leave; repeated work is drafted as skills on the way out"),),
      also="exit, logout, /quit", hidden_aliases=("/quit",)),

    # ── agents & tasks ────────────────────────────────────────────────────
    C("/task", "agents & tasks", "start and manage sub-agents",
      (("/task new <name> <goal>", "start one in its own tmux window"),
       ("/task list", "every task and its state"),
       ("/task attach <name>", "jump into its window"),
       ("/task back", "return from an attached task"),
       ("/task guide <name> <text>", "steer it mid-run (also Ctrl+G)"),
       ("/task pause|resume|kill <name>", "stop, continue or end it"),
       ("/task stats <name>", "tokens, cost and limits"),
       ("/task history <name>", "cost and model per call"),
       ("/task replay <name>", "every turn, as the model saw it"),
       ("/task events <name>", "its event stream"),
       ("/task diff|undo <name>", "what its last step changed, or put it back"),
       ("/task checkpoint <name>", "save its context"),
       ("/task revert <name> <vN>", "restore a checkpoint"),
       ("/task inspect <name>", "a shell in its folder"),
       ("/task clean", "delete finished task folders (asks first)")),
      '/task new deploy "build and deploy the api"'),
    C("/dash", "agents & tasks", "full-screen command center",
      (("/dash", "lanes, approvals and the breaker; q quits"),)),
    C("/inbox", "agents & tasks", "everything waiting on you",
      (("/inbox", "approvals, breaker trips, skill drafts, runbooks"),
       ("/inbox show <K>", "details for one item"),
       ("/inbox approve <K>", "let it run"),
       ("/inbox reject <K>", "refuse it")),
      "/inbox approve 1"),
    C("/approve", "agents & tasks", "allow a sub-agent's command",
      (("/approve", "list what sub-agents are waiting on"),
       ("/approve <id>", "allow one"),
       ("/approve reject <id>", "refuse one"))),
    C("/why", "agents & tasks", "what the model saw last time",
      (("/why", "the last decision in this shell"),
       ("/why <agent>", "the last decision of one agent"))),
    C("/tools", "agents & tasks", "tools each agent may call",
      (("/tools", "tools per role, and the tier each runs at"),)),
    C("/undo", "agents & tasks", "put back what a step changed",
      (("/undo", "the last step: diff first, then asks"),
       ("/undo list", "snapshots you can restore"),
       ("/undo <id>", "restore one"))),

    # ── memory & skills ───────────────────────────────────────────────────
    C("/palace", "memory & skills", "what Sable has learned",
      (("/palace", "rooms and what is in them"),
       ("/palace <room>", "one room"),
       ("/palace find <text>", "search memories"),
       ("/palace why <id>", "where a memory came from"),
       ("/palace consolidate", "merge near-duplicates")),
      "/palace find nginx"),
    C("/remember", "memory & skills", "save a fact",
      (("/remember <text> [--room R]", "store it (room user by default)"),),
      "/remember I prefer tabs over spaces"),
    C("/forget", "memory & skills", "remove a fact",
      (("/forget <id>", "delete one memory"),)),
    C("/memory", "memory & skills", "this session's context",
      (("/memory", "view it"),
       ("/memory versions", "saved snapshots"),
       ("/memory revert <id>", "restore one"),
       ("/memory clear", "start the context fresh"))),
    C("/skill", "memory & skills", "learned skills: review, approve",
      (("/skill list", "every skill and its status"),
       ("/skill show <name>", "read one"),
       ("/skill approve|reject|disable <name>", "decide on a draft"),
       ("/skill new <name>", "write one yourself"),
       ("/skill edit <name>", "open it in $EDITOR"),
       ("/skill stats", "confidence and uses"),
       ("/skill doctor", "failing, stale, duplicate or unsigned skills"),
       ("/skill sign <name>", "sign it"),
       ("/skill publish <name> [file]", "pack it as a signed archive"),
       ("/skill install <file|url> [--yes] [--replace]", "install an archive")),
      "/skill approve deploy-api"),
    C("/corrections", "memory & skills", "commands you corrected",
      (("/corrections", "what Sable learned from your edits"),
       ("/corrections delete <id>", "forget one"))),

    # ── automation ────────────────────────────────────────────────────────
    C("/schedule", "automation", "recurring jobs in plain words",
      (('/schedule "<sentence>"', "draft a job; you approve it"),
       ("/schedule list", "every job"),
       ("/schedule pause|resume <N>", "stop or restart one"),
       ("/schedule run-now <N>", "run one now"),
       ("/schedule rm <N>", "delete one")),
      '/schedule "every night at 2 prune docker images"'),
    C("/watch", "automation", "react to disk, file, log, url",
      (("/watch add disk|file|log|http ...", "start watching"),
       ("/watch list", "every watcher"),
       ("/watch rm <N>", "stop one")),
      "/watch add file /etc/nginx/nginx.conf"),
    C("/notify", "automation", "push alerts to your phone",
      (("/notify setup", "connect an ntfy topic"),
       ("/notify test", "send a test push"),
       ("/notify status", "what is set up"))),

    # ── safety ────────────────────────────────────────────────────────────
    C("/audit", "safety", "who ran what, and how it went",
      (("/audit [--since 1h] [--agent NAME]", "the ledger, filtered"),
       ("/audit --export jsonl", "write it out")),
      "/audit --since 30m"),
    C("/secret", "safety", "keyring secrets for commands",
      (("/secret add <name>", "store one; use it as $SECRET:name"),
       ("/secret list", "names only, never values"),
       ("/secret rm <name>", "delete one"))),
    C("/stepup", "safety", "TOTP for deny-tier commands",
      (("/stepup setup", "pair an authenticator app"),
       ("/stepup status", "is it on"),
       ("/stepup off", "turn it off"))),
    C("/breaker", "safety", "runaway-cost circuit breaker",
      (("/breaker", "trips so far"), ("/breaker reset", "clear them"))),
    C("/budget", "safety", "daily token budget",
      (("/budget reset", "clear the hard stop after the budget ran out"),)),

    # ── connect ───────────────────────────────────────────────────────────
    C("/mcp", "connect", "MCP servers and their tools",
      (("/mcp add <name> <command|url>", "connect a server"),
       ("/mcp list", "servers and tools"),
       ("/mcp remove <name>", "disconnect one"),
       ("/mcp trust|untrust <server.tool>", "skip or require confirmation"),
       ("/mcp search <query> [--add N]", "find servers in the registry")),
      "/mcp add fs npx @modelcontextprotocol/server-filesystem /app"),
    C("/plugin", "connect", "MCP server + skills + hooks",
      (("/plugin add <folder>", "install one"),
       ("/plugin list", "what is installed"),
       ("/plugin remove <name>", "uninstall"))),
    C("/host", "connect", "other servers, over SSH",
      (("/host add <NAME> user@host [--port N] [--sable]", "add a host"),
       ("/host list", "every host"),
       ("/host test <NAME>", "check it answers"),
       ("/host rm <NAME>", "remove it"),
       ("@NAME <command>", "run on one host (its policy applies)"),
       ("@all <command>", "run on every host")),
      "@web1 df -h"),

    # ── settings ──────────────────────────────────────────────────────────
    C("/config", "settings", "settings panel",
      (("/config", "backend, model, budgets, routing"),), also="Ctrl+X"),
    C("/model", "settings", "current backend and model", (("/model", "show them"),)),
    C("/mode", "settings", "routing: auto or >> prefix",
      (("/mode", "toggle; in prefix mode only >> lines reach the model"),)),
    C("/theme", "settings", "colours",
      (("/theme <name>", "default, mono or high-contrast"),)),
    C("/layout", "settings", "tmux pane layout",
      (("/layout <name>", "focus, fleet or minimal"),)),
    C("/new", "settings", "fresh tmux session", (("/new", "rebuild the shell, sidebar and tasks panes"),)),
    C("/route", "settings", "why a line went to bash or AI",
      (('/route why "<line>"', "the router's scores and reasons"),),
      '/route why "list big files"'),
    C("/selfcheck", "settings", "weekly report, no model calls",
      (("/selfcheck", "goals, skills and costs from existing logs"),)),
]

KEYS = [
    (">> text", "send the line to the model"),
    ("Ctrl+B", "next line runs as plain bash"),
    ("Ctrl+G", "steer a running agent"),
    ("Ctrl+P", "search commands, skills, tasks"),
    ("Ctrl+T", "show or hide the sidebar"),
    ("? / !", "explain or fix the last failure"),
    ("Tab", "complete commands, subcommands"),
]

_BY_NAME = {c.name: c for c in COMMANDS}
for _c in COMMANDS:
    for _a in _c.hidden_aliases:
        _BY_NAME[_a] = _c


def find(name: str) -> Command | None:
    """A command by name, with or without the slash; aliases resolve."""
    name = name.strip().split()[0] if name.strip() else ""
    if name and not name.startswith("/"):
        name = "/" + name
    return _BY_NAME.get(name)


def subcommands(cmd: Command) -> list[tuple[str, str]]:
    """Literal words that may follow the command, with what each does.

    Taken from the usage forms: the second word when it is a plain word, and
    each alternative of `a|b|c`. Placeholders (`<x>`, `[x]`, quoted text) are
    not subcommands.
    """
    seen: dict[str, str] = {}
    for form, what in cmd.usage:
        parts = form.split()
        if len(parts) < 2 or parts[0] != cmd.name:
            continue
        word = parts[1]
        if word[0] in '<["\'-':
            continue
        for alt in word.split("|"):
            if re.fullmatch(r"[a-z][a-z0-9-]*", alt):
                seen.setdefault(alt, what)
    return list(seen.items())


def plain_help() -> str:
    """The flat help text the Ctrl+P palette parses: `  <usage>  <what>` lines."""
    lines = ["", "Sable commands", ""]
    for c in COMMANDS:
        for form, what in c.usage:
            lines.append(f"  {form}  {what}")
    lines.append("")
    lines += [f"  {k}  {v}" for k, v in KEYS]
    return "\n".join(lines) + "\n"


# ── rendering ───────────────────────────────────────────────────────────────

NAME_WIDTH = max(len(c.name) for c in COMMANDS) + 2


def _keys_lines(width: int) -> list[Text]:
    lines = [Text("KEYS", style=f"bold {DIM}")]
    for k, v in KEYS:
        t = Text()
        t.append(f"{k:<{NAME_WIDTH}}", style=TEXT)
        room = width - NAME_WIDTH
        t.append(v if len(v) <= room else v[:room - 1] + "…", style=DIM)
        lines.append(t)
    return lines


def _group_lines(group: str, width: int) -> list[Text]:
    lines = [Text(group.upper(), style=f"bold {DIM}")]
    for c in (c for c in COMMANDS if c.group == group):
        t = Text()
        t.append(f"{c.name:<{NAME_WIDTH}}", style=ACCENT)
        room = width - NAME_WIDTH
        summary = c.summary if len(c.summary) <= room else c.summary[:room - 1] + "…"
        t.append(summary, style=DIM)
        lines.append(t)
    return lines


def overview(width: int) -> Text:
    """`/help`: a header, then the groups in two columns (one when narrow)."""
    out = Text()
    out.append("\n◆ ", style=ACCENT)
    out.append("sable commands", style=f"bold {TEXT}")
    out.append("     /help <command> for details · Tab completes · Ctrl+P searches\n\n", style=FAINT)

    ncols = columns_for(width)
    col = (width - 2) // ncols
    # KEYS is laid out as one more group, so it fills the last row's gap and
    # the whole list fits a normal pane without scrolling.
    blocks = [_group_lines(g, col - 3) for g in GROUPS] + [_keys_lines(col - 3)]
    for start in range(0, len(blocks), ncols):
        row_blocks = blocks[start:start + ncols]
        for i in range(max(len(b) for b in row_blocks)):
            row = Text()
            for j, block in enumerate(row_blocks):
                cell = block[i] if i < len(block) else Text()
                row.append_text(cell)
                later = any(i < len(b) for b in row_blocks[j + 1:])
                if later:
                    row.append(" " * max(2, col - len(cell.plain)))
            out.append_text(row)
            out.append("\n")
        if start + ncols < len(blocks):
            out.append("\n")
    return out


def columns_for(width: int) -> int:
    """Three columns on a wide pane, two on a normal one, one when narrow."""
    return 3 if width >= 140 else 2 if width >= 100 else 1


def detail(cmd: Command, width: int = 100) -> Text:
    """`/help <command>`: the command's forms, aligned, and an example."""
    out = Text()
    out.append("\n")
    out.append(cmd.name, style=f"bold {ACCENT}")
    out.append("  ·  ", style=FAINT)
    out.append(cmd.summary, style=TEXT)
    out.append(f"   {cmd.group}\n\n", style=FAINT)
    form_width = min(max(len(f) for f, _ in cmd.usage) + 3, 44)
    for form, what in cmd.usage:
        out.append("  ")
        if len(form) >= form_width:
            out.append(form, style=ACCENT)
            out.append(f"\n  {'':<{form_width}}")
        else:
            out.append(f"{form:<{form_width}}", style=ACCENT)
        out.append(f"{what}\n", style=DIM)
    if cmd.also:
        out.append("\n  also      ", style=FAINT)
        out.append(f"{cmd.also}\n", style=TEXT)
    if cmd.example:
        out.append("\n  example   " if not cmd.also else "  example   ", style=FAINT)
        out.append(f"{cmd.example}\n", style=TEXT)
    return out


def unknown(name: str) -> Text:
    """`/help <nope>`: say so, and suggest the closest command."""
    close = difflib.get_close_matches("/" + name.lstrip("/"), list(_BY_NAME), n=1, cutoff=0.5)
    out = Text()
    out.append(f"\nno command {'/' + name.lstrip('/')}", style=TEXT)
    if close:
        out.append("   did you mean ", style=FAINT)
        out.append(close[0], style=ACCENT)
        out.append("?", style=FAINT)
    out.append("\n/help lists them all\n", style=FAINT)
    return out


def show_help(arg: str = "", console: Console | None = None) -> None:
    """Print `/help` or `/help <command>`."""
    console = console or Console(highlight=False)
    width = console.width
    if not arg.strip():
        console.print(overview(width))
        return
    cmd = find(arg)
    console.print(detail(cmd, width) if cmd else unknown(arg.strip()))
