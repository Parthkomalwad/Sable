"""K9: incidents become runbooks, offered in /inbox, never auto-run."""
import sqlite3
from types import SimpleNamespace

import pytest

from sable.memory import palace, runbooks

SRC = {"session": "s1", "goal": "g", "agent": "orchestrator"}
STEPS = [{"command": "df -h"}, {"command": "systemctl restart nginx"}]


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(palace, "ROOT", tmp_path / "palace")
    monkeypatch.setattr(palace, "DB", tmp_path / "s.db")


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "q.db")
    yield c
    c.close()


def test_failure_signals():
    for g in ("nginx is down", "site returns 502", "api not responding", "disk full on /var",
              "fix watcher #3", "This command failed. Propose one command"):
        assert runbooks.is_failure_signal(g), g
    assert not runbooks.is_failure_signal("list my files")
    assert runbooks.watcher_of("fix watcher #12 now") == 12


def test_drafts_only_on_signal_with_verify_and_change():
    assert runbooks.draft("show disk usage", STEPS, ["curl -f localhost"], SRC) is None
    assert runbooks.draft("nginx is down", STEPS, [], SRC) is None  # no passing verify
    assert runbooks.draft("nginx is down", STEPS[:1], ["x"], SRC) is None  # nothing changed
    fid = runbooks.draft("nginx is down, watcher #4", STEPS, ["curl -f localhost"], SRC)
    f = palace.why(fid)
    assert f.room == "incidents" and f.tier == "episodic"
    assert f.text == ("runbook for watcher #4: symptom: nginx is down, watcher #4 | "
                      "checks: df -h | fix: systemctl restart nginx | "
                      "verify: curl -f localhost")
    assert f.sources[0]["session"] == "s1"


def test_matches_later_alert_by_id_and_by_text():
    fid = runbooks.draft("watcher #4 failing: http localhost 502", STEPS, ["curl -f localhost"], SRC)
    assert runbooks.find(4) == (fid, ["systemctl restart nginx"])
    assert runbooks.find(9, "http localhost 502") == (fid, ["systemctl restart nginx"])
    assert runbooks.find(9, "disk /home 95") is None


def test_offer_shows_in_inbox_and_runs_only_on_approve(tmp_path, conn, monkeypatch):
    import sys

    from sable.app.builtins import inbox
    from sable.ui import state

    ran = []  # planner stubbed: the real one needs ptyprocess and a tty
    stub = SimpleNamespace(execute_plan=lambda plan, cwd, d="": ran.append(plan) or 0)
    import sable.agents
    # Both: `from sable.agents import planner` reads the package attribute
    # once another test has imported the real planner.
    monkeypatch.setitem(sys.modules, "sable.agents.planner", stub)
    monkeypatch.setattr(sable.agents, "planner", stub, raising=False)
    fid = runbooks.draft("watcher #4 is down", STEPS, ["curl -f localhost"], SRC)
    assert runbooks.offer(conn, 4, "http localhost 502") == fid
    assert ran == []  # filing an offer runs nothing
    items = [i for i in state.inbox_all(tmp_path / "q.db") if i.kind == "runbook"]
    assert [i.text for i in items] == [f"run runbook {fid} for watcher #4"]
    assert inbox.key(items[0]) == f"r{items[0].id}"
    db = SimpleNamespace(_conn=conn)
    assert inbox._decide(items[0], False, db).startswith("rejected")
    assert ran == []
    runbooks.offer(conn, 4, "")
    item = [i for i in state.inbox_all(tmp_path / "q.db") if i.kind == "runbook"][0]
    inbox._decide(item, True, db)
    assert ran == [["systemctl restart nginx"]]
    assert not [i for i in state.inbox_all(tmp_path / "q.db") if i.kind == "runbook"]


def test_watcher_fire_offers_and_push_says_so(conn, monkeypatch):
    from sable.daemon import approvals, jobs, notify, watchers

    fired, sent = [], []
    monkeypatch.setattr(jobs, "_publish", lambda k, p: fired.append(p))
    runbooks.draft("watcher #4 is down", STEPS, ["curl -f localhost"], SRC)
    watchers._fire(conn, {"id": 4, "kind": "http", "target": "u", "arg": "200", "tier": "notify",
                          "steps": []}, "502")
    assert fired[0]["runbook"]
    monkeypatch.setattr(approvals, "_get", lambda c, k: "0")
    monkeypatch.setattr(approvals, "_put", lambda c, k, v: None)
    monkeypatch.setattr(approvals, "_events", lambda cur: (
        [SimpleNamespace(kind="watch.fired", payload=fired[0])], 1))
    monkeypatch.setattr(notify, "send", lambda t, b, a=(): sent.append(b) or True)
    approvals._push_events(conn)
    assert "a runbook exists: run it from /inbox" in sent[0]
