#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
[ "$(tr -d ' \n' < error_count.txt)" = "3" ]
