"""
RED tests -- analytics.get_shadow_current_return_daily_series (guard-alpha-
preconditions AC-5, amended per PM ruling on recon finding 2 + the EOD-only
addendum).

THE TRAP THIS FILE PINS (recon finding 2): the feature plan originally read
"daily diffs of shadow_history.current_return". Project memory
(project_shadow_return_per_day_proven_empirically.md), proven against 29k
live production rows, established that shadow_history.current_return is
ALREADY a per-day return ("today's change"), NOT cumulative-since-open --
production evidence: EOD current_return oscillates and flips sign day to day
and resets to a fresh small value at every day's open.
analytics.get_symphony_cumulative_return (analytics.py:816-843) treats it the
same way: prod_current = PROD(1 + current_return_day/100), compounding
per-day values. If this accessor differenced consecutive current_return rows
(np.diff-style), it would difference an ALREADY-per-day series -- a "return of
a return" -- corrupting both rho and sharpe_daily. The tests below assert the
RAW per-day values pass through UNCHANGED.

THE ADDENDUM (PM ruling): shadow_history can carry multiple rows per trading
day (the engine records observations throughout the session). The accessor
must select EXACTLY the EOD row (last by ts_utc) per day -- an intraday row
would silently change the sampling frequency and invalidate the rho-vs-SR
comparison (same failure class as AC-2, on the data side).

Schema/seeding pattern mirrors tests/analytics/test_shadow_trajectory_position_epoch.py
(the established convention for this DB shape).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import analytics
import database as _db

_SHADOW_SCHEMA_WITH_EPOCH = """
    CREATE TABLE shadow_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT NOT NULL,
        ts_et TEXT,
        trading_day TEXT NOT NULL,
        symphony_id TEXT NOT NULL,
        account_id TEXT,
        cycle_id TEXT,
        current_return REAL NOT NULL,
        shadow_return REAL NOT NULL,
        is_post_trigger INTEGER NOT NULL DEFAULT 0,
        trigger_id INTEGER,
        position_epoch TEXT
    )
"""

_SHADOW_SCHEMA_LEGACY_NO_EPOCH = """
    CREATE TABLE shadow_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT NOT NULL,
        ts_et TEXT,
        trading_day TEXT NOT NULL,
        symphony_id TEXT NOT NULL,
        account_id TEXT,
        cycle_id TEXT,
        current_return REAL NOT NULL,
        shadow_return REAL NOT NULL,
        is_post_trigger INTEGER NOT NULL DEFAULT 0,
        trigger_id INTEGER
    )
"""

_SYM_ID = "sym-shadow-precond-001"


@pytest.fixture(autouse=True)
def clear_shadow_cr_cache():
    """Mirrors test_shadow_trajectory_position_epoch.py's autouse cache-clear
    fixture -- if the implementer reuses database._shadow_cr_cache (or adds a
    sibling cache), stale entries must never bleed across tests."""
    _db._shadow_cr_cache.clear()
    yield
    _db._shadow_cr_cache.clear()


def _insert_row(
    conn,
    *,
    ts_utc,
    trading_day,
    current_return,
    shadow_return,
    position_epoch,
    with_epoch_column=True,
):
    if with_epoch_column:
        conn.execute(
            "INSERT INTO shadow_history (ts_utc, trading_day, symphony_id, "
            "current_return, shadow_return, position_epoch) VALUES (?, ?, ?, ?, ?, ?)",
            (ts_utc, trading_day, _SYM_ID, current_return, shadow_return, position_epoch),
        )
    else:
        conn.execute(
            "INSERT INTO shadow_history (ts_utc, trading_day, symphony_id, "
            "current_return, shadow_return) VALUES (?, ?, ?, ?, ?)",
            (ts_utc, trading_day, _SYM_ID, current_return, shadow_return),
        )


# ---------------------------------------------------------------------------
# Raw per-day pass-through, NOT diffed (recon finding 2)
# ---------------------------------------------------------------------------


class TestRawPerDayPassThroughNotDiffed:
    def test_oscillating_production_pattern_returned_unchanged(self, tmp_path: Path):
        """Values chosen to mirror the exact empirical production pattern
        from the memory (iaSOOUsmnCJHiZvbrWfs: +1.68, +2.17, -5.50, +2.26,
        +1.0) -- a genuinely cumulative-since-open series could never swing
        -5.50 -> +2.26 repeatedly; a per-day series does this routinely."""
        db_file = str(tmp_path / "shadow.db")
        conn = sqlite3.connect(db_file)
        conn.execute(_SHADOW_SCHEMA_WITH_EPOCH)
        epoch = "2026-05-18T09:31:00Z"
        oscillating = [1.68, 2.17, -5.50, 2.26, 1.0]
        for i, cr in enumerate(oscillating):
            day = f"2026-05-{18 + i:02d}"
            _insert_row(
                conn,
                ts_utc=f"{day}T19:00:00Z",
                trading_day=day,
                current_return=cr,
                shadow_return=cr,
                position_epoch=epoch,
            )
        conn.commit()
        conn.close()

        result = analytics.get_shadow_current_return_daily_series(_SYM_ID, db_file)

        assert result == pytest.approx(oscillating), (
            f"Expected the raw per-day current_return values unchanged, got "
            f"{result!r}. If this instead looks like a diff of {oscillating!r} "
            "(e.g. [0.49, -7.67, 7.76, -1.26]), the implementation is WRONGLY "
            "differencing an already-per-day series -- see this module's "
            "docstring (recon finding 2)."
        )

    def test_not_diffed_general_case(self, tmp_path: Path):
        db_file = str(tmp_path / "shadow.db")
        conn = sqlite3.connect(db_file)
        conn.execute(_SHADOW_SCHEMA_WITH_EPOCH)
        epoch = "2026-06-01T09:31:00Z"
        values = [3.0, -1.5, 0.75, 2.2, -0.4, 1.1]
        for i, cr in enumerate(values):
            day = f"2026-06-{1 + i:02d}"
            _insert_row(
                conn,
                ts_utc=f"{day}T19:00:00Z",
                trading_day=day,
                current_return=cr,
                shadow_return=cr,
                position_epoch=epoch,
            )
        conn.commit()
        conn.close()

        result = analytics.get_shadow_current_return_daily_series(_SYM_ID, db_file)

        # The WRONG (diffed) computation, for a clear negative-signal message.
        wrong_diffed = [values[i] - values[i - 1] for i in range(1, len(values))]

        assert result == pytest.approx(values), (
            f"result={result!r} does not match raw values={values!r}. "
            f"If it instead matches diffed={wrong_diffed!r}, the accessor is "
            "differencing an already-per-day series."
        )


# ---------------------------------------------------------------------------
# Field selection: reads current_return, never shadow_return
# ---------------------------------------------------------------------------


class TestFieldSelectionCurrentReturnNeverShadowReturn:
    def test_reads_current_return_column_not_shadow_return(self, tmp_path: Path):
        """Deliberately DIFFERENT current_return vs shadow_return per row --
        AC-5 explicitly forbids reading shadow_return (the frozen exit value)
        for this feature's live/secondary sample."""
        db_file = str(tmp_path / "shadow.db")
        conn = sqlite3.connect(db_file)
        conn.execute(_SHADOW_SCHEMA_WITH_EPOCH)
        epoch = "2026-04-01T09:31:00Z"
        current_returns = [2.0, -3.0, 1.5]
        shadow_returns = [-99.0, -99.0, -99.0]  # deliberately wrong sentinel
        for i, (cr, sr) in enumerate(zip(current_returns, shadow_returns)):
            day = f"2026-04-{1 + i:02d}"
            _insert_row(
                conn,
                ts_utc=f"{day}T19:00:00Z",
                trading_day=day,
                current_return=cr,
                shadow_return=sr,
                position_epoch=epoch,
            )
        conn.commit()
        conn.close()

        result = analytics.get_shadow_current_return_daily_series(_SYM_ID, db_file)

        assert result == pytest.approx(current_returns), (
            f"Expected current_return values {current_returns!r}, got {result!r}. "
            f"If this matches shadow_returns={shadow_returns!r} instead, the "
            "accessor is reading the WRONG column (shadow_return is the frozen "
            "exit value, never the live if-held series)."
        )


# ---------------------------------------------------------------------------
# EOD-only selection (PM addendum): multiple intraday rows per day
# ---------------------------------------------------------------------------


class TestEodOnlySelection:
    def test_only_last_row_per_day_by_ts_utc_contributes(self, tmp_path: Path):
        """Three intraday snapshots on ONE trading day, plus a second day
        with a single EOD row. Using any non-EOD intraday value (or
        averaging/summing all of them) would produce a different, wrong
        series -- this is the discriminating assertion."""
        db_file = str(tmp_path / "shadow.db")
        conn = sqlite3.connect(db_file)
        conn.execute(_SHADOW_SCHEMA_WITH_EPOCH)
        epoch = "2026-07-01T09:31:00Z"
        day1 = "2026-07-01"
        # Three intraday rows for day1: 09:35 (early, wrong if picked),
        # 12:00 (midday, wrong if picked), 19:00 (true EOD).
        _insert_row(
            conn,
            ts_utc=f"{day1}T09:35:00Z",
            trading_day=day1,
            current_return=0.10,
            shadow_return=0.10,
            position_epoch=epoch,
        )
        _insert_row(
            conn,
            ts_utc=f"{day1}T12:00:00Z",
            trading_day=day1,
            current_return=1.50,
            shadow_return=1.50,
            position_epoch=epoch,
        )
        _insert_row(
            conn,
            ts_utc=f"{day1}T19:00:00Z",
            trading_day=day1,
            current_return=2.75,
            shadow_return=2.75,
            position_epoch=epoch,
        )
        day2 = "2026-07-02"
        _insert_row(
            conn,
            ts_utc=f"{day2}T19:00:00Z",
            trading_day=day2,
            current_return=-1.20,
            shadow_return=-1.20,
            position_epoch=epoch,
        )
        conn.commit()
        conn.close()

        result = analytics.get_shadow_current_return_daily_series(_SYM_ID, db_file)

        assert result == pytest.approx([2.75, -1.20]), (
            f"Expected [2.75 (the 19:00 EOD row), -1.20], got {result!r}. "
            "A result of length 4 (all rows) or containing 0.10/1.50 means "
            "intraday rows leaked into the series, silently changing the "
            "sampling frequency (PM addendum)."
        )


# ---------------------------------------------------------------------------
# Epoch scoping: current epoch only, mirrors _get_shadow_cumulative_trajectory
# ---------------------------------------------------------------------------


class TestCurrentEpochScoping:
    def test_only_latest_epoch_rows_are_returned(self, tmp_path: Path):
        db_file = str(tmp_path / "shadow.db")
        conn = sqlite3.connect(db_file)
        conn.execute(_SHADOW_SCHEMA_WITH_EPOCH)
        epoch_a = "2026-01-05T14:30:00Z"  # older position
        epoch_b = "2026-03-02T14:30:00Z"  # current position
        older_values = [1.0, -2.0, 0.5]
        current_values = [-0.5, 0.8, -1.2, 2.0]
        for i, cr in enumerate(older_values):
            day = f"2026-01-{5 + i:02d}"
            _insert_row(
                conn,
                ts_utc=f"{day}T19:00:00Z",
                trading_day=day,
                current_return=cr,
                shadow_return=cr,
                position_epoch=epoch_a,
            )
        for i, cr in enumerate(current_values):
            day = f"2026-03-{2 + i:02d}"
            _insert_row(
                conn,
                ts_utc=f"{day}T19:00:00Z",
                trading_day=day,
                current_return=cr,
                shadow_return=cr,
                position_epoch=epoch_b,
            )
        conn.commit()
        conn.close()

        result = analytics.get_shadow_current_return_daily_series(_SYM_ID, db_file)

        assert result == pytest.approx(current_values), (
            f"Expected only the CURRENT epoch's values {current_values!r}, got "
            f"{result!r}. A result including any of {older_values!r} means an "
            "older position's returns leaked across the epoch boundary "
            "(never stitch across epochs, AC-5)."
        )

    def test_legacy_schema_without_epoch_column_degrades_to_single_segment(self, tmp_path: Path):
        """Mirrors _get_shadow_cumulative_trajectory's tolerance of a
        pre-migration table with no position_epoch column at all."""
        db_file = str(tmp_path / "shadow_legacy.db")
        conn = sqlite3.connect(db_file)
        conn.execute(_SHADOW_SCHEMA_LEGACY_NO_EPOCH)
        values = [0.5, -0.3, 1.2]
        for i, cr in enumerate(values):
            day = f"2026-02-{1 + i:02d}"
            _insert_row(
                conn,
                ts_utc=f"{day}T19:00:00Z",
                trading_day=day,
                current_return=cr,
                shadow_return=cr,
                position_epoch=None,
                with_epoch_column=False,
            )
        conn.commit()
        conn.close()

        result = analytics.get_shadow_current_return_daily_series(_SYM_ID, db_file)

        assert result == pytest.approx(values), (
            f"Legacy (no position_epoch column) DB must degrade to a single "
            f"implicit segment, not raise or return None -- got {result!r}."
        )


# ---------------------------------------------------------------------------
# Absent-symphony honesty
# ---------------------------------------------------------------------------


class TestAbsentSymphony:
    def test_symphony_with_zero_rows_returns_none(self, tmp_path: Path):
        db_file = str(tmp_path / "shadow_empty.db")
        conn = sqlite3.connect(db_file)
        conn.execute(_SHADOW_SCHEMA_WITH_EPOCH)
        conn.commit()
        conn.close()

        result = analytics.get_shadow_current_return_daily_series("nonexistent-sym", db_file)

        assert result is None, (
            "A symphony absent from shadow_history entirely must return None "
            "(distinguishable from 'present but short', which N_MIN_OBS "
            f"gating at the stats layer handles) -- got {result!r}."
        )

    def test_symphony_with_one_day_returns_short_list_not_none(self, tmp_path: Path):
        """Distinguishes 'genuinely absent' (None) from 'present but thin'
        (a real, short list) -- insufficiency is compute_persistence_stats's
        job via N_MIN_OBS, not this accessor's."""
        db_file = str(tmp_path / "shadow_one_day.db")
        conn = sqlite3.connect(db_file)
        conn.execute(_SHADOW_SCHEMA_WITH_EPOCH)
        epoch = "2026-08-01T09:31:00Z"
        _insert_row(
            conn,
            ts_utc="2026-08-01T19:00:00Z",
            trading_day="2026-08-01",
            current_return=0.7,
            shadow_return=0.7,
            position_epoch=epoch,
        )
        conn.commit()
        conn.close()

        result = analytics.get_shadow_current_return_daily_series(_SYM_ID, db_file)

        assert result == pytest.approx([0.7]), (
            f"A symphony present with exactly one recorded day must return a "
            f"real 1-element list, not None -- got {result!r}."
        )
