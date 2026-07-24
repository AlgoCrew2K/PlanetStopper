"""
RED tests -- analytics.get_shadow_current_return_daily_series (guard-alpha-
preconditions AC-5, amended per PM ruling on recon finding 2 + the EOD-only
addendum + the PM's LIVE-GATE R1 correction, 2026-07-24).

R1 LIVE-GATE FINDING (supersedes the epoch-scoping half of the original
AC-5 amendment -- read this before TestAllEpochsConcatenated below): a
read-only probe of the production droplet DB found position_epoch is a UUID
that churns every 1-2 trading days (14-18 distinct epochs per symphony over
23 retained trading days). Scoping this accessor to the CURRENT epoch only
(as originally specified, mirroring the epoch-additive $-saved semantic)
capped every symphony's shadow n_obs at 1-2 -- permanently INSUFFICIENT_DATA
in production, even though 23 real trading days of data existed. The
epoch-additive rule is load-bearing for the CUMULATIVE $-saved computation
(get_symphony_cumulative_return, where chaining a prior epoch's absolute
level into a new position would fabricate phantom returns) but does NOT
apply to PER-DAY current_return values: each EOD current_return is that
day's own if-held return (production-proven, see
project_shadow_return_per_day_proven_empirically.md) regardless of which
trigger-cycle epoch it happened to fall in -- concatenating per-day values
ACROSS epochs is statistically valid, unlike chaining a cumulative level.
THE FIX: drop the epoch filter entirely. Keep EOD-per-day selection (one
row per trading_day, still via MAX(ts_utc)) and no-differencing (still raw
per-day values) -- those two contracts are unaffected and still pinned
below.

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
# R1 (PM live-gate correction, 2026-07-24): ALL epochs concatenated, NOT
# scoped to current epoch. This class REPLACES the pre-live-gate
# TestCurrentEpochScoping, which pinned the opposite (now-superseded)
# behavior -- see this module's docstring for the full rationale.
# ---------------------------------------------------------------------------


class TestAllEpochsConcatenated:
    def test_production_shaped_churning_epochs_all_contribute(self, tmp_path: Path):
        """PRODUCTION-SHAPED fixture (PM's explicit live-gate request):
        UUID-like epoch labels churning every 1-2 trading days (matching the
        14-18-epochs-per-23-days production pattern), minute-cadence rows
        per day (several intraday snapshots, not just one), and a weekend
        gap in the trading-day sequence. Asserts n_obs == the number of
        DISTINCT trading days, regardless of how many epoch boundaries fall
        within that span -- on the droplet's real data this yields ~23; this
        fixture uses a smaller but structurally identical 7-trading-day
        span (5 weekdays, a weekend gap, then 2 more weekdays) crossing 5
        distinct epochs."""
        db_file = str(tmp_path / "shadow.db")
        conn = sqlite3.connect(db_file)
        conn.execute(_SHADOW_SCHEMA_WITH_EPOCH)

        # 7 trading days (Mon-Fri, weekend gap, Mon-Tue) -- EOD current_return
        # per day (the value that must survive into the result).
        days_and_eod = [
            ("2026-07-13", -1.20),  # Mon -- epoch_1
            ("2026-07-14", 0.85),  # Tue -- epoch_1 (churns after 2 days)
            ("2026-07-15", 2.40),  # Wed -- epoch_2 (churns after 1 day)
            ("2026-07-16", -3.10),  # Thu -- epoch_3
            ("2026-07-17", 0.55),  # Fri -- epoch_3 (churns after 2 days)
            # weekend gap: 2026-07-18/19 have no trading_day rows at all
            ("2026-07-20", 1.75),  # Mon -- epoch_4 (churns after 1 day)
            ("2026-07-21", -0.40),  # Tue -- epoch_5
        ]
        epoch_by_day = {
            "2026-07-13": "3f9a2b10-3a4b-4c1d-9e2f-1a2b3c4d5e01",
            "2026-07-14": "3f9a2b10-3a4b-4c1d-9e2f-1a2b3c4d5e01",
            "2026-07-15": "7c1e6d20-8b3c-4f2a-a1d4-9b8c7d6e5f02",
            "2026-07-16": "a4d8f931-1c2d-4e3f-b5a6-8c7d6e5f4a03",
            "2026-07-17": "a4d8f931-1c2d-4e3f-b5a6-8c7d6e5f4a03",
            "2026-07-20": "d2e5c847-9a1b-4c2d-8e3f-6a5b4c3d2e04",
            "2026-07-21": "18b3a962-4d5e-4f1a-9c2b-3d4e5f6a7b05",
        }
        # Minute-cadence intraday rows per day: several snapshots culminating
        # in the true EOD row (last by ts_utc) -- proves EOD-only selection
        # (existing contract) survives dense intraday rows even under heavy
        # epoch churn.
        intraday_offsets_min = [31, 90, 180, 270, 385]  # 09:31, 10:30, ... EOD ~15:55

        for day, eod_value in days_and_eod:
            epoch = epoch_by_day[day]
            for offset in intraday_offsets_min:
                hh = 9 + offset // 60
                mm = offset % 60
                is_eod_row = offset == intraday_offsets_min[-1]
                # Only the LAST (EOD) row carries the "real" value; earlier
                # intraday rows carry an obviously-wrong sentinel so a bug
                # that picks a non-EOD row is loudly caught, not silently
                # coincidentally correct.
                value = eod_value if is_eod_row else -999.0
                _insert_row(
                    conn,
                    ts_utc=f"{day}T{hh:02d}:{mm:02d}:00Z",
                    trading_day=day,
                    current_return=value,
                    shadow_return=value,
                    position_epoch=epoch,
                )
        conn.commit()
        conn.close()

        result = analytics.get_shadow_current_return_daily_series(_SYM_ID, db_file)

        expected = [eod for _day, eod in days_and_eod]
        distinct_trading_days = len({d for d, _ in days_and_eod})

        assert result is not None
        assert len(result) == distinct_trading_days == 7, (
            f"Expected n_obs == 7 (the distinct trading days spanned, "
            f"regardless of 5 epoch boundaries crossing them), got "
            f"len(result)={len(result)!r}. A result capped at 1-2 means the "
            "epoch filter is still active -- exactly the live-gate bug "
            f"(every symphony permanently INSUFFICIENT_DATA in production)."
        )
        assert result == pytest.approx(expected), (
            f"Expected the 7 real EOD values {expected!r} in trading-day "
            f"order, got {result!r} -- either wrong values leaked through "
            "(a non-EOD -999.0 sentinel) or the day ordering is wrong."
        )

    def test_legacy_schema_without_epoch_column_still_works(self, tmp_path: Path):
        """A pre-migration table with no position_epoch column at all must
        behave identically to the epoch-column case now that the epoch
        filter is dropped entirely -- there is no longer any branching on
        column presence to test separately."""
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
            f"Legacy (no position_epoch column) DB must still return all "
            f"trading days -- got {result!r}."
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
