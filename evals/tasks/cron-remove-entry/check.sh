#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
! grep -q cleanup.sh cron/jobs.cron && grep -q health.sh cron/jobs.cron && grep -q report.sh cron/jobs.cron
