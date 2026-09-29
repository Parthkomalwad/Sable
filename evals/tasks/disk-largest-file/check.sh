#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
[ "$(tr -d ' \n' < largest.txt)" = "b.bin" ]
