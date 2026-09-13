#!/usr/bin/env bash
# Linux/macOS/WSL playground launcher. See scripts/playground.ps1 for modes.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="sable-playground"
MODE="${1:-}"

if [ "$MODE" = "--rebuild" ]; then MODE="${2:-}"; docker build -t "$IMAGE" -f "$ROOT/docker/Dockerfile.playground" "$ROOT"; fi
if [ -z "$(docker images -q "$IMAGE")" ]; then docker build -t "$IMAGE" -f "$ROOT/docker/Dockerfile.playground" "$ROOT"; fi

# Load .env from the repo root, if there is one. A variable already set in the
# shell wins, so `SABLE_MODEL=gpt-4o ./scripts/playground.sh` overrides the file
# for one run without editing it.
declare -A FROM_FILE=()
if [ -f "$ROOT/.env" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        case "$(printf '%s' "$line" | tr -d '[:space:]')" in ""|'#'*) continue ;; esac
        [ "${line#*=}" = "$line" ] && continue          # no '=' on the line
        key="${line%%=*}"
        val="${line#*=}"
        key="$(printf '%s' "$key" | tr -d '[:space:]')"
        val="${val#"${val%%[![:space:]]*}"}"            # ltrim
        val="${val%"${val##*[![:space:]]}"}"            # rtrim
        # Strip one layer of surrounding quotes, which people add out of habit.
        case "$val" in
            \"*\") val="${val#\"}"; val="${val%\"}" ;;
            \'*\') val="${val#\'}"; val="${val%\'}" ;;
        esac
        [ -n "$val" ] && FROM_FILE["$key"]="$val"
    done < "$ROOT/.env"
fi

ENV_ARGS=()
for n in ANTHROPIC_API_KEY OPENAI_API_KEY SABLE_BACKEND SABLE_MODEL SABLE_API_BASE SABLE_MOCK_LLM; do
    v="${!n:-}"
    [ -z "$v" ] && v="${FROM_FILE[$n]:-}"
    [ -n "$v" ] && ENV_ARGS+=(-e "$n=$v")
done

exec docker run -it --rm \
    -v "$ROOT:/app" \
    -v sable-playground-home:/root \
    --add-host=host.docker.internal:host-gateway \
    --cap-add=SYS_ADMIN --security-opt seccomp=unconfined \
    "${ENV_ARGS[@]}" "$IMAGE" $MODE
