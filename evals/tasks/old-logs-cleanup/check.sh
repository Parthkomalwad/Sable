#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
[ ! -e logs/old/a.log ] && [ ! -e logs/old/b.log ] && [ -e logs/old/c.log ] && [ -e logs/old/keep.txt ]
