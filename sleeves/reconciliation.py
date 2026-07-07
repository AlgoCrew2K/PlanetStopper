"""Tolerance-based broker-truth reconciliation (AC-9).

Pure functions over dicts -- no DB access, no network calls, no imports from
sleeves/alpaca_orders.py. The caller is responsible for fetching sleeve-ledger
state and broker-truth (via sleeves.alpaca_orders.get_account/get_positions)
and passing plain values in; that separation is what makes this module
independently unit-testable and keeps the whole-repo order-endpoint
containment invariant (AC-15) simple -- reconciliation never becomes a second
place capable of reaching the broker.

Verdict is always exactly one of two values -- never a third, partial, or
soft state (AC-9): "OK" or "PAUSED_RECONCILIATION". Any breach -> paused.
Enforcing "no order while paused" is a P3/runner integration concern, out of
P1 scope -- this module only computes the verdict.

Breach vocabulary (machine-readable, appears verbatim as a substring of an
entry in ``.breaches``):
    "unknown_position:<SYMBOL>"  -- broker holds a symbol our ledger has no
                                    record of (orphaned bracket leg / manual
                                    operator intervention at the broker).
    "missing_position:<SYMBOL>"  -- our ledger believes we hold a symbol the
                                    broker has zero (or no) position in.
    "position_drift:<SYMBOL>"    -- both sides show the symbol, but qty
                                    differs beyond the relative tolerance.
    "cash_drift"                 -- cash differs beyond the absolute tolerance.

A symbol present on both sides with qty=0 on both is NOT a breach (a
fully-closed position still tracked by the ledger is expected, not drift).
"""

from __future__ import annotations

from dataclasses import dataclass

_OK = "OK"
_PAUSED = "PAUSED_RECONCILIATION"


@dataclass(frozen=True)
class ReconciliationResult:
    """Verdict of a reconciliation check. ``ok`` is True iff ``breaches`` is empty."""

    ok: bool
    verdict: str
    breaches: list[str]


def reconcile_positions(
    sleeve_positions: dict[str, float],
    broker_positions: dict[str, float],
    tolerance_pct: float,
) -> ReconciliationResult:
    """Diff our ledger's position beliefs against broker-truth positions.

    ``tolerance_pct`` is a relative tolerance (e.g. 0.005 = 0.5%) applied
    against the larger of the two quantities for a symbol present on both
    sides.
    """
    breaches: list[str] = []
    for symbol in sorted(set(sleeve_positions) | set(broker_positions)):
        sleeve_qty = sleeve_positions.get(symbol, 0.0)
        broker_qty = broker_positions.get(symbol, 0.0)

        if symbol not in sleeve_positions:
            if broker_qty != 0.0:
                breaches.append(f"unknown_position:{symbol}")
            continue

        if symbol not in broker_positions:
            if sleeve_qty != 0.0:
                breaches.append(f"missing_position:{symbol}")
            continue

        if sleeve_qty == 0.0 and broker_qty == 0.0:
            continue

        reference = max(abs(sleeve_qty), abs(broker_qty))
        drift = abs(sleeve_qty - broker_qty) / reference
        if drift > tolerance_pct:
            breaches.append(f"position_drift:{symbol}")

    verdict = _PAUSED if breaches else _OK
    return ReconciliationResult(ok=not breaches, verdict=verdict, breaches=breaches)


def reconcile_cash(
    sleeve_cash_usd: float, broker_cash_usd: float, tolerance_usd: float
) -> ReconciliationResult:
    """Diff our ledger's cash belief against broker-truth cash.

    ``tolerance_usd`` is an absolute tolerance (fees/rounding). Drift is
    direction-agnostic -- broker cash being higher OR lower than expected is
    equally a bookkeeping-mismatch breach.
    """
    if abs(sleeve_cash_usd - broker_cash_usd) > tolerance_usd:
        return ReconciliationResult(ok=False, verdict=_PAUSED, breaches=["cash_drift"])
    return ReconciliationResult(ok=True, verdict=_OK, breaches=[])


def reconcile_sleeve(
    *,
    sleeve_positions: dict[str, float],
    broker_positions: dict[str, float],
    sleeve_cash_usd: float,
    broker_cash_usd: float,
    position_tolerance_pct: float,
    cash_tolerance_usd: float,
) -> ReconciliationResult:
    """Combined pre/post-trade reconciliation verdict (AC-9).

    Breaches is the union of the position and cash checks; verdict is
    PAUSED_RECONCILIATION if either check breaches.
    """
    position_result = reconcile_positions(
        sleeve_positions, broker_positions, position_tolerance_pct
    )
    cash_result = reconcile_cash(sleeve_cash_usd, broker_cash_usd, cash_tolerance_usd)
    breaches = position_result.breaches + cash_result.breaches
    verdict = _PAUSED if breaches else _OK
    return ReconciliationResult(ok=not breaches, verdict=verdict, breaches=breaches)
