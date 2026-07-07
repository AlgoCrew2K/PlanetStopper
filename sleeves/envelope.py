"""
The envelope hard box for managed sleeves (AC-2, AC-3).

sleeves.envelope.clamp_order is the SOLE enforcement point for a sleeve's
risk limits: ticker allowlist, max single-position % of sleeve equity,
per-order dollar cap, max daily turnover, and long-only/no-shorting. It is a
pure function (no I/O, no state) — every clamp/refusal decision is returned
to the caller as data; persisting the decision (e.g. into sleeve_rule_fires)
is the caller's job, not this module's.

Reduce-only semantics (AC-2): clamp_order NEVER returns qty > the requested
qty. Every limit can only shrink the order. If shrinking every applicable
limit to its floor would leave qty <= 0, the order is REFUSED
(approved=False, qty=0) rather than silently sent at a smaller-but-nonzero
size that the caller didn't ask for -- REASON_REDUCED_TO_ZERO reports this
specific outcome, distinct from a categorical refusal like an allowlist miss.
"""

from __future__ import annotations

import dataclasses
import math

# Reason codes (AC-2 "every clamp/refusal logged with reason"). Machine
# readable; the caller (P2/P3 runner) persists these into sleeve_rule_fires.
REASON_NOT_IN_ALLOWLIST = "NOT_IN_ALLOWLIST"
REASON_MAX_POSITION_PCT = "MAX_POSITION_PCT"
REASON_MAX_ORDER_USD = "MAX_ORDER_USD"
REASON_MAX_DAILY_TURNOVER = "MAX_DAILY_TURNOVER"
REASON_LONG_ONLY_NO_SHORT = "LONG_ONLY_NO_SHORT"
# Reported instead of a specific limit's reason when clamping that limit to
# its floor would leave qty <= 0 -- the order is refused outright rather
# than silently sent at a size the caller never requested.
REASON_REDUCED_TO_ZERO = "REDUCED_TO_ZERO"


@dataclasses.dataclass(frozen=True)
class ClampResult:
    """Outcome of clamping one proposed order against a sleeve's envelope.

    approved: False means refuse the order outright (qty == 0).
    qty: final qty to submit; NEVER greater than original_qty.
    clamped: True iff qty != original_qty (a limit reduced the size).
    reason: populated whenever clamped is True or approved is False; None
            for an order that passes through completely unclamped.
    """

    approved: bool
    qty: float
    original_qty: float
    clamped: bool
    reason: str | None


def clamp_order(
    *,
    symbol: str,
    side: str,
    qty: float,
    price: float,
    envelope: dict,
    sleeve_equity: float,
    current_position_qty: float = 0.0,
    turnover_used_usd: float = 0.0,
) -> ClampResult:
    """Clamp a proposed order to the sleeve's envelope (the hard box).

    envelope shape (operator-authored, schema-validated in P2):
        {"allowlist": [...], "max_position_pct": float, "max_order_usd": float,
         "max_daily_turnover_usd": float, "long_only": bool}

    Processing order:
      1. Allowlist -- a categorical gate, not a magnitude clamp. A symbol
         outside the allowlist is refused outright with its own specific
         reason (REASON_NOT_IN_ALLOWLIST), independent of qty magnitude.
      2. Per-side magnitude caps, each expressed as a ceiling on qty:
         - sell: capped at current_position_qty (long-only -- v1 never
           permits shorting, so this cap always applies on the sell side).
         - buy: capped by max_position_pct's remaining room
           (max_position_pct * sleeve_equity / price - current_position_qty).
         - both sides: capped by max_order_usd / price and by the
           remaining max_daily_turnover_usd budget / price.
         Each cap is floored to a whole share (orders are whole-share; see
         sleeves.sizing for the same floor-toward-zero discipline) and the
         TIGHTEST cap wins via sequential min-reduction.
      3. If the magnitude clamping above reduces qty to <= 0, the order is
         refused and the reason is normalized to REASON_REDUCED_TO_ZERO --
         regardless of which specific limit was the one that hit zero.
    """
    original_qty = float(qty)

    allowlist = envelope.get("allowlist", [])
    if symbol not in allowlist:
        return ClampResult(
            approved=False,
            qty=0.0,
            original_qty=original_qty,
            clamped=True,
            reason=REASON_NOT_IN_ALLOWLIST,
        )

    # Each candidate is (reason, max_allowed_qty) -- a ceiling this single
    # limit dimension places on qty. Reduce-only: candidates only ever
    # shrink the running qty via sequential min-reduction below.
    candidates: list[tuple[str, float]] = []

    if side == "sell":
        candidates.append((REASON_LONG_ONLY_NO_SHORT, max(current_position_qty, 0.0)))
    elif side == "buy":
        max_position_pct = envelope.get("max_position_pct")
        if max_position_pct is not None and price > 0:
            max_position_notional = max_position_pct * sleeve_equity
            max_total_qty = max_position_notional / price
            candidates.append(
                (REASON_MAX_POSITION_PCT, max(max_total_qty - current_position_qty, 0.0))
            )

    max_order_usd = envelope.get("max_order_usd")
    if max_order_usd is not None and price > 0:
        candidates.append((REASON_MAX_ORDER_USD, max_order_usd / price))

    max_daily_turnover_usd = envelope.get("max_daily_turnover_usd")
    if max_daily_turnover_usd is not None and price > 0:
        remaining_turnover = max(max_daily_turnover_usd - turnover_used_usd, 0.0)
        candidates.append((REASON_MAX_DAILY_TURNOVER, remaining_turnover / price))

    qty = original_qty
    reason: str | None = None
    clamped = False
    for cand_reason, raw_cap in candidates:
        cap = math.floor(raw_cap)
        if cap < qty:
            qty = float(cap)
            reason = cand_reason
            clamped = True

    if qty <= 0:
        return ClampResult(
            approved=False,
            qty=0.0,
            original_qty=original_qty,
            clamped=True,
            reason=REASON_REDUCED_TO_ZERO,
        )

    return ClampResult(
        approved=True,
        qty=qty,
        original_qty=original_qty,
        clamped=clamped,
        reason=reason,
    )


def is_envelope_widened(old_envelope: dict, new_envelope: dict) -> bool:
    """True iff new_envelope is LESS restrictive than old_envelope in any
    dimension -- used by the P3 arming route to decide whether the
    widen-requires-re-ceremony gate (AC-3) fires. Narrowing (or an identical
    envelope) returns False; any single widened dimension returns True.
    """
    old_allowlist = set(old_envelope.get("allowlist", []))
    new_allowlist = set(new_envelope.get("allowlist", []))
    if not new_allowlist.issubset(old_allowlist):
        return True

    for key in ("max_position_pct", "max_order_usd", "max_daily_turnover_usd"):
        old_val = old_envelope.get(key)
        new_val = new_envelope.get(key)
        if old_val is not None and new_val is not None and new_val > old_val:
            return True

    old_long_only = old_envelope.get("long_only", True)
    new_long_only = new_envelope.get("long_only", True)
    return bool(old_long_only and not new_long_only)
