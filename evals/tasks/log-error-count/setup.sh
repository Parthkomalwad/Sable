#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p logs
printf '%s\n' 'INFO start' 'ERROR db down' 'WARN slow' 'ERROR db down' 'INFO ok' 'ERROR disk full' > logs/app.log
