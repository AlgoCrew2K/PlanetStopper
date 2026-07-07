"""
Sleeve cash/position accounting -- the capital conservation invariant.

sleeves.ledger tracks one sleeve's cash, open reservations, and positions
at cost. Every transition function is pure (state in, state out, no I/O,
no input mutation) so the conservation invariant is testable without a
database. The engine is a fresh subprocess per tick (app.py:572-587,697):
persisting a LedgerState snapshot across ticks is the caller's
responsibility (sleeve-db's accessors reconstruct it from sleeve_orders +
sleeve_fills rows) -- this module has zero I/O of its own.

CONSERVATION LAW (must hold after every legal operation):
    cash_usd + reserved_usd + sum(p.cost_basis_usd for p in positions)
        == capital_usd + realized_pnl_usd
No dollar is created or destroyed by the ledger's own bookkeeping.
"""

from __future__ import annotations

import dataclasses
import math


class InsufficientCashError(Exception):
    """Raised by reserve() when notional_usd exceeds available cash_usd --
    AC-1: a sleeve can never spend beyond its allocation, enforced here at
    the ledger (not merely advised by the envelope)."""


class InsufficientPositionError(Exception):
    """Raised by apply_fill() on a sell whose qty exceeds the sleeve's
    currently held qty for that symbol -- long-only, no shorting."""


def _reject_non_finite(**kwargs: float) -> None:
    """Reject NaN / +Inf / -Inf in named dollar/qty parameters at function
    entry. Ledger math must never silently propagate a non-finite value
    into the capital invariant -- mirrors math_engine.py's identical policy
    for exit-decision math."""
    for name, v in kwargs.items():
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError(f"NaN/Inf input not allowed: {name}={v!r}")


@dataclasses.dataclass(frozen=True)
class Position:
    """A sleeve's holding in one symbol, tracked at cost.

    qty: shares currently held.
    cost_basis_usd: total dollars paid for the currently-held qty (this is
    a TOTAL, not a per-share average -- divide by qty for average cost).
    """

    qty: float
    cost_basis_usd: float


@dataclasses.dataclass(frozen=True)
class LedgerState:
    """A sleeve's cash/position accounting snapshot. Immutable -- every
    transition function below returns a NEW LedgerState.

    capital_usd: fixed at sleeve creation (AC-1), never changes.
    cash_usd: spendable cash right now.
    reserved_usd: dollars set aside for open (unfilled) BUY orders. Sells
        never touch this field -- a sell reserves SHARES (share-availability,
        enforced by sleeves.envelope / the caller), not cash.
    realized_pnl_usd: cumulative realized gain/loss from sells.
    positions: symbol -> Position. A fully-sold-out symbol remains present
        with qty==0, cost_basis_usd==0 (never deleted) so callers can rely
        on a stable per-symbol entry once a position has ever been opened.
    """

    capital_usd: float
    cash_usd: float
    reserved_usd: float
    realized_pnl_usd: float
    positions: dict[str, Position]


def new_ledger(capital_usd: float) -> LedgerState:
    """Initialize a sleeve's ledger at creation (AC-1): cash starts equal
    to the fixed capital allocation; no reservations, no positions, no
    realized P&L yet."""
    _reject_non_finite(capital_usd=capital_usd)
    return LedgerState(
        capital_usd=capital_usd,
        cash_usd=capital_usd,
        reserved_usd=0.0,
        realized_pnl_usd=0.0,
        positions={},
    )


def reserve(ledger: LedgerState, notional_usd: float) -> LedgerState:
    """Move notional_usd from cash into open reservations ahead of
    submitting an order (write-ahead: this must be called, and its result
    durably persisted, BEFORE the broker call -- see sleeves/alpaca_orders.py
    sequencing). Raises InsufficientCashError if notional_usd exceeds
    available cash -- AC-1's "never spend beyond allocation" enforced here,
    not just advised by the envelope clamp.
    """
    _reject_non_finite(notional_usd=notional_usd)
    if notional_usd > ledger.cash_usd:
        raise InsufficientCashError(
            f"cannot reserve {notional_usd!r}: only {ledger.cash_usd!r} cash available"
        )
    return dataclasses.replace(
        ledger,
        cash_usd=ledger.cash_usd - notional_usd,
        reserved_usd=ledger.reserved_usd + notional_usd,
    )


def release(ledger: LedgerState, notional_usd: float) -> LedgerState:
    """Return a reservation to cash -- an order was canceled or rejected
    before (or after a partial) fill. Cancel and reject are modeled
    identically: there is no distinct "reject" transition, both simply
    un-reserve the amount that was never filled.
    """
    _reject_non_finite(notional_usd=notional_usd)
    return dataclasses.replace(
        ledger,
        cash_usd=ledger.cash_usd + notional_usd,
        reserved_usd=ledger.reserved_usd - notional_usd,
    )


def apply_fill(
    ledger: LedgerState,
    *,
    symbol: str,
    side: str,
    qty: float,
    price: float,
    reserved_usd: float,
) -> LedgerState:
    """Apply one fill (or partial fill) to the ledger.

    BUY: the reservation resolves into a position. Any difference between
    the amount reserved (reserved_usd) and the actual fill notional
    (qty * price) returns to cash -- a favorable (cheaper) fill leaves the
    excess spendable rather than vanishing; an unfavorable fill draws cash
    down by the shortfall. reserved_usd is reduced by the same amount that
    was reserved for this fill (the caller passes the per-fill share of a
    (possibly larger) original reservation for partial fills).

    SELL: reduces the position at its average cost basis and realizes the
    gain/loss. Sells do NOT touch reserved_usd -- ledger.py's dollar
    reservation bookkeeping is a buy-side-only concept (a sell's
    share-availability is enforced by sleeves.envelope / the caller, not
    here); callers pass reserved_usd=0.0 for sells. Raises
    InsufficientPositionError if qty exceeds the currently held qty for
    symbol (long-only: no shorting).
    """
    _reject_non_finite(qty=qty, price=price, reserved_usd=reserved_usd)

    if side == "buy":
        fill_notional = qty * price
        existing = ledger.positions.get(symbol, Position(qty=0.0, cost_basis_usd=0.0))
        new_positions = dict(ledger.positions)
        new_positions[symbol] = Position(
            qty=existing.qty + qty,
            cost_basis_usd=existing.cost_basis_usd + fill_notional,
        )
        return dataclasses.replace(
            ledger,
            cash_usd=ledger.cash_usd + (reserved_usd - fill_notional),
            reserved_usd=ledger.reserved_usd - reserved_usd,
            positions=new_positions,
        )

    if side == "sell":
        existing = ledger.positions.get(symbol)
        if existing is None or qty > existing.qty:
            held = existing.qty if existing is not None else 0.0
            raise InsufficientPositionError(f"cannot sell {qty!r} {symbol}: only {held!r} held")
        avg_cost_per_share = existing.cost_basis_usd / existing.qty
        cost_removed = qty * avg_cost_per_share
        proceeds = qty * price
        new_positions = dict(ledger.positions)
        new_positions[symbol] = Position(
            qty=existing.qty - qty,
            cost_basis_usd=existing.cost_basis_usd - cost_removed,
        )
        return dataclasses.replace(
            ledger,
            cash_usd=ledger.cash_usd + proceeds,
            realized_pnl_usd=ledger.realized_pnl_usd + (proceeds - cost_removed),
            positions=new_positions,
        )

    raise ValueError(f"side must be 'buy' or 'sell'; got {side!r}")
