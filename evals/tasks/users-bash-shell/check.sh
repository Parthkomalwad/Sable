#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
[ "$(sort bash_users.txt | tr '\n' ' ')" = "deploy root " ]
