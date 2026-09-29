#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
printf '10.0.0.1\n10.0.0.2\n10.0.0.1\n10.0.0.3\n10.0.0.2\n' > allowlist.txt
