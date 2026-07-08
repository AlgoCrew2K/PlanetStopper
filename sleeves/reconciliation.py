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


# ---------------------------------------------------------------------------
# Shared-account reconciliation semantics (BLOCK: s3-ux live finding + PM
# ruling, 2026-07-08). The broker has no concept of our virtual per-sleeve
# partitioning -- only ONE account-level cash figure and ONE position per
# symbol exist, shared by every sleeve. reconcile_sleeve/reconcile_positions/
# reconcile_cash above compare ONE sleeve's belief against the WHOLE account
# and are the wrong model once more than one sleeve exists (a live 7-sleeve
# render reproduced exactly this: every sleeve paused within one tick, since
# each sleeve's own necessarily-smaller capital differed from the full
# account total by far more than any tolerance). The two functions below are
# ONE-SIDED, aggregate, shared-account-aware replacements: the money-safety
# invariant that actually matters is sleeves never COLLECTIVELY claiming more
# than the account truly holds -- the reverse (unallocated float, or the
# operator's own money/positions sharing the account) is normal and must
# never breach. sleeves/tick_orchestrator.py computes the aggregate inputs
# (summed across every non-already-paused sleeve) and calls these once per
# tick, never per sleeve.
# ---------------------------------------------------------------------------


def reconcile_aggregate_cash(
    *,
    total_sleeve_cash_claim_usd: float,
    broker_cash_usd: float,
    cash_tolerance_usd: float,
) -> ReconciliationResult:
    """One-sided aggregate cash check across every sleeve sharing one account.

    Breach iff the sleeves' COMBINED cash claim exceeds the broker's own
    cash figure beyond ``cash_tolerance_usd``. The reverse -- broker cash
    exceeding the combined claim -- is explicitly NOT a breach: unallocated
    float, or the operator's own money sharing the same account, is normal.
    """
    if total_sleeve_cash_claim_usd > broker_cash_usd + cash_tolerance_usd:
        return ReconciliationResult(
            ok=False, verdict=_PAUSED, breaches=["aggregate_cash_exceeds_account"]
        )
    return ReconciliationResult(ok=True, verdict=_OK, breaches=[])


def reconcile_aggregate_position(
    *,
    symbol: str,
    total_sleeve_qty: float,
    broker_qty: float,
    position_tolerance_pct: float,
) -> ReconciliationResult:
    """One-sided, per-symbol aggregate position check across every sleeve.

    Breach iff the sleeves' COMBINED qty for this symbol (summed across
    every sleeve with any history in it) exceeds the broker's own qty for
    that symbol beyond a relative tolerance of ``broker_qty``.
    ``total_sleeve_qty <= broker_qty`` is NEVER a breach -- broker surplus in
    a symbol (an operator-external holding sharing the account, or a symbol
    no sleeve tracks at all, i.e. ``total_sleeve_qty == 0``) is ignored by
    design. A sleeve claim against a broker qty of exactly zero always
    breaches when the claim is nonzero (there is no meaningful percentage of
    zero, mirroring reconcile_positions' missing_position semantics).
    """
    if total_sleeve_qty > broker_qty * (1.0 + position_tolerance_pct):
        return ReconciliationResult(
            ok=False,
            verdict=_PAUSED,
            breaches=[f"aggregate_position_exceeds_broker:{symbol}"],
        )
    return ReconciliationResult(ok=True, verdict=_OK, breaches=[])
