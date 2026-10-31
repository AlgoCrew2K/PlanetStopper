"""``GET /api/chart/<symphony_id>`` keys its held overlay to the ET trading date and names the session.

The route read the host's local date to pick the shadow rows behind the ``held`` overlay. The host runs
on UTC, so from 20:00 ET (the next UTC day) the overlay of the day's own tape found no rows. The overlay
is now keyed to the date the tape belongs to (the engine stamps it in ``chart_history``), else to today's
ET date, and the response says which session the tape is (``session_date``) and which ET day it is
(``et_date``) so the page can caption a previous session's tape.

Worlds are real: shadow rows go in through the real writer (``_refresh_timing_state_world``) and the tape
through ``database.save_chart_history``; the clock is a UTC host's (naive ``now()`` reads UTC, ``now(tz)``
converts). Derived rows change only their day, time and cycle id:
tests/fixtures/refresh_timing/03-c4/PROVENANCE.md.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import app as app_module
import database
from tests.app._refresh_timing_frozen_world import et, retime_rows
from tests.app._refresh_timing_state_world import (
    TRIGGERED_ID,
    UNTRIGGERED_ID,
    build_world,
)

SESSION = "2026-10-21"
NEXT_DAY = "2026-10-22"
SEEDED_CLOCK = "10:43:05"
TAPE_TIME = "10:43"  # the tape's time label for a row stamped 10:43:05


def utc_host(monkeypatch, instant_utc: datetime) -> None:
    """The routes read the clock of a host on UTC: naive ``now()`` is UTC, ``now(tz)`` converts."""

    class _Host(datetime):
        @classmethod
        def now(cls, tz=None):
            return (
                instant_utc.astimezone(tz) if tz is not None else instant_utc.replace(tzinfo=None)
            )

    monkeypatch.setattr(app_module, "datetime", _Host)


def at_et(day: str, clock: str) -> datetime:
    return et(day, clock).astimezone(UTC)


# The other symphony's overlay value, set apart from the captured one so a route that reads the
# wrong symphony's rows shows it.
OTHER_SYMPHONYS_HELD = 7.77


def seed_session(world, day: str, clock: str = SEEDED_CLOCK, cycle: str = "20261021_1043") -> list:
    rows = retime_rows(world.cycle_rows("m"), day=day, clock=clock, cycle_id=cycle)
    row_of(rows, TRIGGERED_ID)["shadow_return"] = OTHER_SYMPHONYS_HELD
    world.seed_rows(rows)
    return rows


def store_tape(day: str | None, symphony_id: str, times: list[str]) -> None:
    store: dict = {
        "symphonies": {
            symphony_id: [
                {"time": t, "return": 1.5 + i, "mc_prob": 40.0} for i, t in enumerate(times)
            ]
        }
    }
    if day is not None:
        store["date"] = day
    database.save_chart_history(store)


def row_of(rows: list, symphony_id: str) -> dict:
    return next(r for r in rows if r["symphony_id"] == symphony_id)


@pytest.fixture
def world(monkeypatch, tmp_path):
    return build_world(monkeypatch, tmp_path, "closed_frozen")


def chart(world, symphony_id: str) -> dict:
    return world.poll(f"/api/chart/{symphony_id}")


class TestTheHeldOverlayIsKeyedToTheEtDate:
    def test_at_8_30_pm_eastern_on_a_utc_host_the_days_tape_carries_its_held_values(
        self, monkeypatch, world
    ):
        rows = seed_session(world, SESSION)
        store_tape(SESSION, UNTRIGGERED_ID, [TAPE_TIME])
        utc_host(monkeypatch, at_et(SESSION, "20:30:00"))

        body = chart(world, UNTRIGGERED_ID)

        points = body["data"]
        assert [p["time"] for p in points] == [TAPE_TIME]
        assert points[0].get("held") == row_of(rows, UNTRIGGERED_ID)["shadow_return"], (
            "the host's UTC date (the next day) picked the shadow rows"
        )
        assert body.get("et_date") == SESSION
        assert body.get("session_date") == SESSION

    def test_the_overlay_never_takes_another_symphonys_rows(self, monkeypatch, world):
        rows = seed_session(world, SESSION)
        store_tape(SESSION, UNTRIGGERED_ID, [TAPE_TIME])
        utc_host(monkeypatch, at_et(SESSION, "20:30:00"))

        held = chart(world, UNTRIGGERED_ID)["data"][0].get("held")

        assert held == row_of(rows, UNTRIGGERED_ID)["shadow_return"]
        assert held != OTHER_SYMPHONYS_HELD

    def test_a_previous_sessions_tape_is_overlaid_with_that_sessions_rows_and_names_both_dates(
        self, monkeypatch, world
    ):
        rows = seed_session(world, SESSION)
        store_tape(SESSION, UNTRIGGERED_ID, [TAPE_TIME])
        utc_host(monkeypatch, at_et(NEXT_DAY, "01:00:00"))

        body = chart(world, UNTRIGGERED_ID)

        assert body["data"][0].get("held") == row_of(rows, UNTRIGGERED_ID)["shadow_return"], (
            "the overlay was keyed to today's date, not the tape's session"
        )
        assert body.get("session_date") == SESSION
        assert body.get("et_date") == NEXT_DAY

    def test_the_days_first_cycle_gives_a_tape_of_the_same_date_as_the_et_day(
        self, monkeypatch, world
    ):
        rows = seed_session(world, NEXT_DAY, clock="09:30:05", cycle="20261022_0930")
        store_tape(NEXT_DAY, UNTRIGGERED_ID, ["09:30"])
        utc_host(monkeypatch, at_et(NEXT_DAY, "09:31:00"))

        body = chart(world, UNTRIGGERED_ID)

        assert body.get("session_date") == body.get("et_date") == NEXT_DAY
        assert body["data"][0].get("held") == row_of(rows, UNTRIGGERED_ID)["shadow_return"]

    def test_a_tape_that_names_no_date_is_overlaid_with_todays_et_rows_and_names_no_session(
        self, monkeypatch, world
    ):
        rows = seed_session(world, SESSION)
        store_tape(None, UNTRIGGERED_ID, [TAPE_TIME])
        utc_host(monkeypatch, at_et(SESSION, "20:30:00"))

        body = chart(world, UNTRIGGERED_ID)

        assert body["data"][0].get("held") == row_of(rows, UNTRIGGERED_ID)["shadow_return"]
        assert body.get("et_date") == SESSION
        assert not body.get("session_date"), "a session was named that the store never recorded"

    def test_with_no_stored_tape_the_tape_built_from_shadow_rows_uses_the_et_day(
        self, monkeypatch, world
    ):
        rows = seed_session(world, SESSION)
        database.save_chart_history({"date": SESSION, "symphonies": {}})
        utc_host(monkeypatch, at_et(SESSION, "20:30:00"))

        body = chart(world, UNTRIGGERED_ID)

        assert [p["time"] for p in body["data"]] == [TAPE_TIME], (
            "the tape built from shadow rows read the host's date"
        )
        assert body["data"][0].get("held") == row_of(rows, UNTRIGGERED_ID)["shadow_return"]
        assert body.get("et_date") == SESSION

    def test_a_symphony_with_no_rows_and_no_tape_still_names_the_et_day_with_empty_data(
        self, monkeypatch, world
    ):
        database.save_chart_history({"date": SESSION, "symphonies": {}})
        utc_host(monkeypatch, at_et(SESSION, "20:30:00"))

        response = world.client.get("/api/chart/not-a-symphony-id")

        assert response.status_code == 200
        body = response.get_json()
        assert body["data"] == []
        assert body.get("et_date") == SESSION

    def test_the_route_reads_no_naive_clock_for_the_date(self):
        """Every date the route computes is an ET date: a naive ``datetime.now()`` is the host's."""
        import inspect
        import re

        source = inspect.getsource(app_module.get_chart_data)
        assert not re.search(r"datetime\.now\(\s*\)", source), (
            "the chart route reads the host's local date"
        )
