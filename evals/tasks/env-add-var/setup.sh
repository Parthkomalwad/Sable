#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
printf 'DB_HOST=db.internal\nDB_PORT=5432\n' > app.env
