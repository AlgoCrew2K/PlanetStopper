"""
H1 — run_monte_carlo value-direction pin (proves the fix is RENAME-ONLY).

WHY THIS SUITE EXISTS
---------------------
The H1 correction hinges on a single empirical claim: the value returned by
``run_monte_carlo`` is ALREADY the "how much we are underperforming" direction
and must NOT be recomputed. The GATE2-PLAN's alternative (recompute to
``below_count/paths``) was rejected because that complement is ~0 at -8%, which
would re-create the suppression bug.

This suite pins the producer's value direction directly against a controlled
analog pool:
  * a deeply-negative symphony return (today badly underperforming the analog
    days) must yield a HIGH value (-> ~100);
  * a strongly-positive symphony return (today outperforming) must yield a LOW
    value (-> ~0);
  * the value must be MONOTONIC NON-INCREASING as today's return improves
    (better today -> fewer analog days beat us -> lower value).

If a future change recomputed run_monte_carlo to the complement, the high/low
ends would SWAP and this suite goes RED — a guard against re-introducing the
inverted metric under the new name.

The analog pool is constructed from a single-ticker holding so the symphony
return on each historical day equals that ticker's daily_ret * 100. The pool
spans a known, symmetric-ish spread of daily returns; we then probe today's
symphony return at points below, inside, and above that spread.

DETERMINISM: run_monte_carlo draws ``simulation_paths`` samples WITH
replacement from the kNN pool using a seeded Generator (seed is an explicit
argument). We pass a fixed seed and a large path count so the empirical CDF is
a faithful, reproducible estimate of the pool CDF.

TOLERANCE POLICY
----------------
The value is a percentage in [0, 100] estimated by a seeded bootstrap. We do
NOT assert an exact float — we assert DIRECTION (high vs low) with generous
margins (>= 90 for "badly underperforming", <= 10 for "outperforming") and a
monotonicity property. These margins are wide because the claim under test is
qualitative (which end of the range), not a precise quantile; a seeded 5000-
path draw against a well-separated probe point lands far inside these margins.
``pytest.approx`` is intentionally NOT used: the assertion is an inequality on
direction, not equality to a producer-computed number (that would hardcode a
producer value — forbidden by feedback_no_hardcoded_test_values).
"""

from __future__ import annotations

import pytest

import math_engine


_SEED = 20260608  # fixed seed -> reproducible bootstrap CDF estimate
_PATHS = 5000     # large path count -> faithful empirical CDF


def _build_analog_pool() -> dict:
    """
    Build a historical_data dict for a single ticker "AAA" whose daily returns
    span a symmetric spread around 0, plus a calm SPY series so today is NOT
    flagged unprecedented by any regime logic the caller applies (run_monte_carlo
    itself does not apply the regime guard — that is the caller's job — but a
    well-behaved SPY keeps the kNN distances meaningful).

    The pool is built long enough to clear the eligible-days sufficiency gate
    (MC_MIN_HISTORY_DAYS + MC_VOL_WINDOW_DAYS - 1). Daily returns are DECIMAL
    fractions (the producer multiplies by PCT_SCALAR internally).
    """
    n_days = (
        math_engine.MC_MIN_HISTORY_DAYS
        + (math_engine.MC_VOL_WINDOW_DAYS - 1)
        + 60  # comfortable margin above the eligibility floor
    )
    # A spread of ticker daily returns in DECIMAL form symmetric around 0,
    # ranging roughly -3%..+3% per day. Deterministic, no RNG in the fixture.
    history: dict[str, dict] = {}
    for i in range(n_days):
        # cycle a value in [-0.03, +0.03]
        frac = -0.03 + 0.06 * (i % 21) / 20.0
        date = f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"
        history[date] = {
            "AAA": {"daily_ret": frac},
            # SPY calm and near-zero so the kNN query point (driven by
            # spy_today_return) sits inside the candidate cloud.
            "SPY": {"daily_ret": 0.0001 * ((i % 5) - 2)},
        }
    return history


def _holdings_for_symphony_return(symphony_return_pct: float) -> list[dict]:
    """
    A single-ticker holding whose allocation is 1.0 and whose last_percent_change
    (DECIMAL) makes current_symphony_return == symphony_return_pct.

    The producer computes:
        current_symphony_return = sum(last_percent_change * PCT_SCALAR * allocation)
    so last_percent_change = symphony_return_pct / PCT_SCALAR with allocation 1.0.
    """
    return [
        {
            "ticker": "AAA",
            "allocation": 1.0,
            "last_percent_change": symphony_return_pct / math_engine.PCT_SCALAR,
        }
    ]


def _run(symphony_return_pct: float) -> float:
    history = _build_analog_pool()
    holdings = _holdings_for_symphony_return(symphony_return_pct)
    value = math_engine.run_monte_carlo(
        holdings,
        history,
        spy_today_return=0.0,  # calm market -> query point inside the cloud
        simulation_paths=_PATHS,
        neighbor_k=math_engine.MC_DEFAULT_NEIGHBOR_K,
        seed=_SEED,
    )
    assert value is not None, (
        "run_monte_carlo returned the insufficient-history sentinel (None); "
        "the analog pool fixture must clear the eligibility floor. Increase "
        "n_days in _build_analog_pool."
    )
    return value


def test_run_monte_carlo_value_is_high_when_badly_underperforming() -> None:
    """
    Today's symphony return is -8% — far below the analog pool's spread
    (~-3%..+3%). Almost every analog day beats today -> the value must be HIGH
    (>= 90). This is the "badly underperforming" end the corrected gate fires on.

    RED trigger (recompute to the complement): the complement
    below_count/paths is ~0 at -8% -> this assertion would fail, catching a
    re-introduction of the inverted metric.
    """
    value = _run(-8.0)
    assert value >= 90.0, (
        f"run_monte_carlo at -8% must be HIGH (>= 90, 'badly underperforming'); "
        f"got {value}. A LOW value here means the metric was recomputed to the "
        f"complement (the rejected GATE2-PLAN recompute) — the value must stay "
        f"the underperformance direction."
    )


def test_run_monte_carlo_value_is_low_when_outperforming() -> None:
    """
    Today's symphony return is +5% — above the analog pool's spread. Almost no
    analog day beats today -> the value must be LOW (<= 10). This is the
    "outperforming" end the corrected gate suppresses on.

    RED trigger (recompute to the complement): the complement is ~100 at +5%.
    """
    value = _run(5.0)
    assert value <= 10.0, (
        f"run_monte_carlo at +5% must be LOW (<= 10, 'outperforming'); got "
        f"{value}. A HIGH value here means the metric direction was inverted."
    )


def test_run_monte_carlo_value_is_monotonic_non_increasing_in_todays_return() -> None:
    """
    Property: as today's symphony return IMPROVES, fewer analog days beat us, so
    the value must be MONOTONIC NON-INCREASING. Probe a sweep from deeply
    negative to strongly positive and assert the sequence never increases.

    This is the structural invariant of "fraction of analogs >= today": it is a
    survival function of today's return, hence non-increasing. A recompute to
    the complement would make it non-DEcreasing -> RED.
    """
    probes = [-8.0, -5.0, -2.0, 0.0, 2.0, 5.0, 10.0]
    values = [_run(p) for p in probes]
    for earlier, later, v0, v1 in zip(probes, probes[1:], values, values[1:]):
        assert v1 <= v0 + 1e-9, (
            f"Non-monotonic: improving today's return from {earlier}% to "
            f"{later}% RAISED the value ({v0} -> {v1}). The value is the "
            f"fraction of analog days that beat today and must be "
            f"non-increasing as today improves."
        )
    # And the endpoints must straddle: deeply-negative high, strongly-positive low.
    assert values[0] >= 90.0 and values[-1] <= 10.0, (
        f"Endpoints must straddle the range: -8% -> {values[0]} (expect >= 90), "
        f"+10% -> {values[-1]} (expect <= 10)."
    )
