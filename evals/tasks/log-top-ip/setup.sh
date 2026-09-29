#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p logs
for ip in 10.0.0.1 10.0.0.2 10.0.0.2 10.0.0.3 10.0.0.2 10.0.0.1; do
  echo "$ip - - [29/Sep/2026] \"GET / HTTP/1.1\" 200 512" >> logs/access.log
done
