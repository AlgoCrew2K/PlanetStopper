"""
Risk-based order sizing for managed sleeves (AC-7).

Four sizing modes translate an operator-authored rule action into a proposed
order qty/notional. Sizing PROPOSES a size; sleeves.envelope DISPOSES (clamps
it to the hard box) — this module never enforces envelope limits itself and
never raises (D-1-style never-raises result object, matching
advisors/composer_backtest_client.BacktestResult).

Units: risk_pct and pct_of_sleeve are FRACTIONS (0.01 == 1%), never
percentage points — matches math_engine.py's percent/fraction discipline.
"""

from __future__ import annotations

import dataclasses
import math


@dataclasses.dataclass(frozen=True)
class SizingResult:
    """Result of one size_order() call. error is None on success; qty and
    notional_usd are 0.0 on any error — never a partial/garbage size."""

    qty: float
    notional_usd: float
    mode: str
    error: str | None


def _error_result(mode: str, error: str) -> SizingResult:
    return SizingResult(qty=0.0, notional_usd=0.0, mode=mode, error=error)


def _floor_to_whole_share(raw_qty: float, fractionable: bool) -> tuple[float, str | None]:
    """Apply the whole-share floor required for non-fractionable (bracket)
    entries.

    Alpaca rejects order_class=bracket/oco/oto with a fractional qty (whole
    shares only). Per AC-7 every entry defaults to a bracket, so flooring is
    the default (fractionable=False). Flooring toward zero — never rounding
    up — is the conservative direction: rounding up a riskPct-sized order
    would silently exceed the risk budget the operator configured. A result
    that floors to 0 shares is reported as an explicit error
    ("qty_rounds_to_zero"), never a silent zero-qty order.
    """
    if fractionable:
        return raw_qty, None
    floored = math.floor(raw_qty)
    if floored <= 0:
        return 0.0, "qty_rounds_to_zero"
    return float(floored), None


def size_order(
    *,
    mode: str,
    sleeve_equity: float,
    price: float,
    stop_price: float | None = None,
    risk_pct: float | None = None,
    pct_of_sleeve: float | None = None,
    dollars: float | None = None,
    shares: float | None = None,
    fractionable: bool = False,
) -> SizingResult:
    """Size a proposed order under one of four modes (AC-7):

    risk_pct:      risk_dollars = risk_pct * sleeve_equity
                    qty = risk_dollars / abs(price - stop_price)
    pct_of_sleeve:  notional = pct_of_sleeve * sleeve_equity; qty = notional / price
    dollars:        notional = dollars; qty = notional / price
    shares:         qty = shares directly; notional = qty * price

    dollars/shares modes are NOT capped by sleeve_equity or any envelope
    limit here — sizing computes a raw proposal; sleeves.envelope.clamp_order
    is the sole enforcement point for equity/position/turnover caps.

    Never raises. Every failure path (unknown mode, missing required
    parameter, non-positive price, degenerate stop distance, or a qty that
    floors to zero) returns SizingResult(error=...) instead.
    """
    if price is None or price <= 0:
        return _error_result(mode, "invalid_price")

    if mode == "risk_pct":
        if risk_pct is None or stop_price is None:
            return _error_result(mode, "degenerate_stop_distance")
        stop_distance = abs(price - stop_price)
        if stop_distance <= 0:
            return _error_result(mode, "degenerate_stop_distance")
        risk_dollars = risk_pct * sleeve_equity
        raw_qty = risk_dollars / stop_distance
        qty, error = _floor_to_whole_share(raw_qty, fractionable)
        if error:
            return _error_result(mode, error)
        return SizingResult(qty=qty, notional_usd=qty * price, mode=mode, error=None)

    if mode == "pct_of_sleeve":
        if pct_of_sleeve is None:
            return _error_result(mode, "missing_pct_of_sleeve")
        notional = pct_of_sleeve * sleeve_equity
        raw_qty = notional / price
        qty, error = _floor_to_whole_share(raw_qty, fractionable)
        if error:
            return _error_result(mode, error)
        return SizingResult(qty=qty, notional_usd=qty * price, mode=mode, error=None)

    if mode == "dollars":
        if dollars is None:
            return _error_result(mode, "missing_dollars")
        raw_qty = dollars / price
        qty, error = _floor_to_whole_share(raw_qty, fractionable)
        if error:
            return _error_result(mode, error)
        return SizingResult(qty=qty, notional_usd=qty * price, mode=mode, error=None)

    if mode == "shares":
        if shares is None:
            return _error_result(mode, "missing_shares")
        qty, error = _floor_to_whole_share(shares, fractionable)
        if error:
            return _error_result(mode, error)
        return SizingResult(qty=qty, notional_usd=qty * price, mode=mode, error=None)

    return _error_result(mode, "unknown_mode")
