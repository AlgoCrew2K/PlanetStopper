"""The one predicate that decides a state entry is no longer held.

A state entry whose newest `shadow_history` row is older than the two most recent completed
sessions that hold rows for other symphonies is "no longer held". Every page asks this one function
(`analytics.no_longer_held_symphony_ids`); it takes the state it is asked about, the database (a path
or an open connection, which is how the closed-market branch passes its committed cut) and an
optional `now` (an aware ET instant) that decides whether the session of today is complete.

Records: the committed droplet captures with one named derived edit, see
`tests/fixtures/no_longer_held/PROVENANCE.md`. Expected sets are derived from which rows a case keeps,
never copied from a run.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import analytics
from tests.ui.test_static_js_page_harness import captured_json, seed_shadow_rows

ET = ZoneInfo("America/New_York")
STALE_ID = "INfCn3eKsu6i4oTTqdUp"  # real id; the PM's droplet check of 2026-10-31 names it
NEWEST_CAPTURED_DAY = "2026-07-09"
SECOND_NEWEST_CAPTURED_DAY = "2026-07-08"
THIRD_NEWEST_CAPTURED_DAY = "2026-07-07"


@pytest.fixture(autouse=True)
def _shadow_db_seam(_isolate_db, monkeypatch):
    monkeypatch.setattr(analytics, "DB_FILE", os.environ["DB_PATH"])


def _state() -> dict:
    return captured_json("bot_state_positions.json")


def _rows_keeping(stale_days: set[str]) -> list[dict]:
    """The captured rows, with the stale symphony's rows cut to `stale_days`."""
    return [
        r
        for r in captured_json("shadow_last_rows.json")
        if r["symphony_id"] != STALE_ID or r["trading_day"] in stale_days
    ]


def _days_before(day: str) -> set[str]:
    return {
        r["trading_day"] for r in captured_json("shadow_last_rows.json") if r["trading_day"] <= day
    }


def _ask(state: dict, **kwargs) -> frozenset[str]:
    return frozenset(
        analytics.no_longer_held_symphony_ids(state, db_path=os.environ["DB_PATH"], **kwargs)
    )


AFTER_CLOSE = datetime(2026, 7, 10, 18, 0, tzinfo=ET)


def test_an_entry_with_no_row_in_the_two_latest_sessions_is_excluded():
    seed_shadow_rows(os.environ["DB_PATH"], _rows_keeping(_days_before("2026-06-30")))
    assert _ask(_state(), now=AFTER_CLOSE) == {STALE_ID}


def test_an_entry_missing_only_the_latest_session_is_still_held():
    """One missed session is not enough: its newest row equals the second most recent session."""
    seed_shadow_rows(os.environ["DB_PATH"], _rows_keeping(_days_before(SECOND_NEWEST_CAPTURED_DAY)))
    assert _ask(_state(), now=AFTER_CLOSE) == frozenset()


def test_an_entry_missing_the_two_latest_sessions_is_excluded():
    """The boundary: one session older than the second most recent."""
    seed_shadow_rows(os.environ["DB_PATH"], _rows_keeping(_days_before(THIRD_NEWEST_CAPTURED_DAY)))
    assert _ask(_state(), now=AFTER_CLOSE) == {STALE_ID}


def test_rows_that_resume_bring_the_entry_back_without_any_other_change():
    old_only = _rows_keeping(_days_before("2026-06-30"))
    seed_shadow_rows(os.environ["DB_PATH"], old_only)
    assert _ask(_state(), now=AFTER_CLOSE) == {STALE_ID}
    resumed = [
        r
        for r in captured_json("shadow_last_rows.json")
        if r["symphony_id"] == STALE_ID and r["trading_day"] == NEWEST_CAPTURED_DAY
    ]
    assert resumed, "the capture holds the symphony's newest-session row"
    seed_shadow_rows(os.environ["DB_PATH"], resumed)
    assert _ask(_state(), now=AFTER_CLOSE) == frozenset(), "the entry must return on its own"


def test_a_new_entry_with_no_rows_at_all_is_not_excluded():
    seed_shadow_rows(os.environ["DB_PATH"], captured_json("shadow_last_rows.json"))
    state = _state()
    new_id = "newlyAddedSymphony01"
    state[new_id] = {**state[STALE_ID], "name": "Newly Added Symphony"}
    assert _ask(state, now=AFTER_CLOSE) == frozenset(), (
        "a symphony with no rows yet is new, not gone"
    )


def test_with_fewer_than_two_completed_sessions_nothing_is_excluded():
    one_session = [
        r
        for r in captured_json("shadow_last_rows.json")
        if r["trading_day"] == NEWEST_CAPTURED_DAY and r["symphony_id"] != STALE_ID
    ]
    seed_shadow_rows(os.environ["DB_PATH"], one_session)
    assert _ask(_state(), now=AFTER_CLOSE) == frozenset()


def test_with_no_rows_anywhere_nothing_is_excluded():
    assert _ask(_state(), now=AFTER_CLOSE) == frozenset()


def test_the_session_in_progress_does_not_count_as_completed():
    """Rows dated today, before the close, do not move the cut: the symphony that missed only
    yesterday must not be excluded because today's partial rows made it look two sessions behind."""
    rows = _rows_keeping(_days_before(SECOND_NEWEST_CAPTURED_DAY))
    today_rows = [
        {
            **r,
            "trading_day": "2026-07-10",
            "ts_utc": "2026-07-10T15:00:03Z",
            "ts_et": "2026-07-10T11:00:01",
        }
        for r in captured_json("shadow_last_rows.json")
        if r["trading_day"] == NEWEST_CAPTURED_DAY and r["symphony_id"] != STALE_ID
    ]
    seed_shadow_rows(os.environ["DB_PATH"], rows + today_rows)
    midday = datetime(2026, 7, 10, 11, 0, tzinfo=ET)
    assert _ask(_state(), now=midday) == frozenset(), (
        "today's rows before the close were counted as a completed session"
    )
    assert _ask(_state(), now=AFTER_CLOSE) == {STALE_ID}, (
        "after the close today is a completed session, and the entry is then two sessions behind"
    )


def test_state_keys_that_are_not_symphonies_are_never_returned():
    seed_shadow_rows(os.environ["DB_PATH"], _rows_keeping(_days_before("2026-06-30")))
    state = _state()
    state["last_successful_cycle_at"] = "2026-07-09T16:00:03-04:00"
    state["last_execution_mode"] = False
    state["date"] = "2026-07-09"
    assert _ask(state, now=AFTER_CLOSE) == {STALE_ID}


def test_an_open_connection_is_read_instead_of_the_path_for_the_closed_market_cut(tmp_path):
    """The closed-market branch hands over a connection that holds only the committed rows."""
    seed_shadow_rows(os.environ["DB_PATH"], _rows_keeping(_days_before("2026-06-30")))
    cut_path = tmp_path / "cut.db"
    full = sqlite3.connect(os.environ["DB_PATH"])
    cut = sqlite3.connect(str(cut_path))
    try:
        schema = full.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'shadow_history'"
        ).fetchone()[0]
        cut.execute(schema)
        for row in full.execute(
            "SELECT * FROM shadow_history WHERE trading_day <= ?", ("2026-06-30",)
        ):
            cut.execute(f"INSERT INTO shadow_history VALUES ({','.join('?' * len(row))})", row)
        cut.commit()
        # The cut ends on 2026-06-30 for everyone, so nobody is behind it.
        assert (
            frozenset(analytics.no_longer_held_symphony_ids(_state(), conn=cut, now=AFTER_CLOSE))
            == frozenset()
        )
        # The full database, asked through its own connection, says the entry is gone.
        assert frozenset(
            analytics.no_longer_held_symphony_ids(_state(), conn=full, now=AFTER_CLOSE)
        ) == {STALE_ID}
    finally:
        full.close()
        cut.close()
