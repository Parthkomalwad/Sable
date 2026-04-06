import os
import pytest
from shell.tasks.sandbox import Sandbox


def test_shared_read_dir_stored(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shared = tmp_path / "shared"
    shared.mkdir()
    sb = Sandbox(task_dir=str(workspace), shared_read_dir=str(shared))
    assert sb._shared_read_dir == str(shared.resolve())


def test_no_shared_read_dir_default(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sb = Sandbox(task_dir=str(workspace))
    assert sb._shared_read_dir is None


def test_bwrap_command_includes_ro_bind_when_shared(tmp_path, monkeypatch):
    """When bwrap is used and shared_read_dir is set, wrap_command includes --ro-bind."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shared = tmp_path / "shared"
    shared.mkdir()
    sb = Sandbox(task_dir=str(workspace), shared_read_dir=str(shared))
    monkeypatch.setattr(sb, "use_bwrap", True)
    cmd = sb.wrap_command("echo hi")
    assert "--ro-bind" in cmd
    assert str(shared.resolve()) in cmd


def test_bash_guard_no_change_when_shared(tmp_path):
    """bash-wrapper fallback: wrap_command is unchanged (reads already allowed)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shared = tmp_path / "shared"
    shared.mkdir()
    sb = Sandbox(task_dir=str(workspace), shared_read_dir=str(shared))
    sb.use_bwrap = False
    cmd = sb.wrap_command("echo hi")
    assert "WORKSPACE" in cmd
    assert "echo hi" in cmd
