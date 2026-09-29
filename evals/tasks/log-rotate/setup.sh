#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p logs && echo 'old line' > logs/app.log
