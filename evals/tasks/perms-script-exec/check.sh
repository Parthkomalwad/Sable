#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
[ -x scripts/deploy.sh ] && [ -x scripts/backup.sh ] && [ ! -x scripts/README ]
