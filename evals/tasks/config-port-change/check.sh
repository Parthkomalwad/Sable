#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
grep -qx 'port=9090' config/app.conf && ! grep -q 8080 config/app.conf && grep -qx 'workers=4' config/app.conf
