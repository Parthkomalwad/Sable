#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
[ "$(wc -l < allowlist.txt)" -eq 3 ] && [ "$(sort -u allowlist.txt | wc -l)" -eq 3 ]
