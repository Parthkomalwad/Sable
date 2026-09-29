#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
[ "$(tr -d ' \n' < usage.txt)" = "$(du -sk data | cut -f1)" ]
