"""`pass` fallback for broker secrets when the Secret Service is missing (F6)."""
from __future__ import annotations

import subprocess

import pytest

from sable.app.builtins.secret import _handle_secret_builtin
from sable.core.config import keyring, passstore
from sable.policy import secrets

VALUE = "s3cr3t-value"


@pytest.fixture
def no_dbus(monkeypatch):
    def boom():
        raise keyring.KeyringUnavailable("no dbus")
    monkeypatch.setattr(keyring, "_collection", boom)
    # store_api_key imports secretstorage itself; make that fail too.
    monkeypatch.setitem(__import__("sys").modules, "secretstorage", None)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An initialised pass store with `pass` faked on PATH."""
    (tmp_path / ".gpg-id").write_text("ABCD\n")
    (tmp_path / "sable").mkdir()
    monkeypatch.setenv("PASSWORD_STORE_DIR", str(tmp_path))
    monkeypatch.setattr(passstore.shutil, "which", lambda n: "/usr/bin/pass")
    calls = []

    def fake_run(argv, input=None, timeout=None, **_kw):
        calls.append((argv, input, timeout))
        entry = tmp_path / f"{argv[-1]}.gpg"
        out = ""
        if argv[1] == "insert":
            entry.write_text(input)  # stands in for gpg
        elif argv[1] == "show":
            out = entry.read_text()
        elif argv[1] == "rm":
            entry.unlink()
        return subprocess.CompletedProcess(argv, 0, out, "")

    monkeypatch.setattr(passstore.subprocess, "run", fake_run)
    return calls


def test_add_show_list_rm_via_pass(no_dbus, store, capsys):
    keyring.store_api_key("secret:db_pass", VALUE)
    assert keyring.lookup("secret:db_pass") == VALUE
    assert keyring.lookup("secret:nope") is None
    assert keyring.services("secret:") == ["secret:db_pass"]
    _handle_secret_builtin("list")
    out = capsys.readouterr().out
    assert "backend: pass" in out and "db_pass" in out and VALUE not in out
    assert keyring.delete("secret:db_pass") is True
    assert keyring.delete("secret:db_pass") is False
    assert keyring.services("secret:") == []


def test_value_goes_on_stdin_not_argv(no_dbus, store):
    keyring.store_api_key("secret:tok", VALUE)
    argv, stdin, _ = store[0]
    assert argv == ["pass", "insert", "-m", "-f", "sable/tok"]
    assert VALUE in stdin
    assert all(VALUE not in " ".join(call[0]) for call in store)


def test_resolve_uses_pass(no_dbus, store):
    keyring.store_api_key("secret:db_pass", VALUE)
    _, env, _ = secrets.resolve("psql -p $SECRET:db_pass")
    assert VALUE in env.values()


def test_name_validation_still_applies(no_dbus, store, capsys):
    _handle_secret_builtin("add ../etc", prompt=lambda _p: VALUE)
    assert "letters, digits" in capsys.readouterr().out
    assert store == []


def test_api_keys_do_not_use_pass(no_dbus, store):
    with pytest.raises(RuntimeError):
        keyring.store_api_key("openai", VALUE)
    assert store == []


def test_timeout_is_refusal(no_dbus, store, monkeypatch, tmp_path):
    (tmp_path / "sable" / "slow.gpg").write_text("x")

    def slow(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"])
    monkeypatch.setattr(passstore.subprocess, "run", slow)
    with pytest.raises(keyring.KeyringUnavailable, match="timed out"):
        keyring.lookup("secret:slow")
    with pytest.raises(secrets.SecretError):
        secrets.resolve("echo $SECRET:slow")


def test_neither_backend_mentions_pass_init(no_dbus, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PASSWORD_STORE_DIR", str(tmp_path))  # no .gpg-id
    with pytest.raises(keyring.KeyringUnavailable, match="pass init"):
        keyring.lookup("secret:x")
    _handle_secret_builtin("add x", prompt=lambda _p: VALUE)
    assert "pass init" in capsys.readouterr().out


def test_secret_service_wins_when_present(monkeypatch, store):
    class Item:
        def get_secret(self):
            return b"from-keyring"

    class Coll:
        def search_items(self, _a):
            return [Item()]
    monkeypatch.setattr(keyring, "_collection", lambda: Coll())
    assert keyring.lookup("secret:any") == "from-keyring"
    assert store == []
