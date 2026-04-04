#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# If run via sudo, use the real user's home — not root's
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
        # Session exists — just reattach (second SSH connection, don't kill it)
        exec tmux attach-session -t "\$SESSION"
    fi

    # No existing session — create fresh with 3 panes
    echo "\$CURRENT_STAMP" > "\$STAMP_FILE"
    _COLS=\$(tput cols 2>/dev/null || echo 220)
    _ROWS=\$(tput lines 2>/dev/null || echo 50)
    tmux new-session -d -s "\$SESSION" -x "\$_COLS" -y "\$_ROWS"
    # Enable mouse scrolling + large scrollback buffer
    tmux set-option -t "\$SESSION" mouse on
    tmux set-option -t "\$SESSION" history-limit 50000

    # Split right: telemetry sidebar (48 cols wide)
    tmux split-window -h -t "\$SESSION":0.0 -l 48
    tmux swap-pane -s "\$SESSION":0.0 -t "\$SESSION":0.1

    # Split bottom of left pane: tasks panel (4 lines tall)
    tmux split-window -v -t "\$SESSION":0.0 -l 4

    # Telemetry sidebar (right pane — 0.1 after swap)
    tmux send-keys -t "\$SESSION":0.1 "trap '' INT; clear; while true; do PYTHONPATH=$INSTALL_DIR PROMPT_TOOLKIT_NO_CPR=1 $VENV_DIR/bin/python -m shell.telemetry.watch; sleep 2; done" Enter

    # Tasks panel (bottom-left pane — 0.2)
    tmux send-keys -t "\$SESSION":0.2 "trap '' INT; while true; do PYTHONPATH=$INSTALL_DIR PROMPT_TOOLKIT_NO_CPR=1 $VENV_DIR/bin/python -m shell.tasks.panel; sleep 2; done" Enter

    # Main shell (top-left pane — 0.0)
    tmux send-keys -t "\$SESSION":0.0 "trap '' INT; EXIT_FLAG=\$HOME/.local/share/agentic-shell/exit_requested; while true; do rm -f \"\$EXIT_FLAG\"; clear; PYTHONPATH=$INSTALL_DIR PROMPT_TOOLKIT_NO_CPR=1 NO_TMUX=1 $VENV_DIR/bin/python -m shell.main; if [ -f \"\$EXIT_FLAG\" ]; then rm -f \"\$EXIT_FLAG\"; echo 'dropping to bash — run agentic-shell to return'; exec /bin/bash; fi; echo '[shell exited — restarting in 2s]'; sleep 2; done" Enter

    tmux select-pane -t "\$SESSION":0.0
    exec tmux attach-session -t "\$SESSION"
else
    exec "\$PYTHON" -m shell.main
fi
EOF
sudo chmod +x "$WRAPPER"

echo "==> Writing ~/.tmux.conf (mouse scroll + large history)"
sudo -u "$REAL_USER" bash -c "cat > $REAL_HOME/.tmux.conf" <<'TMUX_EOF'
set -g mouse on
set -g history-limit 50000
set -g default-terminal "xterm-256color"
# Scroll with mouse wheel; click to select pane
bind -n WheelUpPane   if-shell -F "#{?pane_in_mode,1,#{?alternate_screen,1,0}}" "send-keys -M" "copy-mode -e; send-keys -M"
bind -n WheelDownPane if-shell -F "#{?pane_in_mode,1,#{?alternate_screen,1,0}}" "send-keys -M" "send-keys -M"
TMUX_EOF

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
