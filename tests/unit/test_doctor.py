"""sable doctor and config migrations (Phase 7 Task 4, I6)."""
from __future__ import annotations

import json
import os
import sqlite3
import sys

import pytest

from sable.app import doctor
from sable.core.config import migrate
from sable.core.config.schema import ShellConfig

V1 = {"backend": "ollama", "model": "llama3.1", "setup_complete": True,
      "some_future_key": [1, 2]}


@pytest.mark.parametrize("data, version", [
    ({}, 1), (V1, 1), ({"schema_version": 2}, 2),
])
def test_version_of(data, version):
    assert migrate.version_of(data) == version


def test_v1_to_v2_adds_version_and_defaults_keeps_unknown():
    out, notes = migrate.migrate(dict(V1))
    assert out["schema_version"] == migrate.CURRENT == 2
    assert out["notify"] == {} and out["mcp"] == {} and out["theme"] == "default"
    assert out["some_future_key"] == [1, 2]
    assert out["model"] == "llama3.1"
    assert notes


def test_migrate_does_not_mutate_input_and_is_idempotent():
    src = dict(V1)
    once, _ = migrate.migrate(src)
    assert "schema_version" not in src
    twice, notes = migrate.migrate(once)
    assert twice == once and notes == []


def test_v1_keeps_existing_values():
    out, _ = migrate.migrate({**V1, "notify": {"topic": "x"}})
    assert out["notify"] == {"topic": "x"}


def test_shellconfig_round_trips_schema_version():
    assert ShellConfig.from_dict(V1).to_dict()["schema_version"] == migrate.CURRENT


def _probes(**over):
    base = dict(which=lambda name: "/usr/bin/" + name, bwrap=lambda: True,
                stale=lambda fix: [], palace=lambda fix: ("ok", "fine"),
                rlimits=lambda: True)
    base.update(over)
    return base


def _setup(tmp_path, data):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(data))
    os.chmod(cfg, 0o600)
    db = tmp_path / "s.db"
    sqlite3.connect(db).close()
    return cfg, db


def _by_name(checks):
    return {c.name: c for c in checks}


def test_all_ok(tmp_path):
    cfg, db = _setup(tmp_path, {**V1, "schema_version": 2})
    checks = doctor.run(config_path=cfg, db_path=db, **_probes())
    assert all(c.status == "ok" for c in checks), checks
    assert {"python", "tmux", "sandbox", "database", "stale tasks",
            "config version", "palace"} <= set(_by_name(checks))


def test_missing_tools_and_stale_reported(tmp_path):
    cfg, db = _setup(tmp_path, V1)
    checks = _by_name(doctor.run(
        config_path=cfg, db_path=db,
        **_probes(which=lambda n: None, bwrap=lambda: False,
                  stale=lambda fix: ["t1"])))
    assert checks["tmux"].status == "warn"
    assert checks["sandbox"].status == "warn"
    assert "network" in checks["agent limits"].detail and checks["agent limits"].status == "warn"
    assert checks["stale tasks"].status == "warn" and "t1" in checks["stale tasks"].detail
    assert checks["config version"].status == "warn"
    assert checks["config version"].fix_hint


def test_stale_probe_gets_fix_flag(tmp_path):
    cfg, db = _setup(tmp_path, V1)
    seen = []
    doctor.run(fix=True, config_path=cfg, db_path=db,
               **_probes(stale=lambda fix: seen.append(fix) or []))
    assert seen == [True]


def test_fix_backs_up_and_migrates(tmp_path):
    cfg, db = _setup(tmp_path, V1)
    checks = _by_name(doctor.run(fix=True, config_path=cfg, db_path=db, **_probes()))
    assert checks["config version"].status == "ok"
    assert json.loads(cfg.read_text())["schema_version"] == 2
    backups = list(tmp_path.glob("config.json.bak-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == V1


def test_corrupt_db_fails(tmp_path):
    cfg, _ = _setup(tmp_path, {**V1, "schema_version": 2})
    db = tmp_path / "bad.db"
    db.write_bytes(b"not a database" * 100)
    checks = _by_name(doctor.run(config_path=cfg, db_path=db, **_probes()))
    assert checks["database"].status == "fail"


def test_palace_drift_is_reported_and_fixed(tmp_path, monkeypatch):
    from sable.memory import palace
    monkeypatch.setattr(palace, "ROOT", tmp_path / "palace")
    monkeypatch.setattr(palace, "DB", tmp_path / "s.db")
    palace.remember("myapp logs to /var/log/myapp", "server", {"by": "test"})
    (tmp_path / "s.db").unlink()          # the index is gone, the file is not
    assert doctor._palace_probe(False) == ("warn", "1 fact files, 0 indexed")
    assert doctor._palace_probe(True)[0] == "ok"
    assert doctor._palace_probe(False) == ("ok", "1 fact files")
