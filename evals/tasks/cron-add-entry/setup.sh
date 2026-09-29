#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p cron && echo 'MAILTO=ops@example.com' > cron/backup.cron
