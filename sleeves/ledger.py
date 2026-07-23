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
    not just advised by the envelope clamp. Raises ValueError if
    notional_usd is not strictly positive -- a zero-or-negative reservation
    is meaningless (no order is free) and likely signals a caller bug (e.g.
    an uninitialized price variable) rather than a legitimate no-op.
    """
    _reject_non_finite(notional_usd=notional_usd)
    if notional_usd <= 0:
        raise ValueError(f"notional_usd must be > 0; got {notional_usd!r}")
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
    symbol, OR if the symbol's position is already fully sold out
    (qty <= 0) -- this also covers a qty=0 sell against a sold-out
    position, which would otherwise divide by zero computing an average
    cost per share that no longer exists (long-only: no shorting).

    Raises ValueError if price is not strictly positive, or if qty is
    negative. qty == 0 is permitted (a degenerate no-op fill) specifically
    so that a zero-qty sell reaches the InsufficientPositionError check
    above rather than being rejected here with an undifferentiated error.
    """
    _reject_non_finite(qty=qty, price=price, reserved_usd=reserved_usd)
    if qty < 0:
        raise ValueError(f"qty must be >= 0; got {qty!r}")
    if price <= 0:
        raise ValueError(f"price must be > 0; got {price!r}")

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
        if existing is None or existing.qty <= 0 or qty > existing.qty:
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


# Identical denylist to database.get_daily_turnover_usd's own fail-closed
# convention (database.py) -- an unrecognized/future order status is treated
# as STILL reserving (the conservative, over-reserve direction), never
# silently released.
_RESERVATION_TERMINAL_STATUSES = (
    "filled",
    "canceled",
    "expired",
    "replaced",
    "done_for_day",
    "rejected",
)


def reconstruct_from_history(capital_usd: float, order_history: list[dict]) -> LedgerState:
    """Fold a sleeve's full order+fill history into its CURRENT LedgerState.

    Zero I/O -- the caller fetches `database.get_sleeve_order_history(sleeve_id)`
    (oldest-submitted-first, each order's fills oldest-filled-first) and
    passes the result straight through. This is what lets a fresh-subprocess-
    per-tick engine know how much cash is actually available right now,
    without this module ever persisting a LedgerState snapshot of its own.

    Per buy order (reserved_price is not None -- a sell's reserved_price is
    always NULL, since sells reserve SHARES not cash): reserve() the full
    qty*reserved_price at fold-start (the reservation made before the broker
    call, per sleeves/alpaca_orders.py's invariant #6), apply_fill() each
    fill in order with reserved_usd proportional to that fill's share of the
    original reservation, then -- only if the order's current status is in
    _RESERVATION_TERMINAL_STATUSES and qty was not fully filled -- release()
    the unfilled remainder (reject/cancel/expire all release identically).
    A sell order never reserves; each of its fills applies directly with
    reserved_usd=0.0.
    """
    state = new_ledger(capital_usd)
    for order in order_history:
        symbol = order["symbol"]
        side = order["side"]
        qty = order["qty"]
        reserved_price = order.get("reserved_price")
        fills = order.get("fills") or []

        if side == "buy" and reserved_price is not None:
            state = reserve(state, qty * reserved_price)
            filled_qty_total = 0.0
            for fill in fills:
                filled_qty = fill["filled_qty"]
                state = apply_fill(
                    state,
                    symbol=symbol,
                    side="buy",
                    qty=filled_qty,
                    price=fill["fill_price"],
                    reserved_usd=filled_qty * reserved_price,
                )
                filled_qty_total += filled_qty
            unfilled_qty = qty - filled_qty_total
            if order.get("status") in _RESERVATION_TERMINAL_STATUSES and unfilled_qty > 0:
                state = release(state, unfilled_qty * reserved_price)
        else:
            for fill in fills:
                state = apply_fill(
                    state,
                    symbol=symbol,
                    side=side,
                    qty=fill["filled_qty"],
                    price=fill["fill_price"],
                    reserved_usd=0.0,
                )
    return state


@dataclasses.dataclass(frozen=True)
class RealizedFillAttribution:
    """One sell fill's realized P&L portion, attributed to the rule whose BUY
    lots it closes (buy-side attribution -- see attribute_realized_fills).

    opening_rule_id: the sleeve_orders.rule_id of the BUY that opened the lots
        this portion closes; None for an unattributed (NULL rule_id) buy.
    qty: the portion of the sell fill's qty drawn from this rule's lots.
    realized_delta_usd: proceeds minus average-cost basis for that portion.
    filled_at: the SELL fill's filled_at, UTC verbatim as stored -- callers
        needing a trading-day cut convert to ET themselves.
    """

    opening_rule_id: int | None
    symbol: str
    qty: float
    realized_delta_usd: float
    filled_at: str | None


def attribute_realized_fills(order_history: list[dict]) -> list[RealizedFillAttribution]:
    """Fold a sleeve's order+fill history into fill-level realized-P&L records
    attributed to the rule that OPENED the lots being closed (buy-side
    attribution) -- the per-rule P&L truth behind the dashboard panel, the EOD
    digest, and the churn brake's net-loss test (audit findings #7/#8).

    ATTRIBUTION LAW: realized P&L belongs to the ENTRY rule whose buy fills
    created the position, not to the rule whose sell closed it -- a defensive
    go_to_cash rule selling an entry rule's position is the DESIGN case, and
    its value is loss avoidance, not realized alpha. Each sell fill is
    prorated across the opening rules' lot buckets by held-qty share at each
    bucket's own average cost; the last drawn-from bucket receives the exact
    remainder (clamped to its holding) so the sold qty is conserved exactly in
    float -- naive per-bucket proration drifts by ulps per sell and could make
    a full flatten spuriously raise on a history reconstruct_from_history
    replays cleanly.

    CONSERVATION IDENTITY: sum(r.realized_delta_usd for r in records) equals
    reconstruct_from_history(capital, order_history).realized_pnl_usd -- the
    qty-share proration at per-bucket average cost algebraically reproduces
    apply_fill's symbol-wide average-cost booking (compare approximately;
    float summation order differs).

    Same ordering caveat as reconstruct_from_history: replay follows
    order-history order (orders oldest-submitted first, fills oldest-first per
    order), not the wall-clock interleave of fills across orders.

    Records deliberately carry NO buy-side timing -- lot buckets mix buy days,
    so a per-record "opened today" is ill-defined; round-trip counting derives
    from sleeve_orders/sleeve_fills directly, never from this fold.

    Raises InsufficientPositionError on a sell exceeding the symbol's held qty
    (guarded at the SYMBOL level, mirroring apply_fill) and ValueError on
    degenerate qty/price -- callers must surface a distinct "n/a" marker on
    failure, never a fabricated $0.00 (a value claim, the exact bug this fold
    replaces). Pure, zero I/O; input is not mutated.
    """
    # symbol -> {opening_rule_id: [held_qty, cost_basis_usd]} (insertion order
    # = first-buy order, which fixes which bucket is "last drawn-from").
    lot_buckets: dict[str, dict] = {}
    # Symbol-level running qty maintained exactly the way apply_fill maintains
    # Position.qty, so the over-sell guard can never fire on a history the
    # LedgerState fold accepts.
    held_qty: dict[str, float] = {}
    records: list[RealizedFillAttribution] = []

    for order in order_history:
        symbol = order["symbol"]
        side = order["side"]
        rule_id = order.get("rule_id")
        for fill in order.get("fills") or []:
            qty = fill["filled_qty"]
            price = fill["fill_price"]
            _reject_non_finite(qty=qty, price=price)
            if qty < 0:
                raise ValueError(f"filled_qty must be >= 0; got {qty!r}")
            if price <= 0:
                raise ValueError(f"fill_price must be > 0; got {price!r}")

            if side == "buy":
                bucket = lot_buckets.setdefault(symbol, {}).setdefault(rule_id, [0.0, 0.0])
                bucket[0] += qty
                bucket[1] += qty * price
                held_qty[symbol] = held_qty.get(symbol, 0.0) + qty
                continue
            if side != "sell":
                raise ValueError(f"side must be 'buy' or 'sell'; got {side!r}")

            total_held = held_qty.get(symbol, 0.0)
            if total_held <= 0 or qty > total_held:
                raise InsufficientPositionError(
                    f"cannot sell {qty!r} {symbol}: only {total_held!r} held"
                )
            drawn = [
                (opening_rule_id, bucket)
                for opening_rule_id, bucket in lot_buckets.get(symbol, {}).items()
                if bucket[0] > 0
            ]
            bucket_total = sum(bucket[0] for _, bucket in drawn)
            allocated = 0.0
            for i, (opening_rule_id, bucket) in enumerate(drawn):
                if i < len(drawn) - 1:
                    sold = qty * (bucket[0] / bucket_total)
                else:
                    sold = min(max(qty - allocated, 0.0), bucket[0])
                if sold <= 0:
                    continue
                avg_cost_per_share = bucket[1] / bucket[0]
                cost_removed = sold * avg_cost_per_share
                records.append(
                    RealizedFillAttribution(
                        opening_rule_id=opening_rule_id,
                        symbol=symbol,
                        qty=sold,
                        realized_delta_usd=sold * price - cost_removed,
                        filled_at=fill.get("filled_at"),
                    )
                )
                bucket[0] -= sold
                bucket[1] -= cost_removed
                allocated += sold
            held_qty[symbol] = total_held - qty
    return records
