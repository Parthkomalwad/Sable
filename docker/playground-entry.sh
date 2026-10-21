#!/usr/bin/env bash
# Writes config from env vars, starts sshd, then drops into tmux + sable.
set -euo pipefail

BACKEND="${SABLE_BACKEND:-ollama}"
MODEL="${SABLE_MODEL:-llama3.1}"
API_BASE="${SABLE_API_BASE:-http://host.docker.internal:11434}"

python3 - <<EOF
import json, os, pathlib, stat
cfg = pathlib.Path("/root/.config/agentic-shell/config.json")
cfg.parent.mkdir(parents=True, exist_ok=True)
data = {
  "backend": "$BACKEND", "model": "$MODEL", "api_base": "$API_BASE",
  "routing_mode": "auto", "daily_token_budget": None, "session_token_budget": None,
  "privacy_mode": False, "setup_complete": True, "tasks_base_dir": "~/tasks",
}
key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
if key:
    data["api_key"] = key
cfg.write_text(json.dumps(data, indent=2))
os.chmod(cfg, stat.S_IRUSR | stat.S_IWUSR)
EOF

/usr/sbin/sshd

if [ "${1:-}" = "tests" ]; then
    exec pytest tests/unit/ -q
fi
if [ "${1:-}" = "bash" ]; then
    exec bash
fi

# Three-pane layout, matching what install.sh builds on a real machine:
#   main (top-left)   the shell
#   sidebar (right)   telemetry: session, system, git, processes, tokens, clip
#   tasks (bottom)    live table of running sub-agents
# Without this the playground was a single bare pane, so neither the sidebar
# nor the tasks bar could be seen at all.
export PYTHONPATH=/app

# sabled (Phase 5): no systemd in the container, so run it in the foreground
# in the background. Its log is at ~/.sable/sabled.log.
mkdir -p /root/.sable
nohup python3 -m sable.app.main daemon run >> /root/.sable/sabled.log 2>&1 &

# Size the session generously rather than from tput: a container reports 80x24
# until a client attaches, and reading that here would leave the panes tiny.
# root's login shell is sable (Dockerfile chsh), and tmux starts every pane
# with $SHELL. Without this each pane began as a Sable, and the send-keys lines
# below were typed into it: the main pane ran a second Sable nested inside the
# first (so /exit only left the inner one) and the sidebar and tasks loops ran
# through the router, drawing over each other.
export SHELL=/bin/bash
# -f: the shared Sable look (status bar, borders). -n: name the window
# "shell" rather than letting tmux call it python3.
tmux -f /app/sable/data/tmux-theme.conf new-session -d -s playground -n shell -x 200 -y 50
tmux set-option -t playground mouse on
tmux set-option -t playground history-limit 50000

MAIN=$(tmux display-message -t playground:0.0 -p '#{pane_id}')

SIDE=$(tmux split-window -h -t "$MAIN" -l 48 -P -F '#{pane_id}')
tmux send-keys -t "$SIDE" \
    "clear; while true; do PYTHONPATH=/app python3 -m sable.ui.sidebar.watch; sleep 2; done" Enter

TASKS=$(tmux split-window -v -t "$MAIN" -l 12 -P -F '#{pane_id}')
tmux send-keys -t "$TASKS" \
    "clear; while true; do PYTHONPATH=/app python3 -m sable.ui.sidebar.agents_panel; sleep 2; done" Enter

# /exit drops to bash, as on a real install; `sable` brings the shell back.
tmux send-keys -t "$MAIN" "clear; /usr/local/bin/sable; echo 'dropped to bash, run: sable'; exec /bin/bash" Enter
tmux select-pane -t "$MAIN"

# tmux resizes the session to the attaching client, which squeezes splits made
# beforehand. This helper re-applies them against the client's real size, and
# collapses a pane when the window is too small to carry it.
cat > /usr/local/bin/sable-fit-panes <<FIT
#!/usr/bin/env bash
# Pane IDs, not indices: tmux renumbers indices as panes are created, so the
# sidebar is not reliably pane 1.
SIDE_ID="$SIDE"
TASKS_ID="$TASKS"
W=\$(tmux display-message -t playground -p '#{window_width}')
H=\$(tmux display-message -t playground -p '#{window_height}')
if [ "\$W" -ge 120 ]; then
    tmux resize-pane -t "\$SIDE_ID" -x 48
else
    tmux resize-pane -t "\$SIDE_ID" -x 2 2>/dev/null || true
fi
# Tall windows: the tasks panel sizes its own pane to what it shows.
if [ "\$H" -lt 30 ]; then
    tmux resize-pane -t "\$TASKS_ID" -y 2 2>/dev/null || true
fi
FIT
chmod +x /usr/local/bin/sable-fit-panes

tmux set-hook -t playground client-attached 'run-shell /usr/local/bin/sable-fit-panes'
tmux set-hook -t playground client-resized 'run-shell /usr/local/bin/sable-fit-panes'

exec tmux attach-session -t playground
