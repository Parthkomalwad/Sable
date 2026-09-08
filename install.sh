#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# If run via sudo, use the real user's home not root's
if [ -n "${SUDO_USER:-}" ]; then
    REAL_USER="$SUDO_USER"
    REAL_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
else
    REAL_USER="$(whoami)"
    REAL_HOME="$HOME"
fi

VENV_DIR="$REAL_HOME/.local/share/agentic-shell/venv"
WRAPPER="/usr/local/bin/agentic-shell"
AUDIT_LOG_DIR="/var/log/agentic-shell"

echo "==> Installing agentic-shell for user: $REAL_USER"
echo "    Install dir: $INSTALL_DIR"
echo "    Venv dir:    $VENV_DIR"

echo "==> Creating virtualenv at $VENV_DIR"
sudo -u "$REAL_USER" mkdir -p "$(dirname "$VENV_DIR")"
sudo -u "$REAL_USER" python3 -m venv "$VENV_DIR"

echo "==> Installing Python dependencies"
sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install --quiet --upgrade pip
sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

echo "==> Pre-caching tiktoken encodings"
sudo -u "$REAL_USER" "$VENV_DIR/bin/python" -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" || true

echo "==> Writing wrapper to $WRAPPER"
sudo tee "$WRAPPER" > /dev/null <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$INSTALL_DIR"
export TERM=xterm-256color
export PROMPT_TOOLKIT_NO_CPR=1
export AGENTIC_PYTHON="$VENV_DIR/bin/python"
PYTHON="$VENV_DIR/bin/python"
SESSION="agentic-shell-\${USER}"
STAMP_FILE="\$HOME/.local/share/agentic-shell/install_stamp"
CURRENT_STAMP="$INSTALL_DIR:$VENV_DIR"

if command -v tmux &>/dev/null && [ -z "\$TMUX" ]; then
    if tmux has-session -t "\$SESSION" 2>/dev/null; then
        # Session exists just reattach (second SSH connection, don't kill it)
        exec tmux attach-session -t "\$SESSION"
    fi

    # No existing session create fresh with 3 panes
    echo "\$CURRENT_STAMP" > "\$STAMP_FILE"
    _COLS=\$(tput cols 2>/dev/null || echo 220)
    _ROWS=\$(tput lines 2>/dev/null || echo 50)
    tmux new-session -d -s "\$SESSION" -x "\$_COLS" -y "\$_ROWS"
    # Enable mouse scrolling + large scrollback buffer
    tmux set-option -t "\$SESSION" mouse on
    tmux set-option -t "\$SESSION" history-limit 50000

    # Build layout using captured pane IDs to avoid numbering ambiguity
    # Start: only pane %0 exists (main shell)
    MAIN_PANE=\$(tmux display-message -t "\$SESSION":0.0 -p "#{pane_id}")

    # Split right: telemetry sidebar (48 cols) new pane on the right
    TELE_PANE=\$(tmux split-window -h -t "\$MAIN_PANE" -l 48 -P -F "#{pane_id}")

    # Split main pane bottom: tasks panel (~30% height in lines)
    _TASK_LINES=\$(( _ROWS * 30 / 100 ))
    [ "\$_TASK_LINES" -lt 8 ] && _TASK_LINES=8
    TASK_PANE=\$(tmux split-window -v -t "\$MAIN_PANE" -l "\$_TASK_LINES" -P -F "#{pane_id}")

    # Layout:
    #  MAIN_PANE = top-left  → agentic shell
    #  TELE_PANE = right     → telemetry sidebar
    #  TASK_PANE = bottom-left → tasks panel

    tmux send-keys -t "\$TELE_PANE" "trap '' INT; clear; while true; do PYTHONPATH=$INSTALL_DIR PROMPT_TOOLKIT_NO_CPR=1 $VENV_DIR/bin/python -m shell.telemetry.watch; sleep 2; done" Enter
    tmux send-keys -t "\$TASK_PANE" "trap '' INT; clear; while true; do PYTHONPATH=$INSTALL_DIR PROMPT_TOOLKIT_NO_CPR=1 $VENV_DIR/bin/python -m shell.tasks.panel; sleep 2; done" Enter
    tmux send-keys -t "\$MAIN_PANE" "trap '' INT; EXIT_FLAG=\$HOME/.local/share/agentic-shell/exit_requested; while true; do rm -f \"\$EXIT_FLAG\"; clear; PYTHONPATH=$INSTALL_DIR PROMPT_TOOLKIT_NO_CPR=1 NO_TMUX=1 $VENV_DIR/bin/python -m shell.main; if [ -f \"\$EXIT_FLAG\" ]; then rm -f \"\$EXIT_FLAG\"; echo 'dropping to bash run agentic-shell to return'; exec /bin/bash; fi; echo '[shell exited restarting in 2s]'; sleep 2; done" Enter

    tmux select-pane -t "\$MAIN_PANE"
    exec tmux attach-session -t "\$SESSION"
else
    exec "\$PYTHON" -m shell.main
fi
EOF
sudo chmod +x "$WRAPPER"

echo "==> Writing ~/.tmux.conf (mouse, keybindings, status bar)"
sudo -u "$REAL_USER" bash -c "cat > $REAL_HOME/.tmux.conf" <<'TMUX_EOF'
# ── General ───────────────────────────────────────────────────────────────────
set -g mouse on
set -g history-limit 50000
set -g default-terminal "xterm-256color"

# ── Prefix: Ctrl+A (easier than Ctrl+B) ───────────────────────────────────────
unbind C-b
set -g prefix C-a
bind C-a send-prefix

# ── Status bar at TOP ─────────────────────────────────────────────────────────
set -g status-position top
set -g status-style          "bg=colour234,fg=colour250"
set -g status-left-length    30
set -g status-right-length   40

# Left: session name in a bold cyan pill
set -g status-left  "#[bg=colour27,fg=colour255,bold] ◈ #S #[bg=colour234,fg=colour27]▶ "

# Right: user@host + time
set -g status-right "#[fg=colour244] #H #[fg=colour240]│#[fg=colour250,bold] %H:%M "

# Inactive windows: dimmed, padded
set -g window-status-format         "#[bg=colour236,fg=colour244] #I  #W #[bg=colour234,fg=colour236]▎"
set -g window-status-current-format "#[bg=colour27,fg=colour255,bold] #I  #W #[bg=colour234,fg=colour27]▶"
set -g window-status-separator      ""

# Pane borders
set -g pane-border-style        "fg=colour238"
set -g pane-active-border-style "fg=colour27"

# ── Window switching: Alt+Number jumps directly (0-indexed) ──────────────────
# Alt+1 = main shell (window 0), Alt+2 = first task (window 1), etc.
bind -n M-1 select-window -t :0
bind -n M-2 select-window -t :1
bind -n M-3 select-window -t :2
bind -n M-4 select-window -t :3
bind -n M-5 select-window -t :4

# ── Alt+Left / Alt+Right to cycle windows ─────────────────────────────────────
bind -n M-Left  previous-window
bind -n M-Right next-window

# ── Alt+T = last used window (quick toggle between main and task) ─────────────
bind -n M-t last-window

# ── Mouse scroll ──────────────────────────────────────────────────────────────
bind -n WheelUpPane   if-shell -F "#{?pane_in_mode,1,#{?alternate_screen,1,0}}" "send-keys -M" "copy-mode -e; send-keys -M"
bind -n WheelDownPane if-shell -F "#{?pane_in_mode,1,#{?alternate_screen,1,0}}" "send-keys -M" "send-keys -M"
TMUX_EOF

echo "==> Enabling unprivileged user namespaces (required for bwrap sandbox)"
SYSCTL_CONF="/etc/sysctl.d/99-agentic-shell.conf"
if [ "$(cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null)" != "1" ]; then
    sudo sysctl -w kernel.unprivileged_userns_clone=1 || true
fi
if ! grep -q "unprivileged_userns_clone" "$SYSCTL_CONF" 2>/dev/null; then
    echo "kernel.unprivileged_userns_clone=1" | sudo tee "$SYSCTL_CONF" > /dev/null
fi

echo "==> Creating audit log directory"
sudo mkdir -p "$AUDIT_LOG_DIR"
sudo chmod 1777 "$AUDIT_LOG_DIR"
sudo touch "$AUDIT_LOG_DIR/audit.log"
sudo chmod 0666 "$AUDIT_LOG_DIR/audit.log"

if ! grep -q "$WRAPPER" /etc/shells; then
    echo "==> Registering $WRAPPER in /etc/shells"
    echo "$WRAPPER" | sudo tee -a /etc/shells > /dev/null
fi

# Revert login shell to /bin/bash (chsh approach is unreliable)
# Instead, auto-launch via .bashrc on interactive SSH login
echo "==> Restoring login shell to /bin/bash for $REAL_USER"
sudo chsh -s /bin/bash "$REAL_USER" 2>/dev/null || true

BASHRC="$REAL_HOME/.bashrc"
MARKER="# agentic-shell auto-launch"
if ! grep -q "$MARKER" "$BASHRC" 2>/dev/null; then
    echo "==> Adding agentic-shell auto-launch to $BASHRC"
    cat >> "$BASHRC" <<'BASHRC_EOF'

# agentic-shell auto-launch
if [ -z "$TMUX" ] && [ -z "$AGENTIC_SHELL_NO_AUTO" ] && command -v agentic-shell &>/dev/null; then
    exec agentic-shell
fi
BASHRC_EOF
fi

echo ""
echo "✓ agentic-shell installed successfully for $REAL_USER."
echo "  Venv: $VENV_DIR"
echo "  SSH in to start automatically, or run: agentic-shell"
