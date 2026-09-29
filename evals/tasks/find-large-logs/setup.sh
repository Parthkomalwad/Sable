#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p logs && head -c 2000000 /dev/zero > logs/huge.log && head -c 100 /dev/zero > logs/small.log && head -c 3000000 /dev/zero > logs/big.log
