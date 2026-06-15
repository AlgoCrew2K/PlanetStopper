"""
RED tests for risk-adjusted metric extensions to analytics.py.

Feature: Phase 2 Dashboard — compute + display risk-adjusted metrics
Surface: analytics.compute_quantstats_metrics (add volatility key)

Golden fixture: tests/fixtures/math/volatility_annualized_basic.json
  — formula-derived, NOT a captured producer output.

Project rules enforced:
  - No hardcoded producer-computed values; expected values derive from
    fixture or formula (feedback_no_hardcoded_test_values).
  - Constants named + sourced; no magic numbers.
  - pytest.approx with explicit tolerance + comment explaining why.
  - No module-level mutable state shared across tests.
  - Mock only network and time; math engine tested directly.

These tests are intentionally RED — analytics.compute_quantstats_metrics
does not yet return a 'volatility' key.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

# Path to golden fixture — naming layer per project rule (fixture name includes layer name).
_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "math"
_VOLATILITY_FIXTURE = _FIXTURE_DIR / "volatility_annualized_basic.json"
_DERIVED_FIELDS_FIXTURE = _FIXTURE_DIR / "risk_adjusted_derived_fields.json"

# Annualization basis — 252 trading days per year (industry standard).
# Source: ux-design-deliverable.md §Change 2 "volatility = std(daily_returns_fraction) * sqrt(252)".
_ANNUALIZATION_DAYS = 252

# Minimum observations for any quantstats metric — matches analytics._MIN_QUANTSTATS_OBSERVATIONS.
# Source: analytics.py docstring binding contract.
_MIN_OBS = 2


# ---------------------------------------------------------------------------
# Golden-fixture derivation helpers
# ---------------------------------------------------------------------------


def _derive_annualized_vol(returns_pct: list[float]) -> float:
    """
    Formula-derived annualized volatility from percent-scale returns.

    returns_pct are percent-scale (e.g. 0.1 = 0.1%); analytics divides by 100
    before running quantstats, so we do the same here.
    Formula: sample_std(returns_frac) * sqrt(252)
    """
    returns_frac = [r / 100.0 for r in returns_pct]
    n = len(returns_frac)
    if n < _MIN_OBS:
        return float("nan")
    mean = sum(returns_frac) / n
    # Sample variance (ddof=1) — quantstats uses sample std.
    var = sum((r - mean) ** 2 for r in returns_frac) / (n - 1)
    return math.sqrt(var) * math.sqrt(_ANNUALIZATION_DAYS)


# ---------------------------------------------------------------------------
# Test 1: compute_quantstats_metrics returns 'volatility' key
# ---------------------------------------------------------------------------


def test_compute_quantstats_metrics_includes_volatility_key():
    """
    compute_quantstats_metrics must return a 'volatility' key along with the
    existing 7 keys. This is the Phase 2 extension: annualized volatility becomes
    a first-class metric in the dashboard (ux-design-deliverable.md §Change 2).

    Test is RED until analytics.py adds:
        metrics["volatility"] = _safe(lambda: qs_stats.volatility(series))
    """
    pytest.importorskip("quantstats", reason="quantstats required for volatility computation")
    from analytics import compute_quantstats_metrics

    with _VOLATILITY_FIXTURE.open(encoding="utf-8") as fh:
        fixture = json.load(fh)
    returns = fixture["input_returns_pct_scale"]

    metrics = compute_quantstats_metrics(returns, freq="D")

    assert "volatility" in metrics, (
        "compute_quantstats_metrics must include 'volatility' key (Phase 2 extension). "
        "Add: metrics['volatility'] = _safe(lambda: qs_stats.volatility(series)). "
        f"Current keys: {sorted(metrics.keys())}"
    )


# ---------------------------------------------------------------------------
# Test 2: volatility is a positive finite float for normal input
# ---------------------------------------------------------------------------


def test_compute_quantstats_metrics_volatility_is_positive_finite_float():
    """
    Annualized volatility must be a positive finite float when the input series
    has >= 2 finite observations with non-zero variance.

    Volatility is a standard deviation — it is defined as non-negative.
    A value of 0.0 or negative indicates a bug in the formula or scale.
    """
    pytest.importorskip("quantstats", reason="quantstats required for volatility computation")
    from analytics import compute_quantstats_metrics

    with _VOLATILITY_FIXTURE.open(encoding="utf-8") as fh:
        fixture = json.load(fh)
    returns = fixture["input_returns_pct_scale"]

    metrics = compute_quantstats_metrics(returns, freq="D")

    vol = metrics.get("volatility")
    assert vol is not None, (
        "volatility must not be None for a 30-observation series with non-zero variance"
    )
    assert isinstance(vol, float), f"volatility must be a float; got {type(vol).__name__}={vol!r}"
    assert math.isfinite(vol), f"volatility must be finite (no NaN/Inf); got {vol!r}"
    assert vol > 0.0, (
        "volatility is a std dev — it must be positive for any series with non-zero variance; "
        f"got {vol!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: volatility is in the expected fraction-scale range
# ---------------------------------------------------------------------------


def test_compute_quantstats_metrics_volatility_is_fraction_scale_not_percent_scale():
    """
    Annualized volatility must be returned in FRACTION scale (e.g. 0.035 for
    3.5% annual vol), not percent scale (35.0).

    This is critical: analytics.py divides inputs by 100 before calling quantstats
    (see analytics.py:fraction_vals). If the implementer forgets this, volatility
    comes back at 100x the correct value and the dashboard shows nonsense.

    The fixture series has realistic daily moves of ~0.1–0.4% (percent-scale);
    the annualized vol should be ~3–5% = 0.03–0.05 in fraction scale.
    A value > 1.0 strongly suggests percent-scale leak (100%+ annual vol is
    not impossible but extremely rare for a tame synthetic series).

    Tolerance: range check, not exact value — quantstats may differ slightly
    across versions in its ddof/window handling.

    This test is RED until 'volatility' key is added to compute_quantstats_metrics.
    """
    pytest.importorskip("quantstats", reason="quantstats required for volatility computation")
    from analytics import compute_quantstats_metrics

    with _VOLATILITY_FIXTURE.open(encoding="utf-8") as fh:
        fixture = json.load(fh)
    returns = fixture["input_returns_pct_scale"]
    lo = fixture["expected"]["volatility_range_lo_frac"]
    hi = fixture["expected"]["volatility_range_hi_frac"]

    metrics = compute_quantstats_metrics(returns, freq="D")

    # Assert key presence first — a missing key means the implementation is incomplete.
    assert "volatility" in metrics, (
        "volatility key must exist in metrics dict before we can check its scale. "
        f"Current keys: {sorted(metrics.keys())}"
    )
    vol = metrics["volatility"]

    assert vol is not None, (
        "volatility must not be None for a 30-observation series with non-zero variance"
    )
    assert lo < vol < hi, (
        f"volatility={vol!r} is outside the fraction-scale range ({lo}, {hi}). "
        "A value > 1.0 indicates the implementer returned percent-scale instead of "
        "fraction-scale, or forgot that analytics.py divides inputs by 100 before "
        "calling quantstats."
    )


# ---------------------------------------------------------------------------
# Test 4: volatility is None when fewer than 2 observations
# ---------------------------------------------------------------------------


def test_compute_quantstats_metrics_volatility_is_none_for_insufficient_data():
    """
    volatility must follow the same None-on-insufficient-data contract as all
    other metrics in compute_quantstats_metrics.

    Source: analytics.py binding contract — '_MIN_QUANTSTATS_OBSERVATIONS = 2'.
    """
    from analytics import compute_quantstats_metrics

    for series in ([], [0.1]):
        metrics = compute_quantstats_metrics(series, freq="D")
        assert "volatility" in metrics, (
            f"volatility key must always be present even with insufficient data; "
            f"got keys={list(metrics.keys())} for series={series!r}"
        )
        assert metrics["volatility"] is None, (
            f"volatility must be None for insufficient-data series {series!r}; "
            f"got {metrics['volatility']!r}"
        )


# ---------------------------------------------------------------------------
# Test 5: volatility is formula-consistent with manual derivation
# ---------------------------------------------------------------------------


def test_compute_quantstats_metrics_volatility_consistent_with_formula():
    """
    Volatility must be consistent with std(daily_returns_fraction, ddof=1) * sqrt(252).

    We do NOT pin an exact value (quantstats may use slightly different ddof
    or annualization in future versions), but we assert the result is within
    5% relative tolerance of the formula — a larger gap would indicate the
    implementer used a different formula (e.g. total_return-based or ddof=0).

    Tolerance: 5% relative — wide enough to absorb quantstats version differences,
    narrow enough to catch a formula-level bug (wrong annualization factor, etc.).
    """
    pytest.importorskip("quantstats", reason="quantstats required for volatility computation")
    from analytics import compute_quantstats_metrics

    with _VOLATILITY_FIXTURE.open(encoding="utf-8") as fh:
        fixture = json.load(fh)
    returns = fixture["input_returns_pct_scale"]

    # Formula-derived expected value — see _derive_annualized_vol docstring.
    expected_vol = _derive_annualized_vol(returns)
    assert expected_vol > 0.0, "test precondition: fixture series must have non-zero vol"

    metrics = compute_quantstats_metrics(returns, freq="D")

    # Assert key presence first — missing key means implementation incomplete (RED).
    assert "volatility" in metrics, (
        f"volatility key must be present in metrics; current keys: {sorted(metrics.keys())}"
    )
    vol = metrics["volatility"]

    assert vol is not None, (
        "volatility must not be None for a 30-observation series with non-zero variance"
    )
    # 5% relative tolerance: abs(vol - expected) / expected < 0.05.
    # This is generous for version drift but tight enough to catch formula bugs.
    rel_err = abs(vol - expected_vol) / expected_vol
    assert rel_err < 0.05, (
        f"volatility={vol:.6f} deviates {rel_err:.1%} from formula-derived "
        f"expected={expected_vol:.6f}. Tolerance is 5% relative — a larger "
        f"deviation indicates the wrong formula or annualization basis. "
        f"Formula: sample_std(returns_frac, ddof=1) * sqrt({_ANNUALIZATION_DAYS})"
    )


# ---------------------------------------------------------------------------
# Test 6: all 8 required keys present in the Phase 2 metrics dict
# ---------------------------------------------------------------------------


def test_compute_quantstats_metrics_phase2_has_eight_required_keys():
    """
    After Phase 2, compute_quantstats_metrics must return 8 keys:
    the original 7 plus 'volatility'.

    This test drives the implementer to add the key alongside the others
    rather than as an afterthought in a separate call.
    """
    pytest.importorskip("quantstats", reason="quantstats required for metric computation")
    from analytics import compute_quantstats_metrics

    returns = [
        0.1,
        -0.2,
        0.3,
        -0.1,
        0.2,
        0.4,
        -0.3,
        0.1,
        0.2,
        -0.1,
        0.3,
        -0.2,
        0.1,
        0.4,
        -0.2,
        0.2,
        -0.1,
        0.3,
        0.1,
        -0.2,
    ]
    metrics = compute_quantstats_metrics(returns, freq="D")

    required_keys = {
        "total_return",
        "annualized_return",
        "sharpe",
        "sortino",
        "max_drawdown",
        "calmar",
        "win_rate",
        "volatility",  # Phase 2 addition
    }
    missing = required_keys - set(metrics.keys())
    assert not missing, (
        f"compute_quantstats_metrics Phase 2 must return all 8 keys; missing: {missing}"
    )


# ---------------------------------------------------------------------------
# Test 7: volatility_delta sign convention — positive means bot calmer
# ---------------------------------------------------------------------------


def test_volatility_delta_is_positive_when_bot_has_lower_annualized_vol():
    """
    volatility_delta = held_vol - bot_vol.
    Positive delta means bot is calmer (lower vol) than the if-held baseline.
    This is the GOOD direction for this system (capital preservation focus).

    Source: ux-design-deliverable.md §Change 4:
      "vol_bot_wins = vol_bot <= vol_held — lower vol = better for this system"
      "alpha delta label: α −1.5pp colored --studio-pos when bot is calmer"

    This is a property test of the delta computation convention, not of
    a specific implementation. The implementer must compute:
      volatility_delta = held_vol - bot_vol

    We verify the sign convention holds for a fixture with known direction.
    Fixture: tests/fixtures/math/risk_adjusted_derived_fields.json.
    """
    with _DERIVED_FIELDS_FIXTURE.open(encoding="utf-8") as fh:
        fixture = json.load(fh)

    held_vol = fixture["volatility_delta"]["example_held_vol"]
    bot_vol = fixture["volatility_delta"]["example_bot_vol"]
    expected_delta = fixture["volatility_delta"]["expected_delta"]

    # Compute using the formula; do NOT assert a specific float value —
    # assert the sign (direction) and formula shape instead.
    computed_delta = held_vol - bot_vol

    # The fixture was authored so held_vol > bot_vol => delta > 0 (bot calmer).
    assert computed_delta > 0.0, (
        "Test precondition: fixture must have held_vol > bot_vol so delta > 0. "
        f"held_vol={held_vol}, bot_vol={bot_vol}, delta={computed_delta}"
    )
    # Shape: matches fixture expected within rounding (fixture values are exact).
    # Tolerance: 1e-9 — these are exact fixture values, no floating-point accumulation.
    assert computed_delta == pytest.approx(expected_delta, abs=1e-9), (
        f"volatility_delta formula check: held_vol - bot_vol = "
        f"{held_vol} - {bot_vol} = {computed_delta:.9f}, "
        f"fixture expected {expected_delta}. "
        "If this fails, the fixture expected_delta value is wrong."
    )
    # Sign invariant: lower bot vol means positive delta means 'green' in the UI.
    assert computed_delta > 0.0, (
        "volatility_delta must be positive when bot vol < held vol "
        "(bot is calmer = favorable for this capital-preservation system)"
    )


# ---------------------------------------------------------------------------
# Test 8: max_drawdown_delta sign convention — positive means bot had less drawdown
# ---------------------------------------------------------------------------


def test_max_drawdown_delta_is_positive_when_bot_had_shallower_drawdown():
    """
    max_drawdown_delta sign convention:
      delta = abs(held_mdd) - abs(bot_mdd)

    Positive delta means the held baseline had a LARGER drawdown magnitude
    than the bot — the bot protected capital more effectively.
    This is the primary evidence of Planet Stopper's stated purpose.

    Source: ux-design-deliverable.md §Change 2:
      "MDD Reduction: value = |held_mdd| - |bot_mdd| expressed as percentage points"
      "colored --studio-pos if positive (bot has less drawdown = good)"

    Fixture: tests/fixtures/math/risk_adjusted_derived_fields.json.
    """
    with _DERIVED_FIELDS_FIXTURE.open(encoding="utf-8") as fh:
        fixture = json.load(fh)

    held_mdd = fixture["max_drawdown_delta"]["example_held_mdd"]  # <= 0
    bot_mdd = fixture["max_drawdown_delta"]["example_bot_mdd"]  # <= 0
    expected_delta = fixture["max_drawdown_delta"]["expected_delta"]

    # The correct formula uses absolute values to make "less drawdown = positive".
    # Both held_mdd and bot_mdd are <= 0 (quantstats convention).
    computed_delta = abs(held_mdd) - abs(bot_mdd)

    assert held_mdd < bot_mdd, (
        "Test precondition: held_mdd must be more negative (worse) than bot_mdd. "
        f"held_mdd={held_mdd}, bot_mdd={bot_mdd}"
    )
    assert computed_delta > 0.0, (
        "max_drawdown_delta must be positive when held drawdown > bot drawdown. "
        f"abs(held={held_mdd}) - abs(bot={bot_mdd}) = {computed_delta}"
    )
    assert computed_delta == pytest.approx(expected_delta, abs=1e-9), (
        f"max_drawdown_delta formula: abs({held_mdd}) - abs({bot_mdd}) = "
        f"{computed_delta:.9f}, fixture expected {expected_delta}. "
        "If this fails, the fixture expected_delta value is wrong."
    )


# ---------------------------------------------------------------------------
# Test 9: monotonicity invariant — higher volatility series yields higher vol metric
# ---------------------------------------------------------------------------


def test_annualized_volatility_is_monotonically_higher_for_higher_variance_series():
    """
    Property test: a series with higher daily variance must produce a higher
    annualized volatility than a lower-variance series with the same mean.

    This is an invariant of std deviation — it cannot be violated by any correct
    implementation of std * sqrt(252).

    We use two fixture-derived series rather than arbitrary data to make the
    test deterministic and auditable.
    """
    pytest.importorskip("quantstats", reason="quantstats required")
    from analytics import compute_quantstats_metrics

    # Low-variance series: daily moves of ~0.05% percent-scale
    low_var_series = [
        0.05,
        -0.04,
        0.06,
        -0.03,
        0.05,
        0.04,
        -0.05,
        0.06,
        0.03,
        -0.04,
        0.05,
        -0.03,
        0.04,
        0.06,
        -0.04,
        0.05,
        -0.03,
        0.06,
        0.04,
        -0.05,
    ]
    # High-variance series: daily moves of ~0.5% percent-scale (10x larger)
    high_var_series = [
        0.5,
        -0.4,
        0.6,
        -0.3,
        0.5,
        0.4,
        -0.5,
        0.6,
        0.3,
        -0.4,
        0.5,
        -0.3,
        0.4,
        0.6,
        -0.4,
        0.5,
        -0.3,
        0.6,
        0.4,
        -0.5,
    ]

    low_metrics = compute_quantstats_metrics(low_var_series, freq="D")
    high_metrics = compute_quantstats_metrics(high_var_series, freq="D")

    low_vol = low_metrics.get("volatility")
    high_vol = high_metrics.get("volatility")

    if low_vol is None or high_vol is None:
        pytest.skip("volatility key not yet implemented — skipping monotonicity check")

    assert low_vol < high_vol, (
        f"Higher-variance series must yield higher annualized vol. "
        f"low_var_vol={low_vol:.6f}, high_var_vol={high_vol:.6f}. "
        "This invariant cannot be violated by a correct std * sqrt(252) implementation."
    )


# ---------------------------------------------------------------------------
# Test 10: volatility is non-negative for any input (property invariant)
# ---------------------------------------------------------------------------


def test_annualized_volatility_is_non_negative_for_arbitrary_series():
    """
    Property invariant: annualized volatility is a standard deviation and cannot
    be negative for any finite input series.

    Tests multiple series including all-positive, all-negative, and mixed returns.
    A negative volatility indicates a sign bug in the implementation.
    """
    pytest.importorskip("quantstats", reason="quantstats required")
    from analytics import compute_quantstats_metrics

    test_series = [
        # All positive — pure gains series
        [
            0.1,
            0.2,
            0.15,
            0.05,
            0.25,
            0.1,
            0.3,
            0.12,
            0.08,
            0.18,
            0.1,
            0.2,
            0.15,
            0.05,
            0.25,
            0.1,
            0.3,
            0.12,
            0.08,
            0.18,
        ],
        # All negative — pure loss series
        [
            -0.1,
            -0.2,
            -0.15,
            -0.05,
            -0.25,
            -0.1,
            -0.3,
            -0.12,
            -0.08,
            -0.18,
            -0.1,
            -0.2,
            -0.15,
            -0.05,
            -0.25,
            -0.1,
            -0.3,
            -0.12,
            -0.08,
            -0.18,
        ],
        # Mixed with zero
        [
            0.1,
            0.0,
            -0.1,
            0.2,
            0.0,
            -0.2,
            0.3,
            0.0,
            -0.3,
            0.4,
            0.1,
            0.0,
            -0.1,
            0.2,
            0.0,
            -0.2,
            0.3,
            0.0,
            -0.3,
            0.4,
        ],
        # Constant series — vol should be 0 (or quantstats may return small float/None)
        [
            0.1,
            0.1,
            0.1,
            0.1,
            0.1,
            0.1,
            0.1,
            0.1,
            0.1,
            0.1,
            0.1,
            0.1,
            0.1,
            0.1,
            0.1,
            0.1,
            0.1,
            0.1,
            0.1,
            0.1,
        ],
    ]

    for series in test_series:
        metrics = compute_quantstats_metrics(series, freq="D")
        # The 'volatility' key must be present (Phase 2 extension).
        assert "volatility" in metrics, (
            f"volatility key must be present for series len={len(series)}; "
            f"current keys: {sorted(metrics.keys())}"
        )
        vol = metrics["volatility"]
        if vol is not None:
            assert vol >= 0.0, (
                f"annualized volatility must be >= 0; got {vol!r} for series={series[:5]}..."
                " (std deviation cannot be negative)"
            )
            assert math.isfinite(vol), f"annualized volatility must be finite; got {vol!r}"
