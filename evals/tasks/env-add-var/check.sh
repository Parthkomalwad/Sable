#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
grep -qx 'LOG_LEVEL=info' app.env && grep -qx 'DB_PORT=5432' app.env && [ "$(wc -l < app.env)" -eq 3 ]
