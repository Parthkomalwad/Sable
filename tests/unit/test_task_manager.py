import os
import shlex
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
import sys


@pytest.fixture(autouse=True)
def mock_libtmux():
    """Mock libtmux before importing TaskManager."""
    with patch.dict(sys.modules, {"libtmux": MagicMock()}):
        yield


def _make_manager(tmp_path):
    # Defer import to after libtmux is mocked (via autouse fixture)
    from shell.tasks.manager import TaskManager
    config = MagicMock()
    config.tasks_base_dir = str(tmp_path / "tasks")
    db = MagicMock()
    db._conn = MagicMock()
    db._conn.execute.return_value = MagicMock()
    manager = TaskManager(config=config, db=db)
    return manager


def test_spawn_with_task_base_dir_uses_custom_path(tmp_path):
    """When task_base_dir is provided, task_dir should be task_base_dir/name."""
    manager = _make_manager(tmp_path)
    shared = tmp_path / "tasks" / "my-task-20260406"
    shared.mkdir(parents=True)

    captured = {}

    def fake_send_keys(cmd, enter=True):
        captured["cmd"] = cmd

    with patch.object(manager, "_session") as mock_session:
        mock_win = MagicMock()
        mock_win.window_id = "@1"
        mock_win.active_pane.send_keys = fake_send_keys
        mock_session.return_value.new_window.return_value = mock_win

        manager.spawn(
            name="frontend",
            goal="create react app",
            task_base_dir=str(shared),
        )

    assert "frontend" in captured["cmd"]
    expected_dir = shared / "frontend"
    assert expected_dir.exists()


def test_spawn_without_task_base_dir_uses_global(tmp_path):
    """When task_base_dir is None, task_dir uses global tasks_base."""
    manager = _make_manager(tmp_path)

    with patch.object(manager, "_session") as mock_session:
        mock_win = MagicMock()
        mock_win.window_id = "@1"
        mock_win.active_pane.send_keys = MagicMock()
        mock_session.return_value.new_window.return_value = mock_win

        manager.spawn(name="my-task", goal="do something")

    global_dir = Path(manager._config.tasks_base_dir) / "my-task"
    assert global_dir.exists()


def test_spawn_with_task_base_dir_includes_shared_arg(tmp_path):
    """When task_base_dir is set, the tmux command includes --shared-read-dir."""
    manager = _make_manager(tmp_path)
    shared = tmp_path / "tasks" / "my-task-20260406"
    shared.mkdir(parents=True)

    captured = {}

    def fake_send_keys(cmd, enter=True):
        captured["cmd"] = cmd

    with patch.object(manager, "_session") as mock_session:
        mock_win = MagicMock()
        mock_win.window_id = "@1"
        mock_win.active_pane.send_keys = fake_send_keys
        mock_session.return_value.new_window.return_value = mock_win

        manager.spawn(
            name="frontend",
            goal="create react app",
            task_base_dir=str(shared),
        )

    assert f"--shared-read-dir {shlex.quote(str(shared))}" in captured["cmd"]


def test_spawn_without_task_base_dir_no_shared_arg(tmp_path):
    """When task_base_dir is None, tmux command does NOT include --shared-read-dir."""
    manager = _make_manager(tmp_path)
    captured = {}

    def fake_send_keys(cmd, enter=True):
        captured["cmd"] = cmd

    with patch.object(manager, "_session") as mock_session:
        mock_win = MagicMock()
        mock_win.window_id = "@1"
        mock_win.active_pane.send_keys = fake_send_keys
        mock_session.return_value.new_window.return_value = mock_win

        manager.spawn(name="my-task", goal="do something")

    assert "--shared-read-dir" not in captured.get("cmd", "")
