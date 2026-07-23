"""RED — audit finding #3 (HIGH): sleeve status "PAPER" is unreachable, so the
entire PAPER phase runs inside a SHADOW-status sleeve — the step-0 SHADOW
cleanup cancels a paper-armed rule's orders every tick (live-observed
destroying a real filled bracket), the badge shows a calm standby SHADOW, and
the Disarm kill-switch is disabled in exactly that state.

PM DESIGN RULING (baked into this cycle's brief): arming a rule to PAPER
PROMOTES the sleeve's status to PAPER. The badge map (app.py
_SLEEVE_STATUS_BADGE_CLASSES) and the fixture schema
(tests/fixtures/database/sleeves/sleeves_schema.json) already expect a PAPER
sleeve status — the transition was simply never built into any route.

Pinned contracts:
  * POST .../rules/<id>/arm (AC-13 gate passed) => rule mode PAPER AND the
    owning sleeve's status PAPER.
  * A rejected arm (no recorded SHADOW fire) promotes nothing.
  * A PAPER-status sleeve's non-terminal broker orders SURVIVE the next tick
    (step-0 cleanup is a SHADOW-only behavior).
  * Disarm still demotes sleeve + rules to SHADOW, and the next tick then
    cancels lingering orders (AC-12 unchanged — regression guard).
  * The dashboard panel renders the PAPER badge class, distinct from SHADOW.
  * Capstone: the full production ladder — create route -> real shadow-fire
    tick -> arm route -> real armed tick places a bracket order -> the order
    survives the following tick — works end-to-end.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import app as app_module
import database
import sleeves.alpaca_orders as alpaca_orders
import sleeves.tick_orchestrator as tick_orchestrator
from tests.sleeves._alpaca_fixtures import load_order_fixture
from tests.sleeves._route_shaped import (
    MARKET_OPEN_NOW_UTC,
    create_rule_via_route,
    create_sleeve_via_route,
    tick_patches,
    valid_route_rule_payload,
)


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _seed_shadow_fire(rule_id: int, sleeve_id: int) -> None:
    """Insert the sleeve_rule_fires row a real SHADOW tick writes (unlocks the
    AC-13 arm gate) — seeded directly so these tests isolate the ARM ROUTE
    behavior from wiring finding #1; the capstone test below drives the real
    tick instead."""
    database.insert_sleeve_rule_fire(
        rule_id=rule_id,
        sleeve_id=sleeve_id,
        action="buy",
        rule_class="ENTRY",
        mode_at_fire="SHADOW",
    )


def _arm(client, sleeve_id: int, rule_id: int):
    return client.post(
        f"/api/sleeves/{sleeve_id}/rules/{rule_id}/arm", json={"target_mode": "PAPER"}
    )


def _seed_acked_open_order(sleeve_id: int, *, rule_id: int | None = None) -> str:
    """A broker-acked, unfilled bracket entry — the exact state the audit's
    live smoke had when the SHADOW cleanup destroyed it."""
    client_order_id = f"arm-promo-{sleeve_id}-{rule_id or 0}"
    database.insert_sleeve_order(
        client_order_id=client_order_id,
        sleeve_id=sleeve_id,
        rule_id=rule_id,
        symbol="SPY",
        side="buy",
        qty=4.0,
        order_class="bracket",
        status="accepted",
        reserved_price=500.0,
    )
    database.attach_alpaca_order_id(client_order_id, f"alpaca-{client_order_id}")
    return client_order_id


class TestArmRoutePromotesSleeveStatus:
    def test_successful_paper_arm_promotes_sleeve_status_to_paper(self, client):
        sleeve_id = create_sleeve_via_route(client)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())
        _seed_shadow_fire(rule_id, sleeve_id)

        resp = _arm(client, sleeve_id, rule_id)

        assert resp.status_code == 200
        assert database.get_sleeve_rule(rule_id)["mode"] == "PAPER"
        assert database.get_sleeve(sleeve_id)["status"] == "PAPER", (
            "arming a rule to PAPER must promote the SLEEVE status to PAPER "
            "(PM design ruling) — a paper-armed rule living in a SHADOW-status "
            "sleeve gets its orders cancelled by the step-0 cleanup every "
            "tick, renders a calm standby badge, and disables the Disarm "
            "kill-switch (audit finding #3)"
        )

    def test_rejected_arm_without_shadow_fire_promotes_nothing(self, client):
        sleeve_id = create_sleeve_via_route(client)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())
        # No shadow fire seeded -> AC-13 gate must reject.

        resp = _arm(client, sleeve_id, rule_id)

        assert resp.status_code == 400
        assert database.get_sleeve_rule(rule_id)["mode"] == "SHADOW"
        assert database.get_sleeve(sleeve_id)["status"] == "SHADOW", (
            "a REJECTED arm must not promote the sleeve — promotion is a "
            "side-effect of a successful ceremony only"
        )


class TestPaperSleeveTickCoherence:
    def test_paper_armed_sleeves_open_orders_survive_the_next_tick(self, client):
        """The audit's live-observed money-loser: an armed sleeve's resting
        bracket (entry ack, unfilled) must NOT be cancelled by the next tick's
        SHADOW cleanup once the sleeve is PAPER-armed. Today the arm route
        leaves the sleeve SHADOW, so the tick treats it as disarmed and
        cancels the armed rule's own order."""
        sleeve_id = create_sleeve_via_route(client)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())
        _seed_shadow_fire(rule_id, sleeve_id)
        assert _arm(client, sleeve_id, rule_id).status_code == 200

        client_order_id = _seed_acked_open_order(sleeve_id, rule_id=rule_id)
        new_fixture = load_order_fixture("bracket_new.json")

        with tick_patches(
            account_cash_usd=10_000.0,
            closes_by_symbol={"SPY": 50.0},  # condition false — no new fire noise
            get_order_result=alpaca_orders.OrderResult(order=new_fixture, error=None),
            cancel_order_result=alpaca_orders.OrderResult(order={}, error=None),
        ) as mocks:
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        mocks["cancel_order"].assert_not_called()
        status = database.get_sleeve_order_by_client_id(client_order_id)["status"]
        assert status != "canceled", (
            f"a PAPER-armed sleeve's open order must survive the tick — got "
            f"status {status!r}; 'canceled' means the SHADOW cleanup still "
            f"treats an armed sleeve as disarmed (finding #3 cascade: resting "
            f"brackets, trailing stops and unfilled entries destroyed every "
            f"tick, voiding AC-8 during PAPER)"
        )

    def test_disarm_demotes_sleeve_and_next_tick_cancels_lingering_orders(self, client):
        """AC-12 regression guard: the fix must not break the disarm flow —
        disarm reverts sleeve+rules to SHADOW synchronously, and the ENGINE's
        next tick performs the actual broker cancellation."""
        sleeve_id = create_sleeve_via_route(client)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())
        database.update_sleeve_status(sleeve_id, "PAPER")
        database.update_sleeve_rule_mode(rule_id, "PAPER")

        resp = client.post(f"/api/sleeves/{sleeve_id}/disarm")
        assert resp.status_code == 200
        assert database.get_sleeve(sleeve_id)["status"] == "SHADOW"
        assert database.get_sleeve_rule(rule_id)["mode"] == "SHADOW"

        client_order_id = _seed_acked_open_order(sleeve_id, rule_id=rule_id)
        with tick_patches(
            account_cash_usd=10_000.0,
            closes_by_symbol={"SPY": 50.0},
            get_order_result=alpaca_orders.OrderResult(
                order=load_order_fixture("bracket_new.json"), error=None
            ),
            cancel_order_result=alpaca_orders.OrderResult(order={}, error=None),
        ) as mocks:
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        mocks["cancel_order"].assert_called()
        assert database.get_sleeve_order_by_client_id(client_order_id)["status"] == "canceled", (
            "a DISARMED (SHADOW) sleeve's lingering open order must still be "
            "cancelled on the next tick — the PAPER-survival fix must not "
            "disable the AC-12 cleanup for genuinely disarmed sleeves"
        )


class TestPanelBadgeCoherence:
    def test_panel_renders_paper_badge_after_arm_not_standby(self, client):
        sleeve_id = create_sleeve_via_route(client)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())
        _seed_shadow_fire(rule_id, sleeve_id)
        assert _arm(client, sleeve_id, rule_id).status_code == 200

        panel = app_module._build_sleeves_panel_context()
        entry = next(s for s in panel if s["id"] == sleeve_id)

        badge_map = app_module._SLEEVE_STATUS_BADGE_CLASSES
        assert entry["status"] == "PAPER"
        assert entry["status_badge_class"] == badge_map["PAPER"], (
            f"an armed sleeve must render the PAPER badge class "
            f"({badge_map['PAPER']!r}); got {entry['status_badge_class']!r}"
        )
        assert entry["status_badge_class"] != badge_map["SHADOW"], (
            "the PAPER badge must be visually distinct from the calm SHADOW "
            "standby pill — an armed, trading sleeve rendering as standby is "
            "audit finding #3(a)"
        )


class TestFullLadderEndToEnd:
    def test_create_shadow_fire_arm_paper_order_and_survival_end_to_end(self, client):
        """The epic's definition of functioning, via ONLY production surfaces:
        route-create -> real tick records a SHADOW fire -> arm route promotes
        -> rearm window passes -> real tick places a PAPER bracket order
        (AC-19 fire<->order linkage) -> the order survives the following tick
        un-cancelled."""
        sleeve_id = create_sleeve_via_route(client, capital_usd=10_000.0)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())

        # Tick 1 (condition true): SHADOW fire recorded.
        with tick_patches(account_cash_usd=10_000.0, closes_by_symbol={"SPY": 500.0}):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)
        assert database.get_sleeve_rule_fires(rule_id=rule_id), (
            "fixture sanity (finding #1): the production tick must record the "
            "SHADOW fire this ladder builds on"
        )

        # Arm: AC-13 gate satisfied by the REAL recorded fire.
        resp = client.post(
            f"/api/sleeves/{sleeve_id}/rules/{rule_id}/arm", json={"target_mode": "PAPER"}
        )
        assert resp.status_code == 200
        assert database.get_sleeve(sleeve_id)["status"] == "PAPER"

        ack_fixture = load_order_fixture("bracket_new.json")

        # Ticks 2-4 (condition false): episode rearm (rearm_ticks=3).
        for i in range(1, 4):
            with tick_patches(account_cash_usd=10_000.0, closes_by_symbol={"SPY": 50.0}):
                tick_orchestrator.run_sleeve_tick_for_all_sleeves(
                    now_utc=MARKET_OPEN_NOW_UTC + timedelta(minutes=i)
                )

        # Tick 5 (condition true, rule PAPER): a real bracket order is placed.
        with tick_patches(
            account_cash_usd=10_000.0,
            closes_by_symbol={"SPY": 500.0},
            submit_bracket_result=alpaca_orders.OrderResult(order=ack_fixture, error=None),
        ) as mocks:
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(
                now_utc=MARKET_OPEN_NOW_UTC + timedelta(minutes=4)
            )

        mocks["submit_bracket_order"].assert_called_once()
        orders = database.get_sleeve_orders(sleeve_id=sleeve_id)
        assert len(orders) == 1, (
            f"the armed tick must create exactly one sleeve_orders row; got {len(orders)}"
        )
        order = orders[0]
        assert order["rule_id"] == rule_id
        assert order["alpaca_order_id"] == ack_fixture["id"], (
            "the broker ack's own order id must be attached to the RESERVED row"
        )

        paper_fires = [
            f
            for f in database.get_sleeve_rule_fires(rule_id=rule_id)
            if f["mode_at_fire"] == "PAPER"
        ]
        assert paper_fires, "the armed fire must be recorded with mode_at_fire=PAPER"
        assert paper_fires[0]["order_id"] == order["id"], (
            "AC-19: the armed fire row must carry the real sleeve_orders.id it produced"
        )

        # Tick 6: the resting order survives (no SHADOW-cleanup cancellation).
        with tick_patches(
            account_cash_usd=10_000.0,
            closes_by_symbol={"SPY": 500.0},
            get_order_result=alpaca_orders.OrderResult(order=ack_fixture, error=None),
            cancel_order_result=alpaca_orders.OrderResult(order={}, error=None),
        ) as mocks:
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(
                now_utc=MARKET_OPEN_NOW_UTC + timedelta(minutes=5)
            )

        mocks["cancel_order"].assert_not_called()
        final_status = database.get_sleeve_order_by_client_id(order["client_order_id"])["status"]
        assert final_status != "canceled", (
            f"the paper order must survive the tick after placement; got "
            f"{final_status!r} — the audit's live smoke lost a real filled "
            f"bracket to exactly this cancellation"
        )


class TestArmRouteGuards:
    """Ratified interface pins (sf-dash <-> sf-test, 2026-07-09): the
    promotion in the arm route is guarded — it only ever lifts a SHADOW
    sleeve. Neither guard may regress."""

    def test_arm_while_paused_reconciliation_returns_conflict_and_mutates_nothing(self, client):
        """PAUSED_RECONCILIATION is a money-truth pause — arming a rule must
        never silently clear it (promotion would re-enable rule evaluation
        while the drift that paused the sleeve is uninvestigated)."""
        sleeve_id = create_sleeve_via_route(client)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())
        _seed_shadow_fire(rule_id, sleeve_id)
        database.update_sleeve_status(sleeve_id, "PAUSED_RECONCILIATION")

        resp = _arm(client, sleeve_id, rule_id)

        assert resp.status_code == 409, (
            f"arming inside a PAUSED_RECONCILIATION sleeve must be refused "
            f"with 409; got {resp.status_code} — a successful arm here would "
            f"silently clear a money-truth pause"
        )
        assert database.get_sleeve(sleeve_id)["status"] == "PAUSED_RECONCILIATION", (
            "the pause must survive the refused arm untouched"
        )
        assert database.get_sleeve_rule(rule_id)["mode"] == "SHADOW", (
            "the rule must stay SHADOW after the refused arm"
        )

    def test_arming_a_rule_in_a_live_sleeve_never_demotes_sleeve_status(self, client):
        """Promotion lifts SHADOW->PAPER only: a LIVE sleeve gaining a newly
        paper-armed rule keeps its LIVE status (writing PAPER over LIVE would
        demote the whole sleeve as a side effect of a rule-level action)."""
        sleeve_id = create_sleeve_via_route(client)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())
        _seed_shadow_fire(rule_id, sleeve_id)
        database.update_sleeve_status(sleeve_id, "LIVE")

        resp = _arm(client, sleeve_id, rule_id)

        assert resp.status_code == 200
        assert database.get_sleeve_rule(rule_id)["mode"] == "PAPER"
        assert database.get_sleeve(sleeve_id)["status"] == "LIVE", (
            "arm must never DEMOTE a LIVE sleeve to PAPER — promotion is strictly SHADOW->PAPER"
        )


class TestTwoKeyArmedDispatchGate:
    """Ratified defense-in-depth pin (sf-eng proposal, sf-test ratified,
    2026-07-09 — flagged to PM): the armed order path requires BOTH keys to
    agree — rule mode PAPER/LIVE AND sleeve status PAPER/LIVE. A DB-drift
    state (paper rule inside a SHADOW-status sleeve — unreachable via routes
    after the promotion fix, but exactly the state audit #3/#4 lost money in)
    fails toward shadow: evaluate, record, place NOTHING."""

    def test_paper_rule_in_shadow_status_sleeve_places_no_order(self, client):
        sleeve_id = create_sleeve_via_route(client)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())
        # Seed the drift state directly: rule armed, sleeve NOT promoted.
        database.update_sleeve_rule_mode(rule_id, "PAPER")
        assert database.get_sleeve(sleeve_id)["status"] == "SHADOW", (
            "fixture sanity: the sleeve status must be the stale SHADOW side of the drift"
        )

        with tick_patches(
            account_cash_usd=10_000.0,
            closes_by_symbol={"SPY": 500.0},
            submit_bracket_result=alpaca_orders.OrderResult(order=None, error="HTTP 500"),
        ) as mocks:
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        mocks["submit_bracket_order"].assert_not_called()
        assert database.get_sleeve_orders(sleeve_id=sleeve_id) == [], (
            "a PAPER-mode rule inside a SHADOW-status sleeve is an "
            "inconsistent two-key state and must fail toward shadow (no "
            "broker order) — placing an order here recreates the audit's "
            "cancel-the-armed-order money-loser the moment step-0 cleanup "
            "runs"
        )


class TestArmGateFireWindow:
    def test_arm_gate_sees_shadow_fires_beyond_the_accessor_default_window(self, client):
        """Review gap G8: the AC-13 gate any()s over
        get_sleeve_rule_fires(rule_id) — default limit 100, newest first. A
        rule re-armed after an active PAPER life carries 100+ newer PAPER
        fires that push every SHADOW fire out of the page, spuriously 400ing
        a legitimately re-armable rule. The gate must consult SHADOW-fire
        EXISTENCE (mode-filtered count/exists), never a limited page."""
        sleeve_id = create_sleeve_via_route(client)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())

        # One genuine SHADOW fire, older than everything else (now-derived).
        old_fired_at = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        database.insert_sleeve_rule_fire(
            rule_id=rule_id,
            sleeve_id=sleeve_id,
            action="buy",
            rule_class="ENTRY",
            mode_at_fire="SHADOW",
            fired_at=old_fired_at,
        )
        # 120 newer PAPER fires from the rule's previous armed life (default
        # fired_at = insert time = now) — enough to flood the 100-row page.
        for _ in range(120):
            database.insert_sleeve_rule_fire(
                rule_id=rule_id,
                sleeve_id=sleeve_id,
                action="buy",
                rule_class="ENTRY",
                mode_at_fire="PAPER",
            )

        resp = _arm(client, sleeve_id, rule_id)

        assert resp.status_code == 200, (
            f"the rule HAS a recorded SHADOW fire — the AC-13 gate must see "
            f"it regardless of how many newer PAPER fires exist; got "
            f"{resp.status_code}: {resp.get_data(as_text=True)[:200]!r} "
            f"(the any()-over-limited-page gate silently forgets shadow "
            f"history after ~100 armed fires)"
        )
        assert database.get_sleeve_rule(rule_id)["mode"] == "PAPER"
