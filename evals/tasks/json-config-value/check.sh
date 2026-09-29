#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
[ "$(tr -d ' \n' < workers.txt)" = "6" ]
