#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
printf '[app]\nname=shop\ndebug=true\n' > settings.ini
