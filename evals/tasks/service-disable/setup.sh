#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p services && printf '[Service]\nExecStart=/usr/sbin/nginx\nEnabled=yes\n' > services/nginx.service
