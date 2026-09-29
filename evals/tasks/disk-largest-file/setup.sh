#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p data && head -c 1000 /dev/zero > data/a.bin && head -c 50000 /dev/zero > data/b.bin && head -c 20000 /dev/zero > data/c.bin
