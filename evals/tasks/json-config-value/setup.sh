#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
printf '{\n  "name": "api",\n  "workers": 6,\n  "debug": false\n}\n' > config.json
