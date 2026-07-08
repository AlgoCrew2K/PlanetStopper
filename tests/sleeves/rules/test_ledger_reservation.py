"""
RED tests — the armed-path money-safety sequence (PM ruling, 2026-07-08, AC-1/AC-19).

s2-review's P2 verdict (083526f) found that the armed dispatch path placed
real broker orders with NO cash-safety enforcement anywhere in the call
chain (envelope.clamp_order only checks position/order/turnover-% caps,
never actual liquid cash) and NO durable reservation row (P1 invariant #6's
client_order_id lost-ack-recovery, bypassed entirely). PM ruling: wire the
existing P1 primitives (sleeves.ledger, database's sleeve_orders accessors)
into the armed path THIS cycle -- this is not new scope, it's closing a gap
in wiring P1 already built for exactly this purpose.

PART 1 — CONTRACT this file specifies for `sleeves/ledger.py` (a NEW pure
function added to the existing P1 module, matching the precedent set by
`sleeves/alpaca_orders.py` gaining `submit_order` in P2):

    _RESERVATION_TERMINAL_STATUSES = (
        "filled", "canceled", "expired", "replaced", "done_for_day", "rejected",
    )  # identical denylist to database.get_daily_turnover_usd's own fail-closed
       # convention -- an unrecognized/future status is treated as STILL
       # reserving (the conservative, over-reserve direction), never silently
       # released.

    def reconstruct_from_history(capital_usd: float, order_history: list[dict]) -> LedgerState:
        # Pure function -- NO I/O (matches ledger.py's own "zero I/O" module
        # invariant). The CALLER fetches `database.get_sleeve_order_history
        # (sleeve_id)` and passes the result straight through. Folds
        # reserve()/apply_fill()/release() over the full history
        # (oldest-submitted-first, oldest-filled-first per
        # get_sleeve_order_history's documented ordering) to reconstruct the
        # CURRENT LedgerState -- this is what lets a fresh-subprocess-per-tick
        # engine know "how much cash is actually available right now" without
        # persisting a LedgerState snapshot of its own.
        #
        # Per order (side="buy", reserved_price is not None -- a sell's
        # reserved_price is always NULL per the established
        # get_daily_turnover_usd convention, since sells reserve SHARES not
        # cash):
        #   1. reserve(qty * reserved_price) at fold-start (the reservation
        #      that was made before the broker call, per invariant #6).
        #   2. apply_fill(...) for each fill, in order, with reserved_usd
        #      proportional to that fill's share of the original reservation
        #      (filled_qty * reserved_price).
        #   3. If the order's CURRENT status is in
        #      _RESERVATION_TERMINAL_STATUSES and qty was not fully filled,
        #      release() the unfilled remainder (reject/cancel/expire all
        #      release identically -- matches release()'s own docstring).
        # Sell orders (reserved_price is None): never reserve(); each fill
        # applies directly with reserved_usd=0.0.

PART 2 — CONTRACT extensions to `sleeves/rules/actions.py`:

    ActionContext gains two new REQUIRED fields:
        rule_id: int          # needed for insert_sleeve_order's rule_id +
                                # the fire row's AC-19 order_id trace
        capital_usd: float    # the sleeve's FIXED capital allocation (the
                                # ledger.reconstruct_from_history seed) --
                                # distinct from sleeve_equity_usd (cash+
                                # positions marked-to-market), never changes
                                # after sleeve creation (AC-1)

    ActionResult gains one new field (default None -- existing call sites
    that never place a real order are unaffected):
        order_id: int | None = None   # the INTERNAL sleeve_orders.id (soft
                                        # FK target for the fire row, AC-19)
                                        # when a reservation row was inserted;
                                        # None for shadow / refused / no-order
                                        # actions (notify)

    _REASON_INSUFFICIENT_CASH = "insufficient_cash"  # refused_reason value,
                                                        # mirrors envelope.py's
                                                        # REASON_* naming

    THE MONEY-SAFETY SEQUENCE for a "buy" (both shadow and armed compute this
    identically up through the refusal check -- shadow needs to reflect the
    would-have-been outcome in its snapshot, per the PM ruling):
      1. (existing) sizing.size_order -> envelope.clamp_order. Not approved
         -> refused (existing clamp_reason path, unchanged).
      2. NEW: reconstruct current ledger state via
         `sleeves.ledger.reconstruct_from_history(ctx.capital_usd,
         database.get_sleeve_order_history(ctx.sleeve_id))`, then attempt
         `sleeves.ledger.reserve(ledger_state, clamp_result.qty * ctx.price)`.
         `InsufficientCashError` -> ActionResult(refused_reason=
         _REASON_INSUFFICIENT_CASH, executed=False, order_id=None) -- NO
         broker call, NO sleeve_orders row, in EITHER shadow or armed mode.
      3. shadow=True and cash sufficient -> existing would-have-been result,
         still zero DB writes, zero broker calls (order_id=None).
      4. shadow=False and cash sufficient -> mint client_order_id, INSERT a
         'RESERVED' sleeve_orders row via database.insert_sleeve_order
         (capturing the returned INTERNAL id) BEFORE calling
         alpaca_orders.submit_bracket_order -- invariant #6's crash-safety
         ordering. On broker ack (order_result.error is None): call
         database.attach_alpaca_order_id(client_order_id, the broker's own
         order id). On broker rejection (order_result.error is not None):
         call database.update_sleeve_order_status(client_order_id,
         status=<a terminal status>) -- this IS "releasing the reservation":
         since LedgerState is never itself persisted (only order/fill ROWS
         are), marking the row terminal is what makes the NEXT
         reconstruct_from_history() correctly treat it as released. Return
         ActionResult(order_id=<the internal id from step 4's insert>,
         executed=(order_result.error is None), ...).

    "sell" / "go_to_cash" / "set_stop": do NOT reserve cash (sells/closes
    generate cash or touch no cash at all -- ledger.py's reservation
    bookkeeping is buy-side-only per its own docstring) but DO get the SAME
    RESERVED-row-before-submit / attach-on-ack / release-via-status-update-
    on-reject sequence (P1 invariant #6 applies to every order-placing
    action, not just entries) and the same `order_id` threading.
"""

from __future__ import annotations

import json
from unittest.mock import ANY, patch

import pytest

pytest.importorskip(
    "sleeves.rules.actions", reason="RED phase — sleeves.rules.actions not implemented yet"
)

import database  # noqa: E402
import sleeves.alpaca_orders as alpaca_orders  # noqa: E402
import sleeves.envelope as envelope  # noqa: E402
import sleeves.ledger as ledger  # noqa: E402
import sleeves.rules.actions as actions  # noqa: E402


def _ctx(**overrides) -> actions.ActionContext:
    base = dict(
        sleeve_id=1,
        rule_id=7,
        symbol="SPY",
        price=100.0,
        sleeve_equity_usd=10_000.0,
        capital_usd=10_000.0,
        current_position_qty=0.0,
        turnover_used_usd=0.0,
        envelope={
            "allowlist": ["SPY"],
            "max_position_pct": 0.9,
            "max_order_usd": 50_000.0,
            "max_daily_turnover_usd": 100_000.0,
            "long_only": True,
        },
        live_mode=False,
        live_keys_present=False,
        discord_webhook_url=None,
    )
    base.update(overrides)
    return actions.ActionContext(**base)


def _order_row(
    *, side: str, qty: float, reserved_price, status: str, symbol: str = "SPY", fills=()
) -> dict:
    """Build a database.get_sleeve_order_history-shaped row."""
    return {
        "side": side,
        "qty": qty,
        "reserved_price": reserved_price,
        "status": status,
        "symbol": symbol,
        "fills": list(fills),
    }


def _fill(*, filled_qty: float, fill_price: float) -> dict:
    return {"filled_qty": filled_qty, "fill_price": fill_price}


_BUY_ACTION = {
    "type": "buy",
    "sizing": {"mode": "shares", "shares": 10},
    "stop_loss_pct": 0.05,
}
_SELL_ACTION = {"type": "sell", "sizing": {"mode": "shares", "shares": 5}}
_SET_STOP_ACTION = {"type": "set_stop", "trail_percent": 0.05}


# ---------------------------------------------------------------------------
# PART 1 — sleeves.ledger.reconstruct_from_history (pure function)
# ---------------------------------------------------------------------------


class TestReconstructFromHistory:
    def test_empty_history_returns_a_fresh_ledger(self):
        result = ledger.reconstruct_from_history(10_000.0, [])
        assert result == ledger.new_ledger(10_000.0)

    def test_single_fully_filled_buy_order_matches_direct_reserve_then_fill(self):
        history = [
            _order_row(
                side="buy",
                qty=10.0,
                reserved_price=100.0,
                status="filled",
                fills=[_fill(filled_qty=10.0, fill_price=99.5)],
            )
        ]
        result = ledger.reconstruct_from_history(10_000.0, history)

        expected = ledger.new_ledger(10_000.0)
        expected = ledger.reserve(expected, 10.0 * 100.0)
        expected = ledger.apply_fill(
            expected, symbol="SPY", side="buy", qty=10.0, price=99.5, reserved_usd=10.0 * 100.0
        )
        assert result == expected

    def test_partially_filled_still_open_order_keeps_the_remainder_reserved(self):
        # status is NOT in the terminal set -- the unfilled remainder must
        # stay reserved (the order could still fill more).
        history = [
            _order_row(
                side="buy",
                qty=10.0,
                reserved_price=100.0,
                status="partially_filled",
                fills=[_fill(filled_qty=4.0, fill_price=100.0)],
            )
        ]
        result = ledger.reconstruct_from_history(10_000.0, history)

        expected = ledger.new_ledger(10_000.0)
        expected = ledger.reserve(expected, 10.0 * 100.0)
        expected = ledger.apply_fill(
            expected, symbol="SPY", side="buy", qty=4.0, price=100.0, reserved_usd=4.0 * 100.0
        )
        assert result == expected
        assert result.reserved_usd == pytest.approx(6.0 * 100.0)

    def test_partially_filled_rejected_order_releases_the_unfilled_remainder(self):
        # status IS terminal -- the order will never fill more, so the
        # unfilled remainder must be released back to cash.
        history = [
            _order_row(
                side="buy",
                qty=10.0,
                reserved_price=100.0,
                status="rejected",
                fills=[_fill(filled_qty=4.0, fill_price=100.0)],
            )
        ]
        result = ledger.reconstruct_from_history(10_000.0, history)

        expected = ledger.new_ledger(10_000.0)
        expected = ledger.reserve(expected, 10.0 * 100.0)
        expected = ledger.apply_fill(
            expected, symbol="SPY", side="buy", qty=4.0, price=100.0, reserved_usd=4.0 * 100.0
        )
        expected = ledger.release(expected, 6.0 * 100.0)
        assert result == expected
        assert result.reserved_usd == pytest.approx(0.0)

    def test_unrecognized_status_fails_closed_stays_reserved(self):
        # Matches get_daily_turnover_usd's own fail-closed precedent: an
        # unrecognized/future status is treated as STILL open (over-reserve
        # is the safe direction), never silently released.
        history = [
            _order_row(
                side="buy",
                qty=10.0,
                reserved_price=100.0,
                status="some_future_status_not_yet_invented",
                fills=[_fill(filled_qty=4.0, fill_price=100.0)],
            )
        ]
        result = ledger.reconstruct_from_history(10_000.0, history)
        assert result.reserved_usd == pytest.approx(6.0 * 100.0)

    def test_sell_order_never_reserves_cash_and_realizes_pnl(self):
        history = [
            _order_row(
                side="buy",
                qty=10.0,
                reserved_price=100.0,
                status="filled",
                fills=[_fill(filled_qty=10.0, fill_price=100.0)],
            ),
            _order_row(
                side="sell",
                qty=4.0,
                reserved_price=None,
                status="filled",
                fills=[_fill(filled_qty=4.0, fill_price=110.0)],
            ),
        ]
        result = ledger.reconstruct_from_history(10_000.0, history)

        expected = ledger.new_ledger(10_000.0)
        expected = ledger.reserve(expected, 10.0 * 100.0)
        expected = ledger.apply_fill(
            expected, symbol="SPY", side="buy", qty=10.0, price=100.0, reserved_usd=10.0 * 100.0
        )
        expected = ledger.apply_fill(
            expected, symbol="SPY", side="sell", qty=4.0, price=110.0, reserved_usd=0.0
        )
        assert result == expected
        assert result.realized_pnl_usd > 0.0

    def test_conservation_law_holds_after_a_mixed_multi_order_history(self):
        history = [
            _order_row(
                side="buy",
                qty=10.0,
                reserved_price=100.0,
                status="filled",
                fills=[_fill(filled_qty=10.0, fill_price=98.0)],
            ),
            _order_row(
                side="buy",
                qty=5.0,
                reserved_price=105.0,
                status="rejected",
                fills=[],
            ),
            _order_row(
                side="sell",
                qty=3.0,
                reserved_price=None,
                status="filled",
                fills=[_fill(filled_qty=3.0, fill_price=101.0)],
            ),
        ]
        result = ledger.reconstruct_from_history(10_000.0, history)
        conserved = (
            result.cash_usd
            + result.reserved_usd
            + sum(p.cost_basis_usd for p in result.positions.values())
        )
        assert conserved == pytest.approx(result.capital_usd + result.realized_pnl_usd)


# ---------------------------------------------------------------------------
# PART 2 — actions.py: the armed-path money-safety sequence
# ---------------------------------------------------------------------------


class TestInsufficientCashRefusal:
    @pytest.mark.parametrize("shadow", [True, False])
    def test_buy_exceeding_available_cash_is_refused_with_no_order_row_or_broker_call(self, shadow):
        # capital_usd=10_000, but history already reserved almost all of it.
        near_exhausting_history = [
            _order_row(side="buy", qty=98.0, reserved_price=100.0, status="new", fills=[])
        ]
        ctx = _ctx(capital_usd=10_000.0)
        approved_clamp = envelope.ClampResult(
            approved=True, qty=10.0, original_qty=10.0, clamped=False, reason=None
        )
        with (
            patch.object(
                database, "get_sleeve_order_history", return_value=near_exhausting_history
            ),
            patch.object(envelope, "clamp_order", return_value=approved_clamp),
            patch.object(database, "insert_sleeve_order") as mock_insert,
            patch.object(alpaca_orders, "submit_bracket_order") as mock_bracket,
        ):
            result = actions.dispatch_action(_BUY_ACTION, ctx=ctx, shadow=shadow)
        mock_insert.assert_not_called()
        mock_bracket.assert_not_called()
        assert result.executed is False
        assert result.refused_reason == "insufficient_cash"
        assert result.order_id is None

    def test_buy_within_available_cash_is_not_refused_for_cash(self):
        ctx = _ctx(capital_usd=10_000.0)
        approved_clamp = envelope.ClampResult(
            approved=True, qty=10.0, original_qty=10.0, clamped=False, reason=None
        )
        with (
            patch.object(database, "get_sleeve_order_history", return_value=[]),
            patch.object(envelope, "clamp_order", return_value=approved_clamp),
        ):
            result = actions.dispatch_action(_BUY_ACTION, ctx=ctx, shadow=True)
        assert result.refused_reason != "insufficient_cash"


class TestReservedRowBeforeBrokerCall:
    def test_armed_buy_inserts_a_reserved_order_row_before_calling_submit_bracket_order(self):
        ctx = _ctx(capital_usd=10_000.0)
        approved_clamp = envelope.ClampResult(
            approved=True, qty=10.0, original_qty=10.0, clamped=False, reason=None
        )
        call_order = []

        def _insert_side_effect(*args, **kwargs):
            call_order.append("insert_sleeve_order")
            assert kwargs.get("status") == "RESERVED"
            assert kwargs.get("client_order_id"), "a client_order_id must be minted before insert"
            return 42

        def _bracket_side_effect(*args, **kwargs):
            call_order.append("submit_bracket_order")
            return alpaca_orders.OrderResult(
                order={"id": "broker-1", "status": "accepted"}, error=None
            )

        with (
            patch.object(database, "get_sleeve_order_history", return_value=[]),
            patch.object(envelope, "clamp_order", return_value=approved_clamp),
            patch.object(database, "insert_sleeve_order", side_effect=_insert_side_effect),
            patch.object(alpaca_orders, "submit_bracket_order", side_effect=_bracket_side_effect),
            patch.object(database, "attach_alpaca_order_id") as mock_attach,
        ):
            result = actions.dispatch_action(_BUY_ACTION, ctx=ctx, shadow=False)

        assert call_order == ["insert_sleeve_order", "submit_bracket_order"], (
            f"the RESERVED row must be inserted BEFORE the broker call (invariant #6 "
            f"lost-ack recovery), got order: {call_order}"
        )
        assert result.order_id == 42
        assert mock_attach.called

    def test_armed_buy_success_attaches_the_brokers_own_order_id(self):
        ctx = _ctx(capital_usd=10_000.0)
        approved_clamp = envelope.ClampResult(
            approved=True, qty=10.0, original_qty=10.0, clamped=False, reason=None
        )
        with (
            patch.object(database, "get_sleeve_order_history", return_value=[]),
            patch.object(envelope, "clamp_order", return_value=approved_clamp),
            patch.object(database, "insert_sleeve_order", return_value=42),
            patch.object(
                alpaca_orders,
                "submit_bracket_order",
                return_value=alpaca_orders.OrderResult(
                    order={"id": "broker-order-xyz", "status": "accepted"}, error=None
                ),
            ),
            patch.object(database, "attach_alpaca_order_id") as mock_attach,
            patch.object(database, "update_sleeve_order_status") as mock_status,
        ):
            actions.dispatch_action(_BUY_ACTION, ctx=ctx, shadow=False)
        assert mock_attach.called
        _, kwargs = mock_attach.call_args
        args, _ = mock_attach.call_args
        # accept either positional or keyword calling convention
        alpaca_id_arg = kwargs.get("alpaca_order_id") or (args[1] if len(args) > 1 else None)
        assert alpaca_id_arg == "broker-order-xyz"
        mock_status.assert_not_called()

    def test_armed_buy_broker_rejection_marks_the_row_terminal_never_attaching_an_id(self):
        ctx = _ctx(capital_usd=10_000.0)
        approved_clamp = envelope.ClampResult(
            approved=True, qty=10.0, original_qty=10.0, clamped=False, reason=None
        )
        with (
            patch.object(database, "get_sleeve_order_history", return_value=[]),
            patch.object(envelope, "clamp_order", return_value=approved_clamp),
            patch.object(database, "insert_sleeve_order", return_value=42),
            patch.object(
                alpaca_orders,
                "submit_bracket_order",
                return_value=alpaca_orders.OrderResult(order=None, error="HTTP 422"),
            ),
            patch.object(database, "attach_alpaca_order_id") as mock_attach,
            patch.object(database, "update_sleeve_order_status") as mock_status,
        ):
            result = actions.dispatch_action(_BUY_ACTION, ctx=ctx, shadow=False)
        mock_attach.assert_not_called()
        assert mock_status.called, (
            "a broker rejection must mark the sleeve_orders row terminal -- this IS the "
            "'release the reservation' mechanism, since the next reconstruct_from_history() "
            "call only releases an unfilled remainder for a row in a terminal status."
        )
        assert result.executed is False

    def test_shadow_buy_never_inserts_a_reserved_row_even_with_sufficient_cash(self):
        ctx = _ctx(capital_usd=10_000.0)
        approved_clamp = envelope.ClampResult(
            approved=True, qty=10.0, original_qty=10.0, clamped=False, reason=None
        )
        with (
            patch.object(database, "get_sleeve_order_history", return_value=[]),
            patch.object(envelope, "clamp_order", return_value=approved_clamp),
            patch.object(database, "insert_sleeve_order") as mock_insert,
            patch.object(alpaca_orders, "submit_bracket_order") as mock_bracket,
        ):
            result = actions.dispatch_action(_BUY_ACTION, ctx=ctx, shadow=True)
        mock_insert.assert_not_called()
        mock_bracket.assert_not_called()
        assert result.order_id is None


class TestNonBuyActionsGetReservationRowButNoCashCheck:
    def test_armed_sell_gets_a_reserved_row_before_submit_order_with_no_cash_check(self):
        ctx = _ctx(capital_usd=10_000.0, current_position_qty=5.0)
        approved_clamp = envelope.ClampResult(
            approved=True, qty=5.0, original_qty=5.0, clamped=False, reason=None
        )
        # A history that would exhaust cash for a BUY must not block a SELL --
        # sells generate cash, they don't spend it.
        exhausted_history = [
            _order_row(side="buy", qty=100.0, reserved_price=100.0, status="new", fills=[])
        ]
        call_order = []
        with (
            patch.object(database, "get_sleeve_order_history", return_value=exhausted_history),
            patch.object(envelope, "clamp_order", return_value=approved_clamp),
            patch.object(
                database,
                "insert_sleeve_order",
                side_effect=lambda *a, **k: (call_order.append("insert"), 99)[1],
            ),
            patch.object(
                alpaca_orders,
                "submit_order",
                side_effect=lambda *a, **k: (
                    call_order.append("submit"),
                    alpaca_orders.OrderResult(
                        order={"id": "b-1", "status": "accepted"}, error=None
                    ),
                )[1],
            ),
            patch.object(database, "attach_alpaca_order_id"),
        ):
            result = actions.dispatch_action(_SELL_ACTION, ctx=ctx, shadow=False)
        assert call_order == ["insert", "submit"]
        assert result.order_id == 99
        assert result.refused_reason != "insufficient_cash"

    def test_armed_set_stop_gets_a_reserved_row_before_submit_trailing_stop_order(self):
        ctx = _ctx(current_position_qty=10.0)
        call_order = []
        with (
            patch.object(
                database,
                "insert_sleeve_order",
                side_effect=lambda *a, **k: (call_order.append("insert"), 55)[1],
            ),
            patch.object(
                alpaca_orders,
                "submit_trailing_stop_order",
                side_effect=lambda *a, **k: (
                    call_order.append("submit"),
                    alpaca_orders.OrderResult(
                        order={"id": "b-2", "status": "accepted"}, error=None
                    ),
                )[1],
            ),
            patch.object(database, "attach_alpaca_order_id"),
        ):
            result = actions.dispatch_action(_SET_STOP_ACTION, ctx=ctx, shadow=False)
        assert call_order == ["insert", "submit"]
        assert result.order_id == 55
