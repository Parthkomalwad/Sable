"""The agent bus (Phase 1, A1).

The bus replaces a `status.md` / `result.md` file handshake that the
orchestrator polled every few seconds. These tests pin the properties that
made it worth replacing:

  - ordering is by `id`, so a reader's cursor is a single integer
  - a reader that starts late still sees everything after its cursor, which
    is what fixes the read-then-unlink race the files had
  - publishing never raises, because an agent reporting its own progress
    must not die when the report fails
"""
from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from sable.core.events.bus import EventBus
from sable.core.events.types import AgentEvent, EventKind


@pytest.fixture
def bus(tmp_path):
    instance = EventBus(db_path=tmp_path / "sessions.db")
    try:
        yield instance
    finally:
        instance.close()


class TestPublish:
    def test_returns_the_new_row_id(self, bus):
        assert bus.publish("w1", EventKind.STARTED) == 1
        assert bus.publish("w1", EventKind.STATUS) == 2

    def test_ids_increase_monotonically(self, bus):
        """The cursor contract: a bigger id is always a later event."""
        ids = [bus.publish("w1", EventKind.STATUS, {"step": n}) for n in range(10)]
        assert ids == sorted(ids)
        assert len(set(ids)) == 10

    def test_payload_round_trips(self, bus):
        bus.publish("w1", EventKind.STATUS, {"step": 3, "cmd": "ls -la", "ok": True})
        event = bus.since()[0]
        assert event.payload == {"step": 3, "cmd": "ls -la", "ok": True}

    def test_missing_payload_becomes_an_empty_dict(self, bus):
        bus.publish("w1", EventKind.STARTED)
        assert bus.since()[0].payload == {}

    def test_unserialisable_payload_does_not_raise(self, bus):
        """`default=str` in payload_json: a stray object must not kill the agent."""
        assert bus.publish("w1", EventKind.STATUS, {"path": object()}) is not None

    def test_publish_returns_none_instead_of_raising_on_db_error(self, bus):
        """The property the whole design leans on.

        An agent publishes progress from inside its own turn loop. If the bus
        can throw, a full disk or a locked database takes down the work
        itself, which is strictly worse than losing the status line.
        """
        bus.close()   # every subsequent write now fails
        assert bus.publish("w1", EventKind.STATUS) is None

    def test_creates_its_own_table(self, tmp_path):
        """A worker is a fresh process and may publish before Database exists."""
        with EventBus(db_path=tmp_path / "fresh.db") as fresh:
            assert fresh.publish("w1", EventKind.STARTED) == 1


class TestSince:
    def test_empty_bus_returns_nothing(self, bus):
        assert bus.since() == []

    def test_returns_only_events_after_the_cursor(self, bus):
        bus.publish("w1", EventKind.STARTED)
        second = bus.publish("w1", EventKind.STATUS)
        bus.publish("w1", EventKind.COMPLETED)
        kinds = [e.kind for e in bus.since(cursor=second)]
        assert kinds == [EventKind.COMPLETED]

    def test_filters_by_agent(self, bus):
        bus.publish("w1", EventKind.STARTED)
        bus.publish("w2", EventKind.STARTED)
        bus.publish("w1", EventKind.COMPLETED)
        assert [e.agent for e in bus.since(agent="w1")] == ["w1", "w1"]

    def test_orders_by_id_not_timestamp(self, bus, tmp_path):
        """Timestamps tie at second resolution; ids never do."""
        for _ in range(5):
            bus.publish("w1", EventKind.STATUS)
        # Force every row to share one timestamp, the tie the id protects against.
        bus._conn.execute("UPDATE agent_events SET ts = '2026-01-01T00:00:00+00:00'")
        bus._conn.commit()
        ids = [e.id for e in bus.since()]
        assert ids == sorted(ids)

    def test_respects_the_limit(self, bus):
        for _ in range(10):
            bus.publish("w1", EventKind.STATUS)
        assert len(bus.since(limit=3)) == 3

    def test_corrupt_payload_is_surfaced_not_dropped(self, bus):
        """A bad write stays visible to whoever is reading the stream."""
        bus._conn.execute(
            "INSERT INTO agent_events (ts, agent, kind, payload_json) VALUES (?,?,?,?)",
            ("2026-01-01T00:00:00+00:00", "w1", EventKind.STATUS, "{not json"),
        )
        bus._conn.commit()
        assert bus.since()[0].payload == {"_raw": "{not json"}


class TestLatestId:
    def test_zero_on_an_empty_bus(self, bus):
        assert bus.latest_id() == 0

    def test_tracks_the_head(self, bus):
        bus.publish("w1", EventKind.STARTED)
        last = bus.publish("w1", EventKind.STATUS)
        assert bus.latest_id() == last


class TestTail:
    def test_yields_backlog_then_stops_at_timeout(self, bus):
        bus.publish("w1", EventKind.STARTED)
        bus.publish("w1", EventKind.STATUS)
        assert len(list(bus.tail(timeout=0.5))) == 2

    def test_stops_on_a_terminal_event(self, bus):
        bus.publish("w1", EventKind.STARTED)
        bus.publish("w1", EventKind.COMPLETED)
        bus.publish("w1", EventKind.STATUS)   # after the end, must not be yielded
        kinds = [e.kind for e in bus.tail(agent="w1", stop_on_terminal=True, timeout=2)]
        assert kinds == [EventKind.STARTED, EventKind.COMPLETED]

    def test_picks_up_events_published_after_it_started(self, bus, tmp_path):
        """The live case: a reader blocked on an agent that has not spoken yet."""
        def publish_soon():
            time.sleep(0.3)
            writer = EventBus(db_path=tmp_path / "sessions.db")
            writer.publish("w1", EventKind.COMPLETED, {"result": "done"})
            writer.close()

        thread = threading.Thread(target=publish_soon)
        thread.start()
        try:
            events = list(bus.tail(agent="w1", stop_on_terminal=True, timeout=10))
        finally:
            thread.join()
        assert [e.kind for e in events] == [EventKind.COMPLETED]
        assert events[0].payload == {"result": "done"}


class TestWaitFor:
    def test_returns_none_on_timeout(self, bus):
        assert bus.wait_for("w1", timeout=0.4) is None

    def test_returns_the_matching_event(self, bus):
        bus.publish("w1", EventKind.STARTED)
        bus.publish("w1", EventKind.COMPLETED, {"result": "ok"})
        event = bus.wait_for("w1", timeout=2)
        assert event is not None
        assert event.kind == EventKind.COMPLETED

    def test_ignores_other_agents(self, bus):
        bus.publish("w2", EventKind.COMPLETED)
        assert bus.wait_for("w1", timeout=0.4) is None

    def test_a_cursor_from_before_the_spawn_catches_a_fast_agent(self, bus):
        """The race the `cursor` argument exists for.

        A worker can finish before its parent starts waiting. Capturing the
        head first means the wait sees the whole history and returns at once,
        instead of blocking for an event that already happened.
        """
        before = bus.latest_id()
        bus.publish("w1", EventKind.COMPLETED, {"result": "fast"})
        event = bus.wait_for("w1", cursor=before, timeout=2)
        assert event is not None and event.payload == {"result": "fast"}

    def test_accepts_a_single_kind(self, bus):
        bus.publish("w1", EventKind.STATUS, {"step": 1})
        event = bus.wait_for("w1", kinds=EventKind.STATUS, timeout=2)
        assert event is not None and event.kind == EventKind.STATUS

    def test_failed_counts_as_terminal(self, bus):
        """`wait_for` defaults to "however it ends", not "only on success"."""
        bus.publish("w1", EventKind.FAILED, {"reason": "boom"})
        event = bus.wait_for("w1", timeout=2)
        assert event is not None and event.kind == EventKind.FAILED


class TestCrossProcessDurability:
    """Two connections, as the orchestrator and a worker really are."""

    def test_a_second_bus_sees_the_first_bus_writes(self, tmp_path):
        path = tmp_path / "sessions.db"
        with EventBus(db_path=path) as writer, EventBus(db_path=path) as reader:
            writer.publish("w1", EventKind.COMPLETED, {"result": "cross"})
            events = reader.since()
        assert [e.payload for e in events] == [{"result": "cross"}]

    def test_events_survive_reopening(self, tmp_path):
        path = tmp_path / "sessions.db"
        with EventBus(db_path=path) as first:
            first.publish("w1", EventKind.STARTED)
        with EventBus(db_path=path) as second:
            assert len(second.since()) == 1


class TestEventKind:
    def test_terminal_set_is_exactly_the_end_states(self):
        assert EventKind.TERMINAL == {
            EventKind.COMPLETED, EventKind.FAILED, EventKind.LOST,
        }

    @pytest.mark.parametrize("kind", ["completed", "failed", "lost"])
    def test_terminal_kinds_report_themselves(self, kind):
        assert AgentEvent(agent="w1", kind=kind).is_terminal

    @pytest.mark.parametrize("kind", ["started", "status", "turn", "spawned"])
    def test_non_terminal_kinds_do_not(self, kind):
        assert not AgentEvent(agent="w1", kind=kind).is_terminal

    def test_an_unknown_kind_is_carried_not_rejected(self):
        """An older reader must survive a newer runtime's vocabulary."""
        assert not AgentEvent(agent="w1", kind="kind-from-the-future").is_terminal
