#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p config && printf 'host=0.0.0.0\nport=8080\nworkers=4\n' > config/app.conf
