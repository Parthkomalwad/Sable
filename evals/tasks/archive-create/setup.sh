#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p site && echo '<h1>hi</h1>' > site/index.html && echo 'body{}' > site/style.css
