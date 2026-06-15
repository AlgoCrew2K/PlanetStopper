"""
Adversarial RED test — zero-volume tickers must NOT be counted in the
VWAP-signal weighting denominator.

Background
----------
The quant-risk-researcher flagged a contract gap in
``math_engine.compute_vwap_signals``: a holding whose ticker has had ZERO
traded volume for the session has no economically meaningful VWAP. Such a
ticker must NOT contribute to ``weighted_vwap_diff`` and — critically — must
NOT be counted in ``valid_vwap_weight`` (the denominator the caller later
uses to normalize / gate the VWAP signal).

The existing implementation of ``compute_vwap_signals`` guards only on
``v > 0`` (a degenerate-VWAP guard). It does NOT inspect traded volume. A
``live_vwaps`` entry that carries a stale, non-zero ``vwap`` together with a
``volume`` of 0 would today be counted in BOTH accumulators — diluting the
weighting with a ticker that did not trade.

What "VALIDATED contract" means here
------------------------------------
Per the cycle spec, the validated requirement is: zero-volume tickers are
excluded from the weighting. This file pins that requirement. It is RED
until the producer reads a per-ticker volume signal and skips entries whose
session volume is zero.

These tests are deliberately able to FAIL on the current implementation —
that is the point of an adversarial RED test. The implementer makes them
GREEN by adding a volume-aware skip; the implementer must NOT make them pass
by weakening the assertion.

Provenance
----------
All expected values are derived BY HAND in each test from the controlled
fixture inputs (allocations, prices, vwaps). No producer-computed literal is
hardcoded.
"""

from __future__ import annotations

import pytest

import math_engine

# Tolerance: same policy as test_vwap_signals.py. rel=1e-9 catches any
# algorithm error by orders of magnitude; abs=1e-12 anchors zero comparisons.
TOL_REL = 1e-9
TOL_ABS = 1e-12


def test_zero_volume_ticker_excluded_from_valid_weight() -> None:
    """A ticker with session volume 0 must NOT add to ``valid_vwap_weight``.

    Two holdings, each allocation 0.5:
      - REAL: volume 100000, vwap 100.0, last_price 101.0  -> qualifies
      - DEAD: volume 0,      vwap  50.0, last_price  50.0  -> must be SKIPPED

    DEAD has a non-zero (stale) vwap, so the existing ``v > 0`` guard does
    NOT skip it — only a volume-aware guard does. The denominator must be
    just REAL's allocation (0.5), NOT 1.0.
    """
    holdings = [
        {"ticker": "REAL", "allocation": 0.5},
        {"ticker": "DEAD", "allocation": 0.5},
    ]
    live_vwaps = {
        "REAL": {"last_price": 101.0, "vwap": 100.0, "volume": 100000},
        "DEAD": {"last_price": 50.0, "vwap": 50.0, "volume": 0},
    }

    _, valid_vwap_weight = math_engine.compute_vwap_signals(
        holdings=holdings, live_vwaps=live_vwaps
    )

    # Derived by hand: only REAL (volume > 0) qualifies -> weight == 0.5.
    expected_weight = 0.5
    assert valid_vwap_weight == pytest.approx(expected_weight, rel=TOL_REL, abs=TOL_ABS), (
        "Zero-volume ticker DEAD was counted in valid_vwap_weight. A ticker "
        "with 0 session volume has no meaningful VWAP and must NOT be in the "
        f"weighting denominator. Expected weight {expected_weight} (REAL "
        f"alone), got {valid_vwap_weight}."
    )


def test_zero_volume_ticker_excluded_from_weighted_diff() -> None:
    """A zero-volume ticker must NOT add to ``weighted_vwap_diff``.

    REAL trades 1% above its VWAP; DEAD (volume 0) trades 20% above a stale
    VWAP. If DEAD leaks in, the diff is badly inflated.

      REAL: alloc 0.5, p 101.0, v 100.0 -> 0.5 * (101-100)/100 = 0.005
      DEAD: alloc 0.5, p  60.0, v  50.0 -> would add 0.5 * (60-50)/50 = 0.1

    Expected (DEAD excluded) == 0.005. A producer that counts DEAD yields
    0.105 — a 21x error, far outside tolerance.
    """
    holdings = [
        {"ticker": "REAL", "allocation": 0.5},
        {"ticker": "DEAD", "allocation": 0.5},
    ]
    live_vwaps = {
        "REAL": {"last_price": 101.0, "vwap": 100.0, "volume": 100000},
        "DEAD": {"last_price": 60.0, "vwap": 50.0, "volume": 0},
    }

    weighted_vwap_diff, _ = math_engine.compute_vwap_signals(
        holdings=holdings, live_vwaps=live_vwaps
    )

    # Derived by hand: only REAL contributes.
    expected_diff = 0.5 * (101.0 - 100.0) / 100.0  # == 0.005
    assert weighted_vwap_diff == pytest.approx(expected_diff, rel=TOL_REL, abs=TOL_ABS), (
        "Zero-volume ticker DEAD leaked into weighted_vwap_diff. Expected "
        f"{expected_diff} (REAL alone); a producer that counted DEAD's stale "
        f"VWAP would return ~0.105. Got {weighted_vwap_diff}."
    )


def test_all_holdings_zero_volume_returns_zero_zero() -> None:
    """If EVERY holding has zero volume, the result is (0.0, 0.0).

    No qualifying ticker => both accumulators stay at their 0.0 initial
    values. Catches a producer that, lacking a volume guard, would emit a
    non-zero signal built entirely from stale VWAPs.
    """
    holdings = [
        {"ticker": "DEAD1", "allocation": 0.6},
        {"ticker": "DEAD2", "allocation": 0.4},
    ]
    live_vwaps = {
        "DEAD1": {"last_price": 10.0, "vwap": 9.0, "volume": 0},
        "DEAD2": {"last_price": 20.0, "vwap": 25.0, "volume": 0},
    }

    weighted_vwap_diff, valid_vwap_weight = math_engine.compute_vwap_signals(
        holdings=holdings, live_vwaps=live_vwaps
    )

    assert valid_vwap_weight == 0.0, (
        "All holdings have zero session volume; valid_vwap_weight must be "
        f"exactly 0.0 (no qualifying ticker). Got {valid_vwap_weight!r}."
    )
    assert weighted_vwap_diff == 0.0, (
        "All holdings have zero session volume; weighted_vwap_diff must be "
        f"exactly 0.0 — no signal can be built from stale VWAPs alone. "
        f"Got {weighted_vwap_diff!r}."
    )


def test_positive_volume_ticker_still_qualifies() -> None:
    """Guard against an over-eager fix: a ticker WITH positive volume must
    still be counted.

    A single holding, volume 500000 (> 0), vwap 100.0, last_price 102.0.
    Expected: weight == allocation, diff == alloc*(p-v)/v. This test fails if
    the implementer makes the zero-volume tests pass by skipping ALL tickers
    (e.g. requiring a volume key that legitimate entries also lack, or
    inverting the comparison).
    """
    holdings = [{"ticker": "REAL", "allocation": 0.8}]
    live_vwaps = {
        "REAL": {"last_price": 102.0, "vwap": 100.0, "volume": 500000},
    }

    weighted_vwap_diff, valid_vwap_weight = math_engine.compute_vwap_signals(
        holdings=holdings, live_vwaps=live_vwaps
    )

    expected_weight = 0.8
    expected_diff = 0.8 * (102.0 - 100.0) / 100.0  # == 0.016
    assert valid_vwap_weight == pytest.approx(expected_weight, rel=TOL_REL, abs=TOL_ABS), (
        "A ticker with positive session volume must still count toward "
        f"valid_vwap_weight. Expected {expected_weight}, got {valid_vwap_weight}."
    )
    assert weighted_vwap_diff == pytest.approx(expected_diff, rel=TOL_REL, abs=TOL_ABS), (
        "A ticker with positive session volume must still contribute to "
        f"weighted_vwap_diff. Expected {expected_diff}, got {weighted_vwap_diff}."
    )


# ---------------------------------------------------------------------------
# Data-integrity boundary — a non-finite VWAP must FAIL LOUD, even for a
# zero-volume ticker that the volume guard would otherwise skip.
# ---------------------------------------------------------------------------
#
# compute_vwap_signals validates last_price/vwap finiteness via
# _reject_non_finite (math_engine.py:381) in a loop over EVERY live_vwaps
# entry, BEFORE the per-holding loop where the zero-volume skip lives.
# Consequence: an entry that is BOTH zero-volume AND carries a non-finite
# (NaN / +Inf / -Inf) vwap raises a ValueError — it is NOT silently skipped
# by the volume guard.
#
# This is the CORRECT behavior and a deliberate contract boundary: a
# non-finite VWAP is an upstream data-integrity defect and must fail loud,
# not be swallowed. The function docstring already states the caller supplies
# normalized finite floats. These tests pin that boundary so a future
# refactor cannot weaken it into "NaN swallowed when volume happens to be
# zero" — which would hide a real upstream bug. They assert CURRENT correct
# behavior; they are not RED.


@pytest.mark.parametrize(
    "bad_vwap",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
    ids=["nan", "+inf", "-inf"],
)
def test_non_finite_vwap_raises_even_when_ticker_is_zero_volume(bad_vwap: float) -> None:
    """A zero-volume ticker carrying a non-finite vwap must raise ValueError,
    NOT be silently skipped by the volume guard.

    The finiteness check runs before the volume skip, so the entry fails loud.
    This is the documented data-integrity boundary: a NaN/Inf VWAP is an
    upstream defect. The volume guard must NOT be allowed to absorb it.
    """
    holdings = [{"ticker": "DEAD", "allocation": 0.5}]
    live_vwaps = {
        # volume 0 -> the volume guard WOULD skip this entry if it ran first.
        # vwap non-finite -> the finiteness check (which runs first) raises.
        "DEAD": {"last_price": 50.0, "vwap": bad_vwap, "volume": 0},
    }

    with pytest.raises(ValueError, match="NaN input not allowed"):
        math_engine.compute_vwap_signals(holdings=holdings, live_vwaps=live_vwaps)


@pytest.mark.parametrize(
    "bad_last_price",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
    ids=["nan", "+inf", "-inf"],
)
def test_non_finite_last_price_raises_even_when_ticker_is_zero_volume(
    bad_last_price: float,
) -> None:
    """Companion to the vwap case: a zero-volume ticker with a non-finite
    last_price must also raise ValueError rather than be skipped.

    Pins that BOTH validated fields (last_price and vwap) are protected by
    the fail-loud boundary regardless of the entry's volume.
    """
    holdings = [{"ticker": "DEAD", "allocation": 0.5}]
    live_vwaps = {
        "DEAD": {"last_price": bad_last_price, "vwap": 50.0, "volume": 0},
    }

    with pytest.raises(ValueError, match="NaN input not allowed"):
        math_engine.compute_vwap_signals(holdings=holdings, live_vwaps=live_vwaps)
