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

    def reconcile_sleeve_or_pause(
        sleeve_id: int, *, position_tolerance_pct: float, cash_tolerance_usd: float,
        live_mode: bool = False, live_keys_present: bool = False,
        discord_webhook_url: str | None = None,
    ) -> sleeves.reconciliation.ReconciliationResult:
        # Reconstructs the sleeve's ledger from database.get_sleeve_order_history
        # via sleeves.ledger.reconstruct_from_history, fetches broker truth via
        # sleeves.alpaca_orders.get_account/get_positions, and calls
        # sleeves.reconciliation.reconcile_sleeve. On breach
        # (verdict == "PAUSED_RECONCILIATION"), calls
        # database.update_sleeve_status(sleeve_id, "PAUSED_RECONCILIATION") and
        # best-effort posts a Discord alert (never raises on webhook failure).
        # Returns the ReconciliationResult either way.

    def cancel_open_orders_for_shadow_sleeve(
        sleeve_id: int, *, live_mode: bool = False, live_keys_present: bool = False,
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

    def run_sleeve_tick_for_all_sleeves(
        *, now_utc: datetime, discord_webhook_url: str | None = None,
    ) -> list:
        # For every sleeve returned by database.get_all_sleeves():
        #   0. if the sleeve's status is "SHADOW", calls
        #      cancel_open_orders_for_shadow_sleeve for it (cleans up any
        #      lingering orders from a just-disarmed sleeve) and then skips
        #      straight to rule evaluation for it (no fill-polling/
        #      reconciliation needed for a sleeve with no live-armed rules).
        #   1. poll_and_apply_fills
        #   2. reconcile_sleeve_or_pause — if this call PAUSES the sleeve this
        #      tick (or the sleeve was ALREADY paused coming in), rule
        #      evaluation is skipped for it this tick.
        #   3. otherwise, assembles the sleeve's enabled rules + sense context
        #      and calls sleeves.rules.runner.evaluate_rules for it.
        # A single sleeve's exception during any of the above is caught and
        # logged; processing continues for the remaining sleeves (mirrors the
        # engine-wiring fail-safe contract in alpha_bot_execution.main()).

Fixture-derivation rule: every producer value asserted here (fill price/qty,
broker cash/position figures) comes from the JSON fixture at assertion time —
never a hardcoded literal expectation (feedback_no_hardcoded_test_values).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip(
    "sleeves.tick_orchestrator", reason="RED phase — sleeves.tick_orchestrator not implemented yet"
)

import sleeves.tick_orchestrator as tick_orchestrator  # noqa: E402

import database  # noqa: E402
from sleeves import alpaca_orders  # noqa: E402
from tests.sleeves._alpaca_fixtures import (  # noqa: E402
    load_account_fixture,
    load_order_fixture,
    load_positions_fixture,
)

_NOW_UTC = datetime(2026, 7, 8, 14, 31, 0, tzinfo=UTC)


def _make_sleeve(capital_usd: float = 10000.0) -> int:
    return database.create_sleeve("test-sleeve", capital_usd, envelope_json="{}")


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


# ---------------------------------------------------------------------------
# reconcile_sleeve_or_pause
# ---------------------------------------------------------------------------


class TestReconcileSleeveOrPause:
    def test_position_drift_against_broker_truth_pauses_the_sleeve(self):
        sleeve_id = _make_sleeve()
        positions_fixture = load_positions_fixture("positions.json")
        account_fixture = load_account_fixture("account.json")

        # Sleeve's own ledger has zero order history -> ledger believes it
        # holds NOTHING, while the broker fixture reports a real SPY position.
        # That mismatch is a genuine unknown_position breach.
        with (
            patch.object(
                alpaca_orders,
                "get_positions",
                return_value=alpaca_orders.OrderResult(order=positions_fixture, error=None),
            ),
            patch.object(
                alpaca_orders,
                "get_account",
                return_value=alpaca_orders.OrderResult(order=account_fixture, error=None),
            ),
        ):
            result = tick_orchestrator.reconcile_sleeve_or_pause(
                sleeve_id,
                position_tolerance_pct=0.005,
                cash_tolerance_usd=1.0,
            )

        assert result.ok is False, (
            f"broker reports a position ({positions_fixture[0]['symbol']}) the "
            f"sleeve's own ledger has zero record of — this must be a breach"
        )
        assert result.verdict == "PAUSED_RECONCILIATION"

        sleeve_row = database.get_sleeve(sleeve_id)
        assert sleeve_row["status"] == "PAUSED_RECONCILIATION", (
            "a reconciliation breach must persist PAUSED_RECONCILIATION onto "
            "the sleeve's own status column — a mismatch must never be silently "
            "swallowed."
        )

    def test_matching_broker_truth_within_tolerance_does_not_pause(self):
        sleeve_id = _make_sleeve()
        # Both sides agree: zero positions, matching cash (using the sleeve's
        # own freshly-created capital as both the ledger's and broker's cash,
        # so this scenario is guaranteed within tolerance regardless of the
        # sleeve fixture's exact capital value).
        sleeve_row = database.get_sleeve(sleeve_id)
        matching_account = {
            "id": "acct-match-001",
            "account_number": "PA0000MATCH1",
            "status": "ACTIVE",
            "currency": "USD",
            "cash": str(sleeve_row["capital_usd"]),
            "portfolio_value": str(sleeve_row["capital_usd"]),
            "buying_power": str(sleeve_row["capital_usd"]),
            "equity": str(sleeve_row["capital_usd"]),
            "pattern_day_trader": False,
            "trading_blocked": False,
            "account_blocked": False,
            "shorting_enabled": False,
        }

        with (
            patch.object(
                alpaca_orders,
                "get_positions",
                return_value=alpaca_orders.OrderResult(order=[], error=None),
            ),
            patch.object(
                alpaca_orders,
                "get_account",
                return_value=alpaca_orders.OrderResult(order=matching_account, error=None),
            ),
        ):
            result = tick_orchestrator.reconcile_sleeve_or_pause(
                sleeve_id,
                position_tolerance_pct=0.005,
                cash_tolerance_usd=1.0,
            )

        assert result.ok is True, f"matching broker truth must not breach; got {result.breaches}"
        assert result.verdict == "OK"

        sleeve_row_after = database.get_sleeve(sleeve_id)
        assert sleeve_row_after["status"] != "PAUSED_RECONCILIATION", (
            "a clean reconciliation must never pause the sleeve"
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


# ---------------------------------------------------------------------------
# run_sleeve_tick_for_all_sleeves — per-sleeve orchestration
# ---------------------------------------------------------------------------


class TestRunSleeveTickForAllSleeves:
    def test_shadow_sleeve_gets_its_open_orders_cancelled_via_the_tick(self):
        """Integration check for the AC-12 design correction: the tick, not
        the disarm route, is what actually calls cancel_open_orders_for_
        shadow_sleeve for a SHADOW-status sleeve."""
        sleeve_id = _make_sleeve()
        assert database.get_sleeve(sleeve_id)["status"] == "SHADOW"

        with patch.object(
            tick_orchestrator, "cancel_open_orders_for_shadow_sleeve", return_value=[]
        ) as mock_cancel_shadow:
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
        failing_sleeve_id = _make_sleeve()
        healthy_sleeve_id = _make_sleeve()
        database.create_sleeve_rule(
            healthy_sleeve_id, "healthy-sleeve-rule", json_doc="{}", mode="SHADOW"
        )

        def _poll_side_effect(sleeve_id, **_kw):
            if sleeve_id == failing_sleeve_id:
                raise RuntimeError("simulated broker polling failure")
            return []

        with (
            patch.object(tick_orchestrator, "poll_and_apply_fills", side_effect=_poll_side_effect),
            patch.object(
                tick_orchestrator,
                "reconcile_sleeve_or_pause",
                return_value=MagicMock(ok=True, verdict="OK", breaches=[]),
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

        healthy_sleeve_evaluated = any(
            call.kwargs.get("sleeve_row", {}).get("id") == healthy_sleeve_id
            for call in mock_evaluate.call_args_list
        )
        assert healthy_sleeve_evaluated, (
            "the healthy sleeve must still be processed even though a "
            "different sleeve's fill-polling raised — one sleeve's failure "
            "must never block another sleeve's tick."
        )
