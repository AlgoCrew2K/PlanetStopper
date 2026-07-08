"""sleeves/rules/actions.py -- action dispatch (AC-6, AC-7, Security).

`dispatch_action` is the ONE place a rule's `then` action turns into either a
"would-have-ordered" shadow record or a real broker call. The load-bearing
security property (plan's "envelope clamp is structurally on every order
path"): for "buy"/"sell"/"go_to_cash", this module ALWAYS calls
sleeves.sizing.size_order, THEN sleeves.envelope.clamp_order on the result,
and may only reach sleeves.alpaca_orders (when shadow=False) using the qty
envelope.clamp_order returned -- never the raw sizing qty, and never when the
clamp refused.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

import requests

import sleeves.alpaca_orders as alpaca_orders
import sleeves.envelope as envelope
import sleeves.sizing as sizing

# Matches sleeves/alpaca_orders.py's own explicit-timeout convention -- a
# Discord notify call must never hang the tick indefinitely.
_NOTIFY_REQUEST_TIMEOUT_S = 10.0

_NOTIFY_FIELD_WHITELIST = frozenset(
    {"symbol", "action", "qty", "price", "reason", "rule_name", "sleeve_name"}
)

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
    symbol: str
    price: float
    sleeve_equity_usd: float
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


def _dispatch_buy(action: dict, *, ctx: ActionContext, shadow: bool) -> ActionResult:
    # schema.py guarantees a schema-valid "buy" declares at least one of
    # these, each in (0, 1) -- this module trusts that shape and never
    # falls back to a default of its own (PM decision, AC-7).
    declared_stop_pct = action.get("stop_loss_pct") or action.get("trailing_stop_pct")
    stop_price = ctx.price * (1 - declared_stop_pct)

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

    order_result = alpaca_orders.submit_bracket_order(
        symbol=ctx.symbol,
        qty=clamp_result.qty,
        side="buy",
        take_profit_price=take_profit_price,
        stop_loss_price=stop_price,
        live_mode=ctx.live_mode,
        live_keys_present=ctx.live_keys_present,
    )
    return ActionResult(
        "buy",
        would_have_qty,
        would_have_notional,
        order_result.error is None,
        order_result,
        clamp_result,
        order_result.error,
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

    order_result = alpaca_orders.submit_order(
        symbol=ctx.symbol,
        qty=clamp_result.qty,
        side="sell",
        live_mode=ctx.live_mode,
        live_keys_present=ctx.live_keys_present,
    )
    return ActionResult(
        action_type,
        would_have_qty,
        would_have_notional,
        order_result.error is None,
        order_result,
        clamp_result,
        order_result.error,
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

    order_result = alpaca_orders.submit_trailing_stop_order(
        symbol=ctx.symbol,
        qty=qty,
        side="sell",
        trail_percent=action.get("trail_percent"),
        trail_price=action.get("trail_price"),
        live_mode=ctx.live_mode,
        live_keys_present=ctx.live_keys_present,
    )
    return ActionResult(
        "set_stop",
        qty,
        would_have_notional,
        order_result.error is None,
        order_result,
        None,
        order_result.error,
    )


def _dispatch_notify(action: dict, *, ctx: ActionContext) -> ActionResult:
    # Never touches sleeves.alpaca_orders, shadow or armed -- a notify is a
    # pure side-effect, never a trade action.
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
