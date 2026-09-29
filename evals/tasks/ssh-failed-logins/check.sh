#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
[ "$(tr -d ' \n' < failed.txt)" = "3" ]
