#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p scripts && for s in deploy backup; do printf '#!/bin/sh\necho %s\n' $s > scripts/$s.sh; chmod 644 scripts/$s.sh; done && echo notes > scripts/README
