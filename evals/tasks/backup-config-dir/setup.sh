#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p config/sub && echo a > config/one.conf && echo b > config/sub/two.conf
