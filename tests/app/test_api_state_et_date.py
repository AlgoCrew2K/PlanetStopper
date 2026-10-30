"""Route tests: every "today" that /api/state computes is the ET trading date.

The production droplet's clock is Etc/UTC, so from 20:00 EDT (19:00 EST) the host's
calendar date is already tomorrow's. Three reads in the live and waiting branches of
``get_state()`` took the host date: the symphony list's ``trading_day`` (which feeds
``get_symphony_today_change``), the ``get_shadow_divergence`` day, and the lower bound
of the today's-trigger query. These tests freeze ``app.datetime`` the way
``tests/app/test_held_basis_route_convergence.py`` does: a naive ``now()`` returns the
UTC wall clock, which is what the droplet's ``datetime.now()`` returns.

The daily scheduler slots keep their host-clock times: only the routes' "today" moves
to ET, so a scheduler test pins the slots' times and the absence of a time zone.

Expected dates are derived from the frozen instant and from each fixture record with
``zoneinfo``, never typed. Fixture provenance:
``tests/fixtures/refresh_timing/02-c4/PROVENANCE.md``.
"""

from __future__ import annotations

import copy
import json
import os
import sqlite3
from datetime import UTC, datetime, time
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import analytics as analytics_module
import app as app_module
import database as database_module
from tests.app.test_held_basis_route_convergence import _frozen_datetime_class

_ET = ZoneInfo("America/New_York")
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "refresh_timing" / "02-c4"


def _load_fixture(name: str):
    with open(_FIXTURE_DIR / name, encoding="utf-8") as fh:
        return json.load(fh)


_STATE_ENTRY: dict = _load_fixture("symphony_state_entry.json")
_SYMPHONY_ID: str = next(iter(_STATE_ENTRY))
_CYCLE_MARKER: str = _load_fixture("cycle_marker.json")["last_successful_cycle_at"]
_DERIVED: dict = _load_fixture("derived_records.json")

# 20:30 EDT on Thursday 2026-10-22, which is 00:30 UTC on Friday 2026-10-23.
_AFTER_UTC_ROLLOVER = datetime(2026, 10, 23, 0, 30, tzinfo=UTC)
# 10:00 EDT on 2026-10-22, the morning after a 23:30 EDT trigger.
_MORNING_AFTER = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)
# 12:00 EDT, when UTC and ET share the calendar date.
_MIDDAY_SHARED_DATE = datetime(2026, 10, 22, 16, 0, tzinfo=UTC)
# The Monday after DST ends (2026-11-01): 00:30 EST and 09:00 EST.
_DST_MONDAY_SMALL_HOURS = datetime(2026, 11, 2, 5, 30, tzinfo=UTC)
_DST_MONDAY_MORNING = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)


def _et_date(instant: datetime) -> str:
    return instant.astimezone(_ET).strftime("%Y-%m-%d")


def _utc_date(instant: datetime) -> str:
    return instant.astimezone(UTC).strftime("%Y-%m-%d")


def _record_et_date(record: dict) -> str:
    moment = datetime.strptime(record["ts_utc"], _TS_FORMAT).replace(tzinfo=UTC)
    return _et_date(moment)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _seed_live_symphony() -> None:
    """One captured symphony entry plus the captured top-level cycle marker, saved
    through the real ``database.save_state`` (no ``last_market_close_snapshot``, so a
    closed market still reaches the branch under test)."""
    state = {
        "last_successful_cycle_at": _CYCLE_MARKER,
        _SYMPHONY_ID: copy.deepcopy(_STATE_ENTRY[_SYMPHONY_ID]),
    }
    database_module.save_state(state)


def _seed_trigger(record: dict) -> None:
    conn = sqlite3.connect(os.environ["DB_PATH"])
    conn.execute(
        "INSERT INTO exit_triggers "
        "(ts_utc, ts_et, symphony_id, account_id, triggered_reason, at_return, cycle_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            record["ts_utc"],
            record["ts_et"],
            record["symphony_id"],
            record["account_id"],
            record["triggered_reason"],
            record["at_return"],
            record["cycle_id"],
        ),
    )
    conn.commit()
    conn.close()


def _seed_shadow_row(record: dict) -> None:
    conn = sqlite3.connect(os.environ["DB_PATH"])
    conn.execute(
        "INSERT INTO shadow_history "
        "(ts_utc, ts_et, trading_day, symphony_id, account_id, cycle_id, "
        " current_return, shadow_return, is_post_trigger, position_epoch) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record["ts_utc"],
            record["ts_et"],
            record["trading_day"],
            record["symphony_id"],
            record["account_id"],
            record["cycle_id"],
            record["current_return"],
            record["shadow_return"],
            record["is_post_trigger"],
            record["position_epoch"],
        ),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _freeze(monkeypatch, instant: datetime) -> None:
    monkeypatch.setattr(app_module, "datetime", _frozen_datetime_class(instant))


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(analytics_module, "DB_FILE", os.environ["DB_PATH"])
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _spy_on_today_change(monkeypatch) -> list[tuple]:
    """Pass-through spy on the analytics reader the card loops call, so the test sees
    the trading day each caller supplies while the real function still runs."""
    calls: list[tuple] = []
    real = analytics_module.get_symphony_today_change

    def spy(sym_dict, *args, **kwargs):
        calls.append((sym_dict.get("trading_day"), kwargs.get("trading_day")))
        return real(sym_dict, *args, **kwargs)

    monkeypatch.setattr(analytics_module, "get_symphony_today_change", spy)
    return calls


def _get_state(client, instant: datetime, monkeypatch) -> dict:
    _freeze(monkeypatch, instant)
    resp = client.get("/api/state")
    assert resp.status_code == 200, f"/api/state returned {resp.status_code}"
    return resp.get_json()


# ---------------------------------------------------------------------------
# The symphony list and the shadow divergence day
# ---------------------------------------------------------------------------


class TestLiveBranchUsesTheEtTradingDate:
    def test_fixture_instant_straddles_the_utc_date_boundary(self):
        assert _utc_date(_AFTER_UTC_ROLLOVER) != _et_date(_AFTER_UTC_ROLLOVER)
        assert _DERIVED["shadow_row_on_d"]["trading_day"] == _et_date(_AFTER_UTC_ROLLOVER)
        assert _DERIVED["shadow_row_on_d"]["trading_day"] == _et_date(_MIDDAY_SHARED_DATE)

    def test_cards_carry_the_et_trading_day_after_the_utc_date_rolls(self, client, monkeypatch):
        _seed_live_symphony()
        calls = _spy_on_today_change(monkeypatch)
        body = _get_state(client, _AFTER_UTC_ROLLOVER, monkeypatch)

        assert body.get("status") == "active", (
            f"the live branch must be the one exercised; got status={body.get('status')!r}"
        )
        assert calls, "the live branch must read today's change for each card"
        expected_day = _et_date(_AFTER_UTC_ROLLOVER)
        for card_day, passed_day in calls:
            assert card_day == expected_day, (
                f"a card's trading_day must be the ET trading date {expected_day}; got "
                f"{card_day} (the host's UTC date is {_utc_date(_AFTER_UTC_ROLLOVER)})"
            )
            assert passed_day == expected_day

    def test_shadow_divergence_reads_the_et_trading_day(self, client, monkeypatch):
        _seed_live_symphony()
        row = _DERIVED["shadow_row_on_d"]
        _seed_shadow_row(row)
        body = _get_state(client, _AFTER_UTC_ROLLOVER, monkeypatch)

        by_symphony = body["shadow_divergence"]["by_symphony"]
        assert _SYMPHONY_ID in by_symphony, (
            f"shadow divergence for the ET trading day {row['trading_day']} must include "
            f"the symphony that has a row on that day; got {sorted(by_symphony)}"
        )
        assert by_symphony[_SYMPHONY_ID]["today"] == pytest.approx(
            row["current_return"] - row["shadow_return"]
        )

    def test_waiting_branch_reads_the_et_trading_day(self, client, monkeypatch):
        row = _DERIVED["shadow_row_on_d"]
        _seed_shadow_row(row)
        body = _get_state(client, _AFTER_UTC_ROLLOVER, monkeypatch)

        assert body.get("status") == "waiting", (
            f"with no bot_state the waiting branch must be the one exercised; "
            f"got status={body.get('status')!r}"
        )
        by_symphony = body["shadow_divergence"]["by_symphony"]
        assert _SYMPHONY_ID in by_symphony, (
            f"the waiting branch must read shadow divergence for the ET trading day "
            f"{row['trading_day']}; got {sorted(by_symphony)}"
        )
        assert by_symphony[_SYMPHONY_ID]["today"] == pytest.approx(
            row["current_return"] - row["shadow_return"]
        )

    def test_dashboard_root_passes_the_et_trading_day_after_the_utc_date_rolls(
        self, client, monkeypatch
    ):
        _seed_live_symphony()
        calls = _spy_on_today_change(monkeypatch)
        _freeze(monkeypatch, _AFTER_UTC_ROLLOVER)

        resp = client.get("/")

        assert resp.status_code == 200
        assert calls, "the dashboard must read today's change for each card"
        expected_day = _et_date(_AFTER_UTC_ROLLOVER)
        for card_day, passed_day in calls:
            assert passed_day == expected_day
            assert card_day == expected_day


# ---------------------------------------------------------------------------
# The today's-trigger query
# ---------------------------------------------------------------------------

_TRIGGER_CASES = [
    pytest.param(
        _AFTER_UTC_ROLLOVER,
        "trigger_evening_of_d",
        True,
        id="afternoon-trigger-read-after-the-utc-date-rolls",
    ),
    pytest.param(
        _MORNING_AFTER,
        "trigger_prior_et_evening",
        False,
        id="prior-et-evening-trigger-is-not-today",
    ),
    pytest.param(
        _DST_MONDAY_SMALL_HOURS,
        "trigger_before_et_midnight_after_dst_end",
        False,
        id="sunday-evening-trigger-at-0030-est-monday",
    ),
    pytest.param(
        _DST_MONDAY_MORNING,
        "trigger_before_et_midnight_after_dst_end",
        False,
        id="sunday-evening-trigger-at-0900-est-monday",
    ),
    pytest.param(
        _DST_MONDAY_SMALL_HOURS,
        "trigger_after_et_midnight_after_dst_end",
        True,
        id="monday-small-hours-trigger-at-0030-est-monday",
    ),
    pytest.param(
        _DST_MONDAY_MORNING,
        "trigger_after_et_midnight_after_dst_end",
        True,
        id="monday-small-hours-trigger-at-0900-est-monday",
    ),
    pytest.param(
        _MIDDAY_SHARED_DATE,
        "trigger_midday_shared_date",
        True,
        id="midday-trigger-when-utc-and-et-share-the-date",
    ),
]


class TestTodaysTriggerBoundIsEtMidnight:
    @pytest.mark.parametrize(("instant", "record_name", "counted"), _TRIGGER_CASES)
    def test_trigger_is_todays_only_when_it_happened_on_the_request_et_date(
        self, client, monkeypatch, instant, record_name, counted
    ):
        record = _DERIVED[record_name]
        # Fixture integrity: the record means what the case says, by an independent
        # derivation of its ET date.
        assert (_record_et_date(record) == _et_date(instant)) is counted

        _seed_live_symphony()
        _seed_trigger(record)
        body = _get_state(client, instant, monkeypatch)

        assert body.get("status") == "active"
        last_trigger = body["state"][_SYMPHONY_ID].get("last_trigger")
        if counted:
            assert last_trigger is not None, (
                f"a trigger at {record['ts_utc']} happened on the ET date "
                f"{_et_date(instant)} and must be today's trigger"
            )
            assert last_trigger["ts_utc"] == record["ts_utc"]
            assert last_trigger["triggered_reason"] == record["triggered_reason"]
        else:
            assert last_trigger is None, (
                f"a trigger at {record['ts_utc']} (ET date {_record_et_date(record)}) "
                f"must not count as today's trigger on the ET date {_et_date(instant)}; "
                f"got {last_trigger}"
            )


class TestValuesWhenUtcAndEtShareTheDate:
    def test_shadow_divergence_and_last_trigger_match_the_records(self, client, monkeypatch):
        _seed_live_symphony()
        row = _DERIVED["shadow_row_on_d"]
        trigger = _DERIVED["trigger_midday_shared_date"]
        _seed_shadow_row(row)
        _seed_trigger(trigger)
        calls = _spy_on_today_change(monkeypatch)

        body = _get_state(client, _MIDDAY_SHARED_DATE, monkeypatch)

        assert _utc_date(_MIDDAY_SHARED_DATE) == _et_date(_MIDDAY_SHARED_DATE)
        divergence = body["shadow_divergence"]["by_symphony"][_SYMPHONY_ID]["today"]
        assert divergence == pytest.approx(row["current_return"] - row["shadow_return"])
        assert body["state"][_SYMPHONY_ID]["last_trigger"]["ts_utc"] == trigger["ts_utc"]
        assert calls
        assert {card_day for card_day, _ in calls} == {_et_date(_MIDDAY_SHARED_DATE)}


# ---------------------------------------------------------------------------
# The daily scheduler slots keep their host-clock times
# ---------------------------------------------------------------------------


class _StopScheduler(BaseException):
    """Leaves run_scheduler's endless loop. Derived from BaseException so a loop-level
    ``except Exception`` can never swallow it."""


def _registered_jobs(monkeypatch) -> list:
    """The jobs run_scheduler registers, read from a real scheduler that runs none."""
    import schedule

    class _RegisterOnly(schedule.Scheduler):
        def run_pending(self):
            raise _StopScheduler

    scheduler = _RegisterOnly()
    monkeypatch.setattr(
        app_module,
        "schedule",
        SimpleNamespace(every=scheduler.every, run_pending=scheduler.run_pending),
    )
    with pytest.raises(_StopScheduler):
        app_module.run_scheduler()
    return scheduler.jobs


_DAILY_SLOTS = [
    pytest.param("_run_trigger_retention", time(2, 0), id="trigger-retention-0200"),
    pytest.param("_run_lens_pipeline", time(3, 0), id="lens-pipeline-0300"),
    pytest.param("_run_incubation_tick", time(3, 30), id="incubation-tick-0330"),
    pytest.param(
        "_run_retirement_recommender_tick", time(3, 45), id="retirement-recommender-tick-0345"
    ),
]


class TestDailySlotsStayOnTheHostClock:
    @pytest.mark.parametrize(("function_name", "expected_time"), _DAILY_SLOTS)
    def test_daily_slot_keeps_its_time_and_carries_no_time_zone(
        self, monkeypatch, function_name, expected_time
    ):
        target = getattr(app_module, function_name)
        slots = [
            job
            for job in _registered_jobs(monkeypatch)
            if getattr(job.job_func, "func", None) is target
        ]

        assert len(slots) == 1, f"{function_name} must be registered exactly once"
        (slot,) = slots
        assert (slot.unit, slot.interval) == ("days", 1)
        assert slot.at_time == expected_time
        assert slot.at_time_zone is None, (
            f"{function_name} must run at its host-clock time, not a named time zone"
        )
