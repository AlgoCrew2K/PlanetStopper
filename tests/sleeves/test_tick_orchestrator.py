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
        #      NOT exempt from poll_and_apply_fills / reconcile_sleeve_or_pause
        #      — every disarmed sleeve stays SHADOW permanently until
        #      re-armed, and can still hold real residual broker-side
        #      exposure (an order that filled at the broker in the TOCTOU
        #      window between the disarm click and this tick's cancel
        #      attempt, or a position from before it was disarmed). Skipping
        #      reconciliation entirely for every SHADOW sleeve meant that
        #      exposure could drift forever with NOTHING ever catching it
        #      again. So steps 1-2 below now run for EVERY sleeve regardless
        #      of status (SHADOW included) — only "already
        #      PAUSED_RECONCILIATION coming into this tick" skips them, same
        #      as before. A SHADOW sleeve that breaches reconciliation still
        #      transitions to PAUSED_RECONCILIATION exactly like any other
        #      sleeve — SHADOW is not a drift-detection exemption.
        #   1. poll_and_apply_fills — for every sleeve (see BLOCK 1 above).
        #   2. reconcile_sleeve_or_pause — for every sleeve not already
        #      PAUSED_RECONCILIATION coming in (see BLOCK 1 above). If this
        #      call PAUSES the sleeve this tick (or it was already paused
        #      coming in), rule evaluation is skipped for it this tick.
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

        poll_and_apply_fills and reconcile_sleeve_or_pause are ALSO mocked
        here (in addition to cancel_open_orders_for_shadow_sleeve) even
        though this test isn't about them -- after the BLOCK 1 fix
        (9d8e46c) they now run for every sleeve including SHADOW ones, and
        left unmocked they'd reach the real sleeves.alpaca_orders.get_order/
        get_positions/get_account, which attempt genuine outbound network
        calls to Alpaca's API. That's both a house-rule violation (zero live
        network in tests) and a real flakiness/slowness risk in an
        environment where the connection doesn't fail fast (alpaca_orders.py's
        own retry/backoff schedule can add up to ~15s per call) --
        s3-engine flagged this exact risk while landing the BLOCK 1 fix.
        See test_shadow_sleeve_still_gets_reconciled_not_just_cancelled /
        test_shadow_sleeve_also_gets_polled_for_fills_not_just_cancelled
        below for the dedicated, correctly-mocked reconciliation/poll tests."""
        sleeve_id = _make_sleeve()
        assert database.get_sleeve(sleeve_id)["status"] == "SHADOW"

        with (
            patch.object(
                tick_orchestrator, "cancel_open_orders_for_shadow_sleeve", return_value=[]
            ) as mock_cancel_shadow,
            patch.object(tick_orchestrator, "poll_and_apply_fills", return_value=[]),
            patch.object(
                tick_orchestrator,
                "reconcile_sleeve_or_pause",
                return_value=MagicMock(ok=True, verdict="OK", breaches=[]),
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

    def test_shadow_sleeve_still_gets_reconciled_not_just_cancelled(self):
        """BLOCK 1 (s3-review): a SHADOW-status sleeve (every disarmed sleeve,
        permanently, until re-armed) must ALSO be reconciled each tick, not
        just have its open orders cancelled. A TOCTOU race (an order fills
        at the broker between the disarm click and this tick's cancel
        attempt) or a real residual position from before disarm would
        otherwise NEVER be caught again — reconciliation is the plan's own
        safety net for exactly this drift, and "no live-armed rules" does
        not mean "nothing at the broker to track"."""
        sleeve_id = _make_sleeve()
        assert database.get_sleeve(sleeve_id)["status"] == "SHADOW"

        with (
            patch.object(
                tick_orchestrator, "cancel_open_orders_for_shadow_sleeve", return_value=[]
            ),
            patch.object(tick_orchestrator, "poll_and_apply_fills", return_value=[]),
            patch.object(
                tick_orchestrator,
                "reconcile_sleeve_or_pause",
                return_value=MagicMock(ok=True, verdict="OK", breaches=[]),
            ) as mock_reconcile,
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)

        called_sleeve_ids = {
            call.args[0] if call.args else call.kwargs.get("sleeve_id")
            for call in mock_reconcile.call_args_list
        }
        assert sleeve_id in called_sleeve_ids, (
            "reconcile_sleeve_or_pause must be called for a SHADOW-status "
            "sleeve too, not only non-SHADOW ones — SHADOW is not an "
            "exemption from drift detection (a disarmed sleeve can still "
            "hold real broker-side exposure)."
        )

    def test_shadow_sleeve_also_gets_polled_for_fills_not_just_cancelled(self):
        """Companion to the reconciliation fix above: poll_and_apply_fills
        must also run for a SHADOW sleeve (a fill that lands in the TOCTOU
        window right before this tick's cancel attempt must still be
        recorded, not silently dropped because the sleeve is SHADOW)."""
        sleeve_id = _make_sleeve()

        with (
            patch.object(
                tick_orchestrator, "cancel_open_orders_for_shadow_sleeve", return_value=[]
            ),
            patch.object(tick_orchestrator, "poll_and_apply_fills", return_value=[]) as mock_poll,
            patch.object(
                tick_orchestrator,
                "reconcile_sleeve_or_pause",
                return_value=MagicMock(ok=True, verdict="OK", breaches=[]),
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

    def test_shadow_sleeve_reconciliation_breach_still_pauses_it(self):
        """A SHADOW sleeve with a genuine broker-truth mismatch must still
        transition to PAUSED_RECONCILIATION — SHADOW status is not a
        drift-detection exemption. Uses the REAL reconcile_sleeve_or_pause
        (only the broker calls are mocked, via the P1 fixtures) so this is a
        genuine end-to-end breach, not a mocked verdict."""
        sleeve_id = _make_sleeve()
        positions_fixture = load_positions_fixture("positions.json")
        account_fixture = load_account_fixture("account.json")

        with (
            patch.object(
                tick_orchestrator, "cancel_open_orders_for_shadow_sleeve", return_value=[]
            ),
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
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)

        sleeve_row = database.get_sleeve(sleeve_id)
        assert sleeve_row["status"] == "PAUSED_RECONCILIATION", (
            f"a SHADOW sleeve with a genuine broker-truth mismatch (the "
            f"broker fixture reports a {positions_fixture[0]['symbol']} "
            f"position the sleeve's own ledger has zero record of) must "
            f"still be paused for reconciliation; got {sleeve_row['status']!r}"
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

        with (
            patch.object(
                tick_orchestrator, "poll_and_apply_fills", side_effect=_poll_side_effect
            ) as mock_poll,
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
