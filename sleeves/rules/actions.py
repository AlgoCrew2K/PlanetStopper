"""sleeves/rules/actions.py -- action dispatch (AC-1, AC-6, AC-7, AC-19, Security).

`dispatch_action` is the ONE place a rule's `then` action turns into either a
"would-have-ordered" shadow record or a real broker call. The load-bearing
security property (plan's "envelope clamp is structurally on every order
path"): for "buy"/"sell"/"go_to_cash", this module ALWAYS calls
sleeves.sizing.size_order, THEN sleeves.envelope.clamp_order on the result,
and may only reach sleeves.alpaca_orders (when shadow=False) using the qty
envelope.clamp_order returned -- never the raw sizing qty, and never when the
clamp refused.

PM ruling (2026-07-08, AC-1/AC-19) closing a wiring gap s2-review found: the
armed path now also (1) enforces cash-safety via sleeves.ledger before ANY
order is placed -- a "buy" reconstructs current ledger state from the
sleeve's real order/fill history and attempts a reservation; insufficient
cash refuses with zero broker calls and zero DB writes, in both shadow and
armed mode -- and (2) durably records the P1 invariant #6 reservation
sequence for every order-placing action: a RESERVED sleeve_orders row is
inserted (with a freshly minted client_order_id) BEFORE the broker call,
then attached to the broker's own order id on ack, or marked terminal on
rejection (which IS how a reservation is released -- LedgerState is never
itself persisted, only reconstructed from order/fill rows).
"""

from __future__ import annotations

import contextlib
import decimal
import uuid
from dataclasses import dataclass

import requests

import database
import sleeves.alpaca_orders as alpaca_orders
import sleeves.envelope as envelope
import sleeves.ledger as ledger
import sleeves.sizing as sizing

# Matches sleeves/alpaca_orders.py's own explicit-timeout convention -- a
# Discord notify call must never hang the tick indefinitely.
_NOTIFY_REQUEST_TIMEOUT_S = 10.0

_NOTIFY_FIELD_WHITELIST = frozenset(
    {"symbol", "action", "qty", "price", "reason", "rule_name", "sleeve_name"}
)

_REASON_INSUFFICIENT_CASH = "insufficient_cash"  # mirrors envelope.py's REASON_* naming

# PM decision (2026-07-08, AC-7): a "buy" action MUST declare its own exit
# distance (schema.py enforces stop_loss_pct/trailing_stop_pct presence at
# authoring time) -- there is no fallback/default stop or take-profit
# percentage here. When the rule declares no take_profit_pct, the
# take-profit distance is still derived, but ONLY as a fixed multiple of
# the rule's OWN declared stop distance -- never an independent absolute
# percentage disconnected from the trade's own risk. 2:1 reward:risk is a
# standard position-sizing heuristic (Van Tharp); it scales with whatever
# stop the rule declares, so it is not itself a "default risk parameter" in
# the sense the PM decision forbids -- it never substitutes for a missing
# stop, only for a missing take-profit target.
_TAKE_PROFIT_REWARD_RISK_RATIO = 2.0


@dataclass(frozen=True)
class ActionContext:
    sleeve_id: int
    rule_id: int
    symbol: str
    price: float
    sleeve_equity_usd: float
    capital_usd: float
    current_position_qty: float
    turnover_used_usd: float
    envelope: dict
    live_mode: bool = False
    live_keys_present: bool = False
    discord_webhook_url: str | None = None


@dataclass(frozen=True)
class ActionResult:
    action_type: str
    would_have_qty: float | None
    would_have_notional_usd: float | None
    executed: bool
    order_result: alpaca_orders.OrderResult | None
    clamp: envelope.ClampResult | None
    refused_reason: str | None
    order_id: int | None = None  # internal sleeve_orders.id (AC-19 fire trace);
    # None for shadow / refused / no-order (notify) outcomes.


def _size_and_clamp(
    action: dict, *, ctx: ActionContext, side: str, stop_price: float | None = None
) -> tuple[envelope.ClampResult, None] | tuple[None, str]:
    """Returns (clamp_result, None) on a successful size+clamp, or
    (None, error) if sizing itself failed before a clamp was even attempted.

    ``stop_price``, when supplied, feeds sizing.size_order's own stop_price
    parameter (required by risk_pct mode's qty = risk_dollars / stop_distance
    formula) -- for a "buy", this is derived from the action's OWN declared
    stop_loss_pct/trailing_stop_pct (see _dispatch_buy), never a separate
    sizing.stop_price field.
    """
    sizing_spec = action.get("sizing") or {}
    sizing_result = sizing.size_order(
        mode=sizing_spec.get("mode"),
        sleeve_equity=ctx.sleeve_equity_usd,
        price=ctx.price,
        stop_price=stop_price,
        risk_pct=sizing_spec.get("risk_pct"),
        pct_of_sleeve=sizing_spec.get("pct_of_sleeve"),
        dollars=sizing_spec.get("dollars"),
        shares=sizing_spec.get("shares"),
    )
    if sizing_result.error is not None:
        return None, sizing_result.error
    clamp_result = envelope.clamp_order(
        symbol=ctx.symbol,
        side=side,
        qty=sizing_result.qty,
        price=ctx.price,
        envelope=ctx.envelope,
        sleeve_equity=ctx.sleeve_equity_usd,
        current_position_qty=ctx.current_position_qty,
        turnover_used_usd=ctx.turnover_used_usd,
    )
    return clamp_result, None


def _place_order_with_reservation(
    *,
    ctx: ActionContext,
    symbol: str,
    side: str,
    qty: float,
    order_class: str,
    reserved_price: float | None,
    submit_fn,
) -> tuple[int, alpaca_orders.OrderResult]:
    """P1 invariant #6 (lost-ack recovery), applied to every order-placing
    action: insert a RESERVED sleeve_orders row using a client_order_id
    minted HERE, before the broker call; on ack, attach the broker's own
    order id; on rejection, mark the row terminal -- this IS the "release
    the reservation" mechanism, since LedgerState is never itself persisted,
    only reconstructed from order/fill rows (a terminal row is what the next
    reconstruct_from_history() call treats as released).

    ``submit_fn`` receives the minted client_order_id and must return an
    alpaca_orders.OrderResult.
    """
    client_order_id = uuid.uuid4().hex
    order_id = database.insert_sleeve_order(
        client_order_id=client_order_id,
        sleeve_id=ctx.sleeve_id,
        rule_id=ctx.rule_id,
        symbol=symbol,
        side=side,
        qty=qty,
        order_class=order_class,
        status="RESERVED",
        reserved_price=reserved_price,
    )
    order_result = submit_fn(client_order_id)
    if order_result.error is None:
        alpaca_order_id = (order_result.order or {}).get("id")
        database.attach_alpaca_order_id(
            client_order_id=client_order_id, alpaca_order_id=alpaca_order_id
        )
    elif order_result.error.startswith("timeout"):
        # INDETERMINATE, not a rejection (audit 2026-07-09 #10):
        # sleeves/alpaca_orders.py converts requests.Timeout to a
        # "timeout: ..." error precisely because the server may still be
        # processing the original request -- the broker order may be LIVE.
        # Marking the row terminal would release the cash reservation while
        # the broker may be spending it. Resolve via the client_order_id
        # minted above (lost-ack recovery): found -> the normal ack path,
        # one step late; HTTP 404 -> the broker definitively never saw the
        # order (terminal "rejected" releases the reservation -- holding
        # cash for a proven-nonexistent order strands it forever); any
        # other lookup outcome -> the row stays RESERVED (non-terminal,
        # reservation held, the fail-closed direction) and the next tick's
        # poll_and_apply_fills retries the same recovery.
        recovery = alpaca_orders.get_order_by_client_order_id(
            client_order_id=client_order_id,
            live_mode=ctx.live_mode,
            live_keys_present=ctx.live_keys_present,
        )
        if recovery.error is None and recovery.order:
            database.attach_alpaca_order_id(
                client_order_id=client_order_id,
                alpaca_order_id=recovery.order.get("id"),
            )
        elif recovery.error == "HTTP 404":
            database.update_sleeve_order_status(client_order_id=client_order_id, status="rejected")
    else:
        # Unambiguous rejection (an HTTP status the broker actually
        # returned, or transport failure before the request ever went out
        # after retries). Reject and cancel are modeled identically by
        # ledger.release() -- any terminal status makes the next
        # reconstruction release this row's unfilled reservation.
        database.update_sleeve_order_status(client_order_id=client_order_id, status="rejected")
    return order_id, order_result


def _dispatch_buy(action: dict, *, ctx: ActionContext, shadow: bool) -> ActionResult:
    # schema.py guarantees a schema-valid "buy" declares at least one of
    # these, each in (0, 1) -- this module trusts that shape and never
    # falls back to a default of its own (PM decision, AC-7).
    declared_stop_pct = action.get("stop_loss_pct") or action.get("trailing_stop_pct")
    # Audit 2026-07-09 #5/#19: the stop used for SIZING and the stop the
    # broker EXECUTES must be the SAME number, or the worst-case loss at the
    # broker's (floored) stop exceeds the declared risk budget by up to
    # tick/stop-distance -- unbounded as stops tighten (repro: +33% on a
    # $100 budget). Derive the stop in exact decimal arithmetic (both inputs
    # are operator-authored short decimals; float multiplication lands 1 ulp
    # low often enough that ROUND_FLOOR drops mathematically exact on-tick
    # stops a full tick -- finding #19), floor it to the broker's equity
    # tick FIRST via the single shared rounding authority, then feed the
    # rounded value to sizing and the order submission alike.
    exact_stop = decimal.Decimal(str(ctx.price)) * (1 - decimal.Decimal(str(declared_stop_pct)))
    stop_price = alpaca_orders.round_to_equity_tick(exact_stop, rounding="floor")

    clamp_result, sizing_error = _size_and_clamp(action, ctx=ctx, side="buy", stop_price=stop_price)
    if sizing_error is not None:
        return ActionResult("buy", None, None, False, None, None, sizing_error)

    would_have_qty = clamp_result.qty
    would_have_notional = clamp_result.qty * ctx.price
    if not clamp_result.approved:
        return ActionResult(
            "buy",
            would_have_qty,
            would_have_notional,
            False,
            None,
            clamp_result,
            clamp_result.reason,
        )

    # AC-1 cash-safety: reconstruct the sleeve's REAL current ledger state
    # (from its actual order/fill history, never a caller-supplied belief)
    # and attempt the reservation -- computed identically in shadow and
    # armed mode so a SHADOW fire's snapshot reflects the true would-have-
    # been outcome. Zero broker calls / zero sleeve_orders writes either way.
    ledger_state = ledger.reconstruct_from_history(
        ctx.capital_usd, database.get_sleeve_order_history(ctx.sleeve_id)
    )
    try:
        ledger.reserve(ledger_state, clamp_result.qty * ctx.price)
    except ledger.InsufficientCashError:
        return ActionResult(
            "buy",
            would_have_qty,
            would_have_notional,
            False,
            None,
            clamp_result,
            _REASON_INSUFFICIENT_CASH,
        )

    if shadow:
        return ActionResult(
            "buy", would_have_qty, would_have_notional, False, None, clamp_result, None
        )

    declared_take_profit_pct = action.get("take_profit_pct")
    if declared_take_profit_pct is not None:
        take_profit_price = ctx.price * (1 + declared_take_profit_pct)
    else:
        stop_distance = ctx.price - stop_price
        take_profit_price = ctx.price + stop_distance * _TAKE_PROFIT_REWARD_RISK_RATIO

    order_id, order_result = _place_order_with_reservation(
        ctx=ctx,
        symbol=ctx.symbol,
        side="buy",
        qty=clamp_result.qty,
        order_class="bracket",
        reserved_price=ctx.price,
        submit_fn=lambda client_order_id: alpaca_orders.submit_bracket_order(
            symbol=ctx.symbol,
            qty=clamp_result.qty,
            side="buy",
            take_profit_price=take_profit_price,
            stop_loss_price=stop_price,
            client_order_id=client_order_id,
            live_mode=ctx.live_mode,
            live_keys_present=ctx.live_keys_present,
        ),
    )
    return ActionResult(
        "buy",
        would_have_qty,
        would_have_notional,
        order_result.error is None,
        order_result,
        clamp_result,
        order_result.error,
        order_id=order_id,
    )


def _dispatch_sell(action: dict, *, ctx: ActionContext, shadow: bool) -> ActionResult:
    clamp_result, sizing_error = _size_and_clamp(action, ctx=ctx, side="sell")
    if sizing_error is not None:
        return ActionResult("sell", None, None, False, None, None, sizing_error)
    return _finish_sell_like("sell", clamp_result, ctx=ctx, shadow=shadow)


def _finish_sell_like(
    action_type: str, clamp_result: envelope.ClampResult, *, ctx: ActionContext, shadow: bool
) -> ActionResult:
    would_have_qty = clamp_result.qty
    would_have_notional = clamp_result.qty * ctx.price
    if not clamp_result.approved:
        return ActionResult(
            action_type,
            would_have_qty,
            would_have_notional,
            False,
            None,
            clamp_result,
            clamp_result.reason,
        )
    if shadow:
        return ActionResult(
            action_type, would_have_qty, would_have_notional, False, None, clamp_result, None
        )

    # sell/go_to_cash never reserve cash (ledger.py's reservation bookkeeping
    # is buy-side-only) but still get the SAME reservation-row sequence as
    # any other order-placing action (P1 invariant #6 is not entry-only).
    order_id, order_result = _place_order_with_reservation(
        ctx=ctx,
        symbol=ctx.symbol,
        side="sell",
        qty=clamp_result.qty,
        order_class="simple",
        reserved_price=None,
        submit_fn=lambda client_order_id: alpaca_orders.submit_order(
            symbol=ctx.symbol,
            qty=clamp_result.qty,
            side="sell",
            client_order_id=client_order_id,
            live_mode=ctx.live_mode,
            live_keys_present=ctx.live_keys_present,
        ),
    )
    return ActionResult(
        action_type,
        would_have_qty,
        would_have_notional,
        order_result.error is None,
        order_result,
        clamp_result,
        order_result.error,
        order_id=order_id,
    )


def _dispatch_go_to_cash(action: dict, *, ctx: ActionContext, shadow: bool) -> ActionResult:
    # Always sizes to the FULL current position -- never consults
    # sizing.size_order, and ignores a present-but-irrelevant `sizing` field.
    clamp_result = envelope.clamp_order(
        symbol=ctx.symbol,
        side="sell",
        qty=ctx.current_position_qty,
        price=ctx.price,
        envelope=ctx.envelope,
        sleeve_equity=ctx.sleeve_equity_usd,
        current_position_qty=ctx.current_position_qty,
        turnover_used_usd=ctx.turnover_used_usd,
    )
    return _finish_sell_like("go_to_cash", clamp_result, ctx=ctx, shadow=shadow)


def _dispatch_set_stop(action: dict, *, ctx: ActionContext, shadow: bool) -> ActionResult:
    # No sizing/clamp -- protects an EXISTING position, not a new entry.
    qty = ctx.current_position_qty
    would_have_notional = qty * ctx.price
    if shadow:
        return ActionResult("set_stop", qty, would_have_notional, False, None, None, None)

    order_id, order_result = _place_order_with_reservation(
        ctx=ctx,
        symbol=ctx.symbol,
        side="sell",
        qty=qty,
        order_class="trailing_stop",
        reserved_price=None,
        submit_fn=lambda client_order_id: alpaca_orders.submit_trailing_stop_order(
            symbol=ctx.symbol,
            qty=qty,
            side="sell",
            trail_percent=action.get("trail_percent"),
            trail_price=action.get("trail_price"),
            client_order_id=client_order_id,
            live_mode=ctx.live_mode,
            live_keys_present=ctx.live_keys_present,
        ),
    )
    return ActionResult(
        "set_stop",
        qty,
        would_have_notional,
        order_result.error is None,
        order_result,
        None,
        order_result.error,
        order_id=order_id,
    )


def _dispatch_notify(action: dict, *, ctx: ActionContext) -> ActionResult:
    # Never touches sleeves.alpaca_orders, shadow or armed -- a notify is a
    # pure side-effect, never a trade action, and gets no order row.
    raw_fields = action.get("fields") or {}
    # Defense-in-depth beyond schema.py's authoring-time whitelist check:
    # drop any non-whitelisted key even if it somehow reached this call.
    payload = {k: v for k, v in raw_fields.items() if k in _NOTIFY_FIELD_WHITELIST}
    if ctx.discord_webhook_url:
        # A Discord delivery failure must never break the tick.
        with contextlib.suppress(requests.RequestException):
            requests.post(
                ctx.discord_webhook_url,
                json={"content": str(payload)},
                timeout=_NOTIFY_REQUEST_TIMEOUT_S,
            )
    return ActionResult("notify", None, None, False, None, None, None)


def dispatch_action(action: dict, *, ctx: ActionContext, shadow: bool) -> ActionResult:
    action_type = action["type"]
    if action_type == "buy":
        return _dispatch_buy(action, ctx=ctx, shadow=shadow)
    if action_type == "sell":
        return _dispatch_sell(action, ctx=ctx, shadow=shadow)
    if action_type == "go_to_cash":
        return _dispatch_go_to_cash(action, ctx=ctx, shadow=shadow)
    if action_type == "set_stop":
        return _dispatch_set_stop(action, ctx=ctx, shadow=shadow)
    if action_type == "notify":
        return _dispatch_notify(action, ctx=ctx)
    raise ValueError(f"unknown action type: {action_type!r}")
