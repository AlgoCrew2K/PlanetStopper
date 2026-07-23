"""RED — audit MED batch (#9-#15): engine/report hardening.

#9  pacing episode latched BEFORE dispatch — a dispatch crash silences the
    rule permanently (zero fires recorded, latch never rearms while the
    condition stays true). Live-observed: the #1 KeyError left the rule
    latched with zero fire rows across engine runs.
#10 lost-ack recovery unwired — a submit timeout ("server may still be
    processing") marks the row terminal "rejected", releasing the cash
    reservation while the broker order may be live.
#11 aggregate reconciliation fails OPEN on NaN — `claim > threshold` is
    False for NaN, so garbage broker input reads as "all fine", inverting
    the module's own fail-closed convention.
#12 _book_equity_usd's "never conservatively high" claim is false in
    drawdowns — cost-basis book equity overshoots true equity exactly when
    the sleeve is losing, inflating every risk-sized order.
#13 AC-1's cash floor holds only at reserve-time price — an unfavorable
    market fill silently drives sleeve cash negative with no signal.
#14 lifetime fire count silently caps at 100 on panel AND digest
    (get_sleeve_rule_fires default limit rendered as len(fires)).
#15 "today" fire count compares an ET date prefix against UTC-stored
    timestamps — evening fires misattribute to the next day.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, time, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import app as app_module
import database
import reporting
import sleeves.alpaca_orders as alpaca_orders
import sleeves.ledger as ledger
import sleeves.reconciliation as reconciliation
import sleeves.rules.actions as actions
import sleeves.tick_orchestrator as tick_orchestrator
from tests.sleeves._alpaca_fixtures import load_order_fixture, validate_alpaca_order_shape
from tests.sleeves._route_shaped import (
    MARKET_OPEN_NOW_UTC,
    create_rule_via_route,
    create_sleeve_via_route,
    tick_patches,
    valid_route_rule_payload,
)

_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestPacingLatchSurvivesDispatchCrash:
    def test_rule_fires_on_the_tick_after_a_dispatch_crash(self, client):
        """#9: tick 1's action dispatch raises AFTER pacing latched the
        episode; the condition stays true, so the latch can never rearm — the
        rule is silenced forever with zero fires recorded. Contract: a fire
        that recorded NOTHING must not consume the episode; the next healthy
        tick fires."""
        sleeve_id = create_sleeve_via_route(client)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())

        with (
            tick_patches(account_cash_usd=10_000.0, closes_by_symbol={"SPY": 500.0}),
            patch(
                "sleeves.rules.actions.dispatch_action",
                side_effect=RuntimeError("simulated dispatch crash"),
            ),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        assert database.get_sleeve_rule_fires(rule_id=rule_id) == [], (
            "fixture sanity: the crashed tick must have recorded no fire"
        )

        with tick_patches(account_cash_usd=10_000.0, closes_by_symbol={"SPY": 500.0}):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(
                now_utc=MARKET_OPEN_NOW_UTC + timedelta(minutes=1)
            )

        assert database.get_sleeve_rule_fires(rule_id=rule_id), (
            "the tick after a dispatch crash must fire: the crashed tick "
            "recorded zero fires yet consumed the pacing episode, and a "
            "still-true condition never rearms — the rule is permanently "
            "silenced (audit #9, live-observed with the #1 KeyError)"
        )


class TestSubmitTimeoutIsIndeterminate:
    def _armed_buy_ctx(self) -> actions.ActionContext:
        capital_usd = 10_000.0
        sleeve_id = database.create_sleeve(
            f"timeout-{uuid.uuid4().hex}", capital_usd, envelope_json="{}"
        )
        rule_id = database.create_sleeve_rule(sleeve_id, "timeout-rule", json_doc="{}")
        return actions.ActionContext(
            sleeve_id=sleeve_id,
            rule_id=rule_id,
            symbol="SPY",
            price=100.0,
            sleeve_equity_usd=capital_usd,
            capital_usd=capital_usd,
            current_position_qty=0.0,
            turnover_used_usd=0.0,
            envelope={},
        )

    _BUY_ACTION = {
        "type": "buy",
        "sizing": {"mode": "shares", "shares": 2},
        "stop_loss_pct": 0.05,
    }

    def test_submit_timeout_keeps_the_reservation_not_marked_rejected(self):
        """#10: alpaca_orders converts requests.Timeout to error='timeout: ...'
        precisely because "the server may still be processing the original
        request" — the broker order may be LIVE. Marking the row terminal
        'rejected' releases the reservation: the sleeve believes it has the
        cash while the broker may be spending it. A transport-ambiguous
        outcome must stay NON-terminal (recoverable via the minted
        client_order_id) until broker truth resolves it."""
        ctx = self._armed_buy_ctx()
        with (
            patch.object(
                alpaca_orders,
                "submit_bracket_order",
                return_value=alpaca_orders.OrderResult(order=None, error="timeout: Timeout"),
            ),
            # The lost-ack recovery lookup ALSO fails (broker unreachable) —
            # the most ambiguous outcome, and the one that must fail closed.
            patch.object(
                alpaca_orders,
                "get_order_by_client_order_id",
                return_value=alpaca_orders.OrderResult(
                    order=None, error="transport error: ConnectionError"
                ),
            ),
        ):
            actions.dispatch_action(self._BUY_ACTION, ctx=ctx, shadow=False)

        orders = database.get_sleeve_orders(sleeve_id=ctx.sleeve_id)
        assert len(orders) == 1, "fixture sanity: the reservation row must exist"
        assert orders[0]["status"] != "rejected", (
            "a submit TIMEOUT is indeterminate, not a rejection — the broker "
            "may have accepted the order (audit #10)"
        )

        state = ledger.reconstruct_from_history(
            ctx.capital_usd, database.get_sleeve_order_history(ctx.sleeve_id)
        )
        expected_reservation = 2 * ctx.price  # shares sizing: qty 2 @ entry price
        # abs 1e-6 dollars: float bookkeeping of short-decimal values.
        assert state.reserved_usd == pytest.approx(expected_reservation, abs=1e-6), (
            f"the ${expected_reservation:.2f} reservation must survive a "
            f"timeout (got reserved={state.reserved_usd:.2f}) — releasing it "
            f"lets the sleeve double-spend cash a possibly-live broker order "
            f"already claims"
        )

    def test_genuine_broker_rejection_still_releases_the_reservation(self):
        """Companion guard: an unambiguous HTTP rejection is NOT indeterminate
        — the terminal 'rejected' + release behavior must survive the #10 fix."""
        ctx = self._armed_buy_ctx()
        with patch.object(
            alpaca_orders,
            "submit_bracket_order",
            return_value=alpaca_orders.OrderResult(order=None, error="HTTP 422"),
        ):
            actions.dispatch_action(self._BUY_ACTION, ctx=ctx, shadow=False)

        orders = database.get_sleeve_orders(sleeve_id=ctx.sleeve_id)
        assert len(orders) == 1
        assert orders[0]["status"] == "rejected"
        state = ledger.reconstruct_from_history(
            ctx.capital_usd, database.get_sleeve_order_history(ctx.sleeve_id)
        )
        assert state.reserved_usd == pytest.approx(0.0, abs=1e-6)

    def test_timeout_with_successful_lostack_recovery_attaches_the_broker_order(self):
        """Ratified recovery contract (sf-eng plan, 2026-07-09): when the
        submit response is lost but the by-client-order-id lookup finds the
        broker's order, the row proceeds down the normal ack path — broker
        order id attached, non-terminal, reservation held."""
        ctx = self._armed_buy_ctx()
        recovered = dict(load_order_fixture("bracket_new.json"))
        with (
            patch.object(
                alpaca_orders,
                "submit_bracket_order",
                return_value=alpaca_orders.OrderResult(order=None, error="timeout: Timeout"),
            ),
            patch.object(
                alpaca_orders,
                "get_order_by_client_order_id",
                return_value=alpaca_orders.OrderResult(order=recovered, error=None),
            ),
        ):
            actions.dispatch_action(self._BUY_ACTION, ctx=ctx, shadow=False)

        orders = database.get_sleeve_orders(sleeve_id=ctx.sleeve_id)
        assert len(orders) == 1
        assert orders[0]["alpaca_order_id"] == recovered["id"], (
            "the recovered broker order id must be attached to the RESERVED "
            "row (lost-ack recovery = the normal ack path, one step late)"
        )
        assert orders[0]["status"] not in ("rejected", "canceled"), (
            "a recovered live order is not terminal"
        )
        state = ledger.reconstruct_from_history(
            ctx.capital_usd, database.get_sleeve_order_history(ctx.sleeve_id)
        )
        assert state.reserved_usd == pytest.approx(2 * ctx.price, abs=1e-6), (
            "the reservation backs a now-confirmed-live broker order"
        )

    def test_timeout_with_definitive_404_marks_rejected_and_releases(self):
        """The lookup answering HTTP 404 is DEFINITIVE: the broker never saw
        the client_order_id, the order does not exist — terminal 'rejected'
        and the reservation released (holding cash for a proven-nonexistent
        order would strand it forever)."""
        ctx = self._armed_buy_ctx()
        with (
            patch.object(
                alpaca_orders,
                "submit_bracket_order",
                return_value=alpaca_orders.OrderResult(order=None, error="timeout: Timeout"),
            ),
            patch.object(
                alpaca_orders,
                "get_order_by_client_order_id",
                return_value=alpaca_orders.OrderResult(order=None, error="HTTP 404"),
            ),
        ):
            actions.dispatch_action(self._BUY_ACTION, ctx=ctx, shadow=False)

        orders = database.get_sleeve_orders(sleeve_id=ctx.sleeve_id)
        assert len(orders) == 1
        assert orders[0]["status"] == "rejected", (
            "404 on the client-order-id lookup proves the order never "
            "reached the broker — the row is terminally rejected"
        )
        state = ledger.reconstruct_from_history(
            ctx.capital_usd, database.get_sleeve_order_history(ctx.sleeve_id)
        )
        assert state.reserved_usd == pytest.approx(0.0, abs=1e-6)

    def test_poll_recovers_a_stuck_reserved_row_with_no_broker_order_id(self):
        """The tail of the indeterminate branch: a RESERVED row whose
        alpaca_order_id is still NULL (dispatch-time recovery also failed)
        would hold its cash reservation FOREVER — poll_and_apply_fills must
        attempt the client-order-id lookup for such rows instead of skipping
        them unconditionally."""
        capital_usd = 10_000.0
        sleeve_id = database.create_sleeve(
            f"stuck-reserved-{uuid.uuid4().hex}", capital_usd, envelope_json="{}"
        )
        recovered = dict(load_order_fixture("bracket_new.json"))
        client_order_id = f"stuck-{uuid.uuid4().hex}"
        database.insert_sleeve_order(
            client_order_id=client_order_id,
            sleeve_id=sleeve_id,
            symbol=recovered["symbol"],
            side=recovered["side"],
            qty=float(recovered["qty"]),
            order_class=recovered["order_class"],
            status="RESERVED",
            reserved_price=500.0,
            # alpaca_order_id deliberately NULL — the stuck state.
        )

        with (
            patch.object(
                alpaca_orders,
                "get_order_by_client_order_id",
                return_value=alpaca_orders.OrderResult(order=recovered, error=None),
            ),
            patch.object(
                alpaca_orders,
                "get_order",
                return_value=alpaca_orders.OrderResult(order=recovered, error=None),
            ),
        ):
            tick_orchestrator.poll_and_apply_fills(sleeve_id)

        row = database.get_sleeve_order_by_client_id(client_order_id)
        assert row["alpaca_order_id"] == recovered["id"], (
            "the poll must recover a NULL-broker-id RESERVED row via its "
            "client_order_id — otherwise the row (and its cash reservation) "
            "is stuck forever, invisible to every later tick (audit #10's "
            "second half, ratified with sf-eng)"
        )


class TestNaNFailsClosed:
    @pytest.mark.parametrize(
        ("claim", "broker"),
        [(math.nan, 1000.0), (1000.0, math.nan), (math.nan, math.nan)],
    )
    def test_aggregate_cash_with_nan_input_breaches(self, claim, broker):
        result = reconciliation.reconcile_aggregate_cash(
            total_sleeve_cash_claim_usd=claim,
            broker_cash_usd=broker,
            cash_tolerance_usd=1.0,
        )
        assert not result.ok, (
            f"NaN input (claim={claim}, broker={broker}) must fail CLOSED — "
            f"'claim > threshold' is False for NaN, so garbage broker data "
            f"currently reads as a clean reconciliation (audit #11), "
            f"inverting this money-PAUSE gate's own fail-closed convention"
        )

    @pytest.mark.parametrize(
        ("sleeve_qty", "broker_qty"),
        [(math.nan, 10.0), (10.0, math.nan)],
    )
    def test_aggregate_position_with_nan_input_breaches(self, sleeve_qty, broker_qty):
        result = reconciliation.reconcile_aggregate_position(
            symbol="SPY",
            total_sleeve_qty=sleeve_qty,
            broker_qty=broker_qty,
            position_tolerance_pct=0.005,
        )
        assert not result.ok, (
            f"NaN input (sleeve={sleeve_qty}, broker={broker_qty}) must fail CLOSED (audit #11)"
        )

    def test_nan_broker_cash_string_pauses_sleeves_end_to_end(self):
        """float('NaN') parses successfully at tick_orchestrator's
        `float(account['cash'])` — the unguarded broker-side input. The tick
        must pause rather than bless the garbage."""
        sleeve_id = database.create_sleeve(
            f"nan-cash-{uuid.uuid4().hex}", 1_000.0, envelope_json="{}"
        )
        with tick_patches(account_cash_usd=float("nan")):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        assert database.get_sleeve(sleeve_id)["status"] == "PAUSED_RECONCILIATION", (
            "a broker account reporting cash='NaN' must pause the group "
            "(fail closed) — today the one-sided comparison silently passes "
            "it as OK (audit #11)"
        )


class TestBookEquityMarksDrawdownsToMarket:
    def test_sizing_equity_input_never_exceeds_marked_equity_in_a_drawdown(self, client):
        """#12: capital $10k, cash $2k, position bought for $8k now worth $4k
        at the tick's own fetched closes -> true equity $6k. Cost-basis book
        equity says $10k, so a 1%-risk rule risks 1.67% of real equity —
        exactly when the sleeve is losing. Contract: when the tick has closes
        for a held symbol, the equity fed to the runner marks that position
        to those closes (cash + reserved + qty*close); the docstring's
        'never conservatively high' promise becomes true."""
        capital_usd = 10_000.0
        sleeve_id = create_sleeve_via_route(client, capital_usd=capital_usd)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())
        assert rule_id

        entry_qty, entry_price, drawdown_close = 1000.0, 8.0, 4.0
        client_order_id = f"bookeq-{uuid.uuid4().hex}"
        order_pk = database.insert_sleeve_order(
            client_order_id=client_order_id,
            sleeve_id=sleeve_id,
            symbol="SPY",
            side="buy",
            qty=entry_qty,
            status="filled",
            reserved_price=entry_price,
        )
        database.insert_sleeve_fill(
            order_id=order_pk,
            fill_price=entry_price,
            filled_qty=entry_qty,
            filled_at="2026-07-08T14:00:00Z",
        )

        remaining_cash = capital_usd - entry_qty * entry_price
        marked_equity = remaining_cash + entry_qty * drawdown_close

        with (
            tick_patches(
                account_cash_usd=remaining_cash,
                broker_positions=[
                    {
                        "asset_id": "bookeq-asset",
                        "symbol": "SPY",
                        "asset_class": "us_equity",
                        "qty": str(entry_qty),
                        "avg_entry_price": str(entry_price),
                        "side": "long",
                        "market_value": str(entry_qty * drawdown_close),
                        "cost_basis": str(entry_qty * entry_price),
                        "unrealized_pl": str(entry_qty * (drawdown_close - entry_price)),
                        "current_price": str(drawdown_close),
                    }
                ],
                closes_by_symbol={"SPY": drawdown_close},
            ),
            patch(
                "sleeves.rules.runner.evaluate_rules", MagicMock(return_value=[])
            ) as mock_evaluate,
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        equity_calls = [
            call.kwargs["sleeve_equity_usd"]
            for call in mock_evaluate.call_args_list
            if call.kwargs.get("sleeve_row", {}).get("id") == sleeve_id
        ]
        assert equity_calls, "fixture sanity: the sleeve must reach rule evaluation"
        # abs $1: the mark itself is exact float arithmetic; the dollar of
        # slack only absorbs bookkeeping representation error.
        assert equity_calls[0] == pytest.approx(marked_equity, abs=1.0), (
            f"sizing equity input was {equity_calls[0]:.2f}; the tick's own "
            f"closes value the sleeve at {marked_equity:.2f} — cost-basis "
            f"book equity overshoots true equity in drawdowns, inflating "
            f"every risk-sized order exactly when the sleeve is losing "
            f"(audit #12)"
        )


class TestNegativeCashFillIsSignalled:
    def test_fill_driving_sleeve_cash_negative_logs_a_warning(self, caplog):
        """#13: reserve checks qty*reserve-price, but the entry is a MARKET
        order — an unfavorable fill books the shortfall floor-lessly and cash
        goes silently negative (repro-proven in the audit). Contract: the
        poll that records such a fill emits a WARNING+ mentioning cash — an
        operator signal, wherever GREEN chooses to alert beyond that."""
        capital_usd = 10_000.0
        sleeve_id = database.create_sleeve(
            f"negcash-{uuid.uuid4().hex}", capital_usd, envelope_json="{}"
        )

        base = load_order_fixture("bracket_filled.json")
        broker_order = dict(base)
        broker_order["filled_avg_price"] = "1005.0"  # reserved at 990 below
        assert not validate_alpaca_order_shape(broker_order)

        reserved_price = 990.0
        qty = float(broker_order["filled_qty"])
        client_order_id = f"negcash-{uuid.uuid4().hex}"
        database.insert_sleeve_order(
            client_order_id=client_order_id,
            sleeve_id=sleeve_id,
            symbol=broker_order["symbol"],
            side=broker_order["side"],
            qty=qty,
            order_class=broker_order["order_class"],
            status="accepted",
            reserved_price=reserved_price,
        )
        database.attach_alpaca_order_id(client_order_id, broker_order["id"])

        with (
            patch.object(
                alpaca_orders,
                "get_order",
                return_value=alpaca_orders.OrderResult(order=broker_order, error=None),
            ),
            caplog.at_level(logging.WARNING),
        ):
            tick_orchestrator.poll_and_apply_fills(sleeve_id)

        state = ledger.reconstruct_from_history(
            capital_usd, database.get_sleeve_order_history(sleeve_id)
        )
        assert state.cash_usd < 0, (
            "fixture sanity: this unfavorable fill must genuinely drive cash "
            "negative, or the warning assertion below is vacuous"
        )
        assert any(
            r.levelno >= logging.WARNING and "cash" in r.getMessage().lower()
            for r in caplog.records
        ), (
            f"recording a fill that leaves sleeve cash at "
            f"{state.cash_usd:.2f} must log WARNING+ mentioning cash — AC-1's "
            f"floor was breached by market slippage and today NOTHING signals "
            f"it (audit #13; the one-sided cash check cannot see an "
            f"under-claim)"
        )


def _seed_n_fires(rule_id: int, sleeve_id: int, n: int, fired_at: str | None = None) -> None:
    for _ in range(n):
        database.insert_sleeve_rule_fire(
            rule_id=rule_id,
            sleeve_id=sleeve_id,
            action="buy",
            rule_class="ENTRY",
            mode_at_fire="SHADOW",
            fired_at=fired_at,
        )


class TestLifetimeFireCountIsUncapped:
    def test_panel_and_digest_lifetime_counts_exceed_the_accessor_default(self):
        """#14: both surfaces render len(get_sleeve_rule_fires(...)) whose
        default limit is 100 — a rule with 150 real fires shows 100 forever.
        The seeded count is the test's own ground truth."""
        seeded = 150
        sleeve_id = database.create_sleeve(
            f"firecap-{uuid.uuid4().hex}", 1_000.0, envelope_json="{}"
        )
        rule_id = database.create_sleeve_rule(sleeve_id, "firecap-rule", json_doc="{}")
        _seed_n_fires(rule_id, sleeve_id, seeded)

        panel = app_module._build_sleeves_panel_context()
        panel_rule = next(
            r for s in panel if s["id"] == sleeve_id for r in s["rules"] if r["id"] == rule_id
        )
        assert panel_rule["lifetime_fires"] == seeded, (
            f"panel lifetime_fires must equal the true row count ({seeded}); "
            f"got {panel_rule['lifetime_fires']} — the accessor's default "
            f"limit=100 silently caps the figure (audit #14; use a COUNT "
            f"accessor, not len(limited rows))"
        )

        today_str = datetime.now(_ET).strftime("%Y-%m-%d")
        summaries = reporting._build_sleeve_digest_summaries(today_str)
        digest_rule = next(
            r
            for s in summaries
            if s["name"] == database.get_sleeve(sleeve_id)["name"]
            for r in s["rules"]
        )
        assert digest_rule["lifetime_fires"] == seeded, (
            f"digest lifetime count must equal the true row count ({seeded}); "
            f"got {digest_rule['lifetime_fires']} (audit #14)"
        )


class TestTodayFireCountUsesEasternDates:
    def test_yesterday_evening_et_fire_is_not_counted_as_today(self):
        """#15: fires are stored in UTC ('%Y-%m-%d %H:%M:%S', runner.py) but
        counted by comparing an ET date prefix against the raw UTC string. A
        fire late YESTERDAY evening ET carries TODAY's UTC date and
        misattributes. Both instants are derived relative to the test's own
        runtime so this pins the invariant at any wall-clock."""
        sleeve_id = database.create_sleeve(f"etutc-{uuid.uuid4().hex}", 1_000.0, envelope_json="{}")
        rule_id = database.create_sleeve_rule(sleeve_id, "etutc-rule", json_doc="{}")

        today_et = datetime.now(_ET).date()
        # 23:30 ET yesterday -> 03:30/04:30 UTC TODAY (EDT/EST — always
        # crosses UTC midnight), stored exactly as runner.py stores it.
        yesterday_evening_et = datetime.combine(
            today_et - timedelta(days=1), time(23, 30), tzinfo=_ET
        )
        stored_yesterday = yesterday_evening_et.astimezone(_UTC).strftime("%Y-%m-%d %H:%M:%S")
        # 10:00 ET today -> same date in both zones.
        today_morning_et = datetime.combine(today_et, time(10, 0), tzinfo=_ET)
        stored_today = today_morning_et.astimezone(_UTC).strftime("%Y-%m-%d %H:%M:%S")

        _seed_n_fires(rule_id, sleeve_id, 1, fired_at=stored_yesterday)
        _seed_n_fires(rule_id, sleeve_id, 1, fired_at=stored_today)

        panel = app_module._build_sleeves_panel_context()
        panel_rule = next(
            r for s in panel if s["id"] == sleeve_id for r in s["rules"] if r["id"] == rule_id
        )
        assert panel_rule["today_fires"] == 1, (
            f"exactly ONE of the two seeded fires happened today in ET; panel "
            f"counts {panel_rule['today_fires']} — the ET 'today' prefix is "
            f"compared against UTC-stored timestamps (audit #15; convert the "
            f"stored UTC instant to ET before comparing; "
            f"database.get_fire_count_for_rule_on_day shares the same "
            f"defect — fix once)"
        )

        today_str = datetime.now(_ET).strftime("%Y-%m-%d")
        summaries = reporting._build_sleeve_digest_summaries(today_str)
        digest_rule = next(
            r
            for s in summaries
            if s["name"] == database.get_sleeve(sleeve_id)["name"]
            for r in s["rules"]
        )
        assert digest_rule["today_fires"] == 1, (
            f"digest today count must also be ET-correct; got "
            f"{digest_rule['today_fires']} (audit #15)"
        )


class TestLatchCompensationFailsClosedOnPostAckCrash:
    def test_crash_after_broker_ack_never_restores_pacing_or_doubles_the_order(self, client):
        """Reviewer finding SF-R-1 (BLOCK, upheld by PM): the #9 latch
        compensation reads runner-local evidence (fire_ids / action_results
        order_ids), but a crash INSIDE dispatch_action AFTER a successful
        broker submit — e.g. database.attach_alpaca_order_id raising post-ack
        — discards that evidence: both look empty, pacing is wrongly
        restored, and a still-true condition submits a SECOND live order next
        tick while the first sits at the broker. Duplicated money.

        Pinned invariant: a crash anywhere around submit never doubles an
        order. When the broker may have received the order, the compensation
        must fail CLOSED — one lost episode (the documented #9 trade-off) is
        acceptable; a doubled order is not. Mechanism (evidence persisted
        before/atomically-with submit, or a DB-grounded restore criterion) is
        GREEN's choice; this test pins only the two-tick outcome."""
        sleeve_id = create_sleeve_via_route(client, capital_usd=10_000.0)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())
        database.insert_sleeve_rule_fire(
            rule_id=rule_id,
            sleeve_id=sleeve_id,
            action="buy",
            rule_class="ENTRY",
            mode_at_fire="SHADOW",
        )
        arm = client.post(
            f"/api/sleeves/{sleeve_id}/rules/{rule_id}/arm", json={"target_mode": "PAPER"}
        )
        assert arm.status_code == 200, "fixture sanity: the arm ceremony must succeed"

        ack = load_order_fixture("bracket_new.json")

        # Tick 1: broker ACKS the order, then the post-ack DB write crashes.
        # Only attach_alpaca_order_id is patched — the pre-submit RESERVED
        # insert must succeed for this to be the real SF-R-1 window.
        with (
            tick_patches(
                account_cash_usd=10_000.0,
                closes_by_symbol={"SPY": 500.0},
                submit_bracket_result=alpaca_orders.OrderResult(order=ack, error=None),
            ) as tick1_mocks,
            patch.object(
                database,
                "attach_alpaca_order_id",
                side_effect=RuntimeError("simulated DB failure after broker ack"),
            ),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        tick1_mocks["submit_bracket_order"].assert_called_once()
        orders_after_crash = database.get_sleeve_orders(sleeve_id=sleeve_id)
        assert len(orders_after_crash) == 1, (
            "fixture sanity: the pre-submit RESERVED row must exist — the "
            "crash landed AFTER the broker accepted the order"
        )
        assert orders_after_crash[0]["alpaca_order_id"] is None, (
            "fixture sanity: the post-ack attach crashed, so the broker id "
            "was never persisted — the exact evidence gap SF-R-1 exploits"
        )
        fires_after_crash = database.get_sleeve_rule_fires(rule_id=rule_id)
        assert [f["mode_at_fire"] for f in fires_after_crash] == ["SHADOW"], (
            "fixture sanity: the crashed tick recorded no NEW fire row — "
            "only the seeded arm-gate SHADOW fire exists"
        )

        # Tick 2: condition STILL TRUE; the first order is LIVE at the
        # broker (the lost-ack recovery finds it by client_order_id). The
        # rule must stay latched — no second fire, no second submit.
        with tick_patches(
            account_cash_usd=10_000.0,
            closes_by_symbol={"SPY": 500.0},
            get_order_result=alpaca_orders.OrderResult(order=ack, error=None),
            submit_bracket_result=alpaca_orders.OrderResult(order=ack, error=None),
        ) as tick2_mocks:
            with patch.object(
                alpaca_orders,
                "get_order_by_client_order_id",
                return_value=alpaca_orders.OrderResult(order=ack, error=None),
            ):
                tick_orchestrator.run_sleeve_tick_for_all_sleeves(
                    now_utc=MARKET_OPEN_NOW_UTC + timedelta(minutes=1)
                )

        tick2_mocks["submit_bracket_order"].assert_not_called()
        final_orders = database.get_sleeve_orders(sleeve_id=sleeve_id)
        assert len(final_orders) == 1, (
            f"exactly ONE sleeve_orders row may exist across both ticks; got "
            f"{len(final_orders)} — the latch compensation read the crash-"
            f"discarded runner-local evidence, saw 'no order', restored the "
            f"episode, and re-fired into a SECOND live broker order "
            f"(SF-R-1): the one failure the #9 design comment itself calls "
            f"worse than a lost episode"
        )
