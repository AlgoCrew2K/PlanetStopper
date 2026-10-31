"""RED tests: one cycle line per engine run and per skipped minute, from the record it publishes.

``_publish_cycle(record)`` is the one choke point every engine run and every skipped minute
passes through, so the log line, the SSE event and ``/health`` read one ``CycleRecord``:

  - A finished run logs ``cycle cycle_id=<id> outcome=<outcome> started_at=<ISO> ended_at=<ISO>
    duration_s=<one decimal> exit_code=<int or ->``. ``cycle_id``, ``outcome`` and both times are
    the record's; ``exit_code`` is the record's, never recomputed. ``duration_s`` is a difference
    of the daemon's monotonic clock (``app._monotonic``), so a wall-clock step, a midnight or a
    DST crossing cannot distort it.
  - A skipped minute logs ``cycle cycle_id=<the skip's own id> outcome=skipped started_at=<skip
    time> running_cycle_id=<id> running_since=<ISO> running_s=<one decimal>``, the running age
    measured the way ``/health`` measures it. That is the one line for the skip.

The engine process is replaced at ``subprocess.run`` (its process boundary); the wall and
monotonic clocks are the daemon's two seams (``app._now_et`` and ``app._monotonic``). No producer
data is involved: the exit codes and clock readings are the test's own.
"""

from __future__ import annotations

import json
import queue
import subprocess
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import app as app_module

_ET = ZoneInfo("America/New_York")
_SATURDAY_NOON = datetime(2026, 10, 24, 12, 0, 0, tzinfo=_ET)


def _one(lines):
    assert len(lines) == 1, f"expected exactly one cycle line, got {[x.text for x in lines]!r}"
    return lines[0]


@pytest.fixture()
def run_engine(monkeypatch, tmp_path, et_clock, monotonic_clock, published_cycles):
    """Run trigger_alpha_bot once; the engine takes `monotonic_s` on the monotonic clock.

    The wall clock moves independently (`wall_s`, or jumps to `end_wall`), so a duration taken
    from wall-clock subtraction differs from one taken from the monotonic clock.
    """
    monkeypatch.setattr(app_module, "LOG_FILE", str(tmp_path / "engine.log"))

    def run(*, monotonic_s, wall_s=0.0, end_wall=None, exit_code=0):
        def engine(cmd, **_kwargs):
            monotonic_clock.advance(monotonic_s)
            et_clock.now = (
                end_wall if end_wall is not None else et_clock.now + timedelta(seconds=wall_s)
            )
            if exit_code:
                raise subprocess.CalledProcessError(exit_code, cmd)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(app_module.subprocess, "run", engine)
        app_module.trigger_alpha_bot()
        (record,) = published_cycles
        return record

    return run


class TestFinishedRunLine:
    def test_a_market_hours_run_logs_its_record_values_and_the_monotonic_duration(
        self, run_engine, timing_records
    ):
        record = run_engine(monotonic_s=38.4, wall_s=41.0)

        line = _one(timing_records.of("cycle"))
        assert line.words == ()
        assert line.fields == {
            "cycle_id": record.cycle_id,
            "outcome": "cycle",
            "started_at": record.started_at,
            "ended_at": record.completed_at,
            "duration_s": "38.4",
            "exit_code": "0",
        }
        assert record.outcome == "cycle"

    def test_a_closed_market_run_logs_the_closed_outcome(
        self, run_engine, et_clock, timing_records
    ):
        et_clock.now = _SATURDAY_NOON

        record = run_engine(monotonic_s=2.0, wall_s=2.0)

        line = _one(timing_records.of("cycle"))
        assert line.fields["outcome"] == "closed" == record.outcome
        assert line.fields["cycle_id"] == record.cycle_id

    def test_a_failed_run_logs_the_failed_outcome_and_the_exit_code(
        self, run_engine, timing_records
    ):
        record = run_engine(monotonic_s=1.5, wall_s=1.5, exit_code=3)

        line = _one(timing_records.of("cycle"))
        assert line.fields["outcome"] == "failed" == record.outcome
        assert line.fields["exit_code"] == "3" == str(record.exit_code)
        assert line.fields["duration_s"] == "1.5"

    @pytest.mark.parametrize(
        ("started", "ended"),
        [
            pytest.param(
                datetime(2026, 10, 22, 23, 59, 50, tzinfo=UTC).astimezone(_ET),
                datetime(2026, 10, 23, 0, 0, 28, tzinfo=UTC).astimezone(_ET),
                id="utc-midnight",
            ),
            pytest.param(
                datetime(2026, 10, 22, 23, 59, 50, tzinfo=_ET),
                datetime(2026, 10, 23, 0, 0, 28, tzinfo=_ET),
                id="et-midnight",
            ),
        ],
    )
    def test_a_run_that_crosses_midnight_carries_dated_offset_times_and_the_right_duration(
        self, run_engine, et_clock, timing_records, started, ended
    ):
        et_clock.now = started
        expected_s = (ended - started).total_seconds()

        run_engine(monotonic_s=expected_s, end_wall=ended)

        line = _one(timing_records.of("cycle"))
        begin = datetime.fromisoformat(line.fields["started_at"])
        end = datetime.fromisoformat(line.fields["ended_at"])
        assert begin.utcoffset() is not None and end.utcoffset() is not None, (
            "both times carry their offsets"
        )
        assert (end - begin).total_seconds() == expected_s
        assert line.fields["duration_s"] == f"{expected_s:.1f}"
        assert line.fields["started_at"][:10] == started.date().isoformat()
        assert line.fields["ended_at"][:10] == ended.date().isoformat()

    def test_a_wall_clock_step_during_the_run_does_not_change_the_duration(
        self, run_engine, timing_records
    ):
        run_engine(monotonic_s=12.5, wall_s=-3600.0)

        assert _one(timing_records.of("cycle")).fields["duration_s"] == "12.5"


class TestSkippedMinuteLine:
    def test_the_skipped_form_names_the_running_cycle_and_how_long_it_has_run(
        self, et_clock, monotonic_clock, published_cycles, timing_records
    ):
        run = app_module._begin_engine_run(et_clock.now)
        monotonic_clock.advance(75.0)
        et_clock.now += timedelta(seconds=75)

        app_module.threaded_trigger()

        (skip,) = [r for r in published_cycles if r.outcome == "skipped"]
        line = _one(timing_records.of("cycle"))
        assert line.words == ()
        assert line.fields == {
            "cycle_id": skip.cycle_id,
            "outcome": "skipped",
            "started_at": skip.started_at,
            "running_cycle_id": run.cycle_id,
            "running_since": run.started_at.isoformat(),
            "running_s": "75.0",
        }
        assert skip.cycle_id != run.cycle_id

    def test_a_skipped_minute_is_not_logged_as_a_second_line(
        self, et_clock, monotonic_clock, published_cycles, timing_records, caplog
    ):
        app_module._begin_engine_run(et_clock.now)

        with caplog.at_level("DEBUG", logger="alphabot"):
            app_module.threaded_trigger()

        _one(timing_records.of("cycle"))
        assert [r for r in caplog.records if "skipped" in r.getMessage()] == []


class TestOneLinePerRunThroughTheScheduler:
    def test_a_scheduled_run_logs_one_line_when_the_engine_thread_ends(
        self,
        stub_engine,
        et_clock,
        monotonic_clock,
        published_cycles,
        join_engine_threads,
        timing_records,
    ):
        app_module.threaded_trigger()
        assert join_engine_threads()

        (record,) = published_cycles
        line = _one(timing_records.of("cycle"))
        assert line.fields["cycle_id"] == record.cycle_id
        assert line.fields["outcome"] == record.outcome
        assert line.fields["duration_s"] == "0.0"


class TestLineAndEventAreOneRecord:
    def test_the_event_and_the_line_carry_the_same_id_outcome_and_times(
        self, monkeypatch, tmp_path, et_clock, monotonic_clock, timing_records
    ):
        clients: list[queue.SimpleQueue] = [queue.SimpleQueue()]
        monkeypatch.setattr(app_module, "_sse_clients", clients)
        monkeypatch.setattr(app_module, "_cycle_refresh_body", lambda: None)
        monkeypatch.setattr(app_module._account_totals_cache, "mark_stale", lambda: None)
        monkeypatch.setattr(app_module, "LOG_FILE", str(tmp_path / "engine.log"))

        def engine(cmd, **_kwargs):
            monotonic_clock.advance(9.0)
            et_clock.now += timedelta(seconds=9)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(app_module.subprocess, "run", engine)

        app_module.trigger_alpha_bot()

        name, data = clients[0].get(timeout=5)
        assert name == "cycle-complete"
        payload = json.loads(data)
        line = _one(timing_records.of("cycle"))
        assert payload["cycle_id"] == line.fields["cycle_id"]
        assert payload["outcome"] == line.fields["outcome"]
        assert payload["started_at"] == line.fields["started_at"]
        assert payload["completed_at"] == line.fields["ended_at"]
