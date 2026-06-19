"""
RED tests — M5: record_exit_trigger ts_et DST boundary fix.

Context: the ts_et default in record_exit_trigger is hardcoded as
`(now_utc - timedelta(hours=4))` (EDT offset) regardless of DST.  During
EST (November–March) this produces a time that is one hour AHEAD of the
correct Eastern time.

Fix: replace the hardcoded `-4h` with `datetime.now(ZoneInfo("America/New_York"))`
— mirroring the `get_current_et()` body in alpha_bot_execution.py.

Coverage (DST-1 FAILS RED until the ZoneInfo fix lands; DST-2 and DST-3
pass immediately as non-regression guards):

  DST-1: During an EST instant (UTC-5), the default ts_et must reflect the
         correct EST offset — NOT the EDT (UTC-4) approximation.
  DST-2: During an EDT instant (UTC-4, summer), the default ts_et must be
         unchanged from the old behaviour — non-regression guard.
  DST-3: When a caller supplies ts_et, the supplied value must be preserved —
         the ZoneInfo fallback must NOT overwrite a caller-provided ts_et.

Mock strategy: `database.datetime` is patched with a replacement class whose
`now()` dispatches by timezone type — returns the pinned UTC instant when
called with UTC, and returns the pinned ET-aware instant when called with a
ZoneInfo object.  strftime is left on the real datetime objects so formatting
is unaffected.

Fixture: no external fixture file; UTC/ET instants are embedded constants.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import database as db_module
from database import init_db, run_migrations

# ---------------------------------------------------------------------------
# DB isolation fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def _migrated_db(tmp_path, monkeypatch):
    """Per-test isolated SQLite DB with the full migration stack (function-scoped)."""
    db_path = str(tmp_path / "test_m5_dst.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


# ---------------------------------------------------------------------------
# Helper: extract ts_et from the single row just written for a symphony_id
# ---------------------------------------------------------------------------


def _read_ts_et(db_path: str, symphony_id: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT ts_et FROM exit_triggers WHERE symphony_id = ?",
            (symphony_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"No exit_triggers row found for symphony_id={symphony_id!r}"
    return row[0]


# ---------------------------------------------------------------------------
# Shared mock factory
# ---------------------------------------------------------------------------


def _make_datetime_mock(utc_instant: datetime, et_instant: datetime):
    """Return a mock replacement for `database.datetime`.

    .now(UTC)            → utc_instant
    .now(ZoneInfo(...))  → et_instant
    All other attributes proxy to the real datetime class so strftime etc. work.
    """
    mock = MagicMock(wraps=datetime)

    def _now(tz=None):
        if tz is UTC:
            return utc_instant
        if isinstance(tz, ZoneInfo):
            return et_instant
        return datetime.now(tz)

    mock.now = MagicMock(side_effect=_now)
    return mock


# ---------------------------------------------------------------------------
# DST-1: EST instant — stored ts_et must be UTC-5, NOT UTC-4
# ---------------------------------------------------------------------------


def test_default_ts_et_uses_est_offset_during_est(_migrated_db):
    """
    DST-1: When the system clock reads an EST instant (UTC-5), the default
    ts_et stored by record_exit_trigger must be 1 hour BEHIND the buggy
    EDT approximation.

    Chosen instant: 2026-01-15 20:00:00 UTC
      Correct EST (UTC-5):  2026-01-15T15:00:00
      Buggy EDT (UTC-4):    2026-01-15T16:00:00  ← what the old code stores

    Will be RED until the hardcoded `-timedelta(hours=4)` is replaced with
    `datetime.now(ZoneInfo('America/New_York'))` in record_exit_trigger.
    """
    utc_instant = datetime(2026, 1, 15, 20, 0, 0, tzinfo=UTC)
    # America/New_York in January is EST = UTC-5 → 15:00
    et_instant = datetime(2026, 1, 15, 15, 0, 0, tzinfo=ZoneInfo("America/New_York"))

    expected_ts_et = "2026-01-15T15:00:00"
    buggy_ts_et = "2026-01-15T16:00:00"  # old -4h approximation — must NOT appear

    mock_dt = _make_datetime_mock(utc_instant, et_instant)

    with patch.object(db_module, "datetime", mock_dt):
        db_module.record_exit_trigger(
            symphony_id="sym-dst1-est",
            triggered_reason="Trailing Stop",
            # deliberately omit ts_utc and ts_et to exercise the default path
        )

    stored = _read_ts_et(_migrated_db, "sym-dst1-est")

    assert stored != buggy_ts_et, (
        f"ts_et stored {stored!r} which matches the BUGGY EDT approximation "
        f"{buggy_ts_et!r}. The hardcoded -4h offset is wrong during EST (UTC-5). "
        "Fix: replace `(now_utc - timedelta(hours=4))` with "
        "`datetime.now(ZoneInfo('America/New_York'))` in record_exit_trigger."
    )
    assert stored == expected_ts_et, (
        f"Expected ts_et={expected_ts_et!r} (correct EST, UTC-5) for UTC instant "
        f"2026-01-15T20:00:00Z; got {stored!r}. "
        "Fix: use ZoneInfo('America/New_York') for the ts_et default."
    )


# ---------------------------------------------------------------------------
# DST-2: EDT instant — stored ts_et must still be correct (non-regression)
# ---------------------------------------------------------------------------


def test_default_ts_et_uses_edt_offset_during_edt(_migrated_db):
    """
    DST-2: During EDT (summer, UTC-4), the stored ts_et must still be correct.

    Chosen instant: 2026-07-10 19:00:00 UTC
      Correct EDT (UTC-4): 2026-07-10T15:00:00
      (This also matches what the old code produces — no regression.)

    This test ensures the ZoneInfo fix does not break summer instants.
    """
    utc_instant = datetime(2026, 7, 10, 19, 0, 0, tzinfo=UTC)
    # America/New_York in July is EDT = UTC-4 → 15:00
    et_instant = datetime(2026, 7, 10, 15, 0, 0, tzinfo=ZoneInfo("America/New_York"))

    expected_ts_et = "2026-07-10T15:00:00"

    mock_dt = _make_datetime_mock(utc_instant, et_instant)

    with patch.object(db_module, "datetime", mock_dt):
        db_module.record_exit_trigger(
            symphony_id="sym-dst2-edt",
            triggered_reason="VWAP Breakdown",
        )

    stored = _read_ts_et(_migrated_db, "sym-dst2-edt")

    assert stored == expected_ts_et, (
        f"Expected ts_et={expected_ts_et!r} (correct EDT, UTC-4) for UTC instant "
        f"2026-07-10T19:00:00Z; got {stored!r}. "
        "The ZoneInfo fix must correctly resolve EDT for summer instants."
    )


# ---------------------------------------------------------------------------
# DST-3: caller-supplied ts_et must not be overwritten
# ---------------------------------------------------------------------------


def test_caller_supplied_ts_et_is_preserved(_migrated_db):
    """
    DST-3: When a caller explicitly passes ts_et, the ZoneInfo fallback must
    NOT overwrite it.  The `ts_et = ts_et or ...` guard must remain intact.

    The caller-supplied value is deliberately unusual to ensure no coincidental
    match with a clock-derived value.
    """
    caller_ts_et = "2025-03-08T09:59:00"  # DST transition hour — deliberately unusual
    caller_ts_utc = "2025-03-08T14:59:00Z"

    db_module.record_exit_trigger(
        symphony_id="sym-dst3-supplied",
        triggered_reason="Take-Profit",
        ts_utc=caller_ts_utc,
        ts_et=caller_ts_et,
    )

    stored = _read_ts_et(_migrated_db, "sym-dst3-supplied")

    assert stored == caller_ts_et, (
        f"Caller-supplied ts_et={caller_ts_et!r} was overwritten; got {stored!r}. "
        "The `ts_et = ts_et or ...` override guard must be preserved. "
        "The ZoneInfo fallback is ONLY for when ts_et is None."
    )
