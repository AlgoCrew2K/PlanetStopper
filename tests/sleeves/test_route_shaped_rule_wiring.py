"""RED — audit findings #1 (CRIT: sleeve_id never merged, rule engine 100%
inert via the production path) and #2 (CRIT: doc-supplied "mode" overrides the
row's forced-SHADOW mode).

Root cause (VERDICT-branch.md #1/#2): sleeves/tick_orchestrator.py's
``_load_enabled_rules`` builds ``{"id": row["id"], "mode": row["mode"], **doc}``
— the doc merges LAST, and ``sleeve_id`` is never merged at all. The create
route (app.py create_sleeve_rule_route) stores the raw client payload as
json_doc; ``sleeve_id`` lives only in the URL + the sleeve_rules.sleeve_id
column, so every rule created the production way crashes on ``rule["sleeve_id"]``
(runner.py) on its first fireable tick — swallowed per-sleeve, zero fires,
forever. And a crafted/imported doc carrying ``"mode"``/``"id"``/``"sleeve_id"``
keys overrides the row's authoritative columns.

CONTRACT pinned here: the ROW is authoritative for id / mode / sleeve_id.
Whether GREEN achieves that by inverting the merge order
(``{**doc, "id": ..., "sleeve_id": ..., "mode": ...}``), by stripping reserved
keys at validation/store time, or both, these tests only pin the observable
behavior: a route-created rule fires via a real tick, and no doc key can
reassign a rule's identity, sleeve, or arming mode.

HARD RULE for anyone touching this file: rule payloads here must NEVER gain a
``sleeve_id``/``mode``/``id`` key to make a test pass — that is the exact
fixture bug (tests/sleeves/rules/conftest.py make_stored_rule) that let two
CRITICALs ship behind 1200 green tests.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

import app as app_module
import database
import sleeves.tick_orchestrator as tick_orchestrator
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


class TestRouteCreatedRuleFiresViaRealTick:
    """Finding #1 — the epic's core function, exercised the production way."""

    def test_route_created_rule_records_a_shadow_fire_on_a_real_tick(self, client):
        sleeve_id = create_sleeve_via_route(client, capital_usd=10_000.0)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())

        # Fixture sanity: the stored doc is the raw route payload — it must
        # NOT contain sleeve_id (that absence is exactly what production
        # produces and what the engine must survive).
        stored_doc = json.loads(database.get_sleeve_rule(rule_id)["json_doc"])
        assert "sleeve_id" not in stored_doc, (
            "fixture sanity: the route-stored json_doc must not contain "
            "sleeve_id — if it does, this test is no longer route-shaped"
        )

        # SMA(20) of a constant 500-close series is 500 > 100 -> condition
        # true; SHADOW mode places no order, so no broker order doubles needed.
        with tick_patches(account_cash_usd=10_000.0, closes_by_symbol={"SPY": 500.0}):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        fires = database.get_sleeve_rule_fires(rule_id=rule_id)
        assert fires, (
            "a rule created via the REAL route (doc without sleeve_id) must "
            "fire and record a sleeve_rule_fires row on a fireable tick — "
            "zero fires means the engine is inert on the production path "
            "(audit finding #1: KeyError 'sleeve_id' swallowed per-sleeve)"
        )
        fire = fires[0]
        assert fire["sleeve_id"] == sleeve_id, (
            "the fire must be attributed to the sleeve that owns the rule "
            "(the sleeve_rules row / URL sleeve), never a doc-derived value"
        )
        assert fire["mode_at_fire"] == "SHADOW", (
            "a route-created rule is born SHADOW (AC-6); its first fire must "
            "record mode_at_fire=SHADOW"
        )

    def test_shadow_fire_from_real_tick_unlocks_the_ac13_arm_gate(self, client):
        """Corollary of #1: with zero recordable SHADOW fires the whole arming
        ladder is dead from rung 1 — the AC-13 gate can never pass. The full
        create -> real tick -> arm flow must succeed end-to-end."""
        sleeve_id = create_sleeve_via_route(client, capital_usd=10_000.0)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())

        with tick_patches(account_cash_usd=10_000.0, closes_by_symbol={"SPY": 500.0}):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        resp = client.post(
            f"/api/sleeves/{sleeve_id}/rules/{rule_id}/arm", json={"target_mode": "PAPER"}
        )
        assert resp.status_code == 200, (
            f"SHADOW->PAPER arm must succeed after a real recorded shadow fire; "
            f"got {resp.status_code}: {resp.get_data(as_text=True)[:300]!r} — "
            f"if this 400s with 'no recorded SHADOW evaluation', the "
            f"production path never recorded the fire (finding #1)"
        )
        assert database.get_sleeve_rule(rule_id)["mode"] == "PAPER"


class TestDocKeysNeverOverrideRowColumns:
    """Finding #2 — the row's id/mode/sleeve_id are authoritative; a stored
    doc's copies of those keys must be inert."""

    def test_stored_doc_mode_paper_never_overrides_row_forced_shadow(self, client):
        """A crafted/imported json_doc carrying "mode": "PAPER" (and its own
        sleeve_id, so the tick reaches action dispatch even before the #1 fix)
        must still evaluate as SHADOW: the ROW mode column — forced SHADOW at
        creation, changed only by the arm ceremony — is the arming source of
        truth. Today the doc merges last and wins: shadow=False, a real order
        is placed with zero ceremony (AC-6/AC-13 bypass, proven live in the
        audit)."""
        sleeve_id = create_sleeve_via_route(client, capital_usd=10_000.0)
        doc = valid_route_rule_payload()
        doc["mode"] = "PAPER"  # the attack/import payload
        doc["sleeve_id"] = sleeve_id  # present so pre-#1-fix code reaches dispatch
        rule_id = database.create_sleeve_rule(
            sleeve_id, doc["name"], json_doc=json.dumps(doc), mode="SHADOW"
        )
        assert database.get_sleeve_rule(rule_id)["mode"] == "SHADOW", (
            "fixture sanity: the ROW is born SHADOW regardless of doc content"
        )

        import sleeves.alpaca_orders as alpaca_orders

        with tick_patches(
            account_cash_usd=10_000.0,
            closes_by_symbol={"SPY": 500.0},
            submit_bracket_result=alpaca_orders.OrderResult(order=None, error="HTTP 500"),
        ) as mocks:
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        mocks["submit_bracket_order"].assert_not_called()
        assert database.get_sleeve_orders(sleeve_id=sleeve_id) == [], (
            "a never-armed rule (row mode SHADOW) must place ZERO orders no "
            "matter what mode its stored doc claims — a doc-supplied "
            "'mode': 'PAPER' bypassing the arming ceremony is audit CRIT #2"
        )
        for fire in database.get_sleeve_rule_fires(rule_id=rule_id):
            assert fire["mode_at_fire"] == "SHADOW", (
                f"every fire of a row-SHADOW rule must record "
                f"mode_at_fire=SHADOW; got {fire['mode_at_fire']!r} — the doc "
                f"mode leaked into the merged rule dict"
            )

    def test_route_created_payload_with_mode_key_cannot_arm_itself(self, client):
        """Same invariant via the actual route: the create route accepts a
        payload carrying "mode": "PAPER" today (schema does not close
        top-level keys). Whether GREEN rejects/strips the key at create time
        or neutralizes it at load time, the tick must treat the rule as
        SHADOW. (Before the #1 fix this passes vacuously — the KeyError kills
        the rule first; it becomes the standing route-level regression guard
        the moment sleeve_id is merged.)"""
        sleeve_id = create_sleeve_via_route(client, capital_usd=10_000.0)
        payload = valid_route_rule_payload()
        payload["mode"] = "PAPER"
        resp = client.post(f"/api/sleeves/{sleeve_id}/rules", json=payload)
        if resp.status_code != 200:
            # GREEN chose to REJECT reserved keys at the route — acceptable;
            # nothing was created, nothing can trade.
            assert database.get_sleeve_rules_for_sleeve(sleeve_id) == []
            return
        rule_id = resp.get_json()["rule"]["id"]

        import sleeves.alpaca_orders as alpaca_orders

        with tick_patches(
            account_cash_usd=10_000.0,
            closes_by_symbol={"SPY": 500.0},
            submit_bracket_result=alpaca_orders.OrderResult(order=None, error="HTTP 500"),
        ) as mocks:
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        mocks["submit_bracket_order"].assert_not_called()
        assert database.get_sleeve_orders(sleeve_id=sleeve_id) == []
        for fire in database.get_sleeve_rule_fires(rule_id=rule_id):
            assert fire["mode_at_fire"] == "SHADOW"

    def test_doc_supplied_sleeve_id_and_id_never_reattribute_fires(self, client):
        """A wrong doc-supplied sleeve_id must not book fires (and, by the
        same wiring, orders/ledger history) against ANOTHER sleeve; a
        doc-supplied id must not masquerade as another rule's identity in the
        fire log (it also keys pacing state — runner.py uses rule['id'])."""
        sleeve_a = create_sleeve_via_route(client, capital_usd=10_000.0)
        sleeve_b = create_sleeve_via_route(client, capital_usd=10_000.0)

        doc = valid_route_rule_payload()
        doc["sleeve_id"] = sleeve_b  # hostile/wrong cross-attribution
        doc["id"] = 999_999  # bogus identity
        rule_id = database.create_sleeve_rule(
            sleeve_a, doc["name"], json_doc=json.dumps(doc), mode="SHADOW"
        )

        with tick_patches(account_cash_usd=20_000.0, closes_by_symbol={"SPY": 500.0}):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        assert database.get_sleeve_rule_fires(sleeve_id=sleeve_b) == [], (
            "no fire may ever be attributed to sleeve B — the rule belongs to "
            "sleeve A's sleeve_rules row; a doc-supplied sleeve_id silently "
            "rebooking history against another sleeve is audit finding #1's "
            "second corollary"
        )
        fires_for_row_id = database.get_sleeve_rule_fires(rule_id=rule_id)
        assert fires_for_row_id, (
            "the rule must fire under its REAL row id — zero fires under the "
            "row id means the doc 'id' (999999) took over rule identity "
            "(fire attribution + pacing keys)"
        )
        for fire in fires_for_row_id:
            assert fire["sleeve_id"] == sleeve_a


class TestMultiTickProductionCadence:
    """Finding #1's silent-failure shape: the audit's live smoke showed the
    KeyError repeating EVERY tick with the episode latch already consumed —
    so even a later fix of a single tick would leave the rule dead. Pin that
    consecutive route-shaped ticks keep evaluating (latch/rearm machinery
    functioning) rather than dying identically every minute."""

    def test_route_created_rule_relatches_and_refires_across_real_ticks(self, client):
        sleeve_id = create_sleeve_via_route(client, capital_usd=10_000.0)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())

        # Tick 1: condition true -> fire + latch. Ticks 2-4: condition false
        # (closes 50 -> SMA 50 < 100) -> rearm after rearm_ticks=3. Tick 5:
        # condition true again -> second fire.
        tick_prices = [500.0, 50.0, 50.0, 50.0, 500.0]
        for i, px in enumerate(tick_prices):
            with tick_patches(account_cash_usd=10_000.0, closes_by_symbol={"SPY": px}):
                tick_orchestrator.run_sleeve_tick_for_all_sleeves(
                    now_utc=MARKET_OPEN_NOW_UTC + timedelta(minutes=i)
                )

        fires = database.get_sleeve_rule_fires(rule_id=rule_id)
        assert len(fires) == 2, (
            f"expected exactly 2 fires across the true/false/false/false/true "
            f"tick sequence (fire -> latch -> rearm -> fire); got {len(fires)} "
            f"— 0 means the production path is inert (finding #1), 1 means "
            f"the episode latch never rearmed, >2 means the latch is broken "
            f"in the other direction"
        )
