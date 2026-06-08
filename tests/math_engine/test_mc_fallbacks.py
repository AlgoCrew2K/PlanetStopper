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
        the EARLIER guard (the MC_INSUFFICIENT_HISTORY_SENTINEL out-of-band
        signal) and pins the observable regression contract for the
        short-history input class. This dead-code condition is documented here
        so it is visible to the next implementer who adjusts MC_MIN_HISTORY_DAYS.

    Branch B — missing-ticker SPY substitution (line 491 else-branch):
        Fires when a holding's ticker is absent from a historical day's data.
        Returns_matrix cell is set to spy_ret instead of the ticker's own return.
        This branch IS reachable and is exercised by fixtures 03 and 04.

Fixture files referenced:
    tests/fixtures/math_engine/mc_fallbacks/01_short_history_spy_vol_fallback.json
    tests/fixtures/math_engine/mc_fallbacks/02_long_history_normal_path.json
    tests/fixtures/math_engine/mc_fallbacks/03_missing_ticker_spy_substitution.json
    tests/fixtures/math_engine/mc_fallbacks/04_present_ticker_normal_path.json

Cluster 2 update (mc-knn-exit-gate, AC-1/AC-2):
    Test 1's contract changed. run_monte_carlo no longer returns the in-band
    100.0 for insufficient history — it returns the distinct out-of-band
    sentinel MC_INSUFFICIENT_HISTORY_SENTINEL (None). The prior 100.0 was
    fail-dangerous: 100 >= MC_BREAKDOWN_THRESHOLD permanently vetoed the protective
    trailing stop. Tests 2 and 3's pinned values were re-captured from the fixed
    producer (AC-1 standardizes the kNN feature vector, AC-4 excludes the
    early-window days — both intentionally shift neighbour selection).

Tolerance policy:
    Test 1 asserts identity with the out-of-band sentinel (None) — there is no
    probability to compare. Test 4 uses exact equality because its expected
    value (0.0) is a deterministic boundary result (all simulated paths are
    strictly below current_return), not a Monte Carlo draw.

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


def _run_from_fixture_raw(fixture: dict[str, Any]) -> Any:
    """
    Build inputs from a fixture and call run_monte_carlo under the fixed seed,
    returning the raw result WITHOUT coercion. The result is a float for the
    sufficient-history path, or the out-of-band insufficient-history sentinel
    (None) when the history is below MC_MIN_HISTORY_DAYS — float() must NOT be
    applied blindly.
    """
    inputs = fixture["inputs"]
    history = _build_history(inputs["historical_data_spec"])
    return math_engine.run_monte_carlo(
        inputs["holdings"],
        history,
        inputs["spy_today_return"],
        simulation_paths=inputs["simulation_paths"],
        neighbor_k=inputs["neighbor_k"],
        seed=inputs["numpy_seed"],
    )


def _run_from_fixture(fixture: dict[str, Any]) -> float:
    """
    Call run_monte_carlo and return its result as a float. Use only for fixtures
    on the sufficient-history path — it fails loudly if the function returned
    the out-of-band insufficient sentinel (None), which would otherwise crash on
    float(None).
    """
    result = _run_from_fixture_raw(fixture)
    assert result is not None, (
        f"Fixture '{fixture['name']}' expected a sufficient-history MC "
        f"probability but run_monte_carlo returned the insufficient sentinel. "
        f"Use _run_from_fixture_raw for the insufficient-history path."
    )
    return float(result)


# ---------------------------------------------------------------------------
# Test 1 — Short-history input returns the out-of-band insufficient sentinel
# ---------------------------------------------------------------------------

def test_short_history_spy_vol_fallback_returns_sentinel_and_is_finite() -> None:
    """
    15 days of SPY history is below MC_MIN_HISTORY_DAYS (20).
    run_monte_carlo must return the out-of-band insufficient-history sentinel
    (MC_INSUFFICIENT_HISTORY_SENTINEL = None) — NOT an in-band probability —
    without raising.

    Updated for Cluster 2 AC-2: the prior contract returned the in-band 100.0,
    which was fail-dangerous (100 >= MC_BREAKDOWN_THRESHOLD permanently vetoed the
    protective trailing stop). The function now signals "MC could not run"
    out-of-band so the caller can distinguish it from a genuine probability.
    """
    fixture = _load_fixture("01_short_history_spy_vol_fallback.json")
    assert fixture.get("expected_is_insufficient_sentinel") is True, (
        "Fixture 01 must declare expected_is_insufficient_sentinel: true."
    )
    actual = _run_from_fixture_raw(fixture)

    # The contract is a DISTINCT out-of-band sentinel — None, not a probability.
    assert actual is None, (
        f"run_monte_carlo returned {actual!r} for a 15-day history "
        f"(< MC_MIN_HISTORY_DAYS). It must return the out-of-band insufficient "
        f"sentinel (MC_INSUFFICIENT_HISTORY_SENTINEL = None), not an in-band "
        f"value. Derivation: {fixture['derivation']}"
    )
    # The sentinel must be exactly the module constant.
    assert actual is math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL, (
        f"run_monte_carlo's insufficient-history return {actual!r} is not the "
        f"named module constant MC_INSUFFICIENT_HISTORY_SENTINEL "
        f"({math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL!r})."
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
    The 50-day (normal path) result must be a real probability, and the 15-day
    (insufficient-history) result must be the out-of-band sentinel — the two
    must differ.

    This guards against a regression where the normal path accidentally returns
    the sentinel, or the short-circuit path leaks an in-band value — either
    would make the "MC could not run" signal invisible to the caller.
    """
    short_fixture = _load_fixture("01_short_history_spy_vol_fallback.json")
    long_fixture = _load_fixture("02_long_history_normal_path.json")

    short_result = _run_from_fixture_raw(short_fixture)
    long_result = _run_from_fixture_raw(long_fixture)

    assert short_result is None, (
        f"15-day history must return the insufficient sentinel (None); "
        f"got {short_result!r}."
    )
    assert long_result is not None, (
        f"50-day history must return a real MC probability, not the "
        f"insufficient sentinel."
    )
    assert short_result != long_result, (
        f"Short-history sentinel ({short_result!r}) equals long-history MC "
        f"output ({long_result!r}). These must differ: the sentinel is the "
        f"degenerate no-data case; the MC output is a real probability."
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
# Property: every fixture returns either a bounded finite probability or the
# out-of-band insufficient-history sentinel
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
    Property invariant: run_monte_carlo must return EITHER a finite float in
    [0, 100] (a real probability) OR the out-of-band insufficient-history
    sentinel (None) — never NaN, Inf, or a number outside [0, 100].

    The short-history fixture (01) is on the insufficient-history path and
    legitimately returns the sentinel; the other three return real
    probabilities. Parametrizing over all four enrolls any new fixture in the
    invariant automatically.
    """
    fixture = _load_fixture(fixture_filename)
    actual = _run_from_fixture_raw(fixture)

    if actual is None:
        # The insufficient-history sentinel is a valid out-of-band result.
        assert actual is math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL, (
            f"{fixture_filename}: run_monte_carlo returned None but it is not "
            f"the named MC_INSUFFICIENT_HISTORY_SENTINEL constant."
        )
        return

    assert math.isfinite(actual), (
        f"{fixture_filename}: run_monte_carlo returned non-finite {actual!r}. "
        "Probability outputs must never be NaN or Inf."
    )
    assert 0.0 <= actual <= 100.0, (
        f"{fixture_filename}: run_monte_carlo returned {actual!r} outside [0, 100]. "
        "All probability outputs must be bounded."
    )
