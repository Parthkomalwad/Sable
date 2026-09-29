#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
grep -qx 'Enabled=no' services/nginx.service && grep -q ExecStart services/nginx.service
