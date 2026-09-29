#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
tar tzf site-backup.tar.gz | grep -q 'site/index.html' && tar tzf site-backup.tar.gz | grep -q 'site/style.css'
