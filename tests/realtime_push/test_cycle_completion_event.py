"""RED tests: the cycle record, the cycle-complete event and execution-path safety.

Covers what trigger_alpha_bot() publishes for each engine exit path (the five-key event
payload, the outcome from the engine's own clock windows, `changed` read from observed
state and never from the exit code), the notifier's fan-out of (event name, payload
JSON), and the mask-and-refresh sequence that stays for engine runs.

No live subprocess execution. subprocess.run is replaced by a stub engine that writes
the state each real exit path writes. Marker values come from the 2026-07-09 droplet
captures through the `captured_markers` fixture.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import analytics
import app as app_module
import database

_ET = ZoneInfo("America/New_York")
_PAYLOAD_KEYS = {"cycle_id", "started_at", "completed_at", "outcome", "changed"}
_CYCLE_ID_FORMAT = re.compile(
    r"^(?P<second>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d)#(?P<n>\d+)$"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_completed_process(returncode: int = 0) -> MagicMock:
    """Return a mock CompletedProcess-like object."""
    m = MagicMock()
    m.returncode = returncode
    return m


# ---------------------------------------------------------------------------
# AC-1: trigger_alpha_bot notifies SSE clients after subprocess exits
# ---------------------------------------------------------------------------


class TestTriggerAlphaBotPublishesEvent:
    def test_notify_cycle_complete_called_after_subprocess_exits_success(self):
        """trigger_alpha_bot() must call _notify_cycle_complete() when the
        engine subprocess exits successfully (returncode 0).

        Fails until rt-impl wires _notify_cycle_complete() into trigger_alpha_bot()
        after the subprocess.run() call.
        """
        with (
            patch("subprocess.run", return_value=_make_completed_process(0)),
            patch("builtins.open", MagicMock()),  # suppress LOG_FILE open
            patch.object(app_module, "_notify_cycle_complete") as mock_notify,
        ):
            app_module.trigger_alpha_bot()

        assert mock_notify.called, (
            "trigger_alpha_bot() must call _notify_cycle_complete() after "
            "subprocess.run() returns. rt-impl: add "
            "`_notify_cycle_complete()` immediately after the subprocess.run() call."
        )

    def test_notify_cycle_complete_called_after_subprocess_exits_failure(self):
        """trigger_alpha_bot() must call _notify_cycle_complete() even when the
        engine subprocess exits with a non-zero returncode (CalledProcessError).

        The cycle is done — clients need to poll to see the error/stale state.
        Fails until rt-impl wires the notify call outside the try/except success path.
        """
        import subprocess

        with (
            patch(
                "subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "alpha_bot_execution.py"),
            ),
            patch("builtins.open", MagicMock()),
            patch.object(app_module, "_notify_cycle_complete") as mock_notify,
        ):
            app_module.trigger_alpha_bot()  # must not raise

        assert mock_notify.called, (
            "trigger_alpha_bot() must call _notify_cycle_complete() even when the "
            "subprocess raises CalledProcessError. "
            "rt-impl: place _notify_cycle_complete() in a finally: block."
        )

    def test_notify_puts_named_event_and_payload_json_on_every_registered_queue(
        self, make_cycle_record, sse_queue, account_refresh_spy
    ):
        """_notify_cycle_complete(record) puts ("cycle-complete", <payload JSON>) on every
        queue in _sse_clients, so each SSE generator can write the event name and data.

        Rejects an implementation that keeps the bare "cycle-complete" string (the
        generator would then have no payload to write) or puts a dict instead of JSON text.
        """
        q1, q2 = sse_queue(), sse_queue()
        record = make_cycle_record(outcome="cycle", changed=True)

        app_module._notify_cycle_complete(record)

        for q in (q1, q2):
            assert not q.empty(), "a registered client queue received nothing"
            item = q.get_nowait()
            assert isinstance(item, tuple) and len(item) == 2, (
                f"queue item must be (event name, payload JSON text); got {item!r}"
            )
            name, data = item
            assert name == "cycle-complete"
            assert isinstance(data, str), f"payload must be JSON text; got {type(data).__name__}"
            assert json.loads(data) == record.event_payload()


# ---------------------------------------------------------------------------
# AC-6: _notify_cycle_complete does not block the scheduler thread
# ---------------------------------------------------------------------------


class TestNotifyCycleCompleteIsNonBlocking:
    def test_notify_does_not_raise_with_empty_client_list(self, make_cycle_record):
        """_notify_cycle_complete() must silently succeed with zero clients.

        Called from trigger_alpha_bot on every cycle — must never raise.
        Fails until rt-impl adds null-guard / try-except in _notify_cycle_complete.
        """
        lock = getattr(app_module, "_sse_clients_lock", threading.Lock())
        original_clients = list(getattr(app_module, "_sse_clients", []))

        try:
            with lock:
                app_module._sse_clients.clear()
            # Must not raise
            app_module._notify_cycle_complete(make_cycle_record())
        except Exception as exc:
            pytest.fail(
                f"_notify_cycle_complete() raised with empty _sse_clients: {exc!r}. "
                "It must be safe to call with zero registered clients."
            )
        finally:
            with lock:
                app_module._sse_clients.extend(original_clients)

    def test_notify_completes_within_short_wall_clock_budget(self, make_cycle_record):
        """_notify_cycle_complete() must return quickly — it runs on the scheduler
        thread which drives the minute-cadence engine execution.

        Wall-clock budget: 100 ms. Any blocking call inside the notifier
        (network, DB, long loop) violates AC-6.

        Fails if rt-impl accidentally puts blocking I/O inside _notify_cycle_complete.
        """
        import time

        q: queue.SimpleQueue = queue.SimpleQueue()
        lock = getattr(app_module, "_sse_clients_lock", threading.Lock())

        try:
            with lock:
                app_module._sse_clients.append(q)

            start = time.monotonic()
            app_module._notify_cycle_complete(make_cycle_record())
            elapsed_ms = (time.monotonic() - start) * 1000

            assert elapsed_ms < 100, (  # 100 ms ceiling — ample for a queue.put()
                f"_notify_cycle_complete() took {elapsed_ms:.1f} ms — exceeds 100 ms budget. "
                "It must not block the scheduler thread. "
                "rt-impl: use queue.SimpleQueue.put_nowait() — no I/O, no sleep."
            )
        finally:
            with lock:
                try:
                    app_module._sse_clients.remove(q)
                except ValueError:
                    pass

    def test_notify_does_not_raise_when_client_queue_is_full(self, make_cycle_record):
        """_notify_cycle_complete() must not raise if a client's queue is full.

        A slow/dead client may have a full bounded queue. The notifier must
        skip or swallow the exception so other clients still receive the event.

        Fails until rt-impl guards the put() with a try/except (Queue.Full).
        Note: SimpleQueue is unbounded — this test uses a bounded queue.Queue to
        explicitly probe the failure mode.
        """
        import queue as q_module

        bounded_q: q_module.Queue = q_module.Queue(maxsize=1)
        bounded_q.put("already-full")  # fill it

        lock = getattr(app_module, "_sse_clients_lock", threading.Lock())

        try:
            with lock:
                app_module._sse_clients.append(bounded_q)

            try:
                app_module._notify_cycle_complete(make_cycle_record())
            except Exception as exc:
                pytest.fail(
                    f"_notify_cycle_complete() raised when a client queue was full: {exc!r}. "
                    "It must catch queue.Full and continue to the next client."
                )
        finally:
            with lock:
                try:
                    app_module._sse_clients.remove(bounded_q)
                except ValueError:
                    pass


# ---------------------------------------------------------------------------
# Helpers for the engine-exit scenarios
# ---------------------------------------------------------------------------


def _et(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=_ET)


def _only(records: list):
    assert len(records) == 1, (
        f"trigger_alpha_bot() must hand exactly one CycleRecord to _publish_cycle(); got {len(records)}"
    )
    return records[0]


def _event_item(q) -> tuple:
    item = q.get_nowait()
    assert isinstance(item, tuple) and len(item) == 2, (
        f"queue item must be (event name, payload JSON text); got {item!r}"
    )
    return item


def _as_instant(value) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def _minute_of(stamp: str) -> datetime:
    """The scheduler spawns at :00, so the engine's own stamp (:01.xxx) belongs to that minute."""
    return datetime.fromisoformat(stamp).astimezone(_ET).replace(second=0, microsecond=0)


@pytest.fixture()
def post_mortem_dir(monkeypatch, tmp_path):
    directory = tmp_path / "post_mortems"
    directory.mkdir()
    monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(directory))
    return directory


# ---------------------------------------------------------------------------
# AC-1: the record each engine exit publishes
# ---------------------------------------------------------------------------


class TestEngineRunPublishesRecord:
    def test_data_cycle_that_advances_last_successful_cycle_at_is_changed(
        self, captured_markers, seed_bot_state, stub_engine, frozen_clock, captured_records
    ):
        started = frozen_clock(_minute_of(captured_markers.cycle_at_after))
        seed_bot_state(captured_markers.cycle_at_before, captured_markers.day_before)
        stub_engine(
            writes=lambda: seed_bot_state(
                captured_markers.cycle_at_after, captured_markers.day_before
            )
        )

        app_module.trigger_alpha_bot()

        record = _only(captured_records)
        assert record.outcome == "cycle"
        assert record.changed is True
        assert record.exit_code == 0
        assert _as_instant(record.started_at) == started
        assert _as_instant(record.completed_at) >= started

    def test_event_payload_is_exactly_the_five_contract_keys(
        self, captured_markers, seed_bot_state, stub_engine, frozen_clock, captured_records
    ):
        started = frozen_clock(_et(2026, 7, 9, 10, 14))
        seed_bot_state(captured_markers.cycle_at_before, captured_markers.day_before)
        stub_engine(
            writes=lambda: seed_bot_state(
                captured_markers.cycle_at_after, captured_markers.day_before
            )
        )

        app_module.trigger_alpha_bot()

        record = _only(captured_records)
        payload = record.event_payload()
        assert set(payload) == _PAYLOAD_KEYS, (
            "exit_code, running_cycle_id and running_since stay on the record, not in the event"
        )
        json.dumps(payload)  # raises when a value is not JSON-serialisable
        assert payload["cycle_id"] == record.cycle_id
        assert payload["outcome"] == record.outcome
        assert payload["changed"] is record.changed
        for key in ("started_at", "completed_at"):
            moment = _as_instant(payload[key])
            assert moment.utcoffset() == started.utcoffset(), f"{key} must carry the ET offset"

    def test_cycle_ids_are_unique_even_within_one_second(
        self, captured_markers, seed_bot_state, stub_engine, frozen_clock, captured_records
    ):
        started = frozen_clock(_et(2026, 7, 9, 10, 43))
        seed_bot_state(captured_markers.cycle_at_before, captured_markers.day_before)
        stub_engine()

        app_module.trigger_alpha_bot()
        app_module.trigger_alpha_bot()

        assert len(captured_records) == 2, "each call must publish one CycleRecord"
        first, second = captured_records
        assert first.cycle_id != second.cycle_id, "two runs in one second must not share an id"
        for record in (first, second):
            match = _CYCLE_ID_FORMAT.match(record.cycle_id)
            assert match, (
                f"cycle_id must read '<ISO second with offset>#<n>'; got {record.cycle_id!r}"
            )
            assert match["second"] == started.isoformat(timespec="seconds")

    def test_closed_market_run_with_nothing_written_is_not_changed(
        self, captured_markers, seed_bot_state, stub_engine, frozen_clock, captured_records
    ):
        frozen_clock(_et(2026, 7, 9, 20, 0))
        seed_bot_state(captured_markers.cycle_at_after, captured_markers.day_after)
        stub_engine()

        app_module.trigger_alpha_bot()

        record = _only(captured_records)
        assert (record.outcome, record.changed) == ("closed", False)

    def test_blackout_minute_that_writes_the_post_mortem_is_changed(
        self,
        captured_markers,
        seed_bot_state,
        stub_engine,
        frozen_clock,
        captured_records,
        post_mortem_dir,
    ):
        frozen_clock(_et(2026, 6, 24, 15, 53))
        seed_bot_state(captured_markers.cycle_at_before, captured_markers.day_before)
        stub_engine(
            writes=lambda: shutil.copyfile(
                captured_markers.post_mortem_dir / "post_mortem_2026-06-24.json",
                post_mortem_dir / "post_mortem_2026-06-24.json",
            )
        )

        app_module.trigger_alpha_bot()

        record = _only(captured_records)
        assert (record.outcome, record.changed) == ("blackout", True)

    def test_blackout_minute_that_writes_nothing_is_not_changed(
        self,
        captured_markers,
        seed_bot_state,
        stub_engine,
        frozen_clock,
        captured_records,
        post_mortem_dir,
    ):
        frozen_clock(_et(2026, 6, 24, 15, 55))
        seed_bot_state(captured_markers.cycle_at_before, captured_markers.day_before)
        shutil.copyfile(
            captured_markers.post_mortem_dir / "post_mortem_2026-06-24.json",
            post_mortem_dir / "post_mortem_2026-06-24.json",
        )
        stub_engine()

        app_module.trigger_alpha_bot()

        record = _only(captured_records)
        assert (record.outcome, record.changed) == ("blackout", False)

    @pytest.mark.parametrize("rewrite", ["mtime_only", "size_only"])
    def test_rewriting_an_existing_post_mortem_is_changed(
        self,
        rewrite,
        captured_markers,
        seed_bot_state,
        stub_engine,
        frozen_clock,
        captured_records,
        post_mortem_dir,
    ):
        """The file's (exists, mtime, size) is the observation: either part alone moves `changed`.

        Rejects an implementation that tests only for the file's existence, only its mtime,
        or only its size.
        """
        frozen_clock(_et(2026, 6, 24, 15, 54))
        seed_bot_state(captured_markers.cycle_at_before, captured_markers.day_before)
        target = post_mortem_dir / "post_mortem_2026-06-24.json"
        shutil.copyfile(captured_markers.post_mortem_dir / "post_mortem_2026-06-24.json", target)
        before = target.stat()

        def _rewrite():
            if rewrite == "mtime_only":
                os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns + 5_000_000_000))
            else:
                with target.open("ab") as handle:
                    handle.write(b" ")
                os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))

        stub_engine(writes=_rewrite)

        app_module.trigger_alpha_bot()

        assert _only(captured_records).changed is True

    def test_close_step_that_saves_the_snapshot_day_is_changed(
        self, captured_markers, seed_bot_state, stub_engine, frozen_clock, captured_records
    ):
        frozen_clock(_et(2026, 7, 9, 16, 0))
        seed_bot_state(captured_markers.cycle_at_after, captured_markers.day_before)
        stub_engine(
            writes=lambda: seed_bot_state(
                captured_markers.cycle_at_after, captured_markers.day_after
            )
        )

        app_module.trigger_alpha_bot()

        record = _only(captured_records)
        assert (record.outcome, record.changed) == ("eod", True)

    def test_close_step_already_run_is_not_changed(
        self, captured_markers, seed_bot_state, stub_engine, frozen_clock, captured_records
    ):
        frozen_clock(_et(2026, 7, 9, 16, 1))
        seed_bot_state(captured_markers.cycle_at_after, captured_markers.day_after)
        stub_engine()

        app_module.trigger_alpha_bot()

        record = _only(captured_records)
        assert (record.outcome, record.changed) == ("eod", False)

    def test_non_zero_exit_before_any_write_is_failed_and_unchanged(
        self, captured_markers, seed_bot_state, stub_engine, frozen_clock, captured_records
    ):
        frozen_clock(_et(2026, 7, 9, 10, 43))
        seed_bot_state(captured_markers.cycle_at_before, captured_markers.day_before)
        stub_engine(exit_code=1)

        app_module.trigger_alpha_bot()

        record = _only(captured_records)
        assert (record.outcome, record.changed, record.exit_code) == ("failed", False, 1)

    def test_non_zero_exit_after_the_close_writes_is_failed_yet_changed(
        self, captured_markers, seed_bot_state, stub_engine, frozen_clock, captured_records
    ):
        """The close step saves the snapshot and the cycle stamp, then reporting raises:
        `changed` follows the observed state, never the exit code."""
        frozen_clock(_et(2026, 7, 9, 16, 0))
        seed_bot_state(captured_markers.cycle_at_before, captured_markers.day_before)
        stub_engine(
            exit_code=1,
            writes=lambda: seed_bot_state(
                captured_markers.cycle_at_after, captured_markers.day_after
            ),
        )

        app_module.trigger_alpha_bot()

        record = _only(captured_records)
        assert (record.outcome, record.changed, record.exit_code) == ("failed", True, 1)

    @pytest.mark.parametrize("advanced", ["last_successful_cycle_at", "snapshot_trading_day"])
    def test_either_marker_alone_marks_the_run_changed(
        self,
        advanced,
        captured_markers,
        seed_bot_state,
        stub_engine,
        frozen_clock,
        captured_records,
    ):
        frozen_clock(_et(2026, 7, 9, 16, 0))
        seed_bot_state(captured_markers.cycle_at_before, captured_markers.day_before)
        after_cycle_at = (
            captured_markers.cycle_at_after
            if advanced == "last_successful_cycle_at"
            else captured_markers.cycle_at_before
        )
        after_day = (
            captured_markers.day_after
            if advanced == "snapshot_trading_day"
            else captured_markers.day_before
        )
        stub_engine(writes=lambda: seed_bot_state(after_cycle_at, after_day))

        app_module.trigger_alpha_bot()

        assert _only(captured_records).changed is True

    def test_spawn_failure_publishes_a_failed_record_with_no_exit_code(
        self, captured_markers, seed_bot_state, stub_engine, frozen_clock, captured_records
    ):
        frozen_clock(_et(2026, 7, 9, 10, 43))
        seed_bot_state(captured_markers.cycle_at_before, captured_markers.day_before)
        stub_engine(spawn_error=FileNotFoundError("alpha_bot_execution.py"))

        try:
            app_module.trigger_alpha_bot()
        except OSError:
            pass  # whether the spawn error propagates is the implementer's call; the record is the contract

        record = _only(captured_records)
        assert (record.outcome, record.exit_code) == ("failed", None)

    @pytest.mark.parametrize("failing_read", ["before", "after", "both"])
    @pytest.mark.parametrize("failure", ["none_pair", "raises"])
    def test_an_unreadable_marker_state_is_reported_as_changed(
        self,
        failing_read,
        failure,
        captured_markers,
        seed_bot_state,
        stub_engine,
        frozen_clock,
        captured_records,
        monkeypatch,
    ):
        """When the state cannot be observed the client refetches: unknown is never "unchanged"."""
        frozen_clock(_et(2026, 7, 9, 20, 0))
        seed_bot_state(captured_markers.cycle_at_after, captured_markers.day_after)
        stub = stub_engine()
        real_read = database.read_cycle_markers
        reads: list[int] = []

        def _flaky_read():
            reads.append(1)
            call_number = len(reads)
            fails = failing_read == "both" or (failing_read == "before") == (call_number == 1)
            if not fails or call_number > 2:
                return real_read()
            if failure == "raises":
                raise RuntimeError("bot_state unreadable")
            return (None, None)

        monkeypatch.setattr(database, "read_cycle_markers", _flaky_read)

        app_module.trigger_alpha_bot()

        assert len(stub.calls) == 1, (
            "an unreadable marker must never stop the engine from being spawned"
        )
        record = _only(captured_records)
        assert record.outcome == "closed"
        assert record.changed is True

    def test_supplied_cycle_id_and_started_at_are_used_and_set_the_outcome(
        self, captured_markers, seed_bot_state, stub_engine, frozen_clock, captured_records
    ):
        """The outcome is classified at the spawn time, not at completion: a run spawned at
        15:52:30 that ends at 15:53:40 is a data cycle, not a blackout minute."""
        frozen_clock(_et(2026, 7, 9, 15, 53, 40))
        seed_bot_state(captured_markers.cycle_at_before, captured_markers.day_before)
        stub_engine()
        spawned = _et(2026, 7, 9, 15, 52, 30)

        app_module.trigger_alpha_bot(cycle_id="2026-07-09T15:52:30-04:00#1", started_at=spawned)

        record = _only(captured_records)
        assert record.cycle_id == "2026-07-09T15:52:30-04:00#1"
        assert _as_instant(record.started_at) == spawned
        assert record.outcome == "cycle"

    def test_each_call_spawns_exactly_one_engine(
        self, captured_markers, seed_bot_state, stub_engine, frozen_clock, captured_records
    ):
        frozen_clock(_et(2026, 7, 9, 10, 43))
        seed_bot_state(captured_markers.cycle_at_before, captured_markers.day_before)
        stub = stub_engine(exit_code=1)

        app_module.trigger_alpha_bot()

        assert len(stub.calls) == 1, "a failed run must not be retried inside the same call"

    def test_publishing_never_rewrites_bot_state(
        self, captured_markers, seed_bot_state, stub_engine, frozen_clock, captured_records
    ):
        frozen_clock(_et(2026, 7, 9, 10, 43))
        seed_bot_state(captured_markers.cycle_at_before, captured_markers.day_before)
        stub_engine()

        def _blob() -> str:
            conn = database.get_ro_connection()
            try:
                return conn.execute("SELECT data FROM bot_state WHERE id = 1").fetchone()[0]
            finally:
                conn.close()

        before = _blob()
        app_module.trigger_alpha_bot()
        assert _blob() == before, "the daemon only reads the markers; the engine owns bot_state"


class TestCycleIdMinting:
    def test_ids_in_one_second_are_distinct_and_share_the_second(self):
        instant = _et(2026, 7, 9, 10, 43, 7)
        ids = [app_module._mint_cycle_id(instant) for _ in range(5)]

        assert len(set(ids)) == 5
        for cycle_id in ids:
            match = _CYCLE_ID_FORMAT.match(cycle_id)
            assert match, f"cycle_id must read '<ISO second with offset>#<n>'; got {cycle_id!r}"
            assert match["second"] == "2026-07-09T10:43:07-04:00"

    def test_a_different_second_gives_a_different_prefix(self):
        a = app_module._mint_cycle_id(_et(2026, 7, 9, 10, 43, 7))
        b = app_module._mint_cycle_id(_et(2026, 7, 9, 10, 44, 7))
        assert a.split("#")[0] != b.split("#")[0]

    def test_winter_offset_is_the_et_offset(self):
        cycle_id = app_module._mint_cycle_id(_et(2026, 1, 12, 10, 43, 0))
        assert cycle_id.startswith("2026-01-12T10:43:00-05:00#")


# ---------------------------------------------------------------------------
# AC-1: the notifier's fan-out and the mask-and-refresh sequence
# ---------------------------------------------------------------------------


class _RecordingThread:
    """Stands in for threading.Thread: records the name, never runs the target."""

    created: list = []

    def __init__(self, target=None, name=None, daemon=None, **_kw):
        type(self).created.append(name)

    def start(self):
        pass


@pytest.fixture()
def recorded_threads():
    _RecordingThread.created = []
    with patch.object(app_module.threading, "Thread", _RecordingThread):
        yield _RecordingThread.created


class TestNotifyFanOut:
    def test_three_clients_each_receive_exactly_one_copy(
        self, make_cycle_record, sse_queue, account_refresh_spy
    ):
        queues = [sse_queue(), sse_queue(), sse_queue()]
        record = make_cycle_record()

        app_module._notify_cycle_complete(record)

        for q in queues:
            assert q.get_nowait() == ("cycle-complete", json.dumps(record.event_payload()))
            assert q.empty(), "a client must receive one copy of one event"

    def test_a_closed_clients_queue_does_not_stop_the_others(
        self, make_cycle_record, sse_queue, account_refresh_spy
    ):
        class _ClosedQueue:
            def put_nowait(self, _item):
                raise RuntimeError("client went away")

        closed = _ClosedQueue()
        with app_module._sse_clients_lock:
            app_module._sse_clients.append(closed)
        live = sse_queue()
        try:
            app_module._notify_cycle_complete(make_cycle_record())
        finally:
            with app_module._sse_clients_lock:
                app_module._sse_clients.remove(closed)

        assert not live.empty(), "the client behind the failing one must still get the event"

    @pytest.mark.parametrize("outcome", ["cycle", "closed", "blackout", "eod", "failed"])
    def test_an_engine_run_masks_the_totals_and_starts_one_refresh(
        self, outcome, make_cycle_record, recorded_threads
    ):
        app_module._account_totals_cache["portfolio_value"] = 4321.0

        app_module._notify_cycle_complete(make_cycle_record(outcome=outcome))

        assert app_module._account_totals_cache.get("portfolio_value") is None, (
            "an engine run still masks the cached totals until the refresh lands"
        )
        assert recorded_threads == ["cycle-refresh"]

    def test_a_skipped_minute_masks_nothing_and_starts_no_refresh(
        self, make_cycle_record, recorded_threads
    ):
        """No engine ran, so no account state changed: the totals stay readable."""
        app_module._account_totals_cache["portfolio_value"] = 4321.0
        skipped = make_cycle_record(
            outcome="skipped",
            changed=False,
            running_cycle_id="2026-07-09T10:43:00-04:00#1",
            running_since="2026-07-09T10:43:00-04:00",
        )

        app_module._notify_cycle_complete(skipped)

        assert app_module._account_totals_cache.get("portfolio_value") == 4321.0
        assert recorded_threads == []

    def test_a_skipped_minute_still_reaches_the_clients(
        self, make_cycle_record, sse_queue, recorded_threads
    ):
        q = sse_queue()

        app_module._notify_cycle_complete(make_cycle_record(outcome="skipped", changed=False))

        name, data = _event_item(q)
        assert name == "cycle-complete"
        assert json.loads(data)["outcome"] == "skipped"


class TestPublishCycle:
    @pytest.mark.parametrize("outcome", ["cycle", "skipped"])
    def test_publish_hands_the_same_record_to_the_notifier(self, outcome, make_cycle_record):
        record = make_cycle_record(outcome=outcome, changed=outcome != "skipped")
        with patch.object(app_module, "_notify_cycle_complete") as notify:
            app_module._publish_cycle(record)

        assert notify.call_count == 1
        assert notify.call_args.args[0] is record

    def test_publish_with_no_clients_does_not_raise(self, make_cycle_record, recorded_threads):
        with app_module._sse_clients_lock:
            held = list(app_module._sse_clients)
            app_module._sse_clients.clear()
        try:
            app_module._publish_cycle(make_cycle_record())
        finally:
            with app_module._sse_clients_lock:
                app_module._sse_clients.extend(held)


class TestEventReachesClientsPromptly:
    def test_event_is_queued_within_a_second_of_the_engine_exit_even_if_the_refresh_hangs(
        self,
        captured_markers,
        seed_bot_state,
        stub_engine,
        frozen_clock,
        sse_queue,
        account_refresh_spy,
    ):
        """The fan-out must not wait on the account refresh (a Composer call that can hang)."""
        frozen_clock(_et(2026, 7, 9, 10, 14))
        seed_bot_state(captured_markers.cycle_at_before, captured_markers.day_before)
        stub = stub_engine(
            writes=lambda: seed_bot_state(
                captured_markers.cycle_at_after, captured_markers.day_before
            )
        )
        q = sse_queue()
        account_refresh_spy.gate.clear()  # the refresh now blocks like a hung Composer call

        app_module.trigger_alpha_bot()
        elapsed = time.monotonic() - stub.returned_at_mono

        assert elapsed < 1.0, f"event took {elapsed:.2f}s after the engine exited; budget is 1 s"
        name, data = _event_item(q)
        payload = json.loads(data)
        assert name == "cycle-complete"
        assert set(payload) == _PAYLOAD_KEYS
        assert payload["outcome"] == "cycle"
        assert payload["changed"] is True


class TestReadCycleMarkers:
    def test_returns_the_cycle_stamp_then_the_snapshot_day(self, captured_markers, seed_bot_state):
        seed_bot_state(captured_markers.cycle_at_before, captured_markers.day_before)

        assert database.read_cycle_markers() == (
            captured_markers.cycle_at_before,
            captured_markers.day_before,
        )

    def test_absent_markers_read_as_none(self):
        assert database.read_cycle_markers() == (None, None)

    def test_a_missing_snapshot_does_not_hide_the_cycle_stamp(
        self, captured_markers, seed_bot_state
    ):
        seed_bot_state(captured_markers.cycle_at_after, None)

        assert database.read_cycle_markers() == (captured_markers.cycle_at_after, None)

    def test_reads_two_fields_without_parsing_the_whole_state(
        self, captured_markers, seed_bot_state, monkeypatch
    ):
        """bot_state is about 2 MB in production: the targeted read must not go through load_state."""
        seed_bot_state(captured_markers.cycle_at_before, captured_markers.day_before)

        def _forbidden():
            raise AssertionError("read_cycle_markers must not parse the whole bot_state blob")

        monkeypatch.setattr(database, "load_state", _forbidden)

        assert database.read_cycle_markers()[0] == captured_markers.cycle_at_before

    def test_any_database_error_reads_as_none_pair(self, monkeypatch):
        def _broken():
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(database, "get_ro_connection", _broken)

        assert database.read_cycle_markers() == (None, None)
