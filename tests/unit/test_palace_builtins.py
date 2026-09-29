"""Phase 7 Task 2 (C5, C2): /palace, /remember, /forget."""
from __future__ import annotations

import sys
import types

import pytest

from sable.app.builtins import palace as cmds
from sable.app.builtins.dispatch import handle_builtin
from sable.memory import palace


@pytest.fixture(autouse=True)
def tmp_palace(tmp_path, monkeypatch):
    monkeypatch.setattr(palace, "ROOT", tmp_path / "palace")
    monkeypatch.setattr(palace, "DB", tmp_path / "s.db")


def _fid(out: str) -> str:
    return out.split()[1]


def test_empty_palace_says_how_facts_arrive(capsys):
    cmds.handle_palace("")
    assert "/remember" in capsys.readouterr().out


def test_remember_defaults_to_user_semantic_by_you(capsys):
    cmds.handle_remember('"deploys go out on Tuesdays"')
    fid = _fid(capsys.readouterr().out)
    f = palace.why(fid)
    assert (f.room, f.tier, f.sources, f.text) == ("user", "semantic", [{"by": "you"}],
                                                    "deploys go out on Tuesdays")


def test_remember_room_flag(capsys):
    cmds.handle_remember("nginx lives in /etc/nginx --room server")
    f = palace.why(_fid(capsys.readouterr().out))
    assert f.room == "server" and f.text == "nginx lives in /etc/nginx"
    cmds.handle_remember("app on 8080 --room=repos/myapp")
    assert palace.why(_fid(capsys.readouterr().out)).room == "repos/myapp"


def test_remember_bad_room_and_usage(capsys):
    cmds.handle_remember("x --room ../etc")
    assert "room must be" in capsys.readouterr().out
    cmds.handle_remember("--room server")
    assert "usage" in capsys.readouterr().out
    assert palace.rooms() == {}


def test_palace_lists_rooms_and_room(capsys):
    palace.remember("the user prefers vim", "user", {"by": "you"})
    palace.remember("disk /data is big", "server", {"session": "s1"}, untrusted=True)
    cmds.handle_palace("")
    out = capsys.readouterr().out
    assert "server" in out and "user" in out and "1 fact" in out
    cmds.handle_palace("server")
    out = capsys.readouterr().out
    assert "disk /data is big" in out and "untrusted" in out and "vim" not in out


def test_unknown_room(capsys):
    cmds.handle_palace("nowhere")
    assert "room must be" in capsys.readouterr().out
    cmds.handle_palace("incidents")
    assert "no facts in incidents" in capsys.readouterr().out


def test_find(capsys):
    palace.remember("myapp logs to /var/log/myapp", "server", {"by": "you"})
    cmds.handle_palace("find myapp logs")
    assert "/var/log/myapp" in capsys.readouterr().out
    cmds.handle_palace("find zebra")
    assert "nothing" in capsys.readouterr().out


def test_why_shows_sources(capsys):
    fid = palace.remember("myapp logs to /var/log/myapp", "server",
                          {"session": "s1", "goal": "where are logs", "commands": ["cat /etc/myapp.conf"]})
    cmds.handle_palace(f"why {fid}")
    out = capsys.readouterr().out
    for s in ("session: s1", "goal: where are logs", "$ cat /etc/myapp.conf", "server", "episodic"):
        assert s in out
    cmds.handle_palace("why fnope")
    assert "no fact" in capsys.readouterr().out


def test_forget(capsys):
    fid = palace.remember("temporary redis fact", "user", {"by": "you"})
    cmds.handle_forget(fid)
    assert "temporary redis fact" in capsys.readouterr().out
    assert palace.recall("redis") == []
    cmds.handle_forget(fid)
    assert "no fact" in capsys.readouterr().out


def test_markup_and_control_chars_are_inert(capsys):
    fid = palace.remember("[bold red]owned[/bold red] \x1b[31mx", "server", {"goal": "[link=x]y"})
    cmds.handle_palace("server")
    out = capsys.readouterr().out
    assert "[bold red]owned" in out and "\x1b[31m" not in out
    cmds.handle_palace(f"why {fid}")
    out = capsys.readouterr().out
    assert "[link=x]y" in out and "\x1b" not in out


def test_consolidate_hook(capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "sable.memory.consolidate", None)
    cmds.handle_palace("consolidate")
    assert "Task 3" in capsys.readouterr().out
    monkeypatch.setitem(sys.modules, "sable.memory.consolidate",
                        types.SimpleNamespace(run=lambda: "merged 2 facts"))
    cmds.handle_palace("consolidate")
    assert "merged 2 facts" in capsys.readouterr().out


def test_dispatch_routes(capsys):
    assert handle_builtin("/remember hi there", None, "s", None)
    assert handle_builtin("/palace", None, "s", None)
    assert "user" in capsys.readouterr().out
    assert not handle_builtin("/palaces", None, "s", None)
