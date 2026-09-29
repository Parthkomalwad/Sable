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


@pytest.fixture(autouse=True)
def _no_local_hooks(monkeypatch, tmp_path_factory):
    """A developer's own ~/.sable/hooks must not run inside the test suite."""
    from sable.policy import hooks

    monkeypatch.setattr(hooks, "HOOKS_DIR", tmp_path_factory.mktemp("hooks"))


@pytest.fixture(autouse=True)
def _no_real_audit_ledger(monkeypatch, tmp_path_factory):
    """`gate()` writes an audit_ledger row; keep it out of the real sessions.db."""
    import sable.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path_factory.mktemp("db") / "sessions.db")


@pytest.fixture(autouse=True)
def _no_real_signing_key(monkeypatch):
    """Skill signing (K8) must never read or create a key in the real keyring."""
    from sable.skills import signing

    monkeypatch.setattr(signing, "_key", lambda create: None)
