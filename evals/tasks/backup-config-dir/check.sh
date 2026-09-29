#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
[ -f config.bak/one.conf ] && [ -f config.bak/sub/two.conf ] && [ -f config/one.conf ]
