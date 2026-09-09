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
exec tmux new-session -s playground /usr/local/bin/sable
