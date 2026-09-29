#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p keys && echo 'not a real key' > keys/id_app && chmod 644 keys/id_app
