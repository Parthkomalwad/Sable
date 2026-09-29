#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
[ "$(tr -d ' \n' < log_path.txt)" = "/var/log/web/access.log" ]
