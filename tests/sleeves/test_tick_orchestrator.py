"""
RED tests — Managed Sleeves P3: sleeves/tick_orchestrator.py (new P3 module).

CONTRACT this file specifies for the GREEN implementer (s3-engine):

    sleeves/tick_orchestrator.py

    def poll_and_apply_fills(
        sleeve_id: int, *, live_mode: bool = False, live_keys_present: bool = False,
    ) -> list[dict]:
        # For every non-terminal sleeve_orders row belonging to sleeve_id that
        # already has an alpaca_order_id (post-ack — a still-RESERVED pre-ack
        # row is skipped, never polled), calls sleeves.alpaca_orders.get_order
        # to fetch broker-truth status. When the broker reports fill quantity
        # beyond what is already recorded in sleeve_fills for that order,
        # inserts the delta as a new sleeve_fills row (database.insert_sleeve_fill)
        # and advances the order's status to the broker's own status string
        # (database.update_sleeve_order_status) — never invents a status.
        # Returns the list of newly-inserted fill dicts (empty if nothing new).
        #
        # BLOCK 2 fix (s3-review, 2026-07-08): a broker error
        # (OrderResult.error is not None) is no longer a silent `continue` —
        # it must log a WARNING (module logger) before moving to the next
        # order. sleeves/alpaca_orders.py never logs internally by design
        # (its own never-raises contract), so this is the ONLY place in the
        # stack that can surface a persistent broker outage/auth failure on
        # the fill-polling path to an operator.

    RETIRED (BLOCK, s3-ux live finding + PM ruling, 2026-07-08):
    reconcile_sleeve_or_pause -- the per-sleeve function that compared ONE
    sleeve's ledger cash against sleeves.alpaca_orders.get_account's
    WHOLE-ACCOUNT cash figure. The broker has no concept of our virtual
    per-sleeve partitioning, so every sleeve's own (necessarily smaller)
    capital differed from the full account total by far more than any
    tolerance -- s3-ux's live render against a real 7-sleeve seeded DB
    reproduced exactly this: all 7 sleeves paused within one tick. See the
    "Shared-account reconciliation semantics" section below (and
    tests/sleeves/test_reconciliation.py's reconcile_aggregate_cash /
    reconcile_aggregate_position) for the correct replacement contract.
    Whatever internal function(s) the GREEN implementer uses to replace it
    are an implementation detail this file does not pin by name -- only the
    end-to-end behavior via run_sleeve_tick_for_all_sleeves is pinned.

    def cancel_open_orders_for_shadow_sleeve(
        sleeve_id: int, *, live_mode: bool = False, live_keys_present: bool = False,
        discord_webhook_url: str | None = None,
    ) -> list[dict]:
        # AC-12 disarm support (design correction, 2026-07-08 — see
        # tests/app/test_sleeves_disarm_and_envelope.py's module docstring):
        # the POST /api/sleeves/<id>/disarm ROUTE never itself reaches
        # sleeves.alpaca_orders (would trip test_dashboard_no_order_path.py's
        # whole-app.py containment scan) — it only reverts the sleeve/rules
        # to SHADOW synchronously. THIS function is where the actual broker
        # cancellation happens: called by run_sleeve_tick_for_all_sleeves for
        # every SHADOW-status sleeve, it cancels every non-terminal
        # sleeve_orders row via sleeves.alpaca_orders.cancel_order and NEVER
        # touches positions or broker-side stops (no close_position/
        # liquidate_position call). A sleeve that has always been SHADOW and
        # never armed simply has zero non-terminal orders, so this is a
        # no-op for it. Returns the list of order dicts that were cancelled.
        #
        # BLOCK 2 fix (s3-review, 2026-07-08): a broker error on cancel_order
        # is no longer a silent `continue`. It must (a) log a WARNING, and
        # (b) best-effort post a Discord alert (new discord_webhook_url
        # param, mirrors reconcile_sleeve_or_pause's own alert pattern —
        # never raises on webhook failure). This path is safety-critical: an
        # operator who clicked disarm must never be left believing an order
        # is cancelled when the broker actually rejected the cancel request.

    def run_sleeve_tick_for_all_sleeves(
        *, now_utc: datetime, discord_webhook_url: str | None = None,
    ) -> list:
        # For every sleeve returned by database.get_all_sleeves():
        #   0. if the sleeve's status is "SHADOW", ALSO calls
        #      cancel_open_orders_for_shadow_sleeve for it (cleans up any
        #      lingering orders from a just-disarmed sleeve), passing
        #      discord_webhook_url through.
        #
        #      BLOCK 1 fix (s3-review, 2026-07-08): a SHADOW-status sleeve is
        #      NOT exempt from fill-polling/reconciliation — every disarmed
        #      sleeve stays SHADOW permanently until re-armed, and can still
        #      hold real residual broker-side exposure (an order that filled
        #      at the broker in the TOCTOU window between the disarm click
        #      and this tick's cancel attempt, or a position from before it
        #      was disarmed). So steps 1-2 below now run for EVERY sleeve
        #      regardless of status (SHADOW included) — only "already
        #      PAUSED_RECONCILIATION coming into this tick" skips them.
        #   1. poll_and_apply_fills — for every sleeve (see BLOCK 1 above).
        #   2. AGGREGATE reconciliation (BLOCK, s3-ux live finding + PM
        #      ruling, 2026-07-08 — see the "Shared-account reconciliation
        #      semantics" section below): computed ONCE per tick across ALL
        #      non-already-paused sleeves together, never per sleeve against
        #      the whole account. A cash breach pauses EVERY sleeve; a
        #      per-symbol position breach pauses only the sleeves holding
        #      that symbol. Either pause this tick (or a sleeve already
        #      PAUSED_RECONCILIATION coming in) skips rule evaluation for
        #      that sleeve this tick. SHADOW sleeves participate in this
        #      aggregate check exactly like any other sleeve — SHADOW is not
        #      a drift-detection exemption.
        #   3. otherwise, assembles the sleeve's enabled rules + sense context
        #      and calls sleeves.rules.runner.evaluate_rules for it — this
        #      still includes SHADOW-status sleeves (AC-6: a SHADOW rule
        #      senses/evaluates/records fires; only PAUSED_RECONCILIATION
        #      skips rule evaluation).
        # A single sleeve's exception during any of the above is caught and
        # logged; processing continues for the remaining sleeves (mirrors the
        # engine-wiring fail-safe contract in alpha_bot_execution.main()).

Fixture-derivation rule: every producer value asserted here (fill price/qty,
broker cash/position figures) comes from the JSON fixture at assertion time —
never a hardcoded literal expectation (feedback_no_hardcoded_test_values).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip(
    "sleeves.tick_orchestrator", reason="RED phase — sleeves.tick_orchestrator not implemented yet"
)

import database  # noqa: E402
import sleeves.tick_orchestrator as tick_orchestrator  # noqa: E402
from sleeves import alpaca_orders  # noqa: E402
from tests.sleeves._alpaca_fixtures import (  # noqa: E402
    load_account_fixture,
    load_order_fixture,
    load_positions_fixture,
)

_NOW_UTC = datetime(2026, 7, 8, 14, 31, 0, tzinfo=UTC)


def _make_sleeve(capital_usd: float = 10000.0, name: str | None = None) -> int:
    """Create a sleeve with a guaranteed-unique name (sleeves.name is UNIQUE).

    A caller-supplied name is honored verbatim; otherwise a fresh uuid4-suffixed
    name is generated so multiple calls within the SAME test (e.g. a two-sleeve
    scenario) never collide on the UNIQUE constraint — bit us for real once
    tests/sleeves/test_tick_orchestrator.py stopped being importorskip-skipped
    and this file's tests started actually running against real GREEN code.
    """
    if name is None:
        name = f"test-sleeve-{uuid.uuid4().hex}"
    return database.create_sleeve(name, capital_usd, envelope_json="{}")


def _make_matching_account(cash_usd: float) -> dict:
    """A minimal Alpaca Account fixture shape whose cash matches cash_usd —
    used by tests that need a CLEAN (non-breaching) broker-truth double so
    the aggregate reconciliation step doesn't interfere with whatever else
    the test is actually checking (never live network; house rule)."""
    return {
        "id": f"acct-clean-{uuid.uuid4().hex}",
        "account_number": "PA0000CLEANTEST",
        "status": "ACTIVE",
        "currency": "USD",
        "cash": str(cash_usd),
        "portfolio_value": str(cash_usd),
        "buying_power": str(cash_usd),
        "equity": str(cash_usd),
        "pattern_day_trader": False,
        "trading_blocked": False,
        "account_blocked": False,
        "shorting_enabled": False,
    }


# ---------------------------------------------------------------------------
# poll_and_apply_fills
# ---------------------------------------------------------------------------


class TestPollAndApplyFills:
    def test_newly_filled_order_records_a_fill_derived_from_broker_fixture(self):
        sleeve_id = _make_sleeve()
        fixture = load_order_fixture("bracket_filled.json")

        client_order_id = "poll-test-co-1"
        order_pk = database.insert_sleeve_order(
            client_order_id=client_order_id,
            sleeve_id=sleeve_id,
            symbol=fixture["symbol"],
            side=fixture["side"],
            qty=float(fixture["qty"]),
            order_class=fixture["order_class"],
            status="accepted",
            reserved_price=float(fixture["filled_avg_price"]),
        )
        database.attach_alpaca_order_id(client_order_id, fixture["id"])

        with patch.object(
            alpaca_orders,
            "get_order",
            return_value=alpaca_orders.OrderResult(order=fixture, error=None),
        ) as mock_get_order:
            new_fills = tick_orchestrator.poll_and_apply_fills(sleeve_id)

        mock_get_order.assert_called()
        assert new_fills, (
            "poll_and_apply_fills must record at least one new fill for an "
            "order the broker now reports as filled."
        )

        recorded = database.get_fills_for_order(order_pk)
        assert recorded, "no sleeve_fills row was inserted for the newly-filled order"
        total_recorded_qty = sum(f["filled_qty"] for f in recorded)
        assert total_recorded_qty == pytest.approx(float(fixture["filled_qty"]), rel=1e-9), (
            "recorded fill quantity must equal the broker fixture's filled_qty — "
            "never a hardcoded/guessed value."
        )
        assert recorded[0]["fill_price"] == pytest.approx(
            float(fixture["filled_avg_price"]), rel=1e-9
        ), "recorded fill price must be derived from the fixture's filled_avg_price"

        updated_order = database.get_sleeve_order_by_client_id(client_order_id)
        assert updated_order["status"] == fixture["status"], (
            "order status must advance to the broker's own status string "
            f"({fixture['status']!r}), never an invented synonym"
        )

    def test_pre_ack_reserved_order_with_no_alpaca_id_is_never_polled(self):
        sleeve_id = _make_sleeve()
        database.insert_sleeve_order(
            client_order_id="poll-test-co-preack",
            sleeve_id=sleeve_id,
            symbol="SPY",
            side="buy",
            qty=10.0,
            status="RESERVED",
            reserved_price=500.0,
            # alpaca_order_id intentionally omitted — still NULL, pre-ack.
        )

        with patch.object(alpaca_orders, "get_order") as mock_get_order:
            tick_orchestrator.poll_and_apply_fills(sleeve_id)

        assert not mock_get_order.called, (
            "a pre-ack RESERVED order (alpaca_order_id still NULL) must never "
            "be polled — there is no broker order id to poll yet."
        )

    def test_polling_the_same_fill_twice_does_not_duplicate_the_fill_row(self):
        """Idempotency: once the broker's reported filled_qty is fully
        recorded, a second poll must not insert a duplicate fill."""
        sleeve_id = _make_sleeve()
        fixture = load_order_fixture("bracket_filled.json")

        client_order_id = "poll-test-co-idempotent"
        order_pk = database.insert_sleeve_order(
            client_order_id=client_order_id,
            sleeve_id=sleeve_id,
            symbol=fixture["symbol"],
            side=fixture["side"],
            qty=float(fixture["qty"]),
            order_class=fixture["order_class"],
            status="accepted",
            reserved_price=float(fixture["filled_avg_price"]),
        )
        database.attach_alpaca_order_id(client_order_id, fixture["id"])

        with patch.object(
            alpaca_orders,
            "get_order",
            return_value=alpaca_orders.OrderResult(order=fixture, error=None),
        ):
            tick_orchestrator.poll_and_apply_fills(sleeve_id)
            second_pass_new_fills = tick_orchestrator.poll_and_apply_fills(sleeve_id)

        assert not second_pass_new_fills, (
            "a second poll against an order whose full broker-reported "
            "filled_qty is already recorded must not create a duplicate fill"
        )
        recorded = database.get_fills_for_order(order_pk)
        total_recorded_qty = sum(f["filled_qty"] for f in recorded)
        assert total_recorded_qty == pytest.approx(float(fixture["filled_qty"]), rel=1e-9), (
            "total recorded fill quantity must still equal the fixture's "
            "filled_qty exactly once, not double-counted across two polls"
        )

    def test_broker_error_on_get_order_is_logged_not_silently_swallowed(self, caplog):
        """BLOCK 2 (s3-review): sleeves.alpaca_orders never logs internally
        by design (its own never-raises contract) -- a broker error here
        must be logged at WARNING+ level, or a persistent Alpaca outage/auth
        failure on the fill-polling path has ZERO observability anywhere in
        the stack."""
        sleeve_id = _make_sleeve()
        client_order_id = "poll-test-broker-error"
        database.insert_sleeve_order(
            client_order_id=client_order_id,
            sleeve_id=sleeve_id,
            symbol="SPY",
            side="buy",
            qty=5.0,
            status="accepted",
            reserved_price=500.0,
        )
        database.attach_alpaca_order_id(client_order_id, "alpaca-poll-broker-error-id")

        with (
            patch.object(
                alpaca_orders,
                "get_order",
                return_value=alpaca_orders.OrderResult(order=None, error="HTTP 500"),
            ),
            caplog.at_level(logging.WARNING),
        ):
            tick_orchestrator.poll_and_apply_fills(sleeve_id)

        assert any(r.levelno >= logging.WARNING for r in caplog.records), (
            "a broker error from alpaca_orders.get_order must be logged at "
            "WARNING level or higher — silently continuing leaves zero "
            "observability for a persistent Alpaca outage/auth failure."
        )


# ---------------------------------------------------------------------------
# Shared-account reconciliation semantics (BLOCK: s3-ux live finding + PM
# ruling, 2026-07-08) — supersedes the retired per-sleeve-vs-whole-account
# reconcile_sleeve_or_pause tests that used to live here.
#
# s3-ux's visual-gate render, driven against a REAL 7-sleeve seeded DB
# (not read-only code inspection), reproduced a genuine BLOCK: every one of
# the 7 sleeves paused within a single tick. Root cause: reconcile_sleeve_or_
# pause compared ONE sleeve's ledger cash against the alpaca_orders.get_account
# call's WHOLE-ACCOUNT cash figure — since the broker has no concept of our
# virtual per-sleeve partitioning, every sleeve's own (necessarily smaller)
# capital differed from the full account total by far more than any
# reasonable tolerance, guaranteeing a breach the moment more than one sleeve
# existed. The retired test_matching_broker_truth_within_tolerance_does_not_
# pause only ever passed because it faked the WHOLE-ACCOUNT cash to equal
# ONE sleeve's own capital — a fixture that is only coherent with exactly one
# sleeve in the database, i.e. the tacit assumption s3-ux's live repro broke.
#
# PM ruling — the correct shared-account semantics:
#   1. CASH becomes an AGGREGATE, ONE-SIDED check across ALL sleeves:
#      Sigma(every sleeve's ledger cash_usd + reserved_usd) <= account_cash +
#      cash_tolerance_usd. Account cash EXCEEDING the sleeve sum is FINE by
#      design (unallocated float / the operator's own money sharing the same
#      account) — the breach direction is one-sided.
#   2. An aggregate cash breach pauses ALL sleeves (+ alerts) — blame is
#      unattributable across virtual slices of one real account; pausing
#      everyone is the conservative direction.
#   3. POSITION reconciliation stays per-symbol but is ALSO aggregated across
#      sleeves: for each symbol with sleeve-attributed history, Sigma(every
#      sleeve's qty for that symbol) <= broker's own qty for that symbol +
#      tolerance. A breach pauses only the sleeves holding that symbol.
#      Broker positions NOT attributable to any sleeve (a symbol no sleeve
#      has ever touched) are IGNORED entirely — the operator's own holdings
#      sharing the account, never drift.
#   4. Per-sleeve cash conservation remains sleeves.ledger's OWN internal law
#      (already enforced there) — it is simply no longer checked against the
#      broker on a PER-sleeve basis.
#
# See sleeves/reconciliation.py's new reconcile_aggregate_cash /
# reconcile_aggregate_position pure functions (tests/sleeves/
# test_reconciliation.py) for the underlying one-sided comparison contract
# this section exercises end-to-end via run_sleeve_tick_for_all_sleeves.
# ---------------------------------------------------------------------------


class TestAggregateSharedAccountReconciliation:
    def _seed_sleeve_with_position(self, sleeve_id: int, *, symbol: str, qty: float, price: float):
        """Give a sleeve a REAL position via a filled buy order + matching
        fill, so sleeves.ledger.reconstruct_from_history reconstructs it —
        exercises the real ledger-reconstruction path, not a mocked
        shortcut."""
        client_order_id = f"agg-test-{uuid.uuid4().hex}"
        order_pk = database.insert_sleeve_order(
            client_order_id=client_order_id,
            sleeve_id=sleeve_id,
            symbol=symbol,
            side="buy",
            qty=qty,
            status="filled",
            reserved_price=price,
        )
        database.insert_sleeve_fill(
            order_id=order_pk,
            fill_price=price,
            filled_qty=qty,
            filled_at="2026-07-08T14:00:00Z",
        )

    def _make_account(self, cash_usd: float) -> dict:
        return {
            "id": f"acct-agg-{uuid.uuid4().hex}",
            "account_number": "PA0000AGGTEST",
            "status": "ACTIVE",
            "currency": "USD",
            "cash": str(cash_usd),
            "portfolio_value": str(cash_usd),
            "buying_power": str(cash_usd),
            "equity": str(cash_usd),
            "pattern_day_trader": False,
            "trading_blocked": False,
            "account_blocked": False,
            "shorting_enabled": False,
        }

    def test_multi_sleeve_account_cash_equals_sum_of_capitals_does_not_pause(self):
        """The baseline shared-account scenario the old per-sleeve check got
        wrong 100% of the time: 3 unequal-capital sleeves, broker cash ==
        the EXACT sum of their capitals (fully allocated, zero float). None
        of them should pause."""
        capitals = [1000.0, 2500.0, 4000.0]
        sleeve_ids = [_make_sleeve(capital_usd=c) for c in capitals]
        account = self._make_account(sum(capitals))

        with (
            patch.object(
                alpaca_orders,
                "get_account",
                return_value=alpaca_orders.OrderResult(order=account, error=None),
            ),
            patch.object(
                alpaca_orders,
                "get_positions",
                return_value=alpaca_orders.OrderResult(order=[], error=None),
            ),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)

        for sid in sleeve_ids:
            assert database.get_sleeve(sid)["status"] != "PAUSED_RECONCILIATION", (
                f"sleeve {sid} was incorrectly paused — account cash exactly "
                f"matches the sum of all sleeve capitals, a clean "
                f"shared-account state (this is the exact scenario the old "
                f"per-sleeve-vs-whole-account check always broke on)."
            )

    def test_open_unfilled_order_does_not_cause_a_false_aggregate_cash_breach(self):
        """s3-review open point (post-APPROVE, c178ee2): the aggregate cash
        claim sums cash_usd + reserved_usd per sleeve. Verifies this is safe
        for a sleeve holding a GENUINELY OPEN (non-terminal, unfilled) order
        -- reserve() only moves money between our own cash_usd and
        reserved_usd buckets (their SUM is invariant across a reservation;
        sleeves.ledger.reserve() proves this: cash_usd decreases by exactly
        what reserved_usd increases), so a sleeve with a pending-but-unfilled
        order still contributes its full original capital_usd to the
        aggregate claim -- no more, no less. This mirrors real broker
        semantics: an order's OWN existence (accepted, not yet filled) does
        not move a broker's settled `cash` figure either -- that only
        happens at fill/settlement (the exact reason sleeves.ledger needed a
        separate reserved_usd bucket in the first place, rather than only
        ever tracking cash_usd). Pins that reasoning as a real test, not an
        unverified assumption, per s3-review's recommendation."""
        sleeve_id = _make_sleeve(capital_usd=5000.0)
        reserved_notional = 2000.0
        database.insert_sleeve_order(
            client_order_id=f"open-order-cash-claim-{sleeve_id}",
            sleeve_id=sleeve_id,
            symbol="SPY",
            side="buy",
            qty=4.0,
            status="accepted",  # non-terminal: accepted by the broker, NOT yet filled
            reserved_price=reserved_notional / 4.0,
        )
        database.attach_alpaca_order_id(
            f"open-order-cash-claim-{sleeve_id}", f"alpaca-open-cash-claim-{sleeve_id}"
        )

        # The broker's real cash has NOT moved for an unfilled order -- so a
        # clean broker-truth account still shows the sleeve's full original
        # capital as available cash.
        account = self._make_account(5000.0)

        with (
            patch.object(
                alpaca_orders,
                "get_account",
                return_value=alpaca_orders.OrderResult(order=account, error=None),
            ),
            patch.object(
                alpaca_orders,
                "get_positions",
                return_value=alpaca_orders.OrderResult(order=[], error=None),
            ),
            # poll_and_apply_fills is mocked so this test is purely about
            # the aggregate cash claim's treatment of an OPEN order -- it
            # must never independently invent a fill.
            patch.object(tick_orchestrator, "poll_and_apply_fills", return_value=[]),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)

        assert database.get_sleeve(sleeve_id)["status"] != "PAUSED_RECONCILIATION", (
            f"a sleeve with a genuinely OPEN, unfilled order (${reserved_notional:.2f} "
            f"reserved, not yet filled) must NOT falsely breach the aggregate "
            f"cash check against a broker-truth account showing the sleeve's "
            f"full original capital ($5000.00) still available — cash_usd + "
            f"reserved_usd is invariant across a reservation with no fill, so "
            f"the aggregate claim must still equal exactly the original "
            f"capital, matching the broker's own unmoved cash figure."
        )

    def test_account_cash_exceeding_sleeve_sum_float_does_not_pause(self):
        """Unallocated float (or the operator's own money) sharing the
        account must never pause anyone — the breach direction is
        one-sided."""
        capitals = [1000.0, 2000.0]
        sleeve_ids = [_make_sleeve(capital_usd=c) for c in capitals]
        account = self._make_account(sum(capitals) + 50000.0)  # large float

        with (
            patch.object(
                alpaca_orders,
                "get_account",
                return_value=alpaca_orders.OrderResult(order=account, error=None),
            ),
            patch.object(
                alpaca_orders,
                "get_positions",
                return_value=alpaca_orders.OrderResult(order=[], error=None),
            ),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)

        for sid in sleeve_ids:
            assert database.get_sleeve(sid)["status"] != "PAUSED_RECONCILIATION", (
                f"sleeve {sid} was incorrectly paused despite a large "
                f"account cash SURPLUS over the sleeve sum — surplus float "
                f"must never breach."
            )

    def test_sleeve_sum_exceeding_account_cash_pauses_all_sleeves(self):
        """The real money-safety invariant: sleeves collectively believe
        they hold more cash than the account actually has -> ALL sleeves
        are paused (blame is unattributable across virtual slices of one
        real account — pausing everyone is the conservative direction)."""
        capitals = [1000.0, 2000.0, 3000.0]
        sleeve_ids = [_make_sleeve(capital_usd=c) for c in capitals]
        account = self._make_account(500.0)  # far less than the 6000 claimed

        with (
            patch.object(
                alpaca_orders,
                "get_account",
                return_value=alpaca_orders.OrderResult(order=account, error=None),
            ),
            patch.object(
                alpaca_orders,
                "get_positions",
                return_value=alpaca_orders.OrderResult(order=[], error=None),
            ),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)

        for sid in sleeve_ids:
            assert database.get_sleeve(sid)["status"] == "PAUSED_RECONCILIATION", (
                f"sleeve {sid} must be paused — an aggregate cash breach "
                f"pauses ALL sleeves, since blame is unattributable across "
                f"virtual slices of one real account."
            )

    def test_operator_external_position_in_untouched_symbol_is_ignored(self):
        """A broker position in a symbol NO sleeve has ever touched (the
        operator's own holding sharing the account) must never pause
        anyone."""
        sleeve_id = _make_sleeve(capital_usd=1000.0)
        account = self._make_account(1000.0)
        broker_positions = [
            {
                "asset_id": "ext-position-asset-id",
                "symbol": "TSLA",
                "asset_class": "us_equity",
                "qty": "50",
                "avg_entry_price": "200.00",
                "side": "long",
                "market_value": "10000.00",
                "cost_basis": "10000.00",
                "unrealized_pl": "0.00",
                "current_price": "200.00",
            }
        ]

        with (
            patch.object(
                alpaca_orders,
                "get_account",
                return_value=alpaca_orders.OrderResult(order=account, error=None),
            ),
            patch.object(
                alpaca_orders,
                "get_positions",
                return_value=alpaca_orders.OrderResult(order=broker_positions, error=None),
            ),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)

        assert database.get_sleeve(sleeve_id)["status"] != "PAUSED_RECONCILIATION", (
            "an operator-external position (TSLA, which no sleeve has ever "
            "touched) must be ignored entirely, never treated as drift"
        )

    def test_two_sleeves_sharing_a_symbol_combined_qty_exceeding_broker_pauses_both(self):
        """Two sleeves both hold SPY; their COMBINED qty exceeds what the
        broker actually shows for SPY -> both sleeves holding it are
        paused. A third, unrelated sleeve with no SPY exposure at all is
        unaffected — pausing is attributable, not blanket, for a
        position-specific breach."""
        sleeve_a = _make_sleeve(capital_usd=5000.0)
        sleeve_b = _make_sleeve(capital_usd=5000.0)
        sleeve_c_unrelated = _make_sleeve(capital_usd=1000.0)

        self._seed_sleeve_with_position(sleeve_a, symbol="SPY", qty=10.0, price=500.0)
        self._seed_sleeve_with_position(sleeve_b, symbol="SPY", qty=10.0, price=500.0)
        # sleeve_c holds nothing at all -- its capital sits entirely as cash.

        # After a fully-filled $5000 buy each, A and B's own cash nets to 0;
        # only sleeve_c's untouched 1000 capital remains a real cash claim.
        account = self._make_account(1000.0)
        broker_positions = [
            {
                "asset_id": "spy-asset-id",
                "symbol": "SPY",
                "asset_class": "us_equity",
                "qty": "15",  # broker shows only 15; sleeves combined claim 20
                "avg_entry_price": "500.00",
                "side": "long",
                "market_value": "7500.00",
                "cost_basis": "7500.00",
                "unrealized_pl": "0.00",
                "current_price": "500.00",
            }
        ]

        with (
            patch.object(
                alpaca_orders,
                "get_account",
                return_value=alpaca_orders.OrderResult(order=account, error=None),
            ),
            patch.object(
                alpaca_orders,
                "get_positions",
                return_value=alpaca_orders.OrderResult(order=broker_positions, error=None),
            ),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)

        assert database.get_sleeve(sleeve_a)["status"] == "PAUSED_RECONCILIATION", (
            "sleeve A holds SPY and the combined SPY claim (20) exceeds "
            "broker truth (15) -- it must be paused"
        )
        assert database.get_sleeve(sleeve_b)["status"] == "PAUSED_RECONCILIATION", (
            "sleeve B holds SPY too -- same breach, same pause"
        )
        assert database.get_sleeve(sleeve_c_unrelated)["status"] != "PAUSED_RECONCILIATION", (
            "sleeve C has zero SPY exposure and must be unaffected by a "
            "SPY-specific breach -- pausing must be attributable to the "
            "sleeves actually holding the breached symbol, never blanket"
        )

    def test_broker_account_and_positions_are_fetched_once_per_tick_not_per_sleeve(self):
        """Efficiency + correctness: with N sleeves, get_account/get_positions
        must be called ONCE for the whole tick's aggregate check, not N
        times -- calling per-sleeve was the root cause of the original bug
        (each sleeve independently compared itself against a redundant fresh
        whole-account fetch) and is also wasteful broker API usage."""
        sleeve_ids = [_make_sleeve(capital_usd=1000.0) for _ in range(4)]
        account = self._make_account(sum(1000.0 for _ in sleeve_ids))

        with (
            patch.object(
                alpaca_orders,
                "get_account",
                return_value=alpaca_orders.OrderResult(order=account, error=None),
            ) as mock_get_account,
            patch.object(
                alpaca_orders,
                "get_positions",
                return_value=alpaca_orders.OrderResult(order=[], error=None),
            ) as mock_get_positions,
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)

        assert mock_get_account.call_count == 1, (
            f"get_account must be called exactly ONCE per tick for the "
            f"aggregate check across all {len(sleeve_ids)} sleeves, not once "
            f"per sleeve; got {mock_get_account.call_count} calls"
        )
        assert mock_get_positions.call_count == 1, (
            f"get_positions must be called exactly ONCE per tick; got "
            f"{mock_get_positions.call_count} calls"
        )


# ---------------------------------------------------------------------------
# cancel_open_orders_for_shadow_sleeve — the disarm route's actual broker
# cancellation, deferred to the engine tick (AC-12 design correction)
# ---------------------------------------------------------------------------


class TestCancelOpenOrdersForShadowSleeve:
    def test_open_order_on_a_shadow_sleeve_is_cancelled(self):
        sleeve_id = _make_sleeve()
        assert database.get_sleeve(sleeve_id)["status"] == "SHADOW", (
            "fixture sanity: a freshly created sleeve starts SHADOW"
        )

        client_order_id = "cancel-test-open-order"
        database.insert_sleeve_order(
            client_order_id=client_order_id,
            sleeve_id=sleeve_id,
            symbol="SPY",
            side="buy",
            qty=5.0,
            status="accepted",
            reserved_price=500.0,
        )
        database.attach_alpaca_order_id(client_order_id, "alpaca-cancel-test-id-1")

        with patch.object(
            alpaca_orders,
            "cancel_order",
            return_value=alpaca_orders.OrderResult(order={"status": "canceled"}, error=None),
        ) as mock_cancel:
            cancelled = tick_orchestrator.cancel_open_orders_for_shadow_sleeve(sleeve_id)

        mock_cancel.assert_called()
        called_ids = {
            call.kwargs.get("order_id") or (call.args[0] if call.args else None)
            for call in mock_cancel.call_args_list
        }
        assert "alpaca-cancel-test-id-1" in called_ids, (
            "the open order's broker order id must be passed to cancel_order"
        )
        assert cancelled, "the function must return the cancelled order(s)"

    def test_filled_order_on_a_shadow_sleeve_is_never_cancelled(self):
        sleeve_id = _make_sleeve()
        client_order_id = "cancel-test-filled-order"
        database.insert_sleeve_order(
            client_order_id=client_order_id,
            sleeve_id=sleeve_id,
            symbol="SPY",
            side="buy",
            qty=5.0,
            status="filled",
            reserved_price=500.0,
        )
        database.attach_alpaca_order_id(client_order_id, "alpaca-cancel-test-filled-id")

        with patch.object(alpaca_orders, "cancel_order") as mock_cancel:
            tick_orchestrator.cancel_open_orders_for_shadow_sleeve(sleeve_id)

        assert not mock_cancel.called, "an already-filled order must never be cancelled"

    def test_cancellation_never_touches_positions_or_broker_side_stops(self):
        sleeve_id = _make_sleeve()
        client_order_id = "cancel-test-no-position-touch"
        database.insert_sleeve_order(
            client_order_id=client_order_id,
            sleeve_id=sleeve_id,
            symbol="SPY",
            side="buy",
            qty=5.0,
            status="accepted",
            reserved_price=500.0,
        )
        database.attach_alpaca_order_id(client_order_id, "alpaca-cancel-test-id-2")

        with (
            patch.object(
                alpaca_orders,
                "cancel_order",
                return_value=alpaca_orders.OrderResult(order={"status": "canceled"}, error=None),
            ),
            patch.object(alpaca_orders, "close_position", create=True) as mock_close,
            patch.object(alpaca_orders, "liquidate_position", create=True) as mock_liquidate,
        ):
            tick_orchestrator.cancel_open_orders_for_shadow_sleeve(sleeve_id)

        mock_close.assert_not_called()
        mock_liquidate.assert_not_called()

    def test_shadow_sleeve_with_no_open_orders_is_a_no_op(self):
        sleeve_id = _make_sleeve()

        with patch.object(alpaca_orders, "cancel_order") as mock_cancel:
            cancelled = tick_orchestrator.cancel_open_orders_for_shadow_sleeve(sleeve_id)

        mock_cancel.assert_not_called()
        assert not cancelled

    def _seed_open_order(self, sleeve_id: int, *, client_order_id: str, alpaca_order_id: str):
        database.insert_sleeve_order(
            client_order_id=client_order_id,
            sleeve_id=sleeve_id,
            symbol="SPY",
            side="buy",
            qty=5.0,
            status="accepted",
            reserved_price=500.0,
        )
        database.attach_alpaca_order_id(client_order_id, alpaca_order_id)

    def test_broker_error_on_cancel_order_is_logged_not_silently_swallowed(self, caplog):
        """BLOCK 2 (s3-review): a cancel_order failure on the disarm path is
        safety-critical -- silently continuing means an operator who clicked
        disarm has zero way to learn the broker actually rejected the
        cancellation. Must be logged at WARNING+ level."""
        sleeve_id = _make_sleeve()
        self._seed_open_order(
            sleeve_id,
            client_order_id="cancel-test-broker-error",
            alpaca_order_id="alpaca-cancel-broker-error-id",
        )

        with (
            patch.object(
                alpaca_orders,
                "cancel_order",
                return_value=alpaca_orders.OrderResult(order=None, error="HTTP 500"),
            ),
            caplog.at_level(logging.WARNING),
        ):
            tick_orchestrator.cancel_open_orders_for_shadow_sleeve(sleeve_id)

        assert any(r.levelno >= logging.WARNING for r in caplog.records), (
            "a broker error from alpaca_orders.cancel_order must be logged "
            "at WARNING level or higher on the disarm-cancellation path."
        )

    def test_broker_error_on_cancel_order_fires_a_discord_alert(self):
        """BLOCK 2 (s3-review): a failed disarm-cancellation must alert the
        operator via Discord (mirrors reconcile_sleeve_or_pause's own
        alert-on-breach pattern) -- the dashboard could otherwise show a
        disarmed/safe sleeve while a real order stays live at the broker,
        with no signal anywhere that the cancel attempt failed."""
        sleeve_id = _make_sleeve()
        self._seed_open_order(
            sleeve_id,
            client_order_id="cancel-test-broker-error-discord",
            alpaca_order_id="alpaca-cancel-broker-error-discord-id",
        )

        with (
            patch.object(
                alpaca_orders,
                "cancel_order",
                return_value=alpaca_orders.OrderResult(order=None, error="HTTP 500"),
            ),
            patch.object(tick_orchestrator, "requests") as mock_requests,
        ):
            tick_orchestrator.cancel_open_orders_for_shadow_sleeve(
                sleeve_id, discord_webhook_url="https://discord.test/webhook"
            )

        (
            mock_requests.post.assert_called(),
            (
                "a cancel_order failure must post a Discord alert when "
                "discord_webhook_url is supplied — silent failure on the "
                "safety-critical disarm path is not acceptable."
            ),
        )

    def test_no_discord_alert_attempted_without_a_webhook_url_configured(self):
        """Never crash / never attempt a None-URL POST when no webhook is
        configured — mirrors reconcile_sleeve_or_pause's/actions.py's own
        `if not discord_webhook_url: return` convention."""
        sleeve_id = _make_sleeve()
        self._seed_open_order(
            sleeve_id,
            client_order_id="cancel-test-broker-error-no-webhook",
            alpaca_order_id="alpaca-cancel-broker-error-no-webhook-id",
        )

        with (
            patch.object(
                alpaca_orders,
                "cancel_order",
                return_value=alpaca_orders.OrderResult(order=None, error="HTTP 500"),
            ),
            patch.object(tick_orchestrator, "requests") as mock_requests,
        ):
            tick_orchestrator.cancel_open_orders_for_shadow_sleeve(
                sleeve_id, discord_webhook_url=None
            )

        mock_requests.post.assert_not_called()


# ---------------------------------------------------------------------------
# run_sleeve_tick_for_all_sleeves — per-sleeve orchestration
# ---------------------------------------------------------------------------


class TestRunSleeveTickForAllSleeves:
    def test_shadow_sleeve_gets_its_open_orders_cancelled_via_the_tick(self):
        """Integration check for the AC-12 design correction: the tick, not
        the disarm route, is what actually calls cancel_open_orders_for_
        shadow_sleeve for a SHADOW-status sleeve.

        poll_and_apply_fills is mocked, and the broker account/positions
        calls are mocked with data matching the sleeve's own capital (via
        _make_matching_account) -- both to keep this a clean, focused unit
        check of exactly what it claims (cancel_open_orders_for_shadow_sleeve
        gets called for a SHADOW sleeve) and to avoid a genuine outbound
        network call to Alpaca (house rule: zero live network in tests).
        See TestAggregateSharedAccountReconciliation for the dedicated
        aggregate-reconciliation coverage (SHADOW sleeves participate in it
        exactly like any other sleeve -- every sleeve there starts SHADOW)."""
        sleeve_id = _make_sleeve()
        sleeve_row = database.get_sleeve(sleeve_id)
        assert sleeve_row["status"] == "SHADOW"

        with (
            patch.object(
                tick_orchestrator, "cancel_open_orders_for_shadow_sleeve", return_value=[]
            ) as mock_cancel_shadow,
            patch.object(tick_orchestrator, "poll_and_apply_fills", return_value=[]),
            patch.object(
                alpaca_orders,
                "get_account",
                return_value=alpaca_orders.OrderResult(
                    order=_make_matching_account(sleeve_row["capital_usd"]), error=None
                ),
            ),
            patch.object(
                alpaca_orders,
                "get_positions",
                return_value=alpaca_orders.OrderResult(order=[], error=None),
            ),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)

        called_sleeve_ids = {
            call.args[0] if call.args else call.kwargs.get("sleeve_id")
            for call in mock_cancel_shadow.call_args_list
        }
        assert sleeve_id in called_sleeve_ids, (
            "run_sleeve_tick_for_all_sleeves must call "
            "cancel_open_orders_for_shadow_sleeve for every SHADOW-status "
            "sleeve — this is how a disarmed sleeve's lingering open orders "
            "actually get cancelled, since the disarm ROUTE itself never "
            "reaches sleeves.alpaca_orders."
        )

    def test_shadow_sleeve_also_gets_polled_for_fills_not_just_cancelled(self):
        """Companion to the AC-12 design correction: poll_and_apply_fills
        must also run for a SHADOW sleeve (a fill that lands in the TOCTOU
        window right before this tick's cancel attempt must still be
        recorded, not silently dropped because the sleeve is SHADOW). The
        broker account/positions calls are mocked clean (matching the
        sleeve's own capital) so this test stays isolated to the poll
        behavior — see TestAggregateSharedAccountReconciliation for the
        dedicated aggregate-reconciliation coverage."""
        sleeve_id = _make_sleeve()
        sleeve_row = database.get_sleeve(sleeve_id)

        with (
            patch.object(
                tick_orchestrator, "cancel_open_orders_for_shadow_sleeve", return_value=[]
            ),
            patch.object(tick_orchestrator, "poll_and_apply_fills", return_value=[]) as mock_poll,
            patch.object(
                alpaca_orders,
                "get_account",
                return_value=alpaca_orders.OrderResult(
                    order=_make_matching_account(sleeve_row["capital_usd"]), error=None
                ),
            ),
            patch.object(
                alpaca_orders,
                "get_positions",
                return_value=alpaca_orders.OrderResult(order=[], error=None),
            ),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)

        called_sleeve_ids = {
            call.args[0] if call.args else call.kwargs.get("sleeve_id")
            for call in mock_poll.call_args_list
        }
        assert sleeve_id in called_sleeve_ids, (
            "poll_and_apply_fills must be called for a SHADOW-status sleeve "
            "too — a fill landing right before this tick's cancellation "
            "attempt must still be recorded."
        )

    def test_paused_sleeve_skips_rule_evaluation_for_this_tick(self):
        sleeve_id = _make_sleeve()
        database.update_sleeve_status(sleeve_id, "PAUSED_RECONCILIATION")
        database.create_sleeve_rule(sleeve_id, "paused-sleeve-rule", json_doc="{}", mode="SHADOW")

        with (
            patch.object(tick_orchestrator, "poll_and_apply_fills", return_value=[]),
            patch(
                "sleeves.rules.runner.evaluate_rules", MagicMock(return_value=[])
            ) as mock_evaluate,
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)

        for call in mock_evaluate.call_args_list:
            sleeve_row_arg = call.kwargs.get("sleeve_row")
            assert not (sleeve_row_arg and sleeve_row_arg.get("id") == sleeve_id), (
                "evaluate_rules must never be called for a sleeve that is "
                "already PAUSED_RECONCILIATION — rule evaluation must be "
                "skipped entirely for a paused sleeve."
            )

    def test_one_sleeves_exception_does_not_block_processing_of_another(self):
        """Both sleeves are moved to PAPER (never SHADOW) so this test
        actually exercises the poll_and_apply_fills failure path it claims
        to: run_sleeve_tick_for_all_sleeves routes a SHADOW-status sleeve
        through cancel_open_orders_for_shadow_sleeve instead of
        poll_and_apply_fills (AC-12 design correction), which would make the
        injected poll_and_apply_fills exception below silently never fire for
        two default-SHADOW sleeves — a real gap s3-engine's GREEN
        implementation flagged in this exact test."""
        failing_sleeve_id = _make_sleeve()
        database.update_sleeve_status(failing_sleeve_id, "PAPER")
        healthy_sleeve_id = _make_sleeve()
        database.update_sleeve_status(healthy_sleeve_id, "PAPER")
        database.create_sleeve_rule(
            healthy_sleeve_id, "healthy-sleeve-rule", json_doc="{}", mode="SHADOW"
        )

        def _poll_side_effect(sleeve_id, **_kw):
            if sleeve_id == failing_sleeve_id:
                raise RuntimeError("simulated broker polling failure")
            return []

        # Both sleeves used the _make_sleeve() default capital (10000.0
        # each); mock the broker account/positions clean (matching the
        # combined 20000.0 total) so the aggregate reconciliation step
        # doesn't interfere with the exception-isolation behavior this test
        # actually checks, and so no genuine outbound network call is made
        # (house rule: zero live network in tests).
        with (
            patch.object(
                tick_orchestrator, "poll_and_apply_fills", side_effect=_poll_side_effect
            ) as mock_poll,
            patch.object(
                alpaca_orders,
                "get_account",
                return_value=alpaca_orders.OrderResult(
                    order=_make_matching_account(20000.0), error=None
                ),
            ),
            patch.object(
                alpaca_orders,
                "get_positions",
                return_value=alpaca_orders.OrderResult(order=[], error=None),
            ),
            patch(
                "sleeves.rules.runner.evaluate_rules", MagicMock(return_value=[])
            ) as mock_evaluate,
        ):
            try:
                tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)
            except BaseException as exc:  # pragma: no cover — only fires on regression
                pytest.fail(
                    f"run_sleeve_tick_for_all_sleeves must not propagate a "
                    f"single sleeve's exception — got {type(exc).__name__}: {exc}"
                )

        # Fixture-sanity: proves the injected exception was actually reached
        # for the failing sleeve (not silently routed around it, e.g. by a
        # SHADOW-status sleeve taking the cancel-orders branch instead —
        # exactly the bug class this test's docstring documents).
        polled_sleeve_ids = {
            call.args[0] if call.args else call.kwargs.get("sleeve_id")
            for call in mock_poll.call_args_list
        }
        assert failing_sleeve_id in polled_sleeve_ids, (
            "fixture sanity: poll_and_apply_fills must actually have been "
            "called for the failing sleeve — if this fails, the injected "
            "exception never fired and the test below would pass vacuously."
        )

        healthy_sleeve_evaluated = any(
            call.kwargs.get("sleeve_row", {}).get("id") == healthy_sleeve_id
            for call in mock_evaluate.call_args_list
        )
        assert healthy_sleeve_evaluated, (
            "the healthy sleeve must still be processed even though a "
            "different sleeve's fill-polling raised — one sleeve's failure "
            "must never block another sleeve's tick."
        )
