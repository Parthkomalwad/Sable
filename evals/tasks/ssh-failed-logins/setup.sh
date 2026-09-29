#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p logs && printf '%s\n' 'sshd[1]: Failed password for root from 1.2.3.4' 'sshd[2]: Accepted publickey for deploy' 'sshd[3]: Failed password for admin from 5.6.7.8' 'sshd[4]: Failed password for root from 1.2.3.4' > logs/auth.log
