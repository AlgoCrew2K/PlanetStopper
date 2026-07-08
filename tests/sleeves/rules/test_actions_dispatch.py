"""
RED tests — sleeves/rules/actions.py: action dispatch (AC-6, AC-7, Security).

CONTRACT this file specifies for the GREEN implementer (s2-rules-impl):

    sleeves/rules/actions.py

    @dataclass(frozen=True)
    class ActionContext:
        sleeve_id: int
        symbol: str
        price: float
        sleeve_equity_usd: float
        current_position_qty: float
        turnover_used_usd: float
        envelope: dict                      # sleeves.envelope's envelope dict shape
        live_mode: bool = False
        live_keys_present: bool = False
        discord_webhook_url: str | None = None

    @dataclass(frozen=True)
    class ActionResult:
        action_type: str
        would_have_qty: float | None         # the CLAMPED qty (never the raw
                                                # sizing qty) that was or would
                                                # have been submitted
        would_have_notional_usd: float | None
        executed: bool                        # True iff a real order was placed
                                                # (always False when shadow=True)
        order_result: "alpaca_orders.OrderResult | None"
        clamp: "envelope.ClampResult | None"   # None for notify/set_stop (no sizing)
        refused_reason: str | None             # populated when sizing errored or
                                                # the clamp refused (approved=False)

    def dispatch_action(action: dict, *, ctx: ActionContext, shadow: bool) -> ActionResult: ...

STRUCTURAL NON-BYPASSABILITY (the load-bearing security contract, plan's
"Order-path containment... envelope clamp is structurally on every order
path"): for "buy"/"sell"/"go_to_cash", dispatch_action MUST call
sleeves.sizing.size_order, THEN sleeves.envelope.clamp_order on the result,
and MAY ONLY call a sleeves.alpaca_orders order-placing function (when
shadow=False) using the qty envelope.clamp_order returned — NEVER the raw
sizing qty, and NEVER when the clamp refused. This file proves that
BEHAVIORALLY (patching envelope.clamp_order to always refuse and asserting
zero alpaca_orders calls result), which is a stronger and less brittle proof
than an AST-dominance analysis.

NEW alpaca_orders.py function this cycle needs (P1 RESERVED but never
implemented this name — see docs/generated/sleeves.md's "reserved-but-unused-
in-P1" list and tests/sleeves/test_containment_invariants.py's
_BROKER_ORDER_SYMBOLS, which already includes "submit_order"): P1 only built
submit_bracket_order (always attaches take-profit/stop-loss legs — wrong
shape for a plain closing sell) and submit_trailing_stop_order. A "sell" /
"go_to_cash" action needs a plain, non-bracket order:

    def submit_order(
        *, symbol, qty, side, order_type="market", client_order_id=None,
        time_in_force="day", live_mode=False, live_keys_present=False, max_retries=4,
    ) -> alpaca_orders.OrderResult
        # Same conventions as submit_bracket_order/submit_trailing_stop_order:
        # raw requests, never raises, D-1 error redaction, client_order_id
        # lost-ack recovery, bounded retry/backoff. This function lives in
        # sleeves/alpaca_orders.py (THE single order-capable module) — adding
        # it there, not a new module, keeps the whole-repo containment
        # invariant intact (it already allowlists this exact name).

NO DEFAULTED BRACKET (PM decision, 2026-07-08, AC-7): schema.py guarantees
every "buy" action carries at least one of "stop_loss_pct"/"trailing_stop_pct"
before it ever reaches dispatch_action. actions.py MUST have NO fallback
constant for either leg of the bracket:
  - stop_loss_price = ctx.price * (1 - (action.get("stop_loss_pct") or
    action.get("trailing_stop_pct"))) -- whichever of the two the rule
    declared; this value is ALSO passed as sizing.size_order's `stop_price`
    for risk_pct-mode sizing (closing the loop: the SAME declared exit
    distance drives both the bracket's protective leg AND the position size).
  - take_profit_price: action.get("take_profit_pct") when present
    (price * (1 + take_profit_pct)); when ABSENT, derived as a FIXED,
    documented multiple of the stop distance (price + (price - stop_loss_price)
    * <a named reward:risk ratio constant>) -- tied to the trade's OWN
    declared risk, never an independent/disconnected percentage. There must
    be NO module-level `_DEFAULT_BRACKET_STOP_LOSS_PCT` or
    `_DEFAULT_BRACKET_TAKE_PROFIT_PCT` (or equivalently-named constants)
    anywhere in this module — see TestPlaceholderConstantsRemoved.

Action dispatch per type:
    "buy":           sizing.size_order(...) -> envelope.clamp_order(...) ->
                     (shadow: no-op) | (armed: alpaca_orders.submit_bracket_order
                     using clamp.qty, never the raw sizing qty).
    "sell":          sizing.size_order(...) -> envelope.clamp_order(side="sell", ...)
                     -> (shadow: no-op) | (armed: alpaca_orders.submit_order
                     using clamp.qty).
    "go_to_cash":    sizes to ctx.current_position_qty directly (full
                     liquidation; no `sizing` field is read even if present) ->
                     envelope.clamp_order(side="sell", ...) -> same armed/shadow
                     split as sell, via alpaca_orders.submit_order.
    "set_stop":      no sizing/clamp (protects an existing position, not a new
                     entry) -> shadow: would_have_qty=ctx.current_position_qty,
                     executed=False. armed: alpaca_orders.submit_trailing_stop_order
                     with ctx.current_position_qty and the action's
                     trail_percent/trail_price.
    "notify":        builds a payload from ONLY the whitelisted fields
                     ({"symbol","action","qty","price","reason","rule_name",
                     "sleeve_name"}) -- ANY other key present in action["fields"]
                     is dropped, never forwarded (defense-in-depth beyond
                     schema.py's authoring-time check). NEVER calls anything in
                     sleeves.alpaca_orders, shadow or armed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip(
    "sleeves.rules.actions", reason="RED phase — sleeves.rules.actions not implemented yet"
)

import sleeves.alpaca_orders as alpaca_orders  # noqa: E402
import sleeves.envelope as envelope  # noqa: E402
import sleeves.rules.actions as actions  # noqa: E402
import sleeves.sizing as sizing  # noqa: E402


def _ctx(**overrides) -> actions.ActionContext:
    base = dict(
        sleeve_id=1,
        symbol="SPY",
        price=100.0,
        sleeve_equity_usd=10_000.0,
        current_position_qty=0.0,
        turnover_used_usd=0.0,
        envelope={
            "allowlist": ["SPY"],
            "max_position_pct": 0.5,
            "max_order_usd": 5000.0,
            "max_daily_turnover_usd": 10_000.0,
            "long_only": True,
        },
        live_mode=False,
        live_keys_present=False,
        discord_webhook_url=None,
    )
    base.update(overrides)
    return actions.ActionContext(**base)


# ---------------------------------------------------------------------------
# 1. Structural non-bypassability — the clamp is the ONLY gate to alpaca_orders
# ---------------------------------------------------------------------------


class TestClampIsNonBypassable:
    def test_armed_buy_never_reaches_alpaca_orders_when_clamp_refuses(self):
        action = {
            "type": "buy",
            "sizing": {"mode": "shares", "shares": 10},
            "stop_loss_pct": 0.05,
        }
        with (
            patch.object(
                envelope,
                "clamp_order",
                return_value=envelope.ClampResult(
                    approved=False,
                    qty=0.0,
                    original_qty=10.0,
                    clamped=True,
                    reason=envelope.REASON_REDUCED_TO_ZERO,
                ),
            ),
            patch.object(alpaca_orders, "submit_bracket_order") as mock_submit,
        ):
            result = actions.dispatch_action(action, ctx=_ctx(), shadow=False)
        mock_submit.assert_not_called()
        assert result.executed is False
        assert result.refused_reason is not None

    def test_armed_sell_never_reaches_alpaca_orders_when_clamp_refuses(self):
        action = {"type": "sell", "sizing": {"mode": "shares", "shares": 5}}
        with (
            patch.object(
                envelope,
                "clamp_order",
                return_value=envelope.ClampResult(
                    approved=False,
                    qty=0.0,
                    original_qty=5.0,
                    clamped=True,
                    reason=envelope.REASON_REDUCED_TO_ZERO,
                ),
            ),
            patch.object(alpaca_orders, "submit_order") as mock_submit_order,
            patch.object(alpaca_orders, "submit_bracket_order") as mock_bracket,
        ):
            result = actions.dispatch_action(
                action, ctx=_ctx(current_position_qty=5.0), shadow=False
            )
        mock_submit_order.assert_not_called()
        mock_bracket.assert_not_called()
        assert result.executed is False

    def test_armed_buy_forwards_the_CLAMPED_qty_never_the_raw_sizing_qty(self):
        # sizing asks for 10 shares; the clamp cuts it down to 3. The order
        # actually constructed must carry 3, never 10.
        action = {
            "type": "buy",
            "sizing": {"mode": "shares", "shares": 10},
            "stop_loss_pct": 0.05,
        }
        clamped = envelope.ClampResult(
            approved=True,
            qty=3.0,
            original_qty=10.0,
            clamped=True,
            reason=envelope.REASON_MAX_ORDER_USD,
        )
        with (
            patch.object(envelope, "clamp_order", return_value=clamped),
            patch.object(
                alpaca_orders,
                "submit_bracket_order",
                return_value=alpaca_orders.OrderResult(order={"id": "abc"}, error=None),
            ) as mock_submit,
        ):
            result = actions.dispatch_action(action, ctx=_ctx(), shadow=False)
        assert mock_submit.called
        _, kwargs = mock_submit.call_args
        assert kwargs.get("qty") == 3.0, f"expected clamped qty 3.0 forwarded, got kwargs={kwargs}"
        assert result.would_have_qty == 3.0
        assert result.executed is True

    def test_armed_sell_forwards_the_CLAMPED_qty_via_submit_order_never_a_bracket(self):
        action = {"type": "sell", "sizing": {"mode": "shares", "shares": 5}}
        clamped = envelope.ClampResult(
            approved=True, qty=5.0, original_qty=5.0, clamped=False, reason=None
        )
        with (
            patch.object(envelope, "clamp_order", return_value=clamped),
            patch.object(
                alpaca_orders,
                "submit_order",
                return_value=alpaca_orders.OrderResult(order={"id": "def"}, error=None),
            ) as mock_submit_order,
            patch.object(alpaca_orders, "submit_bracket_order") as mock_bracket,
        ):
            result = actions.dispatch_action(
                action, ctx=_ctx(current_position_qty=5.0), shadow=False
            )
        assert mock_submit_order.called
        mock_bracket.assert_not_called(), "a closing sell must never attach bracket legs"
        _, kwargs = mock_submit_order.call_args
        assert kwargs.get("qty") == 5.0
        assert kwargs.get("side") == "sell"
        assert result.executed is True

    def test_shadow_buy_never_calls_alpaca_orders_even_when_clamp_approves(self):
        action = {
            "type": "buy",
            "sizing": {"mode": "shares", "shares": 10},
            "stop_loss_pct": 0.05,
        }
        approved = envelope.ClampResult(
            approved=True, qty=10.0, original_qty=10.0, clamped=False, reason=None
        )
        with (
            patch.object(envelope, "clamp_order", return_value=approved),
            patch.object(alpaca_orders, "submit_bracket_order") as mock_submit,
        ):
            result = actions.dispatch_action(action, ctx=_ctx(), shadow=True)
        mock_submit.assert_not_called()
        assert result.executed is False
        assert result.would_have_qty == 10.0


# ---------------------------------------------------------------------------
# 1b. No naked entries — a "buy" NEVER reaches the plain submit_order path
#
# AC-7: "every entry defaults to a bracket... no position ever exists without
# its exit." sleeves.alpaca_orders.submit_order (added this cycle for
# sell/go_to_cash) is a PLAIN order with no take-profit/stop-loss legs — it
# is side-agnostic at the alpaca_orders layer (nothing there stops a caller
# from passing side="buy"), so the ONLY thing preventing a rule from opening
# an unprotected naked position is actions.py's OWN dispatch logic never
# routing "buy" through it. Proven behaviorally across every clamp/mode
# combination, not just the happy path — PM-flagged gap (2026-07-08),
# fixing before this lands in a GREEN commit.
# ---------------------------------------------------------------------------


class TestBuyNeverRoutesThroughThePlainSubmitOrderPath:
    @pytest.mark.parametrize("shadow", [True, False])
    @pytest.mark.parametrize("clamp_approved", [True, False])
    def test_buy_never_calls_plain_submit_order_in_any_mode_or_clamp_outcome(
        self, shadow, clamp_approved
    ):
        action = {
            "type": "buy",
            "sizing": {"mode": "shares", "shares": 10},
            "stop_loss_pct": 0.05,
        }
        clamp = (
            envelope.ClampResult(
                approved=True, qty=10.0, original_qty=10.0, clamped=False, reason=None
            )
            if clamp_approved
            else envelope.ClampResult(
                approved=False,
                qty=0.0,
                original_qty=10.0,
                clamped=True,
                reason=envelope.REASON_REDUCED_TO_ZERO,
            )
        )
        with (
            patch.object(envelope, "clamp_order", return_value=clamp),
            patch.object(
                alpaca_orders,
                "submit_bracket_order",
                return_value=alpaca_orders.OrderResult(order={"id": "abc"}, error=None),
            ),
            patch.object(alpaca_orders, "submit_order") as mock_submit_order,
        ):
            actions.dispatch_action(action, ctx=_ctx(), shadow=shadow)
        assert not mock_submit_order.called, (
            f"a 'buy' action reached the plain (non-bracket) submit_order path "
            f"(shadow={shadow}, clamp_approved={clamp_approved}) -- AC-7 naked-entry "
            f"violation: an ENTRY must be structurally unable to exist without a "
            f"broker-side exit, which submit_bracket_order guarantees and plain "
            f"submit_order does not."
        )

    def test_armed_approved_buy_reaches_bracket_construction_not_the_plain_path(self):
        # Positive complement to the negative proof above: confirm the ONE
        # broker call an armed, approved buy DOES make is the bracket path.
        action = {
            "type": "buy",
            "sizing": {"mode": "shares", "shares": 10},
            "stop_loss_pct": 0.05,
        }
        approved = envelope.ClampResult(
            approved=True, qty=10.0, original_qty=10.0, clamped=False, reason=None
        )
        with (
            patch.object(envelope, "clamp_order", return_value=approved),
            patch.object(
                alpaca_orders,
                "submit_bracket_order",
                return_value=alpaca_orders.OrderResult(order={"id": "abc"}, error=None),
            ) as mock_bracket,
            patch.object(alpaca_orders, "submit_order") as mock_submit_order,
        ):
            result = actions.dispatch_action(action, ctx=_ctx(), shadow=False)
        assert mock_bracket.called
        mock_submit_order.assert_not_called()
        assert result.executed is True


# ---------------------------------------------------------------------------
# 1c. Bracket exit params come from the DECLARED stop -- never a constant
#
# PM decision (2026-07-08, AC-7): _DEFAULT_BRACKET_STOP_LOSS_PCT and
# _DEFAULT_BRACKET_TAKE_PROFIT_PCT are removed entirely. A buy's bracket
# prices are derived exclusively from what the rule itself declared.
# ---------------------------------------------------------------------------


class TestBracketExitParamsFromDeclaredStops:
    def test_bracket_stop_loss_price_derives_from_declared_stop_loss_pct(self):
        # ctx price is 100.0 (see _ctx defaults) -- stop_loss_pct=0.05 must
        # produce stop_loss_price == 100 * (1 - 0.05) == 95.0, computed via
        # the SAME formula here, never a hardcoded literal.
        action = {
            "type": "buy",
            "sizing": {"mode": "shares", "shares": 10},
            "stop_loss_pct": 0.05,
        }
        approved = envelope.ClampResult(
            approved=True, qty=10.0, original_qty=10.0, clamped=False, reason=None
        )
        ctx = _ctx()
        expected_stop_price = ctx.price * (1 - 0.05)
        with (
            patch.object(envelope, "clamp_order", return_value=approved),
            patch.object(
                alpaca_orders,
                "submit_bracket_order",
                return_value=alpaca_orders.OrderResult(order={"id": "abc"}, error=None),
            ) as mock_bracket,
        ):
            actions.dispatch_action(action, ctx=ctx, shadow=False)
        _, kwargs = mock_bracket.call_args
        assert kwargs.get("stop_loss_price") == pytest.approx(expected_stop_price)

    def test_bracket_stop_loss_price_derives_from_declared_trailing_stop_pct_when_stop_loss_pct_absent(
        self,
    ):
        action = {
            "type": "buy",
            "sizing": {"mode": "shares", "shares": 10},
            "trailing_stop_pct": 0.08,
        }
        approved = envelope.ClampResult(
            approved=True, qty=10.0, original_qty=10.0, clamped=False, reason=None
        )
        ctx = _ctx()
        expected_stop_price = ctx.price * (1 - 0.08)
        with (
            patch.object(envelope, "clamp_order", return_value=approved),
            patch.object(
                alpaca_orders,
                "submit_bracket_order",
                return_value=alpaca_orders.OrderResult(order={"id": "abc"}, error=None),
            ) as mock_bracket,
        ):
            actions.dispatch_action(action, ctx=ctx, shadow=False)
        _, kwargs = mock_bracket.call_args
        assert kwargs.get("stop_loss_price") == pytest.approx(expected_stop_price)

    def test_bracket_take_profit_price_uses_declared_take_profit_pct_when_present(self):
        action = {
            "type": "buy",
            "sizing": {"mode": "shares", "shares": 10},
            "stop_loss_pct": 0.05,
            "take_profit_pct": 0.10,
        }
        approved = envelope.ClampResult(
            approved=True, qty=10.0, original_qty=10.0, clamped=False, reason=None
        )
        ctx = _ctx()
        expected_take_profit_price = ctx.price * (1 + 0.10)
        with (
            patch.object(envelope, "clamp_order", return_value=approved),
            patch.object(
                alpaca_orders,
                "submit_bracket_order",
                return_value=alpaca_orders.OrderResult(order={"id": "abc"}, error=None),
            ) as mock_bracket,
        ):
            actions.dispatch_action(action, ctx=ctx, shadow=False)
        _, kwargs = mock_bracket.call_args
        assert kwargs.get("take_profit_price") == pytest.approx(expected_take_profit_price)

    def test_take_profit_absent_derives_as_a_fixed_ratio_of_the_declared_stop_distance(self):
        # THE derivation proof: with no take_profit_pct declared, the
        # take-profit distance must be a CONSTANT MULTIPLE of the stop
        # distance the rule itself declared -- never an independent/absolute
        # percentage disconnected from the trade's own risk. Proven by
        # checking the ratio is IDENTICAL across two different stop
        # distances, without this test ever asserting what that ratio is.
        ctx = _ctx()
        approved_qty = 10.0

        def _bracket_prices_for(stop_loss_pct: float) -> tuple[float, float]:
            action = {
                "type": "buy",
                "sizing": {"mode": "shares", "shares": approved_qty},
                "stop_loss_pct": stop_loss_pct,
            }
            approved = envelope.ClampResult(
                approved=True,
                qty=approved_qty,
                original_qty=approved_qty,
                clamped=False,
                reason=None,
            )
            with (
                patch.object(envelope, "clamp_order", return_value=approved),
                patch.object(
                    alpaca_orders,
                    "submit_bracket_order",
                    return_value=alpaca_orders.OrderResult(order={"id": "abc"}, error=None),
                ) as mock_bracket,
            ):
                actions.dispatch_action(action, ctx=ctx, shadow=False)
            _, kwargs = mock_bracket.call_args
            return kwargs["stop_loss_price"], kwargs["take_profit_price"]

        stop_price_a, take_profit_price_a = _bracket_prices_for(0.05)
        stop_price_b, take_profit_price_b = _bracket_prices_for(0.10)

        # Guard against a vacuous pass: if the implementation ignored
        # stop_loss_pct entirely (e.g. still reading a hardcoded constant),
        # both calls would return IDENTICAL prices regardless of input, and
        # the ratio check below would trivially "pass" for the wrong reason.
        # Confirm the declared stop actually changed the stop price first.
        assert stop_price_a != pytest.approx(stop_price_b), (
            "stop_loss_price did not change between stop_loss_pct=0.05 and 0.10 -- "
            "the declared stop is not being read at all (this test's ratio check "
            "would otherwise pass vacuously)."
        )

        ratio_a = (take_profit_price_a - ctx.price) / (ctx.price - stop_price_a)
        ratio_b = (take_profit_price_b - ctx.price) / (ctx.price - stop_price_b)
        assert ratio_a == pytest.approx(ratio_b), (
            f"take-profit distance must scale proportionally with the DECLARED stop "
            f"distance (a fixed reward:risk ratio), not an independent absolute "
            f"percentage -- got ratio_a={ratio_a}, ratio_b={ratio_b}"
        )

    def test_declared_stop_loss_pct_feeds_risk_pct_sizings_own_stop_distance_requirement(self):
        # Closes the PM's motivating point #1: risk_pct sizing's formula
        # (qty = risk_dollars / stop_distance) needs a stop_price -- that
        # price must come from the SAME declared stop_loss_pct that sizes
        # the bracket, not a separate/absent sizing.stop_price field.
        ctx = _ctx(sleeve_equity_usd=10_000.0, price=100.0)
        action = {
            "type": "buy",
            "sizing": {"mode": "risk_pct", "risk_pct": 0.02},
            "stop_loss_pct": 0.05,
        }
        expected_stop_price = ctx.price * (1 - 0.05)
        expected_risk_dollars = 0.02 * ctx.sleeve_equity_usd
        expected_qty = expected_risk_dollars / (ctx.price - expected_stop_price)

        with patch.object(envelope, "clamp_order") as mock_clamp:
            mock_clamp.return_value = envelope.ClampResult(
                approved=True,
                qty=expected_qty,
                original_qty=expected_qty,
                clamped=False,
                reason=None,
            )
            actions.dispatch_action(action, ctx=ctx, shadow=True)
        assert mock_clamp.called, (
            "envelope.clamp_order was never called -- sizing.size_order must have errored "
            "before reaching the clamp, meaning risk_pct sizing did not receive a stop_price "
            "derived from the declared stop_loss_pct (it likely fell back to an absent/None "
            "sizing.stop_price and hit sizing.py's degenerate_stop_distance error instead)."
        )
        _, clamp_kwargs = mock_clamp.call_args
        assert clamp_kwargs.get("qty") == pytest.approx(expected_qty), (
            f"risk_pct sizing did not use the declared stop_loss_pct's derived stop "
            f"price -- expected qty={expected_qty}, clamp_order was called with "
            f"qty={clamp_kwargs.get('qty')}"
        )


class TestPlaceholderConstantsRemoved:
    def test_no_default_bracket_constants_remain_in_actions_module(self):
        import inspect

        source = inspect.getsource(actions)
        for forbidden_name in (
            "_DEFAULT_BRACKET_STOP_LOSS_PCT",
            "_DEFAULT_BRACKET_TAKE_PROFIT_PCT",
        ):
            assert forbidden_name not in source, (
                f"{forbidden_name} still present in sleeves/rules/actions.py -- PM decision "
                f"(2026-07-08) removed this fallback constant entirely; a buy action's exit "
                f"parameters must come exclusively from the rule's own declared "
                f"stop_loss_pct/trailing_stop_pct/take_profit_pct fields."
            )


# ---------------------------------------------------------------------------
# 2. go_to_cash — always sizes to the FULL current position, ignores `sizing`
# ---------------------------------------------------------------------------


class TestGoToCashFullLiquidation:
    def test_go_to_cash_sizes_to_the_entire_current_position(self):
        action = {"type": "go_to_cash"}
        with (
            patch.object(sizing, "size_order") as mock_size,
            patch.object(
                envelope,
                "clamp_order",
                return_value=envelope.ClampResult(
                    approved=True, qty=42.0, original_qty=42.0, clamped=False, reason=None
                ),
            ) as mock_clamp,
        ):
            actions.dispatch_action(action, ctx=_ctx(current_position_qty=42.0), shadow=True)
        # go_to_cash must not even consult sizing.size_order — the qty IS the
        # current position, not a sizing-mode computation.
        mock_size.assert_not_called()
        _, clamp_kwargs = mock_clamp.call_args
        assert clamp_kwargs.get("qty") == 42.0
        assert clamp_kwargs.get("side") == "sell"

    def test_go_to_cash_ignores_a_present_but_irrelevant_sizing_field(self):
        # Even if a stale/malformed doc carries a `sizing` block on go_to_cash,
        # it must be ignored -- the qty is always the full position.
        action = {"type": "go_to_cash", "sizing": {"mode": "shares", "shares": 1}}
        with patch.object(
            envelope,
            "clamp_order",
            return_value=envelope.ClampResult(
                approved=True, qty=42.0, original_qty=42.0, clamped=False, reason=None
            ),
        ) as mock_clamp:
            actions.dispatch_action(action, ctx=_ctx(current_position_qty=42.0), shadow=True)
        _, clamp_kwargs = mock_clamp.call_args
        assert clamp_kwargs.get("qty") == 42.0


# ---------------------------------------------------------------------------
# 3. set_stop — no sizing/clamp; armed path reaches alpaca_orders directly
# ---------------------------------------------------------------------------


class TestSetStopAction:
    def test_shadow_set_stop_never_calls_alpaca_orders(self):
        action = {"type": "set_stop", "trail_percent": 0.05}
        with patch.object(alpaca_orders, "submit_trailing_stop_order") as mock_trail:
            result = actions.dispatch_action(
                action, ctx=_ctx(current_position_qty=10.0), shadow=True
            )
        mock_trail.assert_not_called()
        assert result.executed is False
        assert result.would_have_qty == 10.0

    def test_armed_set_stop_calls_alpaca_orders_with_the_current_position_qty(self):
        action = {"type": "set_stop", "trail_percent": 0.05}
        with patch.object(
            alpaca_orders,
            "submit_trailing_stop_order",
            return_value=alpaca_orders.OrderResult(order={"id": "xyz"}, error=None),
        ) as mock_trail:
            result = actions.dispatch_action(
                action, ctx=_ctx(current_position_qty=10.0), shadow=False
            )
        assert mock_trail.called
        _, kwargs = mock_trail.call_args
        assert kwargs.get("qty") == 10.0
        assert kwargs.get("trail_percent") == 0.05
        assert result.executed is True


# ---------------------------------------------------------------------------
# 4. notify — whitelisted fields only, never touches alpaca_orders
# ---------------------------------------------------------------------------


class TestNotifyAction:
    _WHITELIST = frozenset(
        {"symbol", "action", "qty", "price", "reason", "rule_name", "sleeve_name"}
    )

    def test_notify_never_calls_any_alpaca_orders_function(self):
        action = {"type": "notify", "template": "fired", "fields": {"symbol": "SPY"}}
        with (
            patch("requests.post") as mock_post,
            patch.object(alpaca_orders, "submit_bracket_order") as mock_bracket,
            patch.object(alpaca_orders, "submit_trailing_stop_order") as mock_trail,
        ):
            actions.dispatch_action(
                action, ctx=_ctx(discord_webhook_url="https://discord.example/webhook"), shadow=True
            )
        mock_bracket.assert_not_called()
        mock_trail.assert_not_called()

    def test_notify_drops_any_non_whitelisted_field_defense_in_depth(self):
        # Even if a field bypassed schema.py's authoring-time whitelist check
        # (e.g. an older rule authored before a stricter schema landed),
        # dispatch_action itself must never forward it outward.
        action = {
            "type": "notify",
            "template": "fired",
            "fields": {"symbol": "SPY", "account_number": "1234-5678", "api_key": "secret"},
        }
        with patch("requests.post") as mock_post:
            actions.dispatch_action(
                action, ctx=_ctx(discord_webhook_url="https://discord.example/webhook"), shadow=True
            )
        if mock_post.called:
            _, kwargs = mock_post.call_args
            sent_payload = str(kwargs.get("json", "")) + str(kwargs.get("data", ""))
            assert "1234-5678" not in sent_payload
            assert "secret" not in sent_payload
            assert "account_number" not in sent_payload
            assert "api_key" not in sent_payload

    def test_notify_with_no_webhook_url_is_a_silent_no_op(self):
        action = {"type": "notify", "template": "fired", "fields": {"symbol": "SPY"}}
        with patch("requests.post") as mock_post:
            result = actions.dispatch_action(
                action, ctx=_ctx(discord_webhook_url=None), shadow=True
            )
        mock_post.assert_not_called()
        assert result.action_type == "notify"
