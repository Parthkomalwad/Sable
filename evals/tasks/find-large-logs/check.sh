#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
grep -q huge.log big_logs.txt && grep -q big.log big_logs.txt && ! grep -q small.log big_logs.txt
