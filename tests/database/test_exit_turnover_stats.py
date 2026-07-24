"""
RED -- AC-8 (exit-friction-realized-savings): per-symphony exit-turnover
stats from exit_triggers, with honest retention-bounded coverage.

AC-8 (AMENDED 2026-07-24, recon Finding C): `exit_triggers` is pruned daily
via database.prune_old_triggers/TRIGGER_TELEMETRY_RETENTION_DAYS (default
90 days). A trailing-365-day turnover window sourced from this table cannot
structurally back a true year of history by default. Every window MUST
carry a `coverage_days` field -- the ACTUAL days of retained history behind
that count, capped at the window size -- so a 365-day bucket on a
retention-limited or young system never silently implies full-year
coverage it doesn't have.

Contract (frozen in .claude/tdd-handoff.md v1):
    database.get_exit_turnover_stats(symphony_id, *, now_utc=None) -> {
        30:  {"exit_count": int, "coverage_days": int},
        90:  {"exit_count": int, "coverage_days": int},
        365: {"exit_count": int, "coverage_days": int},
    }
    database.compute_est_annual_friction_drag_pct(turnover_stats, friction_pct) -> float

Re-entry legs are NOT counted -- exit-leg-only (documented limitation, not
tested here since it requires no special handling: exit_triggers only ever
records exit events).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import database

_SYM = "sym-turnover-test"


def _ts_utc_days_ago(now_utc: datetime, days: int) -> str:
    return (now_utc - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed(symphony_id: str, days_ago_list: list[int], now_utc: datetime) -> None:
    for days_ago in days_ago_list:
        row_id = database.record_exit_trigger(
            symphony_id=symphony_id,
            triggered_reason="Trailing Stop",
            at_return=-1.0,
            ts_utc=_ts_utc_days_ago(now_utc, days_ago),
            ts_et=_ts_utc_days_ago(now_utc, days_ago),  # value irrelevant to this query
        )
        assert row_id is not None, "fixture seeding failed -- record_exit_trigger returned None"


# ---------------------------------------------------------------------------
# Empty table
# ---------------------------------------------------------------------------


def test_empty_table_returns_zero_for_every_window():
    now_utc = datetime.now(UTC)
    result = database.get_exit_turnover_stats(_SYM, now_utc=now_utc)

    assert set(result.keys()) == {30, 90, 365}, f"Unexpected window keys: {sorted(result.keys())}"
    for window, stats in result.items():
        assert stats == {"exit_count": 0, "coverage_days": 0}, (
            f"Empty-table window {window} must be {{'exit_count': 0, "
            f"'coverage_days': 0}}, got {stats}."
        )


def test_empty_table_never_raises():
    """AC-8: never raises -- exercised implicitly by the above, restated as
    its own explicit contract test per the D-1 house pattern.
    """
    now_utc = datetime.now(UTC)
    database.get_exit_turnover_stats("symphony-with-zero-history", now_utc=now_utc)


# ---------------------------------------------------------------------------
# exit_count correctness per window
# ---------------------------------------------------------------------------


def test_exit_count_respects_window_boundaries():
    """Seed rows at known ages; each window's exit_count must equal exactly
    the number of seeded rows within that many days.
    """
    now_utc = datetime.now(UTC)
    # 3 rows within 30d, 2 more within 90d (total 5 within 90d), 1 more within
    # 365d (total 6 within 365d), 1 row OLDER than 365d (never counted).
    seed_ages = {
        "within_30": [1, 10, 29],
        "within_90_not_30": [45, 80],
        "within_365_not_90": [200],
        "beyond_365": [400],
    }
    all_ages = [a for group in seed_ages.values() for a in group]
    _seed(_SYM, all_ages, now_utc)

    result = database.get_exit_turnover_stats(_SYM, now_utc=now_utc)

    expected_30 = len(seed_ages["within_30"])
    expected_90 = expected_30 + len(seed_ages["within_90_not_30"])
    expected_365 = expected_90 + len(seed_ages["within_365_not_90"])

    assert result[30]["exit_count"] == expected_30, (
        f"30-day window exit_count expected {expected_30}, got {result[30]['exit_count']}"
    )
    assert result[90]["exit_count"] == expected_90, (
        f"90-day window exit_count expected {expected_90}, got {result[90]['exit_count']}"
    )
    assert result[365]["exit_count"] == expected_365, (
        f"365-day window exit_count expected {expected_365}, got {result[365]['exit_count']} "
        f"-- the beyond_365 row (400 days ago) must NOT be counted."
    )


def test_exit_count_scoped_to_symphony_id():
    """A different symphony's rows must not leak into this symphony's counts."""
    now_utc = datetime.now(UTC)
    _seed(_SYM, [5], now_utc)
    _seed("a-different-symphony", [5, 6, 7], now_utc)

    result = database.get_exit_turnover_stats(_SYM, now_utc=now_utc)
    assert result[30]["exit_count"] == 1, (
        f"exit_count must be scoped to symphony_id={_SYM!r} only, got "
        f"{result[30]['exit_count']} (leaked rows from a different symphony?)"
    )


# ---------------------------------------------------------------------------
# coverage_days honesty -- near-retention-limit vs mature-history extremes
# ---------------------------------------------------------------------------


def test_coverage_days_near_retention_limit_caps_at_actual_span():
    """Near-retention-limit case: the symphony's earliest exit_triggers row
    is only 60 days old (e.g. TRIGGER_TELEMETRY_RETENTION_DAYS pruning has
    already removed anything older, or the symphony is young). The 90-day
    and 365-day windows must report coverage_days == 60 (the ACTUAL span),
    never the nominal window size -- that is the entire point of AC-8's
    honesty requirement.
    """
    now_utc = datetime.now(UTC)
    EARLIEST_AGE_DAYS = 60
    _seed(_SYM, [1, 30, EARLIEST_AGE_DAYS], now_utc)

    result = database.get_exit_turnover_stats(_SYM, now_utc=now_utc)

    assert result[30]["coverage_days"] == 30, (
        "30-day window is fully covered by 60 days of history -- coverage_days "
        f"must be 30 (the window itself), got {result[30]['coverage_days']}."
    )
    assert result[90]["coverage_days"] == EARLIEST_AGE_DAYS, (
        f"90-day window requested but only {EARLIEST_AGE_DAYS} days of history "
        f"exist -- coverage_days must be capped at {EARLIEST_AGE_DAYS}, got "
        f"{result[90]['coverage_days']}. A bare 90 here would silently imply "
        f"coverage the table can't back."
    )
    assert result[365]["coverage_days"] == EARLIEST_AGE_DAYS, (
        f"365-day window requested but only {EARLIEST_AGE_DAYS} days of history "
        f"exist -- coverage_days must be capped at {EARLIEST_AGE_DAYS}, got "
        f"{result[365]['coverage_days']}. This is the exact AC-8/Finding-C "
        f"regression this test guards against."
    )


def test_coverage_days_mature_history_reaches_full_window():
    """Mature-history case: the symphony has exit_triggers rows spanning well
    beyond 365 days (e.g. retention was raised, or pruning hasn't run
    recently). Every window's coverage_days must reach its full nominal
    size -- the cap only bites when actual history is SHORTER than the
    window, never artificially truncates ample history.
    """
    now_utc = datetime.now(UTC)
    _seed(_SYM, [1, 20, 60, 100, 200, 400, 500], now_utc)

    result = database.get_exit_turnover_stats(_SYM, now_utc=now_utc)

    assert result[30]["coverage_days"] == 30
    assert result[90]["coverage_days"] == 90
    assert result[365]["coverage_days"] == 365, (
        f"With 500 days of actual history, the 365-day window must report "
        f"full coverage_days=365, got {result[365]['coverage_days']}."
    )


# ---------------------------------------------------------------------------
# compute_est_annual_friction_drag_pct
# ---------------------------------------------------------------------------


class TestEstAnnualFrictionDragPct:
    def test_extrapolates_from_retention_capped_window(self):
        """When the 365-day window's coverage_days is capped below 365 (Finding
        C), the annual-drag estimate must SCALE UP the observed exit_count to
        a true annual rate, not treat the capped count as if it already were
        a full year's worth.
        """
        friction_pct = 0.5  # matches the real SIM_EXIT_FRICTION_PCT value (0.5 pct pts)
        turnover_stats = {
            30: {"exit_count": 1, "coverage_days": 30},
            90: {"exit_count": 3, "coverage_days": 90},
            365: {"exit_count": 6, "coverage_days": 60},  # retention-capped
        }
        # exits_per_year = 6 * (365 / 60); drag = exits_per_year * 0.5
        expected_exits_per_year = 6 * (365 / 60)
        expected_drag = expected_exits_per_year * friction_pct

        result = database.compute_est_annual_friction_drag_pct(turnover_stats, friction_pct)

        assert result == pytest.approx(expected_drag, abs=1e-9), (
            f"expected {expected_drag} (extrapolated from a retention-capped "
            f"60-day window), got {result}"
        )

    def test_zero_coverage_days_returns_zero_not_a_zero_division_error(self):
        friction_pct = 0.5
        turnover_stats = {
            30: {"exit_count": 0, "coverage_days": 0},
            90: {"exit_count": 0, "coverage_days": 0},
            365: {"exit_count": 0, "coverage_days": 0},
        }
        result = database.compute_est_annual_friction_drag_pct(turnover_stats, friction_pct)
        assert result == pytest.approx(0.0, abs=1e-9), (
            f"Zero-history symphony must produce a drag estimate of 0.0, not "
            f"raise or produce a non-zero artifact. Got {result}."
        )

    def test_full_year_coverage_does_not_extrapolate(self):
        """When coverage_days == 365 exactly (mature history), exits_per_year
        must equal the raw exit_count -- no scaling needed."""
        friction_pct = 0.5
        turnover_stats = {
            30: {"exit_count": 2, "coverage_days": 30},
            90: {"exit_count": 6, "coverage_days": 90},
            365: {"exit_count": 24, "coverage_days": 365},
        }
        expected = 24 * friction_pct
        result = database.compute_est_annual_friction_drag_pct(turnover_stats, friction_pct)
        assert result == pytest.approx(expected, abs=1e-9)
