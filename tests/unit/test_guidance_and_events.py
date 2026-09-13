"""Steering a running agent (A7) and reading its event stream.

`/task <name> guide` delivers a line to a worker's stdin; the worker drains its
guidance queue at the top of each turn, so advice lands on the next turn rather
than interrupting the command in flight. `/task <name> events` renders the same
bus rows the orchestrator and the sidebar read.
"""
from __future__ import annotations

import sqlite3
import sys
from unittest.mock import MagicMock, patch

import pytest

from sable.core.config.schema import ShellConfig


@pytest.fixture(autouse=True)
def mock_libtmux():
    with patch.dict(sys.modules, {"libtmux": MagicMock()}):
        yield


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A real sqlite database with a tasks table, plus DB_PATH redirected."""
    path = tmp_path / "sessions.db"
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE tasks (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             name TEXT NOT NULL UNIQUE, goal TEXT NOT NULL,
             status TEXT NOT NULL DEFAULT 'starting', tmux_window_id TEXT,
             pid INTEGER, step_count INTEGER NOT NULL DEFAULT 0,
             last_output TEXT, created_at TEXT NOT NULL, ended_at TEXT)"""
    )
    conn.commit()
    monkeypatch.setattr("sable.core.db.DB_PATH", path)

    class _DB:
        def __init__(self, connection):
            self._conn = connection

    holder = _DB(conn)
    holder.path = path
    yield holder
    conn.close()


def _add_task(db, name="w1", window_id="@3", status="running"):
    db._conn.execute(
        "INSERT INTO tasks (name, goal, status, tmux_window_id, created_at) "
        "VALUES (?, ?, ?, ?, '2026-09-13T00:00:00')",
        (name, "do a thing", status, window_id),
    )
    db._conn.commit()


def _manager(db, tmp_path, window=None):
    from sable.agents.manager import TaskManager

    config = MagicMock()
    config.tasks_base_dir = str(tmp_path / "tasks")
    manager = TaskManager(config=config, db=db)
    session = MagicMock()
    manager._session = lambda: session
    manager._find_window = lambda _session, _id: window
    return manager


class TestGuide:
    def test_sends_the_text_to_the_agents_pane(self, db, tmp_path):
        _add_task(db)
        window = MagicMock()
        manager = _manager(db, tmp_path, window=window)

        assert manager.guide("w1", "use python3.11") is True

        window.active_pane.send_keys.assert_called_once()
        args, kwargs = window.active_pane.send_keys.call_args
        assert args[0] == "use python3.11"
        assert kwargs["enter"] is True

    def test_sends_literally_so_shell_metacharacters_survive(self, db, tmp_path):
        """tmux would otherwise interpret a semicolon as a command separator."""
        _add_task(db)
        window = MagicMock()
        manager = _manager(db, tmp_path, window=window)

        manager.guide("w1", "run: a; b && c")

        assert manager._find_window is not None
        assert window.active_pane.send_keys.call_args.kwargs["literal"] is True

    def test_empty_guidance_is_refused(self, db, tmp_path):
        _add_task(db)
        window = MagicMock()
        manager = _manager(db, tmp_path, window=window)

        assert manager.guide("w1", "   ") is False
        window.active_pane.send_keys.assert_not_called()

    def test_unknown_agent_is_refused(self, db, tmp_path):
        manager = _manager(db, tmp_path, window=MagicMock())
        assert manager.guide("nobody", "hello") is False

    def test_agent_without_a_window_is_refused(self, db, tmp_path):
        _add_task(db, window_id=None)
        assert _manager(db, tmp_path, window=MagicMock()).guide("w1", "hi") is False

    @pytest.mark.parametrize("status", ["completed", "lost"])
    def test_a_finished_agent_is_refused(self, db, tmp_path, status):
        """Guidance to an agent that has stopped would go to a dead pane, or
        worse, to whatever reused its window."""
        _add_task(db, status=status)
        assert _manager(db, tmp_path, window=MagicMock()).guide("w1", "hi") is False

    def test_a_missing_window_is_refused(self, db, tmp_path):
        _add_task(db)
        assert _manager(db, tmp_path, window=None).guide("w1", "hi") is False

    def test_a_send_failure_is_reported_not_raised(self, db, tmp_path):
        _add_task(db)
        window = MagicMock()
        window.active_pane.send_keys.side_effect = OSError("pane gone")

        assert _manager(db, tmp_path, window=window).guide("w1", "hi") is False

    def test_guidance_is_published_to_the_bus(self, db, tmp_path):
        """So `/task events` and the sidebar can show that a human stepped in."""
        from sable.core.events.bus import EventBus
        from sable.core.events.types import EventKind

        _add_task(db)
        manager = _manager(db, tmp_path, window=MagicMock())

        manager.guide("w1", "use python3.11")

        with EventBus(db_path=db.path) as bus:
            events = [e for e in bus.since(agent="w1") if e.kind == EventKind.GUIDANCE]
        assert len(events) == 1
        assert events[0].payload["text"] == "use python3.11"


class TestTaskGuideBuiltin:
    def _dispatch(self, line, db):
        from sable.app.builtins.dispatch import handle_builtin

        return handle_builtin(
            line, db=db, session_id="s", config=ShellConfig.defaults(), turns=[]
        )

    def test_guide_reaches_the_manager(self, db, tmp_path, capsys):
        _add_task(db)
        manager = MagicMock()
        manager.guide.return_value = True

        with patch("sable.agents.manager.TaskManager", return_value=manager):
            self._dispatch("/task guide w1 use python3.11", db)

        manager.guide.assert_called_once_with("w1", "use python3.11")
        assert "guidance sent" in capsys.readouterr().out

    def test_a_refused_guide_says_why(self, db, tmp_path, capsys):
        manager = MagicMock()
        manager.guide.return_value = False

        with patch("sable.agents.manager.TaskManager", return_value=manager):
            self._dispatch("/task guide w1 hello", db)

        assert "could not reach" in capsys.readouterr().out

    def test_guide_appears_in_the_usage_line(self, db, capsys):
        with patch("sable.agents.manager.TaskManager", return_value=MagicMock()):
            self._dispatch("/task", db)
        assert "guide" in capsys.readouterr().out


class TestTaskEvents:
    def _dispatch(self, line, db):
        from sable.app.builtins.dispatch import handle_builtin

        return handle_builtin(
            line, db=db, session_id="s", config=ShellConfig.defaults(), turns=[]
        )

    def _publish(self, db, agent, kind, **payload):
        from sable.core.events.bus import EventBus

        with EventBus(db_path=db.path) as bus:
            bus.publish(agent, kind, payload)

    def test_reports_when_there_is_nothing(self, db, capsys):
        with patch("sable.agents.manager.TaskManager", return_value=MagicMock()):
            self._dispatch("/task events w1", db)
        assert "no events for 'w1'" in capsys.readouterr().out

    def test_renders_the_stream_in_order(self, db, capsys):
        from sable.core.events.types import EventKind

        self._publish(db, "w1", EventKind.STARTED, goal="build it")
        self._publish(db, "w1", EventKind.STATUS, step=1, command="ls -la")
        self._publish(db, "w1", EventKind.COMPLETED, explanation="all done")

        with patch("sable.agents.manager.TaskManager", return_value=MagicMock()):
            self._dispatch("/task events w1", db)

        out = capsys.readouterr().out
        assert out.index("started") < out.index("status") < out.index("completed")
        assert "ls -la" in out
        assert "all done" in out

    def test_only_the_named_agent_is_shown(self, db, capsys):
        from sable.core.events.types import EventKind

        self._publish(db, "mine", EventKind.STARTED, goal="my goal")
        self._publish(db, "theirs", EventKind.STARTED, goal="their goal")

        with patch("sable.agents.manager.TaskManager", return_value=MagicMock()):
            self._dispatch("/task events mine", db)

        out = capsys.readouterr().out
        assert "my goal" in out
        assert "their goal" not in out

    def test_guidance_shows_in_the_stream(self, db, capsys):
        """A human steering an agent is part of what happened to it."""
        from sable.core.events.types import EventKind

        self._publish(db, "w1", EventKind.GUIDANCE, text="use python3.11")

        with patch("sable.agents.manager.TaskManager", return_value=MagicMock()):
            self._dispatch("/task events w1", db)

        assert "use python3.11" in capsys.readouterr().out

    def test_a_failure_reason_is_shown(self, db, capsys):
        from sable.core.events.types import EventKind

        self._publish(db, "w1", EventKind.FAILED, reason="LLM unreachable")

        with patch("sable.agents.manager.TaskManager", return_value=MagicMock()):
            self._dispatch("/task events w1", db)

        assert "LLM unreachable" in capsys.readouterr().out


class TestOrchestratorRepoContext:
    """K11: the conventions go in, marked as data rather than instructions."""

    def _orchestrator(self, tmp_path, cwd):
        from sable.agents.orchestrator import OrchestratorAgent

        config = MagicMock()
        config.tasks_base_dir = str(tmp_path / "tasks")
        config.model = "m"
        return OrchestratorAgent(
            goal="do a thing", cwd=str(cwd), config=config,
            db_path=str(tmp_path / "x.db"), task_manager=MagicMock(),
        )

    def test_a_repo_claude_md_reaches_the_context(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "CLAUDE.md").write_text("Always use docker compose v2.")

        messages = self._orchestrator(tmp_path, repo)._build_messages()

        assert any("docker compose v2" in m["content"] for m in messages)

    def test_it_is_marked_untrusted(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "CLAUDE.md").write_text("some conventions")

        messages = self._orchestrator(tmp_path, repo)._build_messages()

        assert any('untrusted="true"' in m["content"] for m in messages)

    def test_no_repo_means_no_extra_messages(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()

        messages = self._orchestrator(tmp_path, plain)._build_messages()

        assert not any("project-instructions" in m["content"] for m in messages)

    def test_the_goal_still_comes_first(self, tmp_path):
        """Project conventions inform the work; they do not displace it."""
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "CLAUDE.md").write_text("conventions")

        messages = self._orchestrator(tmp_path, repo)._build_messages()

        assert "<goal>" in messages[0]["content"]
