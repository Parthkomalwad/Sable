"""G6: themes persisted in config, layout presets mapped to tmux commands."""
from __future__ import annotations

import json

from sable.core.config.schema import THEME_NAMES, ShellConfig
from sable.ui import theme
from sable.ui.tmux import layout


def test_theme_names_match_palettes():
    assert set(THEME_NAMES) == set(theme.THEMES)


def test_old_config_loads_with_default_theme():
    data = ShellConfig.defaults().to_dict()
    data.pop("theme")
    assert ShellConfig.from_dict(data).theme == "default"
    data["theme"] = "nope"
    assert ShellConfig.from_dict(data).theme == "default"


def test_theme_persists_and_round_trips(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"model": "m", "backend": "ollama"}))
    assert theme.persist("mono", path)
    data = json.loads(path.read_text())
    assert data == {"model": "m", "backend": "ollama", "theme": "mono"}
    assert ShellConfig.from_dict(data).theme == "mono"


def test_set_theme_changes_sgr():
    try:
        assert not theme.set_theme("nope")
        assert theme.set_theme("high-contrast")
        assert theme.sgr("err") == "\033[1;91m"
    finally:
        theme.set_theme("default")


PANES = [("%0", 160, 40), ("%1", 40, 46), ("%2", 160, 5)]


def test_focus_zooms_main_only():
    assert layout.preset_commands("focus", "%0", PANES, False, 200, 46) == [
        ["tmux", "resize-pane", "-Z", "-t", "%0"]]
    assert layout.preset_commands("focus", "%0", PANES, True, 200, 46) == []


def test_fleet_and_minimal():
    fleet = layout.preset_commands("fleet", "%0", PANES, True, 200, 50)
    assert fleet == [["tmux", "resize-pane", "-Z", "-t", "%0"],
                     ["tmux", "resize-pane", "-t", "%1", "-x", "40"],
                     ["tmux", "resize-pane", "-t", "%2", "-y", "6"]]
    minimal = layout.preset_commands("minimal", "%0", PANES, False, 200, 50)
    assert ["tmux", "resize-pane", "-t", "%1", "-x", "1"] in minimal
    assert ["tmux", "resize-pane", "-t", "%2", "-y", "6"] in minimal


def test_outside_tmux_is_a_clear_no_op(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    assert "not inside tmux" in layout.apply_preset("fleet")
    assert "usage" in layout.apply_preset("bogus")
