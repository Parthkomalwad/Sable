#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
[ "$(tr -d ' \n' < top_ip.txt)" = "10.0.0.2" ]
