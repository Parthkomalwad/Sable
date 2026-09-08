#!/usr/bin/env bash
# Linux/macOS/WSL playground launcher. See scripts/playground.ps1 for modes.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="agentic-playground"
MODE="${1:-}"

if [ "$MODE" = "--rebuild" ]; then MODE="${2:-}"; docker build -t "$IMAGE" -f "$ROOT/docker/Dockerfile.playground" "$ROOT"; fi
if [ -z "$(docker images -q "$IMAGE")" ]; then docker build -t "$IMAGE" -f "$ROOT/docker/Dockerfile.playground" "$ROOT"; fi

ENV_ARGS=()
for n in ANTHROPIC_API_KEY OPENAI_API_KEY AGENTIC_BACKEND AGENTIC_MODEL AGENTIC_API_BASE; do
    [ -n "${!n:-}" ] && ENV_ARGS+=(-e "$n=${!n}")
done

exec docker run -it --rm \
    -v "$ROOT:/app" \
    -v agentic-playground-home:/root \
    --add-host=host.docker.internal:host-gateway \
    --cap-add=SYS_ADMIN --security-opt seccomp=unconfined \
    "${ENV_ARGS[@]}" "$IMAGE" $MODE
