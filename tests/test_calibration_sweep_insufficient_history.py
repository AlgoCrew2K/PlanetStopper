"""
RED tests for AC-4: symphonies with insufficient history (<125 trading days)
are skipped cleanly with a logged warning, no crash.

No live API calls. No hardcoded producer values — threshold derived from fixture.
"""

import json
import logging
import pathlib

import pytest

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent / "fixtures" / "math" / "calibration_sweep_study_fixture.json"
)

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fixture():
    return json.loads(_FIXTURE_PATH.read_text())


# ---------------------------------------------------------------------------
# Helpers to build minimal history_data dicts
# ---------------------------------------------------------------------------


def _make_tick() -> dict:
    """Build a minimal valid tick dict matching synthetic_history.fetch_bars shape.

    run_calibration_sweep passes these to _collect_sim_returns, which calls
    ticks[-1]["return"] (autotuner.py:1402) so the tick must be a dict. Other
    keys consumed by _replay_exit_tick are included to avoid KeyError.
    """
    return {
        "time": "09:30",
        "return": 0.0,
        "mc_prob": None,
        "vol": 0.01,
        "vwap_diff": 0.0,
        "base_atr_pct": 0.01,
        "valid_vwap_weight": 1.0,
    }


def _make_history_data(n_days: int, sym_id: str = "SYM-TEST") -> dict:
    """Build a minimal history_data dict with n_days of fake daily tick lists.

    Each date maps to one minimal tick dict. The tick content is inert (zero
    return) -- we only need valid structure for the objective to run without
    crashing on a KeyError. AC-4 skip gate fires before the objective for short
    symphonies. For AC-4.3/4.4 the objective runs but we only assert row presence.
    """
    all_dates = []
    year, month, day = 2025, 1, 1
    for _ in range(n_days):
        all_dates.append(f"{year}-{str(month).zfill(2)}-{str(day).zfill(2)}")
        day += 1
        if day > 28:  # simplified: 28-day months to avoid date arithmetic
            day = 1
            month += 1
            if month > 12:
                month = 1
                year += 1
    return {sym_id: {d: [_make_tick()] for d in all_dates}}


# ---------------------------------------------------------------------------
# AC-4.1 — short-history symphony produces no report rows
# ---------------------------------------------------------------------------


def test_symphony_below_history_threshold_produces_no_report_rows(fixture):
    """A symphony with <125 trading days must be skipped; result is an empty list.

    Confirms the sweep does not crash and does not return a row for the
    skipped symphony.

    Fails RED until run_calibration_sweep (or an equivalent expanded W2 path)
    adds the skip gate.
    """
    import autotuner

    threshold = fixture["min_history_days"]
    short_days = fixture["short_history_symphony"]["SYM-SHORT"]["history_days"]
    assert short_days < threshold, (
        f"Fixture error: short_days={short_days} is not below threshold={threshold}"
    )

    history_data = _make_history_data(short_days, "SYM-SHORT")
    current_params = {
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BREAK_CONFIRM_TICKS": 3,
        "VWAP_BLEED_ARM_MIN": -3.0,
        "VWAP_BLEED_ARM_MAX": -0.5,
    }

    result = autotuner.run_calibration_sweep(
        history_data=history_data,
        current_params=current_params,
        current_date_str="2025-12-31",
        deviation_dict={},
        random_state=42,
    )

    assert result == [], (
        f"Expected empty list for a symphony with {short_days} days (below "
        f"{threshold}-day threshold), got {len(result)} rows."
    )


# ---------------------------------------------------------------------------
# AC-4.2 — skip is logged as a warning (not silently dropped)
# ---------------------------------------------------------------------------


def test_symphony_below_history_threshold_emits_warning(fixture, caplog):
    """A skipped symphony must produce a log warning (not silent omission).

    The operator needs to know WHY a symphony is absent from the report.
    Silently dropping it with no message is a hidden failure mode.

    Fails RED until the skip gate logs at WARNING level.
    """
    import autotuner

    threshold = fixture["min_history_days"]
    short_days = fixture["short_history_symphony"]["SYM-SHORT"]["history_days"]
    history_data = _make_history_data(short_days, "SYM-SHORT")
    current_params = {
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BREAK_CONFIRM_TICKS": 3,
        "VWAP_BLEED_ARM_MIN": -3.0,
        "VWAP_BLEED_ARM_MAX": -0.5,
    }

    with caplog.at_level(logging.WARNING):
        autotuner.run_calibration_sweep(
            history_data=history_data,
            current_params=current_params,
            current_date_str="2025-12-31",
            deviation_dict={},
            random_state=42,
        )

    warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "SYM-SHORT" in m or "insufficient" in m.lower() or "skip" in m.lower()
        for m in warning_messages
    ), (
        f"Expected a WARNING mentioning the skipped symphony or 'insufficient' / "
        f"'skip'. Got warning messages: {warning_messages}"
    )


# ---------------------------------------------------------------------------
# AC-4.3 — symphony AT or ABOVE threshold is NOT skipped
# ---------------------------------------------------------------------------


def test_symphony_at_history_threshold_is_not_skipped(fixture):
    """A symphony with exactly the minimum days must NOT be skipped.

    The skip gate is strictly <125, not <=125.
    Fails RED until the sweep exists; then green if gate is correct.
    """
    import autotuner

    threshold = fixture["min_history_days"]
    history_data = _make_history_data(threshold, "SYM-AT-THRESHOLD")
    current_params = {
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BREAK_CONFIRM_TICKS": 3,
        "VWAP_BLEED_ARM_MIN": -3.0,
        "VWAP_BLEED_ARM_MAX": -0.5,
    }

    result = autotuner.run_calibration_sweep(
        history_data=history_data,
        current_params=current_params,
        current_date_str="2025-12-31",
        deviation_dict={},
        random_state=42,
    )

    # At-threshold symphony must produce at least one report row
    sym_rows = [r for r in result if r.get("symphony_id") == "SYM-AT-THRESHOLD"]
    assert sym_rows, (
        f"Symphony with exactly {threshold} days must NOT be skipped, but "
        f"produced no rows. Check that the skip gate is strict < {threshold}."
    )


# ---------------------------------------------------------------------------
# AC-4.4 — mixed history: short skipped, adequate proceeds
# ---------------------------------------------------------------------------


def test_mixed_history_short_skipped_adequate_proceeds(fixture):
    """Short symphony is skipped; adequate symphony proceeds in the same call.

    The skip must be per-symphony — other symphonies in the same call are
    unaffected.
    Fails RED until the skip gate is implemented.
    """
    import autotuner

    threshold = fixture["min_history_days"]
    short_days = fixture["short_history_symphony"]["SYM-SHORT"]["history_days"]

    history_data = {}
    history_data.update(_make_history_data(short_days, "SYM-SHORT"))
    history_data.update(_make_history_data(threshold, "SYM-ADEQUATE"))

    current_params = {
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BREAK_CONFIRM_TICKS": 3,
        "VWAP_BLEED_ARM_MIN": -3.0,
        "VWAP_BLEED_ARM_MAX": -0.5,
    }

    result = autotuner.run_calibration_sweep(
        history_data=history_data,
        current_params=current_params,
        current_date_str="2025-12-31",
        deviation_dict={},
        random_state=42,
    )

    short_rows = [r for r in result if r.get("symphony_id") == "SYM-SHORT"]
    adequate_rows = [r for r in result if r.get("symphony_id") == "SYM-ADEQUATE"]

    assert not short_rows, (
        f"SYM-SHORT ({short_days} days) should have been skipped but produced rows"
    )
    assert adequate_rows, (
        f"SYM-ADEQUATE ({threshold} days) should have produced rows but was skipped"
    )
