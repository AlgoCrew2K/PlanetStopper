"""
RED tests — sleeves/sizing.py: risk-based order sizing (AC-7).

CONTRACT this file specifies for the GREEN implementer (sleeve-risk-impl):

    sleeves/sizing.py

    @dataclass(frozen=True)
    class SizingResult:
        qty: float             # 0 on error
        notional_usd: float    # qty * price; 0 on error
        mode: str              # echoes the requested mode
        error: str | None      # None on success; never raises (D-1-style
                                # never-raises result object, matching
                                # composer_backtest_client.BacktestResult)

    def size_order(
        *, mode: str,                     # "risk_pct" | "pct_of_sleeve" | "dollars" | "shares"
        sleeve_equity: float, price: float,
        stop_price: float | None = None,
        risk_pct: float | None = None,
        pct_of_sleeve: float | None = None,
        dollars: float | None = None,
        shares: float | None = None,
        fractionable: bool = False,
    ) -> SizingResult: ...

Sizing math (AC-7):
    risk_pct mode:    risk_dollars = risk_pct * sleeve_equity
                       qty = risk_dollars / abs(price - stop_price)
    pct_of_sleeve:     notional = pct_of_sleeve * sleeve_equity; qty = notional / price
    dollars:           notional = dollars; qty = notional / price
    shares:            qty = shares directly; notional = qty * price

Fractional handling: Alpaca brackets require whole shares (fractional orders
cannot carry OCO/bracket legs). When fractionable=False (the P1 default for
every bracket entry), qty is floored to a whole share. If flooring produces
qty == 0, that is an error (`error="qty_rounds_to_zero"`), never a silent
zero-qty order.

Degenerate stop distance (risk_pct mode only): stop_price missing, equal to
price, or price/stop_price such that abs(price - stop_price) <= 0 is a
fail-safe error (`error="degenerate_stop_distance"`), never a divide-by-zero
crash and never an infinite/absurd qty.

No hardcoded producer values: every expected qty/notional below is DERIVED
from the same formula in the test, not asserted against a literal pulled
from a prior run.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

pytest.importorskip("sleeves.sizing", reason="RED phase — sleeves.sizing not implemented yet")

import sleeves.sizing as sizing  # noqa: E402

# ---------------------------------------------------------------------------
# 1. risk_pct mode
# ---------------------------------------------------------------------------


class TestRiskPctMode:
    def test_risk_pct_qty_derives_from_risk_dollars_over_stop_distance(self):
        sleeve_equity = 10_000.0
        risk_pct = 0.01  # 1% of equity at risk
        price = 100.0
        stop_price = 95.0

        result = sizing.size_order(
            mode="risk_pct",
            sleeve_equity=sleeve_equity,
            price=price,
            stop_price=stop_price,
            risk_pct=risk_pct,
            fractionable=True,  # keep exact-fraction math testable
        )

        risk_dollars = risk_pct * sleeve_equity  # $100
        stop_distance = abs(price - stop_price)  # $5
        expected_qty = risk_dollars / stop_distance  # 20 shares

        assert result.error is None
        assert result.qty == pytest.approx(expected_qty, rel=1e-9)

    def test_risk_pct_notional_is_qty_times_price(self):
        result = sizing.size_order(
            mode="risk_pct",
            sleeve_equity=20_000.0,
            price=50.0,
            stop_price=45.0,
            risk_pct=0.02,
            fractionable=True,
        )
        assert result.error is None
        assert result.notional_usd == pytest.approx(result.qty * 50.0, rel=1e-9)

    def test_risk_pct_non_fractionable_floors_to_whole_share(self):
        sleeve_equity = 10_000.0
        risk_pct = 0.015
        price = 100.0
        stop_price = 97.0  # stop_distance = 3 => raw qty = 150/3 = 50 exactly

        # Choose a risk_pct/stop combo that does NOT divide evenly, to prove flooring.
        result = sizing.size_order(
            mode="risk_pct",
            sleeve_equity=sleeve_equity,
            price=price,
            stop_price=98.7,  # stop_distance = 1.3 => raw qty = 150 / 1.3 = 115.38...
            risk_pct=risk_pct,
            fractionable=False,
        )
        raw_qty = (risk_pct * sleeve_equity) / abs(price - 98.7)
        assert result.error is None
        assert result.qty == math.floor(raw_qty)
        assert result.qty == int(result.qty), (
            "non-fractionable sizing must return a whole-share qty"
        )

    def test_degenerate_stop_distance_zero_is_a_fail_safe_error(self):
        result = sizing.size_order(
            mode="risk_pct",
            sleeve_equity=10_000.0,
            price=100.0,
            stop_price=100.0,  # zero distance
            risk_pct=0.01,
        )
        assert result.error == "degenerate_stop_distance"
        assert result.qty == 0

    def test_missing_stop_price_in_risk_pct_mode_is_a_fail_safe_error(self):
        result = sizing.size_order(
            mode="risk_pct",
            sleeve_equity=10_000.0,
            price=100.0,
            stop_price=None,
            risk_pct=0.01,
        )
        assert result.error == "degenerate_stop_distance"
        assert result.qty == 0

    def test_risk_pct_never_raises_on_degenerate_input(self):
        # D-1-style never-raises contract: a pathological input must return
        # an error result, never propagate an exception.
        try:
            result = sizing.size_order(
                mode="risk_pct",
                sleeve_equity=10_000.0,
                price=100.0,
                stop_price=100.0,
                risk_pct=0.01,
            )
        except Exception as exc:  # pragma: no cover — this branch IS the failure
            pytest.fail(
                f"size_order raised {type(exc).__name__} instead of returning an error result"
            )
        assert result.error is not None

    def test_qty_rounds_to_zero_is_reported_as_error_not_silent_zero(self):
        # Tiny risk budget against a huge stop distance floors to 0 shares —
        # must be flagged, never silently returned as a valid zero-qty order.
        result = sizing.size_order(
            mode="risk_pct",
            sleeve_equity=100.0,
            price=1_000.0,
            stop_price=500.0,  # stop_distance = 500
            risk_pct=0.001,  # risk_dollars = 0.10 -> raw qty = 0.0002
            fractionable=False,
        )
        assert result.qty == 0
        assert result.error == "qty_rounds_to_zero"


# ---------------------------------------------------------------------------
# 2. pct_of_sleeve mode
# ---------------------------------------------------------------------------


class TestPctOfSleeveMode:
    def test_pct_of_sleeve_qty_derives_from_notional_over_price(self):
        sleeve_equity = 10_000.0
        pct = 0.10
        price = 250.0
        result = sizing.size_order(
            mode="pct_of_sleeve",
            sleeve_equity=sleeve_equity,
            price=price,
            pct_of_sleeve=pct,
            fractionable=True,
        )
        expected_notional = pct * sleeve_equity  # $1000
        expected_qty = expected_notional / price  # 4 shares
        assert result.error is None
        assert result.qty == pytest.approx(expected_qty, rel=1e-9)
        assert result.notional_usd == pytest.approx(expected_notional, rel=1e-9)

    def test_pct_of_sleeve_non_fractionable_floors(self):
        sleeve_equity = 10_000.0
        pct = 0.137  # deliberately odd to force a fractional raw qty
        price = 33.0
        result = sizing.size_order(
            mode="pct_of_sleeve",
            sleeve_equity=sleeve_equity,
            price=price,
            pct_of_sleeve=pct,
            fractionable=False,
        )
        raw_qty = (pct * sleeve_equity) / price
        assert result.error is None
        assert result.qty == math.floor(raw_qty)


# ---------------------------------------------------------------------------
# 3. dollars mode
# ---------------------------------------------------------------------------


class TestDollarsMode:
    def test_dollars_qty_derives_from_dollars_over_price(self):
        result = sizing.size_order(
            mode="dollars",
            sleeve_equity=50_000.0,
            price=40.0,
            dollars=1_000.0,
            fractionable=True,
        )
        assert result.error is None
        assert result.qty == pytest.approx(1_000.0 / 40.0, rel=1e-9)
        assert result.notional_usd == pytest.approx(1_000.0, rel=1e-9)

    def test_dollars_mode_is_capped_by_neither_equity_nor_pct(self):
        # sizing.py sizes the order; envelope-level caps are enforced
        # downstream by sleeves.envelope.clamp_order, not here. A dollars
        # request larger than sleeve_equity must still size normally —
        # it is the envelope's job (not sizing's) to clamp it.
        result = sizing.size_order(
            mode="dollars",
            sleeve_equity=1_000.0,
            price=10.0,
            dollars=5_000.0,  # 5x equity — sizing does not reject this
            fractionable=True,
        )
        assert result.error is None
        assert result.qty == pytest.approx(500.0, rel=1e-9)


# ---------------------------------------------------------------------------
# 4. shares mode
# ---------------------------------------------------------------------------


class TestSharesMode:
    def test_shares_mode_qty_is_passed_through(self):
        result = sizing.size_order(
            mode="shares",
            sleeve_equity=10_000.0,
            price=75.0,
            shares=12,
        )
        assert result.error is None
        assert result.qty == 12
        assert result.notional_usd == pytest.approx(12 * 75.0, rel=1e-9)

    def test_shares_mode_with_fractionable_false_and_fractional_request_floors(self):
        result = sizing.size_order(
            mode="shares",
            sleeve_equity=10_000.0,
            price=75.0,
            shares=12.7,
            fractionable=False,
        )
        assert result.error is None
        assert result.qty == 12


# ---------------------------------------------------------------------------
# 5. Unknown mode / missing required params — fail-safe, never raises
# ---------------------------------------------------------------------------


class TestFailSafeInputHandling:
    def test_unknown_mode_returns_error_result(self):
        result = sizing.size_order(
            mode="not_a_real_mode",
            sleeve_equity=10_000.0,
            price=100.0,
        )
        assert result.error is not None
        assert result.qty == 0

    def test_pct_of_sleeve_missing_pct_param_returns_error(self):
        result = sizing.size_order(
            mode="pct_of_sleeve",
            sleeve_equity=10_000.0,
            price=100.0,
            pct_of_sleeve=None,
        )
        assert result.error is not None
        assert result.qty == 0

    def test_dollars_missing_dollars_param_returns_error(self):
        result = sizing.size_order(
            mode="dollars",
            sleeve_equity=10_000.0,
            price=100.0,
            dollars=None,
        )
        assert result.error is not None
        assert result.qty == 0

    def test_shares_missing_shares_param_returns_error(self):
        result = sizing.size_order(
            mode="shares",
            sleeve_equity=10_000.0,
            price=100.0,
            shares=None,
        )
        assert result.error is not None
        assert result.qty == 0

    def test_zero_or_negative_price_returns_error_never_divides_by_zero(self):
        for bad_price in (0.0, -10.0):
            result = sizing.size_order(
                mode="dollars",
                sleeve_equity=10_000.0,
                price=bad_price,
                dollars=500.0,
            )
            assert result.error is not None, (
                f"price={bad_price} must be rejected, not silently sized"
            )
            assert result.qty == 0


# ---------------------------------------------------------------------------
# 6. Property-based invariants (hypothesis)
# ---------------------------------------------------------------------------


class TestSizingProperties:
    @given(
        sleeve_equity=st.floats(
            min_value=100.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False
        ),
        price=st.floats(min_value=0.01, max_value=10_000.0, allow_nan=False, allow_infinity=False),
        pct=st.floats(min_value=0.0001, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    def test_pct_of_sleeve_qty_is_never_negative(self, sleeve_equity, price, pct):
        result = sizing.size_order(
            mode="pct_of_sleeve",
            sleeve_equity=sleeve_equity,
            price=price,
            pct_of_sleeve=pct,
            fractionable=True,
        )
        assert result.qty >= 0, f"qty must never be negative; got {result.qty}"

    @given(
        sleeve_equity=st.floats(
            min_value=100.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False
        ),
        price=st.floats(min_value=1.0, max_value=10_000.0, allow_nan=False, allow_infinity=False),
        stop_offset=st.floats(
            min_value=0.01, max_value=500.0, allow_nan=False, allow_infinity=False
        ),
        risk_pct=st.floats(min_value=0.0001, max_value=0.5, allow_nan=False, allow_infinity=False),
    )
    def test_risk_pct_qty_is_never_negative_and_never_raises(
        self, sleeve_equity, price, stop_offset, risk_pct
    ):
        stop_price = max(price - stop_offset, 0.01)
        try:
            result = sizing.size_order(
                mode="risk_pct",
                sleeve_equity=sleeve_equity,
                price=price,
                stop_price=stop_price,
                risk_pct=risk_pct,
                fractionable=True,
            )
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"size_order raised {type(exc).__name__} on well-formed input")
        assert result.qty >= 0
        if result.error is None:
            assert result.notional_usd >= 0


# ---------------------------------------------------------------------------
# 7. Review finding FLAG #4, sizing half (sleeve-review, commit 2200c66):
# _floor_to_whole_share's fractionable=True branch returns raw_qty verbatim
# with no sign check ("if fractionable: return raw_qty, None") -- a negative
# risk_pct or pct_of_sleeve under fractionable=True currently produces a
# NEGATIVE qty with error=None. Dormant in P1 (fractionable=False is the only
# live path -- AC-7 defaults every entry to a bracket) but a real gap the
# moment anything sets fractionable=True.
# ---------------------------------------------------------------------------


class TestNegativeFractionalInputsAreErrorsNotNegativeQty:
    @pytest.mark.parametrize("fractionable", [True, False])
    def test_negative_risk_pct_is_always_an_explicit_error(self, fractionable):
        result = sizing.size_order(
            mode="risk_pct",
            sleeve_equity=10_000.0,
            price=100.0,
            stop_price=95.0,
            risk_pct=-0.01,
            fractionable=fractionable,
        )
        assert result.error is not None, (
            f"negative risk_pct must always produce an explicit error (fractionable="
            f"{fractionable}); got qty={result.qty}, error={result.error}. The "
            f"fractionable=True path currently skips the floor-to-zero safety net "
            f"entirely, letting a negative input through as a negative qty with "
            f"error=None."
        )
        assert result.qty == 0

    @pytest.mark.parametrize("fractionable", [True, False])
    def test_negative_pct_of_sleeve_is_always_an_explicit_error(self, fractionable):
        result = sizing.size_order(
            mode="pct_of_sleeve",
            sleeve_equity=10_000.0,
            price=100.0,
            pct_of_sleeve=-0.10,
            fractionable=fractionable,
        )
        assert result.error is not None
        assert result.qty == 0

    @pytest.mark.parametrize("fractionable", [True, False])
    def test_negative_dollars_is_always_an_explicit_error(self, fractionable):
        result = sizing.size_order(
            mode="dollars",
            sleeve_equity=10_000.0,
            price=100.0,
            dollars=-500.0,
            fractionable=fractionable,
        )
        assert result.error is not None
        assert result.qty == 0

    @pytest.mark.parametrize("fractionable", [True, False])
    def test_negative_shares_is_always_an_explicit_error(self, fractionable):
        result = sizing.size_order(
            mode="shares",
            sleeve_equity=10_000.0,
            price=100.0,
            shares=-5,
            fractionable=fractionable,
        )
        assert result.error is not None
        assert result.qty == 0

    @given(
        sleeve_equity=st.floats(
            min_value=100.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False
        ),
        price=st.floats(min_value=0.01, max_value=10_000.0, allow_nan=False, allow_infinity=False),
        pct=st.floats(min_value=-1.0, max_value=-0.0001, allow_nan=False, allow_infinity=False),
    )
    def test_negative_pct_of_sleeve_under_fractionable_true_never_yields_negative_qty(
        self, sleeve_equity, price, pct
    ):
        # Widened property-test range vs. the original (which only drew
        # positive pct >= 0.0001) -- this is the exact hypothesis coverage
        # gap sleeve-review identified as letting the bug through unexercised.
        result = sizing.size_order(
            mode="pct_of_sleeve",
            sleeve_equity=sleeve_equity,
            price=price,
            pct_of_sleeve=pct,
            fractionable=True,
        )
        assert result.qty >= 0, (
            f"negative pct_of_sleeve={pct} under fractionable=True produced qty="
            f"{result.qty} (negative) -- must be an explicit error instead."
        )
        if result.qty == 0:
            assert result.error is not None
