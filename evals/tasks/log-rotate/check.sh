#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
grep -q 'old line' logs/app.log.1 && [ -f logs/app.log ] && [ ! -s logs/app.log ]
