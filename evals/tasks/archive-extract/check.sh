#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
grep -qx 'k=v' restore/conf/app.conf
