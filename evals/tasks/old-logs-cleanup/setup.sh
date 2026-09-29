#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p logs/old && touch -d '20 days ago' logs/old/a.log && touch -d '10 days ago' logs/old/b.log && touch logs/old/c.log && touch -d '30 days ago' logs/old/keep.txt
