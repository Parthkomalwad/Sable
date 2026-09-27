"""F6 secret broker: resolve at exec time, refuse rather than fall back."""
from __future__ import annotations

import importlib.util

import pytest

from sable.core.config import keyring
from sable.policy import secrets

VALUE = "hunter2 with spaces"


@pytest.fixture
def fake_keyring(monkeypatch):
    store = {"secret:db_pass": VALUE}
    monkeypatch.setattr(keyring, "lookup", store.get)
    monkeypatch.setattr(keyring, "store_api_key", store.__setitem__)
    monkeypatch.setattr(keyring, "delete", lambda s: store.pop(s, None) is not None)
    monkeypatch.setattr(keyring, "services", lambda p="": sorted(k for k in store if k.startswith(p)))
    return store


@pytest.fixture
def no_keyring(monkeypatch):
    def boom(*_a, **_k):
        raise keyring.KeyringUnavailable("no dbus")
    for fn in ("lookup", "store_api_key", "delete", "services"):
        monkeypatch.setattr(keyring, fn, boom)


def test_no_placeholder_never_touches_keyring(no_keyring):
    assert secrets.resolve("ls -la") == ("ls -la", {}, {})


def test_rewrites_to_env_reference_and_keeps_value_out_of_command(fake_keyring):
    cmd, env, reveal = secrets.resolve("psql -W $SECRET:db_pass")
    assert cmd == 'psql -W "${SABLE_SECRET_DB_PASS}"'
    assert VALUE not in cmd
    assert env == {"SABLE_SECRET_DB_PASS": VALUE}
    assert reveal == {VALUE: "$SECRET:db_pass"}


def test_inside_double_quotes_is_not_requoted(fake_keyring):
    cmd, _, _ = secrets.resolve('psql "postgres://u:$SECRET:db_pass@h/db"')
    assert cmd == 'psql "postgres://u:${SABLE_SECRET_DB_PASS}@h/db"'


def test_inside_single_quotes_refuses(fake_keyring):
    with pytest.raises(secrets.SecretError, match="single quotes"):
        secrets.resolve("echo '$SECRET:db_pass'")


def test_unknown_name_refuses_before_running(fake_keyring):
    with pytest.raises(secrets.SecretError, match="no secret named 'nope'"):
        secrets.resolve("echo $SECRET:nope")


def test_unavailable_keyring_refuses_not_falls_back(no_keyring, monkeypatch):
    monkeypatch.setenv("SABLE_SECRET_DB_PASS", "from-env")
    with pytest.raises(secrets.SecretError, match="keyring unavailable"):
        secrets.resolve("echo $SECRET:db_pass")


def test_redact_puts_placeholder_back(fake_keyring):
    _, _, reveal = secrets.resolve("echo $SECRET:db_pass")
    assert secrets.redact(f"pw={VALUE}\n", reveal) == "pw=$SECRET:db_pass\n"


def test_refusal_is_what_the_model_reads(fake_keyring):
    pytest.importorskip("ptyprocess")
    from sable.agents import runtime
    out = runtime.run_command("echo $SECRET:nope", cwd=".")
    assert out.startswith("[blocked:") and "nope" in out


@pytest.mark.skipif(importlib.util.find_spec("ptyprocess") is None, reason="pty is Unix-only")
def test_command_runs_with_value_and_output_is_redacted(fake_keyring, tmp_path):
    from sable.agents import runtime
    seen = tmp_path / "seen"
    out = runtime.run_command(
        f'printf %s $SECRET:db_pass > {seen}; echo "$SECRET:db_pass"', cwd=str(tmp_path)
    )
    assert seen.read_text() == VALUE          # the child got the real value
    assert VALUE not in out                   # the model does not
    assert "$SECRET:db_pass" in out


def test_builtin_add_list_rm(fake_keyring, capsys):
    from sable.app.builtins.secret import _handle_secret_builtin
    _handle_secret_builtin("add api_tok", prompt=lambda _p: "s3cr3t")
    assert fake_keyring["secret:api_tok"] == "s3cr3t"
    _handle_secret_builtin("list")
    listed = capsys.readouterr().out
    assert "api_tok" in listed and "db_pass" in listed and "s3cr3t" not in listed
    _handle_secret_builtin("rm api_tok")
    assert "secret:api_tok" not in fake_keyring


def test_builtin_reports_unavailable_keyring(no_keyring, capsys):
    from sable.app.builtins.secret import _handle_secret_builtin
    assert _handle_secret_builtin("list") is True
    assert "keyring unavailable" in capsys.readouterr().out
