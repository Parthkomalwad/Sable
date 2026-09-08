#!/usr/bin/env bash
# uninstall.sh Remove agentic-shell and restore the user's login shell to bash.
#
# Usage: bash uninstall.sh
# Must be run as the user whose shell will be restored.

set -euo pipefail

WRAPPER="/usr/local/bin/agentic-shell"
VENV_DIR="$HOME/.local/share/agentic-shell/venv"
CURRENT_USER="$(whoami)"
RESTORE_SHELL="${1:-/bin/bash}"

echo "==> Uninstalling agentic-shell for user: $CURRENT_USER"

# --- 1. Restore login shell ---
CURRENT_SHELL="$(getent passwd "$CURRENT_USER" | cut -d: -f7)"
if [ "$CURRENT_SHELL" = "$WRAPPER" ]; then
    echo "==> Restoring login shell to $RESTORE_SHELL"
    chsh -s "$RESTORE_SHELL" "$CURRENT_USER"
else
    echo "    Login shell is already $CURRENT_SHELL, no change needed."
fi

# --- 2. Remove wrapper binary ---
if [ -f "$WRAPPER" ]; then
    echo "==> Removing $WRAPPER"
    sudo rm -f "$WRAPPER"
fi

# --- 3. Remove from /etc/shells ---
if grep -q "$WRAPPER" /etc/shells 2>/dev/null; then
    echo "==> Removing $WRAPPER from /etc/shells"
    sudo sed -i "\|$WRAPPER|d" /etc/shells
fi

# --- 4. Remove virtualenv (optional keep data files) ---
read -rp "Remove virtualenv at $VENV_DIR? [y/N]: " remove_venv
if [[ "${remove_venv,,}" == "y" ]]; then
    echo "==> Removing virtualenv"
    rm -rf "$VENV_DIR"
fi

# --- 5. Note: config and history are NOT removed ---
echo ""
echo "  Config kept at ~/.config/agentic-shell/ (remove manually if desired)"
echo "  History kept at ~/.local/share/agentic-shell/history"
echo "  Database kept at ~/.local/share/agentic-shell/sessions.db"
echo ""
echo "✓ agentic-shell uninstalled. Log out and back in to use $RESTORE_SHELL."
