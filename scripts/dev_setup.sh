#!/usr/bin/env bash
# dev_setup.sh sets up a local development environment for agentic-shell
# Run once after cloning: bash scripts/dev_setup.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$REPO_ROOT/.venv"

echo "==> Setting up agentic-shell dev environment"
echo "    repo: $REPO_ROOT"

# --- Python venv ---
if [ ! -d "$VENV_DIR" ]; then
    echo "==> Creating virtualenv at .venv"
    python3 -m venv "$VENV_DIR"
else
    echo "==> Virtualenv already exists, skipping creation"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "==> Installing dependencies"
pip install --upgrade pip --quiet
pip install -r "$REPO_ROOT/requirements.txt" --quiet

# --- Pre-cache tiktoken encodings (avoids download on first shell start) ---
echo "==> Pre-caching tiktoken encodings"
python3 -c "import tiktoken; tiktoken.get_encoding('o200k_base'); tiktoken.get_encoding('cl100k_base')"

# --- Create required runtime directories ---
echo "==> Creating runtime directories"
mkdir -p ~/.config/agentic-shell
mkdir -p ~/.local/share/agentic-shell

echo ""
echo "Done. Activate the venv with:"
echo "  source .venv/bin/activate"
echo ""
echo "Run unit tests:"
echo "  pytest tests/unit/"
echo ""
echo "Run integration tests (requires Docker):"
echo "  bash scripts/run_in_docker.sh"
