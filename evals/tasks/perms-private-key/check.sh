#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
[ "$(stat -c %a keys/id_app)" = "600" ]
