"""
Regression-pin tests for two fallback branches in math_engine.run_monte_carlo.

Scope:
    Branch A — SPY vol-window short-history (line 463 else-branch):
        Fires when len(spy_returns) < (MC_VOL_WINDOW_DAYS - 1).
        PRODUCTION GAP: This branch is structurally unreachable with the current
        constant values. MC_MIN_HISTORY_DAYS=20 means the history-guard at line
        445 fires first for any input where len(valid_dates) < 20.
        Since spy_returns is derived from the same valid_dates list,
        len(spy_returns) == len(valid_dates). For the else-branch to fire we
        would need len(valid_dates) >= 20 AND len(spy_returns) < 19, which is
        contradictory. The 'short-history' scenario below (15 days) exercises
        the EARLIER guard (MC_INSUFFICIENT_HISTORY_PROB sentinel) and pins
        the observable regression contract for the short-history input class.
        This dead-code condition is documented here so it is visible to the
        next implementer who adjusts MC_MIN_HISTORY_DAYS.

    Branch B — missing-ticker SPY substitution (line 491 else-branch):
        Fires when a holding's ticker is absent from a historical day's data.
        Returns_matrix cell is set to spy_ret instead of the ticker's own return.
        This branch IS reachable and is exercised by fixtures 03 and 04.

Fixture files referenced:
    tests/fixtures/math_engine/mc_fallbacks/01_short_history_spy_vol_fallback.json
    tests/fixtures/math_engine/mc_fallbacks/02_long_history_normal_path.json
    tests/fixtures/math_engine/mc_fallbacks/03_missing_ticker_spy_substitution.json
    tests/fixtures/math_engine/mc_fallbacks/04_present_ticker_normal_path.json

Tolerance policy:
    Tests 1 and 4 use exact equality because their expected values (100.0 and 0.0)
    are structural sentinels, not Monte Carlo outputs: 100.0 is the
    MC_INSUFFICIENT_HISTORY_PROB constant and 0.0 is a deterministic boundary
    result (all simulated paths are strictly below current_return).

    Tests 2 and 3 use pytest.approx with rel=1e-9 because they are genuine
    Monte Carlo outputs (pseudo-random draws under a fixed seed). The tolerance
    is intentionally tight: a fixed seed and deterministic numpy operations
    should reproduce bit-for-bit. If the tolerance fires it signals a
    floating-point associativity change that must be justified.
"""

from __future__ import annotations

import json
import math
import pathlib
from typing import Any

import numpy as np
import pytest

import math_engine

FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math_engine"
    / "mc_fallbacks"
)


# ---------------------------------------------------------------------------
# History builder (handles spec kinds used in this module's fixtures)
# ---------------------------------------------------------------------------

def _date_key(i: int) -> str:
    """ISO date string for synthetic day index i (lexicographic sort only)."""
    base_day = 1 + i
    month = 1 + (base_day - 1) // 28
    day = 1 + (base_day - 1) % 28
    return f"2024-{month:02d}-{day:02d}"


def _build_history(spec: dict[str, Any]) -> dict[str, dict[str, dict[str, float]]]:
    """
    Expand a fixture's historical_data_spec into the nested dict shape that
    run_monte_carlo consumes: { date: { ticker: { 'daily_ret': float } } }.

    Supported kinds:
      'alternating'         — SPY + AAA, alternating sign per day
      'spy_only'            — SPY only; no holding ticker in any day (triggers
                              missing-ticker SPY substitution for any ticker)
      'alternating_with_xyz'— SPY + XYZ, alternating sign per day
      'constant'            — SPY + AAA, constant return every day
    """
    kind = spec["kind"]
    num_days = spec["num_days"]
    history: dict[str, dict[str, dict[str, float]]] = {}

    if kind == "alternating":
        spy_amp = spec["spy_amplitude"]
        aaa_amp = spec["aaa_amplitude"]
        for i in range(num_days):
            sign = 1 if i % 2 == 0 else -1
            history[_date_key(i)] = {
                "SPY": {"daily_ret": sign * spy_amp},
                "AAA": {"daily_ret": sign * aaa_amp},
            }

    elif kind == "spy_only":
        spy_amp = spec["spy_amplitude"]
        for i in range(num_days):
            sign = 1 if i % 2 == 0 else -1
            history[_date_key(i)] = {
                "SPY": {"daily_ret": sign * spy_amp},
            }

    elif kind == "alternating_with_xyz":
        spy_amp = spec["spy_amplitude"]
        xyz_amp = spec["xyz_amplitude"]
        for i in range(num_days):
            sign = 1 if i % 2 == 0 else -1
            history[_date_key(i)] = {
                "SPY": {"daily_ret": sign * spy_amp},
                "XYZ": {"daily_ret": sign * xyz_amp},
            }

    elif kind == "constant":
        spy_ret = spec["spy_ret"]
        aaa_ret = spec["aaa_ret"]
        for i in range(num_days):
            history[_date_key(i)] = {
                "SPY": {"daily_ret": spy_ret},
                "AAA": {"daily_ret": aaa_ret},
            }

    else:
        raise ValueError(f"Unknown historical_data_spec kind: {kind!r}")

    return history


# ---------------------------------------------------------------------------
# Fixture loader
# ---------------------------------------------------------------------------

def _load_fixture(filename: str) -> dict[str, Any]:
    path = FIXTURE_DIR / filename
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _run_from_fixture(fixture: dict[str, Any]) -> float:
    """Build inputs from fixture and call run_monte_carlo under the fixed seed."""
    inputs = fixture["inputs"]
    history = _build_history(inputs["historical_data_spec"])
    holdings = inputs["holdings"]
    spy_today = inputs["spy_today_return"]
    sim_paths = inputs["simulation_paths"]
    neighbor_k = inputs["neighbor_k"]
    seed = inputs["numpy_seed"]

    return float(
        math_engine.run_monte_carlo(
            holdings,
            history,
            spy_today,
            simulation_paths=sim_paths,
            neighbor_k=neighbor_k,
            seed=seed,
        )
    )


# ---------------------------------------------------------------------------
# Test 1 — Short-history input fires MC_INSUFFICIENT_HISTORY_PROB sentinel
# ---------------------------------------------------------------------------

def test_short_history_spy_vol_fallback_returns_sentinel_and_is_finite() -> None:
    """
    15 days of SPY history is below MC_MIN_HISTORY_DAYS (20).
    run_monte_carlo must return MC_INSUFFICIENT_HISTORY_PROB (100.0) without
    raising and without NaN/Inf.

    The audit description references a 'short-history SPY vol fallback' at
    line 463; in the current implementation that branch is structurally
    unreachable when MC_MIN_HISTORY_DAYS >= MC_VOL_WINDOW_DAYS-1 (see module
    docstring). The test covers the observable regression contract for any
    input where len(valid_dates) < MC_MIN_HISTORY_DAYS: the function must
    return the sentinel and not crash.
    """
    fixture = _load_fixture("01_short_history_spy_vol_fallback.json")
    actual = _run_from_fixture(fixture)

    assert math.isfinite(actual), (
        f"run_monte_carlo returned non-finite value {actual!r} for short-history input. "
        "Expected MC_INSUFFICIENT_HISTORY_PROB sentinel (finite)."
    )
    assert 0.0 <= actual <= 100.0, (
        f"run_monte_carlo returned {actual!r} which is outside [0, 100]. "
        "MC probability outputs must be bounded."
    )
    # Exact equality: 100.0 is a constant sentinel, not a random draw.
    # Any deviation indicates the short-circuit guard changed or the sentinel
    # value was modified.
    assert actual == fixture["expected"], (
        f"Short-history sentinel mismatch: expected {fixture['expected']!r}, "
        f"got {actual!r}. "
        f"Derivation: {fixture['derivation']}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Long-history input runs the full MC path (negative control)
# ---------------------------------------------------------------------------

def test_long_history_normal_path_returns_finite_float_in_range() -> None:
    """
    50 days of SPY history is well above MC_MIN_HISTORY_DAYS (20) and
    MC_VOL_WINDOW_DAYS-1 (19). run_monte_carlo must run the full MC pipeline
    (windowed vol if-branch at line 460, argpartition or arange neighbor
    selection, Monte Carlo draw) and return a finite float in [0, 100].

    This is the negative control for test_short_history_spy_vol_fallback: the
    result must be a genuine MC probability distinct from 100.0, confirming
    the function does NOT short-circuit when history is sufficient.
    """
    fixture = _load_fixture("02_long_history_normal_path.json")
    actual = _run_from_fixture(fixture)

    assert math.isfinite(actual), (
        f"run_monte_carlo returned non-finite value {actual!r} for 50-day history."
    )
    assert 0.0 <= actual <= 100.0, (
        f"run_monte_carlo returned {actual!r} outside [0, 100]."
    )
    # Regression pin: tight relative tolerance because fixed-seed MC must be
    # deterministic to at least 9 significant figures. If this fires it means
    # floating-point operations were reordered, which must be justified.
    assert actual == pytest.approx(fixture["expected"], rel=1e-9), (
        f"Long-history MC regression pin broken: expected {fixture['expected']!r}, "
        f"got {actual!r}. "
        f"Derivation: {fixture['derivation']}"
    )


def test_long_history_result_differs_from_short_history_sentinel() -> None:
    """
    The 50-day (normal path) result must differ from the 15-day
    (MC_INSUFFICIENT_HISTORY_PROB sentinel) result.

    This assertion guards against a regression where the normal path
    accidentally returns the sentinel value or the two code paths converge
    to the same output, which would make the short-circuit invisible.
    """
    short_fixture = _load_fixture("01_short_history_spy_vol_fallback.json")
    long_fixture = _load_fixture("02_long_history_normal_path.json")

    short_result = _run_from_fixture(short_fixture)
    long_result = _run_from_fixture(long_fixture)

    assert short_result != long_result, (
        f"Short-history sentinel ({short_result!r}) equals long-history MC output "
        f"({long_result!r}). These must differ: the sentinel is the degenerate "
        f"no-data case; the MC output is a real probability. A match indicates "
        f"one of the two branches is not executing as expected."
    )


# ---------------------------------------------------------------------------
# Test 3 — Missing-ticker SPY substitution fires, result is valid
# ---------------------------------------------------------------------------

def test_missing_ticker_spy_substitution_returns_finite_float_in_range() -> None:
    """
    Holding ticker 'XYZ' is absent from all 30 historical days (SPY-only
    dataset). For every (day, ticker) pair the inner else at line 491 fires:
    returns_matrix[i, j] = spy_ret.

    run_monte_carlo must return without error, and the result must be a
    finite float in [0, 100].
    """
    fixture = _load_fixture("03_missing_ticker_spy_substitution.json")
    actual = _run_from_fixture(fixture)

    assert math.isfinite(actual), (
        f"run_monte_carlo returned non-finite value {actual!r} when holding "
        "ticker is absent from all historical days."
    )
    assert 0.0 <= actual <= 100.0, (
        f"run_monte_carlo returned {actual!r} outside [0, 100] for missing-ticker input."
    )
    # Regression pin: fixed-seed MC output must be deterministic.
    assert actual == pytest.approx(fixture["expected"], rel=1e-9), (
        f"Missing-ticker SPY-substitution regression pin broken: "
        f"expected {fixture['expected']!r}, got {actual!r}. "
        f"Derivation: {fixture['derivation']}"
    )


# ---------------------------------------------------------------------------
# Test 4 — Ticker present in all days takes normal branch (negative control)
# ---------------------------------------------------------------------------

def test_present_ticker_takes_own_returns_and_differs_from_spy_substitution() -> None:
    """
    Holding ticker 'XYZ' is present in all 30 historical days with its own
    return amplitude (0.002), which is smaller than SPY's (0.005).

    The normal branch at lines 488-489 fires for every (day, ticker) pair:
    returns_matrix[i, j] = day_data[ticker]['daily_ret'].

    The result must:
      1. Be a finite float in [0, 100].
      2. Differ from the missing-ticker fixture result (fixture 03).
         Rationale: when XYZ is absent, the substituted SPY returns (+-0.5 pct)
         span a wider range than XYZ's own returns (+-0.2 pct). With
         current_symphony_return=0.3, XYZ's narrower distribution leaves all
         simulated paths below 0.3, yielding prob=0.0 (deterministic). The
         SPY-substituted distribution straddles 0.3, yielding ~50%.
    """
    missing_fixture = _load_fixture("03_missing_ticker_spy_substitution.json")
    present_fixture = _load_fixture("04_present_ticker_normal_path.json")

    missing_result = _run_from_fixture(missing_fixture)
    present_result = _run_from_fixture(present_fixture)

    assert math.isfinite(present_result), (
        f"run_monte_carlo returned non-finite value {present_result!r} when "
        "ticker is present in all historical days."
    )
    assert 0.0 <= present_result <= 100.0, (
        f"run_monte_carlo returned {present_result!r} outside [0, 100]."
    )
    # Exact equality: present_result=0.0 is deterministic (current_return=0.3
    # is above all XYZ returns=+-0.2), not a random draw.
    assert present_result == present_fixture["expected"], (
        f"Present-ticker regression pin broken: expected {present_fixture['expected']!r}, "
        f"got {present_result!r}. "
        f"Derivation: {present_fixture['derivation']}"
    )
    assert present_result != missing_result, (
        f"Present-ticker result ({present_result!r}) equals missing-ticker result "
        f"({missing_result!r}). These must differ: SPY-substituted returns "
        "(+-0.5 pct) vs own-ticker returns (+-0.2 pct) produce different "
        "distributions relative to current_symphony_return=0.3. A match "
        "indicates the missing-ticker substitution is not changing the "
        "returns matrix as expected."
    )


# ---------------------------------------------------------------------------
# Property: all four fixtures produce finite floats in [0, 100]
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fixture_filename",
    [
        "01_short_history_spy_vol_fallback.json",
        "02_long_history_normal_path.json",
        "03_missing_ticker_spy_substitution.json",
        "04_present_ticker_normal_path.json",
    ],
)
def test_all_mc_fallback_fixtures_return_bounded_finite_probability(
    fixture_filename: str,
) -> None:
    """
    Property invariant: run_monte_carlo must return a finite float in [0, 100]
    for every fallback regime. This catches NaN/Inf propagation through the
    fallback paths and probability values that escape the valid range.

    This test parametrizes over all four mc_fallbacks fixtures so that adding
    a new fixture automatically enrolls it in the invariant check.
    """
    fixture = _load_fixture(fixture_filename)
    actual = _run_from_fixture(fixture)

    assert math.isfinite(actual), (
        f"{fixture_filename}: run_monte_carlo returned non-finite {actual!r}. "
        "Probability outputs must never be NaN or Inf."
    )
    assert 0.0 <= actual <= 100.0, (
        f"{fixture_filename}: run_monte_carlo returned {actual!r} outside [0, 100]. "
        "All probability outputs must be bounded."
    )
