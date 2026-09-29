#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p data && head -c 300000 /dev/zero > data/blob
