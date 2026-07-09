"""RED — audit finding #4 (HIGH): the step-0 cancel runs BEFORE the fill poll,
so an order that FILLED at the broker gets locally marked "canceled" — the
fill is then permanently unrecorded (poll skips terminal rows forever), the
full reservation is released back to cash, and the resulting naked broker
position is invisible to reconciliation. Live-observed with real paper money
(audit smoke pass 1: broker filled 1 share, DB said canceled, ledger said full
cash, both protective legs cancelled at the broker).

Empirical broker semantics carried from the audit (INFO-002): Alpaca accepts
DELETE on a FILLED bracket parent (it cancels the LEGS) — so "the cancel
succeeded" must NEVER be read as "the order didn't fill".

Pinned contract (end-state only — whether GREEN polls before cancelling,
re-polls after the cancel response, or reconciles filled_qty from the cancel
response is implementation freedom):
  * an order the broker reports FILLED is never left marked "canceled";
  * its fill quantities/prices are recorded in sleeve_fills (broker-derived);
  * the ledger reflects the position and spent cash — the reservation is
    resolved by the fill, never released as if nothing happened;
  * a genuinely canceled order that PARTIALLY filled first keeps its partial
    fill and releases only the unfilled remainder.
"""

from __future__ import annotations

import uuid

import database
import sleeves.alpaca_orders as alpaca_orders
import sleeves.ledger as ledger
import sleeves.tick_orchestrator as tick_orchestrator
from tests.sleeves._alpaca_fixtures import (
    load_order_fixture,
    validate_alpaca_order_shape,
)
from tests.sleeves._route_shaped import (
    MARKET_OPEN_NOW_UTC,
    make_broker_position,
    tick_patches,
)


def _make_shadow_sleeve(capital_usd: float) -> int:
    """A freshly created sleeve is SHADOW — the exact disarmed state whose
    step-0 cleanup destroyed the audit's live fill."""
    return database.create_sleeve(
        f"fill-vs-cancel-{uuid.uuid4().hex}", capital_usd, envelope_json="{}"
    )


def _seed_acked_order(sleeve_id: int, broker_order: dict, *, reserved_price: float) -> str:
    client_order_id = f"fvc-{uuid.uuid4().hex}"
    database.insert_sleeve_order(
        client_order_id=client_order_id,
        sleeve_id=sleeve_id,
        symbol=broker_order["symbol"],
        side=broker_order["side"],
        qty=float(broker_order["qty"]),
        order_class=broker_order["order_class"],
        status="accepted",
        reserved_price=reserved_price,
    )
    database.attach_alpaca_order_id(client_order_id, broker_order["id"])
    return client_order_id


class TestFilledOrderSurvivesShadowCleanup:
    def test_broker_filled_order_is_recorded_as_filled_never_canceled(self):
        capital_usd = 10_000.0
        sleeve_id = _make_shadow_sleeve(capital_usd)
        broker_order = load_order_fixture("bracket_filled.json")
        filled_qty = float(broker_order["filled_qty"])
        fill_price = float(broker_order["filled_avg_price"])
        reserved_price = fill_price  # entry reserved at the eventual fill price

        client_order_id = _seed_acked_order(sleeve_id, broker_order, reserved_price=reserved_price)

        # Broker truth AFTER the fill: cash reduced by the fill notional, the
        # shares held — derived from the fixture, never hardcoded.
        post_fill_cash = capital_usd - filled_qty * fill_price
        with tick_patches(
            account_cash_usd=post_fill_cash,
            broker_positions=[make_broker_position(broker_order["symbol"], filled_qty, fill_price)],
            get_order_result=alpaca_orders.OrderResult(order=broker_order, error=None),
            # INFO-002: the DELETE "succeeds" even though the parent filled —
            # the cancel result must not be allowed to overwrite fill truth.
            cancel_order_result=alpaca_orders.OrderResult(order={}, error=None),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        row = database.get_sleeve_order_by_client_id(client_order_id)
        assert row["status"] == broker_order["status"], (
            f"the broker reports this order {broker_order['status']!r}; the DB "
            f"says {row['status']!r} — marking a FILLED order 'canceled' "
            f"destroys the fill record permanently (audit finding #4, "
            f"live-observed losing a real 1-share fill)"
        )

        recorded = database.get_fills_for_order(row["id"])
        assert recorded, (
            "the broker-reported fill must be recorded in sleeve_fills — a "
            "fill landing between ack and a cancel attempt is money truth, "
            "not noise"
        )
        total_recorded = sum(f["filled_qty"] for f in recorded)
        # rel 1e-9: pure float bookkeeping of fixture-derived values — no
        # arithmetic beyond sum, so only representation error is tolerated.
        assert abs(total_recorded - filled_qty) <= 1e-9 * filled_qty

    def test_ledger_reflects_the_fill_reservation_never_released_as_cash(self):
        """The money corollary: after the fill is recorded, the sleeve's
        reconstructed ledger must hold the position with the cash spent —
        NOT full original cash with zero positions (the state the audit
        observed: ledger 500.00/0.00/{} while the broker held the share)."""
        capital_usd = 10_000.0
        sleeve_id = _make_shadow_sleeve(capital_usd)
        broker_order = load_order_fixture("bracket_filled.json")
        filled_qty = float(broker_order["filled_qty"])
        fill_price = float(broker_order["filled_avg_price"])

        _seed_acked_order(sleeve_id, broker_order, reserved_price=fill_price)

        post_fill_cash = capital_usd - filled_qty * fill_price
        with tick_patches(
            account_cash_usd=post_fill_cash,
            broker_positions=[make_broker_position(broker_order["symbol"], filled_qty, fill_price)],
            get_order_result=alpaca_orders.OrderResult(order=broker_order, error=None),
            cancel_order_result=alpaca_orders.OrderResult(order={}, error=None),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        state = ledger.reconstruct_from_history(
            capital_usd, database.get_sleeve_order_history(sleeve_id)
        )
        position = state.positions.get(broker_order["symbol"])
        assert position is not None and abs(position.qty - filled_qty) <= 1e-9 * filled_qty, (
            f"the ledger must hold the {filled_qty} filled shares of "
            f"{broker_order['symbol']}; got {position!r} — a zero-position "
            f"ledger while the broker holds shares bought with sleeve cash is "
            f"the invisible-naked-position state finding #4 produced live"
        )
        # rel-1e-9 float bookkeeping tolerance (fixture-derived arithmetic).
        assert abs(state.cash_usd - post_fill_cash) <= 1e-6, (
            f"sleeve cash must reflect the spend "
            f"({post_fill_cash:.2f}); got {state.cash_usd:.2f} — full-capital "
            f"cash means the reservation was released as if the order never "
            f"filled"
        )
        assert abs(state.reserved_usd) <= 1e-6, (
            "the reservation must be RESOLVED by the recorded fill (not left "
            "open, not released to cash)"
        )


class TestCanceledAfterPartialFill:
    def test_partial_fill_recorded_and_only_remainder_released(self):
        """A cancel that lands after a PARTIAL fill: 'canceled' is the correct
        terminal status, but the partial fill must be recorded and only the
        unfilled remainder's reservation released."""
        capital_usd = 10_000.0
        sleeve_id = _make_shadow_sleeve(capital_usd)

        # Broker truth: canceled AFTER partially filling — status/base shape
        # from the canceled fixture, fill figures from the partial-fill
        # fixture (both schema-validated shapes).
        canceled_base = load_order_fixture("bracket_canceled.json")
        partial = load_order_fixture("bracket_partial_fill.json")
        broker_order = dict(canceled_base)
        broker_order["filled_qty"] = partial["filled_qty"]
        broker_order["filled_avg_price"] = partial["filled_avg_price"]
        assert not validate_alpaca_order_shape(broker_order), (
            "fixture sanity: the crafted canceled-after-partial-fill order "
            "must still be a valid Alpaca Order shape"
        )

        filled_qty = float(broker_order["filled_qty"])
        fill_price = float(broker_order["filled_avg_price"])
        _seed_acked_order(sleeve_id, broker_order, reserved_price=fill_price)

        post_state_cash = capital_usd - filled_qty * fill_price
        with tick_patches(
            account_cash_usd=post_state_cash,
            broker_positions=[make_broker_position(broker_order["symbol"], filled_qty, fill_price)],
            get_order_result=alpaca_orders.OrderResult(order=broker_order, error=None),
            cancel_order_result=alpaca_orders.OrderResult(order={}, error=None),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        history = database.get_sleeve_order_history(sleeve_id)
        assert history and history[0]["fills"], (
            "the partial fill must be recorded even though the order's "
            "terminal status is 'canceled' — cancel-without-fill-recording "
            "is exactly how finding #4 loses money"
        )
        total_recorded = sum(f["filled_qty"] for f in history[0]["fills"])
        assert abs(total_recorded - filled_qty) <= 1e-9 * filled_qty

        state = ledger.reconstruct_from_history(capital_usd, history)
        position = state.positions.get(broker_order["symbol"])
        assert position is not None and abs(position.qty - filled_qty) <= 1e-9 * filled_qty
        # 1e-6 absolute: dollars from float bookkeeping of fixture values.
        assert abs(state.cash_usd - post_state_cash) <= 1e-6, (
            f"cash must equal capital minus the PARTIAL fill notional "
            f"({post_state_cash:.2f}) — the unfilled remainder released, the "
            f"filled portion spent; got {state.cash_usd:.2f}"
        )
        assert abs(state.reserved_usd) <= 1e-6


class TestNakedPositionVisibleToReconciliation:
    def test_broker_position_in_history_symbol_with_zero_ledger_qty_pauses(self):
        """The reconciliation-SCOPE half of audit #4 (review gap G1): the
        aggregate position check only inspects symbols where some LEDGER
        holds nonzero qty (tick_orchestrator symbol_holders), so the exact
        naked-position state the audit produced live — order marked
        canceled, ZERO fills recorded, ledger says full cash / no position,
        while the broker holds shares bought with sleeve cash — is
        structurally invisible: the symbol never enters the check, and
        operator float absorbs the one-sided cash comparison.

        Pinned contract (the audit's own fix guidance): reconciliation scope
        extends to symbols with recorded sleeve ORDER history. A broker
        position in such a symbol while every sleeve's ledger qty is zero is
        unexplained drift -> the sleeves with history in that symbol pause.
        (A symbol NO sleeve ever touched remains ignored — the
        operator-external-holding exemption is untouched; how GREEN bounds
        the history window for legitimately-closed positions is design
        freedom, but THIS state — terminal order, zero fills, broker holds —
        must pause.)"""
        capital_usd = 10_000.0
        sleeve_id = _make_shadow_sleeve(capital_usd)

        # The audit's exact residue: a canceled order with ZERO recorded
        # fills (the lost-fill state finding #4's other half prevents going
        # FORWARD; this pin makes the already-corrupted state DETECTABLE).
        naked_qty, naked_price = 10.0, 500.0
        database.insert_sleeve_order(
            client_order_id=f"naked-{uuid.uuid4().hex}",
            sleeve_id=sleeve_id,
            symbol="SPY",
            side="buy",
            qty=naked_qty,
            order_class="bracket",
            status="canceled",
            reserved_price=naked_price,
        )
        state = ledger.reconstruct_from_history(
            capital_usd, database.get_sleeve_order_history(sleeve_id)
        )
        assert not any(p.qty for p in state.positions.values()), (
            "fixture sanity: the ledger must believe the sleeve is flat"
        )

        # Broker truth: the shares exist, and OPERATOR FLOAT covers the cash
        # gap so the one-sided aggregate cash check cannot catch it — the
        # exact masking the audit called out.
        with tick_patches(
            account_cash_usd=capital_usd + 5_000.0,  # float absorbs the drift
            broker_positions=[make_broker_position("SPY", naked_qty, naked_price)],
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        assert database.get_sleeve(sleeve_id)["status"] == "PAUSED_RECONCILIATION", (
            "a broker position in a symbol this sleeve has ORDER history in, "
            "with zero ledger qty anywhere, is an unexplained naked position "
            "— it must pause the sleeve, not vanish into the "
            "operator-external exemption (audit #4's 'invisible to "
            "reconciliation by design' + review gap G1)"
        )

    def test_symbol_no_sleeve_ever_touched_is_still_ignored(self):
        """Companion guard: extending scope to history symbols must NOT
        revoke the operator-external exemption — a broker holding in a
        symbol with zero sleeve order history stays invisible."""
        sleeve_id = _make_shadow_sleeve(1_000.0)

        with tick_patches(
            account_cash_usd=1_000.0,
            broker_positions=[make_broker_position("TSLA", 50.0, 200.0)],
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        assert database.get_sleeve(sleeve_id)["status"] != "PAUSED_RECONCILIATION", (
            "TSLA has zero sleeve order history — the operator's own holding "
            "sharing the account must never pause anyone (the exemption the "
            "G1 fix must preserve)"
        )
