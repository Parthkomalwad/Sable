#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p sites && printf 'server {\n    listen 80;\n    server_name example.com;\n    root /var/www;\n}\n' > sites/default.conf
