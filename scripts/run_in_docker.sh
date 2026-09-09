#!/usr/bin/env bash
# run_in_docker.sh build the dev Docker image and run integration tests inside it
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_NAME="sable-dev"

echo "==> Building Docker image: $IMAGE_NAME"
docker build -t "$IMAGE_NAME" "$REPO_ROOT/docker" -f "$REPO_ROOT/docker/Dockerfile" --build-arg REPO_ROOT="$REPO_ROOT"

echo "==> Running integration tests inside container"
docker run --rm \
    -v "$REPO_ROOT:/app" \
    -w /app \
    "$IMAGE_NAME" \
    bash -c "pip install -r requirements.txt -q && pytest tests/integration/ -v"
