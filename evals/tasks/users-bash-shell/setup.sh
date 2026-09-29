#!/usr/bin/env bash
set -eu
cd "$EVAL_ROOT"
mkdir -p etc && printf 'root:x:0:0:root:/root:/bin/bash\ndaemon:x:1:1::/usr/sbin:/usr/sbin/nologin\ndeploy:x:1000:1000::/home/deploy:/bin/bash\nwww:x:33:33::/var/www:/usr/sbin/nologin\n' > etc/passwd
