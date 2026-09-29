#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p etc && printf '127.0.0.1 localhost\n' > etc/hosts
