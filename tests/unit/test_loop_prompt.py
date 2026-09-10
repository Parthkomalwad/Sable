"""Tests for _render_prompt() Powerline prompt."""
import sys
import os
import importlib

# Patch subprocess before importing loop to avoid git calls in import
import subprocess as _sp
_real_run = _sp.run

def test_render_prompt_no_git(monkeypatch, tmp_path):
    """Prompt renders path+time segments when not in a git repo."""
    # Make git return empty (no branch)
    def fake_run(cmd, **kwargs):
        class R:
            stdout = ""
            returncode = 1
        return R()
    monkeypatch.setattr(_sp, "run", fake_run)

    # Reload to pick up monkeypatch
    import sable.app.repl as loop_mod
    importlib.reload(loop_mod)

    result = loop_mod._render_prompt("/home/user/project", 0)
    # Must contain path and cursor
    assert "/home/user/project" in result or "~" in result
    assert "❯" in result


def test_render_prompt_with_git(monkeypatch):
    """Prompt includes branch name when git returns one."""
    import subprocess as sp

    def fake_run(cmd, **kwargs):
        class R:
            stdout = "main"
            returncode = 0
        return R()
    monkeypatch.setattr(sp, "run", fake_run)

    import sable.app.repl as loop_mod
    importlib.reload(loop_mod)

    result = loop_mod._render_prompt("/home/user/project", 0)
    assert "main" in result


def test_render_prompt_red_cursor_on_failure(monkeypatch):
    """❯ uses red color when last_exit != 0."""
    import subprocess as sp

    def fake_run(cmd, **kwargs):
        class R:
            stdout = ""
            returncode = 1
        return R()
    monkeypatch.setattr(sp, "run", fake_run)

    import sable.app.repl as loop_mod
    importlib.reload(loop_mod)

    result = loop_mod._render_prompt("/home/user", 1)
    # red = ANSI 203
    assert "203" in result
