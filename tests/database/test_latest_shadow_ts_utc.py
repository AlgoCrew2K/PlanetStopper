"""RED: ``database.get_latest_shadow_ts_utc(trading_day, symphony_id=None)``.

CONTRACT PINNED: a read-only accessor returning the newest stored ``ts_utc`` text (as written, e.g.
``2026-07-08T20:04:03Z``) among the ``shadow_history`` rows of ``trading_day``, across every
symphony when ``symphony_id`` is None and for that symphony alone otherwise; ``None`` when no row
matches. It never writes (a read-only connection), never raises for an unknown day, and ignores
rows of other days however late their ``ts_utc``.

Rows are the captured production rows (``tests/fixtures/prod_droplet/shadow_last_rows.json``)
written through ``database.record_shadow_observation``; expected values are recomputed from those
rows in the test, never typed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import database

ROWS = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures" / "prod_droplet" / "shadow_last_rows.json"
    ).read_text(encoding="utf-8")
)


@pytest.fixture
def seeded():
    for index, row in enumerate(ROWS):
        database.record_shadow_observation(
            symphony_id=row["symphony_id"],
            account_id=row["account_id"],
            cycle_id=row["cycle_id"] or f"fixture-{index}",
            ts_utc=row["ts_utc"],
            ts_et=row["ts_et"],
            trading_day=row["trading_day"],
            current_return=row["current_return"],
            shadow_return=row["shadow_return"],
            is_post_trigger=row["is_post_trigger"],
            trigger_id=None,
            position_epoch=row.get("position_epoch"),
        )
    return ROWS


def accessor():
    assert hasattr(database, "get_latest_shadow_ts_utc"), (
        "database.get_latest_shadow_ts_utc is missing"
    )
    return database.get_latest_shadow_ts_utc


def newest(rows, day, symphony=None):
    stamps = [
        r["ts_utc"]
        for r in rows
        if r["trading_day"] == day and symphony in (None, r["symphony_id"])
    ]
    return max(stamps) if stamps else None


def test_all_symphonies_newest_of_the_day(seeded):
    days = sorted({r["trading_day"] for r in seeded})
    for day in days:
        assert accessor()(day) == newest(seeded, day)


def test_one_symphony_newest_of_the_day(seeded):
    by_day_symphony = sorted({(r["trading_day"], r["symphony_id"]) for r in seeded})
    assert len(by_day_symphony) > 2
    for day, symphony in by_day_symphony:
        assert accessor()(day, symphony_id=symphony) == newest(seeded, day, symphony)
    assert accessor()(by_day_symphony[0][0], symphony_id=by_day_symphony[0][1]) == accessor()(
        by_day_symphony[0][0], by_day_symphony[0][1]
    )


def test_the_scoped_answer_can_be_older_than_the_unscoped_one(seeded):
    day = sorted({r["trading_day"] for r in seeded})[-1]
    in_day = {r["symphony_id"] for r in seeded if r["trading_day"] == day}
    stamps = {s: newest(seeded, day, s) for s in in_day}
    assert len(set(stamps.values())) > 1, (
        "fixture integrity: symphonies must differ in their last row"
    )
    oldest = min(stamps, key=stamps.get)
    assert accessor()(day, symphony_id=oldest) < accessor()(day)


@pytest.mark.parametrize("day", ["1999-01-01", "", "not-a-day"])
def test_an_unknown_day_is_none_not_an_error(seeded, day):
    assert accessor()(day) is None


def test_an_unknown_symphony_is_none(seeded):
    day = sorted({r["trading_day"] for r in seeded})[0]
    assert accessor()(day, symphony_id="no-such-symphony") is None


def test_an_empty_table_is_none():
    assert accessor()("2026-07-08") is None


def test_it_uses_a_read_only_connection(seeded, monkeypatch):
    def forbidden(*_a, **_k):
        raise AssertionError("get_latest_shadow_ts_utc opened a read-write connection")

    monkeypatch.setattr(database, "get_connection", forbidden)
    day = sorted({r["trading_day"] for r in seeded})[0]
    assert accessor()(day) == newest(seeded, day)
