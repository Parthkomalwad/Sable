#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p services && printf '# web service\nlisten = 80\nlog_file = /var/log/web/access.log\nuser = www\n' > services/web.conf
