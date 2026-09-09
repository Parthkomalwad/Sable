# Sable v2 Conversational UX & Visual Design

**Date:** 2026-03-29
**Status:** Approved

---

## Goal

Make the shell feel like a smart assistant, not a command translator. Every interaction should be beautiful, fast-feeling, and conversational. A non-technical user should find it approachable; a power user should find it fast.

---

## 1. Prompt

Powerline-style colored block segments. No special font required uses Unicode block characters (`\ue0b0` with fallback to plain text if unsupported).

```
 ~/sable   main   11:42 ❯
```

**Segments (left to right):**
- **Path block** soft blue background, white text, current working directory (home collapsed to `~`)
- **Git branch block** soft purple background, white text, current branch name (skipped if not a git repo)
- **Time block** dark background, dim white text, `HH:MM`
- **`❯` cursor** soft white normally, red if last command exited non-zero

**Implementation:** `_render_prompt()` in `loop.py`. Uses `HTML()` from prompt_toolkit for ANSI coloring. Git branch read via `subprocess.run(["git", "branch", "--show-current"], capture_output=True)` with a short timeout returns empty string on failure, silently skipped.

---

## 2. Agentic Interaction Flow

### 2a. Thinking indicator

Immediately after the user presses Enter on a natural language input, before the LLM call:

```
  ✦ thinking...
```

Printed via `sys.stdout.write` and flushed. Replaced (overwritten with `\r`) once the LLM responds. If the terminal doesn't support carriage return, a newline is used instead.

### 2b. Explanation + command preview

Once the LLM responds:

```
  ✦ I'll start an nginx container and expose it on port 80.
    The image will be pulled from Docker Hub if not cached locally.

  $ docker run -d -p 80:80 --name nginx nginx

  ↵ run   e edit   q cancel  ›
```

- `✦` in soft purple (`\033[38;5;141m`)
- Explanation: 1–2 sentences, natural language, indented 4 spaces
- Command: `$` prefix, bright white, indented 2 spaces
- Confirm bar: lowercase, dim, `↵ run   e edit   q cancel  ›`

### 2c. Confirm bar behavior

- `Enter` (or anything unrecognised) → run as-is
- `e` → `edit> ` prompt, pre-filled with command, user edits inline
- `q` → `cancelled` and return to prompt

---

## 3. Post-Execution Output

### Success

```
  ✓ done in 1.2s

 ~/sable   main   11:43 ❯
```

- `✓` in soft green (`\033[38;5;114m`)
- Timing shown in seconds with one decimal place
- New prompt appears immediately after

### Failure

```
  ✗ exit 1  (1.2s)

 ~/sable   main   11:43 ❯
```

- `✗` in soft red (`\033[38;5;203m`)
- Exit code shown explicitly

### Pure bash commands (no LLM)

No `✦`, no timing summary. Raw output only, same as today. The shell stays invisible for bash only agentic interactions get the styled treatment.

---

## 4. Color Palette

| Element | Color | ANSI |
|---------|-------|------|
| Path block bg | Soft blue | `\033[48;5;24m` |
| Git block bg | Soft purple | `\033[48;5;55m` |
| Time block bg | Dark grey | `\033[48;5;236m` |
| `✦` agentic marker | Soft purple | `\033[38;5;141m` |
| `$` command | Bright white | `\033[1;37m` |
| `✓` success | Soft green | `\033[38;5;114m` |
| `✗` failure | Soft red | `\033[38;5;203m` |
| Confirm bar | Dim white | `\033[2;37m` |
| `❯` prompt | White / red on fail | `\033[0;37m` / `\033[38;5;203m` |

---

## 5. Code Changes

| File | Change |
|------|--------|
| `shell/loop.py` | Add `_render_prompt()` Powerline segments with git + time |
| `shell/loop.py` | Print `✦ thinking...` immediately before `asyncio.run(_call_llm(...))` |
| `shell/loop.py` | Rewrite `_display_command_preview()` with new styled output |
| `shell/loop.py` | Record `time.monotonic()` before `execute_bash()`, print `✓/✗ done in Xs` after |
| `shell/loop.py` | Track last exit code for prompt `❯` color |
| `shell/executor.py` | No changes needed timing done in loop.py |
| `shell/router.py` | No changes |

**No new dependencies.** Uses only `sys.stdout.write`, `subprocess` (already imported), `time` (stdlib), and prompt_toolkit's existing `HTML()`.

---

## 6. Out of Scope

- Conversational back-and-forth clarifying questions (Phase 3+)
- Auto-run without confirmation (stays as confirm-first for all agentic commands)
- Font/icon changes requiring Nerd Fonts
- Changes to the telemetry sidebar

---

## 7. Success Criteria

- A non-technical user can SSH in and immediately understand what the shell is doing
- The `✦ thinking...` indicator appears within 50ms of pressing Enter
- Prompt renders correctly in tmux with no freeze (all output via `sys.stdout.write`)
- Pure bash commands show zero added UI chrome
- Last-exit-code color on `❯` works correctly
