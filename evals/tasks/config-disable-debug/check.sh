#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
grep -qx 'debug=false' settings.ini && grep -qx 'name=shop' settings.ini
