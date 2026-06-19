"""
Regression tests for chart history and archive functions in database.py:
  - save_chart_history / load_chart_history
  - save_chart_archive / get_rolling_60day_chart

These functions persist operator-facing chart data for the Flask dashboard.
A regression in the archive accumulation or the 60-day rolling window would
silently corrupt the chart view that operators monitor during live trading.

DB isolation: every test gets a fresh SQLite file via the isolated_db fixture,
using the same monkeypatch.setattr(db_module, "DB_FILE", ...) pattern from
test_symphony_strategy.py.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

import database as db_module
from database import (
    get_rolling_60day_chart,
    init_db,
    load_chart_history,
    save_chart_archive,
    save_chart_history,
)

# ---------------------------------------------------------------------------
# Fixture: isolated, fresh SQLite DB per test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """
    Redirect database.DB_FILE to a per-test temp file and initialise schema.

    Scope is function (default) so every test starts with an empty DB.
    Monkeypatch is restored automatically after each test.
    """
    db_path = str(tmp_path / "test_alphabot_state.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    yield db_path


# ---------------------------------------------------------------------------
# Helper: count rows in chart_archive
# ---------------------------------------------------------------------------


def _count_archive_rows(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM chart_archive")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def _date_str(offset_days: int = 0) -> str:
    """Return an ISO date string offset from a fixed base date."""
    base = date(2026, 1, 1)
    return (base + timedelta(days=offset_days)).isoformat()


# ---------------------------------------------------------------------------
# Test 1: save_chart_history -> load_chart_history round-trip
# ---------------------------------------------------------------------------


def test_save_and_load_chart_history_round_trips():
    """
    A chart dict saved with save_chart_history must come back byte-for-byte
    via load_chart_history.  The round-trip covers JSON serialisation fidelity
    for nested dicts, lists, and numeric types.

    Value assertions are on the STRUCTURE returned from save/load — no
    hardcoded producer values.
    """
    chart_data = {
        "symphony-alpha": {
            "dates": ["2026-01-01", "2026-01-02"],
            "returns": [0.01, -0.005],
            "hwm": [1.01, 1.01],
        },
        "symphony-beta": {
            "dates": ["2026-01-01"],
            "returns": [0.02],
            "hwm": [1.02],
        },
    }

    save_chart_history(chart_data)
    loaded = load_chart_history()

    assert loaded == chart_data, (
        "load_chart_history must return exactly what was passed to "
        "save_chart_history; round-trip mismatch detected"
    )


def test_save_chart_history_overwrites_previous():
    """
    A second save_chart_history call must overwrite the first — there is only
    ever one chart_history row (id=1).  Calling load_chart_history after two
    saves must return the second save's data.
    """
    first_data = {"sym1": {"dates": ["2026-01-01"], "returns": [0.01]}}
    second_data = {"sym2": {"dates": ["2026-01-02"], "returns": [0.03]}}

    save_chart_history(first_data)
    save_chart_history(second_data)
    loaded = load_chart_history()

    assert loaded == second_data, (
        "load_chart_history must return the most recent save; got data from an earlier save instead"
    )
    assert "sym1" not in loaded, "first save's data must not persist after a second save"


def test_load_chart_history_after_init_returns_empty_dict():
    """
    After init_db() and before any save, load_chart_history must return an
    empty dict — not None, not a string, not raise.
    """
    result = load_chart_history()
    assert isinstance(result, dict), (
        f"load_chart_history must return a dict on fresh DB; got {type(result)}"
    )
    assert result == {}, "load_chart_history must return {{}} on a freshly initialised DB"


# ---------------------------------------------------------------------------
# Test 2: save_chart_archive accumulates dates; upserts on duplicate
# ---------------------------------------------------------------------------


def test_save_chart_archive_accumulates_distinct_dates(isolated_db):
    """
    Saving archives for 3 distinct (date, symphony_id) pairs must produce
    3 rows in chart_archive.  Row count is verified directly via SQL so that
    a silent INSERT-ignore bug (writing 1 instead of 3) would fail this test.
    """
    symphony_id = "sym-accumulate"
    for i in range(3):
        save_chart_archive(_date_str(i), symphony_id, {"return": i * 0.01})

    assert _count_archive_rows(isolated_db) == 3, (
        "Three saves for three distinct dates must produce exactly 3 rows; "
        f"found {_count_archive_rows(isolated_db)}"
    )


def test_save_chart_archive_upserts_on_duplicate_date_symphony(isolated_db):
    """
    Saving a second archive for the same (date, symphony_id) key must UPDATE
    the existing row — not insert a duplicate.  The INSERT OR REPLACE contract
    in database.py line 140 must hold.

    Verification: row count stays at 1; the loaded data matches the second save.
    """
    date_str = _date_str(0)
    symphony_id = "sym-upsert"
    first_data = {"return": 0.01, "version": 1}
    second_data = {"return": 0.02, "version": 2}

    save_chart_archive(date_str, symphony_id, first_data)
    assert _count_archive_rows(isolated_db) == 1, "precondition: one row after first save"

    save_chart_archive(date_str, symphony_id, second_data)
    assert _count_archive_rows(isolated_db) == 1, (
        "Saving the same (date, symphony_id) a second time must UPDATE, not INSERT; "
        f"row count grew to {_count_archive_rows(isolated_db)}"
    )

    # Verify the stored value was replaced
    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT data FROM chart_archive WHERE date = ? AND symphony_id = ?",
        (date_str, symphony_id),
    )
    import json

    stored = json.loads(cursor.fetchone()[0])
    conn.close()

    assert stored == second_data, (
        "After upsert, stored data must equal the second save; "
        f"got {stored!r} instead of {second_data!r}"
    )


def test_save_chart_archive_different_symphonies_same_date_are_distinct_rows(isolated_db):
    """
    (date, symphony_id) is the composite unique key.  Two different symphonies
    on the same date must produce two independent rows.
    """
    date_str = _date_str(0)
    save_chart_archive(date_str, "sym-a", {"return": 0.01})
    save_chart_archive(date_str, "sym-b", {"return": 0.02})

    assert _count_archive_rows(isolated_db) == 2, (
        "Two saves with the same date but different symphony_ids must produce "
        f"2 rows; got {_count_archive_rows(isolated_db)}"
    )


# ---------------------------------------------------------------------------
# Test 3: get_rolling_60day_chart returns at most 60 distinct dates
# ---------------------------------------------------------------------------


def test_get_rolling_60day_chart_returns_at_most_60_dates(isolated_db):
    """
    When the archive contains 75 distinct dates, get_rolling_60day_chart must
    return data for at most 60 of them — not 75.

    get_rolling_60day_chart returns a nested dict:
        { symphony_id: { date_str: data_dict, ... }, ... }

    The 60-entry limit is on DISTINCT DATES (the LIMIT 60 in the SQL), not
    on total rows.  We use a single symphony_id so date count == row count.
    """
    symphony_id = "sym-rolling"
    for i in range(75):
        save_chart_archive(_date_str(i), symphony_id, {"day": i})

    result = get_rolling_60day_chart(_date_str(74))

    assert isinstance(result, dict), (
        f"get_rolling_60day_chart must return a dict; got {type(result)}"
    )
    assert symphony_id in result, "symphony_id must be a key in the returned dict"

    dates_returned = set(result[symphony_id].keys())
    assert len(dates_returned) <= 60, (
        f"get_rolling_60day_chart must return data for at most 60 distinct dates; "
        f"got {len(dates_returned)}"
    )


def test_get_rolling_60day_chart_returns_most_recent_dates(isolated_db):
    """
    The 60-day window must be the MOST RECENT 60 dates (ORDER BY date DESC
    LIMIT 60 in the SQL).  The oldest dates (day 0..14) must not appear in
    the result when 75 days are stored.
    """
    symphony_id = "sym-recency"
    for i in range(75):
        save_chart_archive(_date_str(i), symphony_id, {"day": i})

    result = get_rolling_60day_chart(_date_str(74))
    dates_returned = set(result.get(symphony_id, {}).keys())

    # The oldest 15 dates (i=0..14) must NOT be in the result
    for i in range(15):
        old_date = _date_str(i)
        assert old_date not in dates_returned, (
            f"Date {old_date} (rank {75 - i} from most-recent) must be excluded "
            f"from the 60-day window; it appeared in the result"
        )

    # The most recent date must be present
    most_recent = _date_str(74)
    assert most_recent in dates_returned, (
        f"Most recent date {most_recent} must be in the 60-day window result"
    )


def test_get_rolling_60day_chart_returns_empty_dict_when_no_archive(isolated_db):
    """
    When chart_archive is empty, get_rolling_60day_chart must return an empty
    dict — not None, not raise.
    """
    result = get_rolling_60day_chart(_date_str(0))
    assert result == {}, (
        f"get_rolling_60day_chart must return {{{{}}}} when archive is empty; got {result!r}"
    )


def test_get_rolling_60day_chart_multiple_symphonies(isolated_db):
    """
    With multiple symphonies across the same dates, the returned dict must
    contain an entry per symphony, each keyed by date.

    Shape assertion: every value in result must be a dict (symphony_id -> date_dict);
    every leaf must be a dict (date_str -> data_dict).
    """
    for sym in ("sym-x", "sym-y"):
        for i in range(5):
            save_chart_archive(_date_str(i), sym, {"sym": sym, "day": i})

    result = get_rolling_60day_chart(_date_str(4))

    assert "sym-x" in result, "sym-x must be a key in the rolling result"
    assert "sym-y" in result, "sym-y must be a key in the rolling result"

    for sym_id, date_map in result.items():
        assert isinstance(date_map, dict), (
            f"result[{sym_id!r}] must be a dict (date -> data); got {type(date_map)}"
        )
        for date_key, data in date_map.items():
            assert isinstance(data, dict), (
                f"result[{sym_id!r}][{date_key!r}] must be a dict; got {type(data)}"
            )
