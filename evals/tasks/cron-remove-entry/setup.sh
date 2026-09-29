#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p cron && printf '*/5 * * * * /opt/health.sh\n0 3 * * * /opt/cleanup.sh\n0 4 * * 0 /opt/report.sh\n' > cron/jobs.cron
