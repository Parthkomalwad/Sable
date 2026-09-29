#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
grep -Eq '^0 2 \* \* \* /usr/local/bin/backup.sh$' cron/backup.cron && grep -q MAILTO cron/backup.cron
