"""RED — AC-11 churn brake (NOT BUILT — audit finding #17) and stale-rule
visibility (feature-plan Edge Cases: "Rule references delisted/renamed symbol
-> rule flagged stale, skipped"; AC-16 lists BENCHED and stale among the
panel's sleeve statuses).

AC-11 (feature-plans/managed-sleeves.md, verbatim): "Churn brake: an ENTRY
rule with >=5 same-day round trips at a meaningful net loss is benched for
the day (visible status, Discord note, auto re-arm next trading day). Exits
are never benched."

Pinned contracts (mechanism — where bench state lives, how "meaningful" is
thresholded — is implementation freedom; these scenarios are unambiguous:
5 completed round trips losing 5% of sleeve capital in one day):
  * the benched ENTRY rule does not fire again that trading day, while an
    identical un-churned rule in another sleeve fires on the same tick
    (self-controlled — the silence is attributable to the brake);
  * it auto-re-arms the next trading day (no operator action);
  * bench state is VISIBLE: panel rule entry benched-truthy, sleeve pill
    renders the BENCHED badge class, digest rule entry benched=True;
  * a Discord note is posted when the bench engages;
  * a DEFENSIVE rule with the identical losing history still fires — exits
    are never benched.

Stale: after a tick where a rule's symbol produced no bars (delisted/renamed
— distinct from a whole-feed failure), the panel exposes a rule-level stale
flag, and a sleeve whose EVERY enabled rule is stale renders the 'stale'
badge class (AC-16's sleeve-status vocabulary; per this cycle's brief the
PAPER/BENCHED/stale badges must all be reachable).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest

import app as app_module
import database
import reporting
import sleeves.alpaca_orders as alpaca_orders
import sleeves.tick_orchestrator as tick_orchestrator
from tests.sleeves._route_shaped import (
    MARKET_OPEN_NOW_UTC,
    NEXT_TRADING_DAY_NOW_UTC,
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


_CAPITAL_USD = 10_000.0
# 5 round trips, each buying 10 @ 100 and selling 10 @ 90: -$100/trip,
# -$500 total = 5% of capital in one day — unambiguously "meaningful".
_TRIPS = 5
_TRIP_QTY = 10.0
_BUY_PRICE = 100.0
_SELL_PRICE = 90.0


def _seed_same_day_losing_round_trips(sleeve_id: int, rule_id: int) -> float:
    """Write the order+fill rows _TRIPS completed losing round trips leave
    behind, all on MARKET_OPEN_NOW_UTC's ET trading day, attributed to
    rule_id. Returns the net realized loss (negative)."""
    for trip in range(_TRIPS):
        for side, price in (("buy", _BUY_PRICE), ("sell", _SELL_PRICE)):
            client_order_id = f"churn-{rule_id}-{trip}-{side}-{uuid.uuid4().hex[:8]}"
            filled_at = (
                MARKET_OPEN_NOW_UTC
                - timedelta(minutes=60 - trip * 10 - (5 if side == "sell" else 0))
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            order_pk = database.insert_sleeve_order(
                client_order_id=client_order_id,
                sleeve_id=sleeve_id,
                rule_id=rule_id,
                symbol="SPY",
                side=side,
                qty=_TRIP_QTY,
                status="filled",
                reserved_price=price if side == "buy" else None,
            )
            database.insert_sleeve_fill(
                order_id=order_pk,
                fill_price=price,
                filled_qty=_TRIP_QTY,
                filled_at=filled_at,
            )
    return _TRIPS * _TRIP_QTY * (_SELL_PRICE - _BUY_PRICE)


def _post_churn_cash(net_loss: float) -> float:
    return _CAPITAL_USD + net_loss  # net_loss is negative


class TestChurnBrakeBenchesTheEntryRule:
    def _make_churned_and_clean_pair(self, client) -> tuple[int, int, int, int, float]:
        """Two sleeves with identical entry rules; only the first has the
        losing-round-trip history. Returns (churned_sleeve, churned_rule,
        clean_sleeve, clean_rule, combined_account_cash)."""
        churned_sleeve = create_sleeve_via_route(client, capital_usd=_CAPITAL_USD)
        churned_rule = create_rule_via_route(client, churned_sleeve, valid_route_rule_payload())
        clean_sleeve = create_sleeve_via_route(client, capital_usd=_CAPITAL_USD)
        clean_rule = create_rule_via_route(client, clean_sleeve, valid_route_rule_payload())

        net_loss = _seed_same_day_losing_round_trips(churned_sleeve, churned_rule)
        combined_cash = _post_churn_cash(net_loss) + _CAPITAL_USD
        return churned_sleeve, churned_rule, clean_sleeve, clean_rule, combined_cash

    def test_churned_entry_rule_is_silent_while_identical_clean_rule_fires(self, client):
        churned_sleeve, churned_rule, _clean_sleeve, clean_rule, cash = (
            self._make_churned_and_clean_pair(client)
        )
        assert churned_sleeve

        with tick_patches(account_cash_usd=cash, closes_by_symbol={"SPY": 500.0}):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        assert database.get_sleeve_rule_fires(rule_id=clean_rule), (
            "fixture sanity/control: the identical rule WITHOUT churn history "
            "must fire on this tick — if it doesn't, the churned rule's "
            "silence below proves nothing"
        )
        assert database.get_sleeve_rule_fires(rule_id=churned_rule) == [], (
            f"an ENTRY rule with {_TRIPS} same-day round trips at a "
            f"{_TRIPS * _TRIP_QTY * (_BUY_PRICE - _SELL_PRICE) / _CAPITAL_USD:.0%} "
            f"net loss must be BENCHED for the day (AC-11) — it fired anyway; "
            f"no churn-brake machinery exists (audit #17: AC-11 is not built)"
        )

    def test_benched_rule_auto_rearms_the_next_trading_day(self, client):
        churned_sleeve, churned_rule, _clean_sleeve, _clean_rule, cash = (
            self._make_churned_and_clean_pair(client)
        )
        assert churned_sleeve

        with tick_patches(account_cash_usd=cash, closes_by_symbol={"SPY": 500.0}):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)
        assert database.get_sleeve_rule_fires(rule_id=churned_rule) == [], (
            "fixture sanity: benched on day 1 (see the companion test)"
        )

        with tick_patches(account_cash_usd=cash, closes_by_symbol={"SPY": 500.0}):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=NEXT_TRADING_DAY_NOW_UTC)

        assert database.get_sleeve_rule_fires(rule_id=churned_rule), (
            "the bench is FOR THE DAY (AC-11: 'auto re-arm next trading "
            "day') — the rule must fire again on the next trading day with "
            "no operator action"
        )

    def test_bench_state_is_visible_on_panel_and_digest(self, client):
        churned_sleeve, churned_rule, _clean_sleeve, _clean_rule, cash = (
            self._make_churned_and_clean_pair(client)
        )

        with tick_patches(account_cash_usd=cash, closes_by_symbol={"SPY": 500.0}):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        panel = app_module._build_sleeves_panel_context()
        sleeve_entry = next(s for s in panel if s["id"] == churned_sleeve)
        rule_entry = next(r for r in sleeve_entry["rules"] if r["id"] == churned_rule)
        assert rule_entry.get("benched"), (
            "AC-11 requires a VISIBLE bench status — the panel rule entry "
            "must expose benched-truthy while the brake is engaged"
        )
        badge_map = app_module._SLEEVE_STATUS_BADGE_CLASSES
        assert sleeve_entry["status_badge_class"] == badge_map["BENCHED"], (
            f"a sleeve with a benched rule must surface the BENCHED badge "
            f"class ({badge_map['BENCHED']!r}); got "
            f"{sleeve_entry['status_badge_class']!r} — BENCHED is one of the "
            f"three advertised statuses no production path can reach "
            f"(audit #17)"
        )

        from datetime import datetime
        from zoneinfo import ZoneInfo

        today_str = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        summaries = reporting._build_sleeve_digest_summaries(today_str)
        digest_entry = next(
            s for s in summaries if s["name"] == database.get_sleeve(churned_sleeve)["name"]
        )
        digest_rule = next(r for r in digest_entry["rules"] if r["name"] == rule_entry["name"])
        assert digest_rule.get("benched") is True, (
            "the digest's benched flag is hardcoded False (reporting.py) — it "
            "must reflect the real bench state so the '— BENCHED' note "
            "renders (AC-17)"
        )

    def test_bench_engagement_posts_a_discord_note(self, client):
        churned_sleeve, _churned_rule, _clean_sleeve, _clean_rule, cash = (
            self._make_churned_and_clean_pair(client)
        )
        assert churned_sleeve

        with (
            tick_patches(account_cash_usd=cash, closes_by_symbol={"SPY": 500.0}),
            patch("requests.post") as mock_post,
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(
                now_utc=MARKET_OPEN_NOW_UTC,
                discord_webhook_url="https://discord.test/webhook",
            )

        bench_notes = [call for call in mock_post.call_args_list if "bench" in str(call).lower()]
        assert bench_notes, (
            "AC-11 requires a Discord note when the churn brake benches a "
            "rule — the operator must learn their rule was silenced for the "
            "day, and why"
        )

    def test_defensive_rule_with_identical_losing_history_is_never_benched(self, client):
        """AC-11's last clause and AC-10's spirit: exits are never benched —
        insurance must not be silenced by its own cost."""
        sleeve_id = create_sleeve_via_route(client, capital_usd=_CAPITAL_USD)
        defensive_payload = valid_route_rule_payload(then=[{"type": "go_to_cash"}])
        defensive_rule = create_rule_via_route(client, sleeve_id, defensive_payload)
        net_loss = _seed_same_day_losing_round_trips(sleeve_id, defensive_rule)

        with tick_patches(
            account_cash_usd=_post_churn_cash(net_loss), closes_by_symbol={"SPY": 500.0}
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        assert database.get_sleeve_rule_fires(rule_id=defensive_rule), (
            "a DEFENSIVE (go_to_cash) rule must fire regardless of its "
            "same-day round-trip loss history — 'Exits are never benched' "
            "(AC-11), a drawdown brake silenced by its own insurance cost is "
            "the exact category error the plan forbids"
        )


class TestStaleRuleVisibility:
    def test_rule_with_no_bars_is_flagged_stale_on_the_panel(self, client):
        """The tick fetches bars for every referenced symbol; a symbol the
        feed no longer knows (delisted/renamed) comes back empty while OTHER
        symbols return fine — the fail-safe not-fireable path already skips
        it silently. The operator needs to SEE that the rule is dead weight:
        rule-level stale flag, and the sleeve 'stale' badge when every
        enabled rule is stale."""
        stale_sleeve = create_sleeve_via_route(client, capital_usd=_CAPITAL_USD)
        stale_payload = valid_route_rule_payload(when={"symbol": "ZZZGONE"})
        stale_rule = create_rule_via_route(client, stale_sleeve, stale_payload)

        healthy_sleeve = create_sleeve_via_route(client, capital_usd=_CAPITAL_USD)
        healthy_rule = create_rule_via_route(client, healthy_sleeve, valid_route_rule_payload())

        # The bar feed KNOWS SPY (healthy control) and returns nothing for
        # ZZZGONE — a per-symbol gap, not a whole-feed outage.
        with tick_patches(account_cash_usd=2 * _CAPITAL_USD, closes_by_symbol={"SPY": 500.0}):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        panel = app_module._build_sleeves_panel_context()
        stale_entry = next(s for s in panel if s["id"] == stale_sleeve)
        stale_rule_entry = next(r for r in stale_entry["rules"] if r["id"] == stale_rule)
        assert stale_rule_entry.get("stale"), (
            "a rule whose symbol produced no bars this tick must expose a "
            "truthy rule-level stale flag on the panel (feature-plan edge "
            "case: 'rule flagged stale, skipped') — today it is skipped "
            "silently and the operator cannot tell a dead rule from a "
            "quiet one"
        )

        badge_map = app_module._SLEEVE_STATUS_BADGE_CLASSES
        assert stale_entry["status_badge_class"] == badge_map["stale"], (
            f"a sleeve whose EVERY enabled rule is stale must render the "
            f"'stale' badge class ({badge_map['stale']!r}); got "
            f"{stale_entry['status_badge_class']!r} — 'stale' is in AC-16's "
            f"advertised status vocabulary but no production path can "
            f"produce it (audit #17)"
        )

        healthy_entry = next(s for s in panel if s["id"] == healthy_sleeve)
        healthy_rule_entry = next(r for r in healthy_entry["rules"] if r["id"] == healthy_rule)
        assert not healthy_rule_entry.get("stale"), (
            "control: the healthy rule (bars present) must NOT be flagged "
            "stale — staleness is per-symbol truth, not a blanket state"
        )
        assert healthy_entry["status_badge_class"] != badge_map["stale"]


class TestChurnBrakeIsConjunctive:
    def test_five_profitable_round_trips_never_bench(self, client):
        """AC-11's trigger is '>=5 same-day round trips AT A MEANINGFUL NET
        LOSS' — conjunctive. High-frequency WINNING turnover is not churn;
        benching it would silence a working rule. (The exact loss threshold
        is GREEN's named constant, PM-ratified separately — this pin only
        forbids benching on trip COUNT alone.)"""
        sleeve_id = create_sleeve_via_route(client, capital_usd=_CAPITAL_USD)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())

        # Same 5-trip cadence as the losing seeds, but each trip PROFITS
        # (sell above buy) — net +5% of capital on the day.
        profit_sell_price = _BUY_PRICE + (_BUY_PRICE - _SELL_PRICE)
        for trip in range(_TRIPS):
            for side, price in (("buy", _BUY_PRICE), ("sell", profit_sell_price)):
                client_order_id = f"churn-win-{rule_id}-{trip}-{side}-{uuid.uuid4().hex[:8]}"
                filled_at = (
                    MARKET_OPEN_NOW_UTC
                    - timedelta(minutes=60 - trip * 10 - (5 if side == "sell" else 0))
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
                order_pk = database.insert_sleeve_order(
                    client_order_id=client_order_id,
                    sleeve_id=sleeve_id,
                    rule_id=rule_id,
                    symbol="SPY",
                    side=side,
                    qty=_TRIP_QTY,
                    status="filled",
                    reserved_price=price if side == "buy" else None,
                )
                database.insert_sleeve_fill(
                    order_id=order_pk,
                    fill_price=price,
                    filled_qty=_TRIP_QTY,
                    filled_at=filled_at,
                )

        net_gain = _TRIPS * _TRIP_QTY * (profit_sell_price - _BUY_PRICE)
        with tick_patches(
            account_cash_usd=_CAPITAL_USD + net_gain, closes_by_symbol={"SPY": 500.0}
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        assert database.get_sleeve_rule_fires(rule_id=rule_id), (
            f"an ENTRY rule with {_TRIPS} same-day round trips at a NET GAIN "
            f"of ${net_gain:+.2f} must NOT be benched — AC-11's condition is "
            f"conjunctive (trips AND meaningful loss); benching on turnover "
            f"alone silences a working rule"
        )


class TestBadgePrecedence:
    """Ratified display precedence (sf-dash proposal, sf-test ratified,
    2026-07-09; review gap G5): PAUSED_RECONCILIATION > BENCHED > stale >
    base status. The money-truth pause outranks every other pill state; a
    brake outranks the calm base pill."""

    def test_paused_reconciliation_outranks_bench_on_the_pill(self, client):
        """A benched rule inside a sleeve that LATER pauses for
        reconciliation must render the PAUSED pill — a bench flag masking a
        money-truth pause would hide the one state that demands immediate
        operator attention."""
        sleeve_id = create_sleeve_via_route(client, capital_usd=_CAPITAL_USD)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())
        net_loss = _seed_same_day_losing_round_trips(sleeve_id, rule_id)

        # Bench engages on this tick (sleeve still SHADOW)...
        with tick_patches(
            account_cash_usd=_post_churn_cash(net_loss), closes_by_symbol={"SPY": 500.0}
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)
        # ...then drift pauses the sleeve.
        database.update_sleeve_status(sleeve_id, "PAUSED_RECONCILIATION")

        panel = app_module._build_sleeves_panel_context()
        entry = next(s for s in panel if s["id"] == sleeve_id)
        badge_map = app_module._SLEEVE_STATUS_BADGE_CLASSES
        assert entry["status_badge_class"] == badge_map["PAUSED_RECONCILIATION"], (
            f"the pill must show the reconciliation pause "
            f"({badge_map['PAUSED_RECONCILIATION']!r}) even while a rule is "
            f"benched; got {entry['status_badge_class']!r} — "
            f"PAUSED_RECONCILIATION outranks BENCHED in the ratified "
            f"precedence"
        )

    def test_bench_outranks_the_calm_paper_base_pill(self, client):
        """A PAPER-armed sleeve whose entry rule just got benched must show
        the BENCHED pill, not the calm 'armed' base pill — the operator is
        owed the at-a-glance signal that the brake engaged."""
        sleeve_id = create_sleeve_via_route(client, capital_usd=_CAPITAL_USD)
        rule_id = create_rule_via_route(client, sleeve_id, valid_route_rule_payload())
        database.insert_sleeve_rule_fire(
            rule_id=rule_id,
            sleeve_id=sleeve_id,
            action="buy",
            rule_class="ENTRY",
            mode_at_fire="SHADOW",
        )
        resp = client.post(
            f"/api/sleeves/{sleeve_id}/rules/{rule_id}/arm", json={"target_mode": "PAPER"}
        )
        assert resp.status_code == 200, "fixture sanity: the arm ceremony must succeed"
        assert database.get_sleeve(sleeve_id)["status"] == "PAPER"

        net_loss = _seed_same_day_losing_round_trips(sleeve_id, rule_id)
        with tick_patches(
            account_cash_usd=_post_churn_cash(net_loss),
            closes_by_symbol={"SPY": 500.0},
            submit_bracket_result=alpaca_orders.OrderResult(order=None, error="HTTP 500"),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=MARKET_OPEN_NOW_UTC)

        panel = app_module._build_sleeves_panel_context()
        entry = next(s for s in panel if s["id"] == sleeve_id)
        badge_map = app_module._SLEEVE_STATUS_BADGE_CLASSES
        assert entry["status_badge_class"] == badge_map["BENCHED"], (
            f"a PAPER sleeve with a benched rule must surface the BENCHED "
            f"pill ({badge_map['BENCHED']!r}); got "
            f"{entry['status_badge_class']!r} — BENCHED outranks the calm "
            f"base status in the ratified precedence"
        )
