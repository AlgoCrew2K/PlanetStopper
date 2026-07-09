"""RED — audit finding #6 (HIGH): each new fill delta is booked at Alpaca's
CUMULATIVE average price (broker `filled_avg_price`) instead of the delta's
own implied price. The delta quantity math is correct; the price is not —
5 @ $10 then 5 @ $12 books $105 of cost instead of $110 (error =
q1*q2*(p1-p2)/(q1+q2)). Ledger cost basis and cash then drift from broker
truth on every multi-price partial fill: beyond tolerance this FALSE-PAUSES
the whole sleeve group; under it, realized P&L is silently misstated, and
_book_equity_usd's sizing input corrupts.

Pinned contract: the i-th recorded fill's price is derived from cumulative
notionals — (broker_filled_qty * filled_avg_price - sum of already-recorded
qty*price) / delta_qty — so that the SUM of recorded fill notionals always
equals the broker's own cumulative notional.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

import database
import sleeves.alpaca_orders as alpaca_orders
import sleeves.ledger as ledger
import sleeves.tick_orchestrator as tick_orchestrator
from tests.sleeves._alpaca_fixtures import (
    load_order_fixture,
    validate_alpaca_order_shape,
)


def _cumulative_states() -> tuple[dict, dict]:
    """Two consecutive broker-truth snapshots of ONE bracket entry, derived
    from the schema-validated partial-fill fixture: first 5 of 10 filled at a
    $10.00 average, then all 10 filled at an $11.00 CUMULATIVE average (i.e.
    the second 5 shares actually executed at $12.00 — the number the broker
    never states directly)."""
    base = load_order_fixture("bracket_partial_fill.json")

    poll_1 = dict(base)
    poll_1["qty"] = "10"
    poll_1["filled_qty"] = "5"
    poll_1["filled_avg_price"] = "10.00"

    poll_2 = dict(base)
    poll_2["qty"] = "10"
    poll_2["filled_qty"] = "10"
    poll_2["filled_avg_price"] = "11.00"
    poll_2["status"] = "filled"

    for crafted in (poll_1, poll_2):
        assert not validate_alpaca_order_shape(crafted), (
            "fixture sanity: crafted cumulative snapshots must remain valid Alpaca Order shapes"
        )
    return poll_1, poll_2


class TestDeltaFillPricing:
    def test_second_partial_fill_books_at_its_own_implied_price_not_the_average(self):
        capital_usd = 10_000.0
        sleeve_id = database.create_sleeve(
            f"delta-price-{uuid.uuid4().hex}", capital_usd, envelope_json="{}"
        )
        poll_1, poll_2 = _cumulative_states()

        client_order_id = f"delta-price-{uuid.uuid4().hex}"
        order_pk = database.insert_sleeve_order(
            client_order_id=client_order_id,
            sleeve_id=sleeve_id,
            symbol=poll_1["symbol"],
            side=poll_1["side"],
            qty=float(poll_1["qty"]),
            order_class=poll_1["order_class"],
            status="accepted",
            reserved_price=float(poll_2["filled_avg_price"]),
        )
        database.attach_alpaca_order_id(client_order_id, poll_1["id"])

        with patch.object(
            alpaca_orders,
            "get_order",
            return_value=alpaca_orders.OrderResult(order=poll_1, error=None),
        ):
            tick_orchestrator.poll_and_apply_fills(sleeve_id)
        with patch.object(
            alpaca_orders,
            "get_order",
            return_value=alpaca_orders.OrderResult(order=poll_2, error=None),
        ):
            tick_orchestrator.poll_and_apply_fills(sleeve_id)

        fills = database.get_fills_for_order(order_pk)
        assert len(fills) == 2, (
            f"fixture sanity: two polls with growing cumulative qty must "
            f"record exactly two delta fills; got {len(fills)}"
        )

        cum_qty_1 = float(poll_1["filled_qty"])
        avg_1 = float(poll_1["filled_avg_price"])
        cum_qty_2 = float(poll_2["filled_qty"])
        avg_2 = float(poll_2["filled_avg_price"])
        delta_qty = cum_qty_2 - cum_qty_1
        # The delta's own execution price, implied by the two cumulative
        # broker states: (10*11.00 - 5*10.00) / 5 = 12.00 here.
        expected_delta_price = (cum_qty_2 * avg_2 - cum_qty_1 * avg_1) / delta_qty

        assert fills[0]["fill_price"] == pytest.approx(avg_1, rel=1e-9), (
            "the FIRST fill's price equals the first cumulative average (they "
            "are the same thing for a first fill)"
        )
        # rel 1e-9: short-decimal rational arithmetic — only float
        # representation error is acceptable.
        assert fills[1]["fill_price"] == pytest.approx(expected_delta_price, rel=1e-9), (
            f"the second delta fill must book at its own implied price "
            f"{expected_delta_price} — booking it at the cumulative average "
            f"{avg_2} understates cost by q1*q2*(p2_true-p1)/(q1+q2) and "
            f"drifts ledger cash/cost from broker truth on every multi-price "
            f"partial fill (audit #6)"
        )

    def test_ledger_cost_basis_and_cash_match_broker_cumulative_notional(self):
        """The money consequence: after full fill, the position's cost basis
        must equal the broker's own cumulative notional (filled_qty * avg),
        and cash must have decreased by exactly that notional — otherwise the
        aggregate cash check sees phantom drift (false group pause) or
        realized P&L misstates on exit."""
        capital_usd = 10_000.0
        sleeve_id = database.create_sleeve(
            f"delta-cash-{uuid.uuid4().hex}", capital_usd, envelope_json="{}"
        )
        poll_1, poll_2 = _cumulative_states()

        client_order_id = f"delta-cash-{uuid.uuid4().hex}"
        database.insert_sleeve_order(
            client_order_id=client_order_id,
            sleeve_id=sleeve_id,
            symbol=poll_1["symbol"],
            side=poll_1["side"],
            qty=float(poll_1["qty"]),
            order_class=poll_1["order_class"],
            status="accepted",
            reserved_price=float(poll_2["filled_avg_price"]),
        )
        database.attach_alpaca_order_id(client_order_id, poll_1["id"])

        for poll in (poll_1, poll_2):
            with patch.object(
                alpaca_orders,
                "get_order",
                return_value=alpaca_orders.OrderResult(order=poll, error=None),
            ):
                tick_orchestrator.poll_and_apply_fills(sleeve_id)

        broker_cumulative_notional = float(poll_2["filled_qty"]) * float(poll_2["filled_avg_price"])
        state = ledger.reconstruct_from_history(
            capital_usd, database.get_sleeve_order_history(sleeve_id)
        )
        position = state.positions[poll_2["symbol"]]

        # 1e-6 absolute dollars: float bookkeeping of fixture-derived values.
        assert position.cost_basis_usd == pytest.approx(broker_cumulative_notional, abs=1e-6), (
            f"ledger cost basis {position.cost_basis_usd:.2f} must equal the "
            f"broker's cumulative notional {broker_cumulative_notional:.2f} "
            f"(filled_qty x filled_avg_price) — the drift IS finding #6"
        )
        assert state.cash_usd == pytest.approx(
            capital_usd - broker_cumulative_notional, abs=1e-6
        ), (
            f"sleeve cash {state.cash_usd:.2f} must reflect exactly the "
            f"broker's cumulative spend; anything else surfaces as phantom "
            f"aggregate-cash drift (false group pause) or hidden P&L error"
        )


class TestMissingAvgPriceNeverPoisonsTheLedger:
    def test_fill_with_null_avg_price_is_deferred_not_booked_at_zero(self):
        """Ratified edge pin (sf-eng proposal, 2026-07-09): a broker snapshot
        reporting filled_qty > 0 with filled_avg_price still null (Alpaca
        propagation lag) currently books a fill at price 0.0 — which later
        CRASHES ledger reconstruction (price <= 0 rejected) for every
        consumer of this sleeve's history, forever. Contract: defer — record
        nothing for that order this tick, do NOT advance it to a terminal
        status (a terminal row is never re-polled, so its fill would be lost
        permanently), and retry next poll."""
        capital_usd = 10_000.0
        sleeve_id = database.create_sleeve(
            f"nullavg-{uuid.uuid4().hex}", capital_usd, envelope_json="{}"
        )

        base = load_order_fixture("bracket_partial_fill.json")
        laggy = dict(base)
        laggy["filled_qty"] = "4"
        laggy["filled_avg_price"] = None  # broker propagation lag
        assert not validate_alpaca_order_shape(laggy), (
            "fixture sanity: null filled_avg_price is a valid Alpaca Order "
            "shape (bracket_new.json ships exactly that)"
        )

        client_order_id = f"nullavg-{uuid.uuid4().hex}"
        order_pk = database.insert_sleeve_order(
            client_order_id=client_order_id,
            sleeve_id=sleeve_id,
            symbol=laggy["symbol"],
            side=laggy["side"],
            qty=float(laggy["qty"]),
            order_class=laggy["order_class"],
            status="accepted",
            reserved_price=500.0,
        )
        database.attach_alpaca_order_id(client_order_id, laggy["id"])

        with patch.object(
            alpaca_orders,
            "get_order",
            return_value=alpaca_orders.OrderResult(order=laggy, error=None),
        ):
            tick_orchestrator.poll_and_apply_fills(sleeve_id)

        fills = database.get_fills_for_order(order_pk)
        assert all(f["fill_price"] > 0 for f in fills), (
            "a 0.0-price fill row is poison — every later "
            "reconstruct_from_history call raises on it (price <= 0), "
            "killing panel, reconciliation, and P&L for this sleeve forever"
        )
        assert fills == [], (
            "with no honest price available, no fill may be recorded this "
            "tick — the delta stays pending for the next poll"
        )
        row = database.get_sleeve_order_by_client_id(client_order_id)
        assert row["status"] not in (
            "filled",
            "canceled",
            "expired",
            "replaced",
            "done_for_day",
            "rejected",
        ), (
            f"the row must stay NON-terminal while its broker-reported fill "
            f"is unrecorded (got {row['status']!r}) — a terminal row is "
            f"never re-polled, so advancing it here loses the fill "
            f"permanently"
        )

        # The ledger must still fold cleanly (nothing poisoned).
        state = ledger.reconstruct_from_history(
            capital_usd, database.get_sleeve_order_history(sleeve_id)
        )
        assert state.capital_usd == capital_usd
