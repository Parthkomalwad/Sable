"""Keep unit tests independent of the machine they run on.

The policy engine reads /etc/sable/policy.toml and ~/.sable/policy.toml. A
developer's own rules must not change what a test sees, so both point at
files that do not exist unless a test writes them.
"""
import pytest

from sable.policy import rules


@pytest.fixture(autouse=True)
def _no_local_policy_files(monkeypatch, tmp_path_factory):
    empty = tmp_path_factory.mktemp("policy")
    monkeypatch.setattr(rules, "ADMIN_POLICY_PATH", empty / "admin.toml")
    monkeypatch.setattr(rules, "USER_POLICY_PATH", empty / "user.toml")
    rules.load.cache_clear()
    yield
    rules.load.cache_clear()
