#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
grep -q 'server_name sable.example.org;' sites/default.conf && grep -q 'listen 80;' sites/default.conf
