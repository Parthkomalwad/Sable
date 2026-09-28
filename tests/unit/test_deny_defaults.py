"""The shipped deny rules: commands with no legitimate use on a live server."""
from __future__ import annotations

import pytest

from sable.policy.engine import decide
from sable.policy.tiers import Tier


@pytest.mark.parametrize("cmd", [
    "rm -rf /", "rm -rf /*", "rm -fr / ", "sudo rm -rf --no-preserve-root /", "rm -r -f /; echo",
    ":(){ :|:& };:", ":() { : | : & } ; :",
    "chmod -R 777 /", "chmod -R 0777 / && ls",
])
def test_denied(cmd):
    assert decide(cmd).tier is Tier.DENY


@pytest.mark.parametrize("cmd", [
    "rm -rf /tmp/build", "rm -rf ./dist", "rm -rf /var/log/app/*",
    "chmod -R 777 /srv/share", "dd if=img of=/dev/sdb", "mkfs.ext4 /dev/sdb1",
])
def test_not_denied(cmd):
    assert decide(cmd).tier is not Tier.DENY
