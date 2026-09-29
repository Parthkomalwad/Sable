#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
src="$(mktemp -d)" && mkdir -p "$src/conf" && echo 'k=v' > "$src/conf/app.conf" && tar czf bundle.tar.gz -C "$src" .
