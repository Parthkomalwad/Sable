"""G5: palette fuzzy match and sources."""
from __future__ import annotations

from unittest.mock import patch

from sable.ui import palette


def test_fuzzy_subsequence_and_ranking():
    assert palette.score("tsk", "/task") is not None
    assert palette.score("xyz", "/task") is None
    items = [palette.Item("builtin", "/tools", "/tools"), palette.Item("builtin", "/task", "/task")]
    assert palette.search("tas", items)[0].label == "/task"


def test_builtins_from_help_run_or_insert():
    items = {i.text.strip(): i for i in palette.builtin_items(
        "  /stats         Last 7 days\n  /task replay <n>     Every turn\n  >> text  x\n")}
    assert items["/stats"].run is True
    assert items["/task"].run is False
    assert len(items) == 2


class FakeDB:
    class _conn:
        @staticmethod
        def execute(sql):
            class R:
                def fetchall(self):
                    return [("build-api", "running")]
            return R()

    def list_snippets(self):
        return [{"id": 1, "note": "list containers", "command": "docker ps -a", "tags": ""}]


def test_gather_covers_every_source():
    class Idx:
        def list_all(self):
            return [{"name": "deploy", "status": "approved"}]

    with patch("sable.skills.index.SkillIndex", Idx):
        items = palette.gather(FakeDB())
    kinds = {i.kind for i in items}
    assert kinds == {"builtin", "skill", "snippet", "task"}
    by_kind = {i.kind: i for i in items if i.kind != "builtin"}
    assert by_kind["snippet"].text == "docker ps -a" and not by_kind["snippet"].run
    assert by_kind["task"].text == "/task attach build-api"
    assert by_kind["skill"].text == "/skill show deploy"
    assert any(i.text == "/theme " for i in items)
