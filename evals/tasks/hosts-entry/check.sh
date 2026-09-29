#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
grep -Eq '^10\.0\.0\.5[[:space:]]+db\.internal$' etc/hosts && grep -q localhost etc/hosts
