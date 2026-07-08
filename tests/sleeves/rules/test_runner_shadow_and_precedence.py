"""
RED tests — sleeves/rules/runner.py: the P2 tick orchestrator (AC-6, AC-10, AC-20).

CONTRACT this file specifies for the GREEN implementer (s2-rules-impl):

    sleeves/rules/runner.py

    @dataclass(frozen=True)
    class FireOutcome:
        rule_id: int
        rule_class: str                          # "DEFENSIVE" | "ENTRY"
        fired: bool
        reason: str | None                        # not-fireable reason (a
                                                     # conditions.py sense_unavailable
                                                     # reason, or a limits.py pacing
                                                     # reason) when fired=False
        sensed_snapshot: dict                      # {sense_key: value-or-None} --
                                                     # market/portfolio values ONLY
                                                     # (D-1: never env/credentials)
        action_results: tuple["actions.ActionResult", ...]
        fire_ids: tuple[int, ...]                  # sleeve_rule_fires.id per
                                                     # action; empty when not fired

    def evaluate_rules(
        *, rules: list[dict], sleeve_row: dict, sleeve_equity_usd: float,
        now_utc: datetime, closes_by_symbol: dict[str, list[float]],
        positions: dict[str, float], fred_cache: dict[str, list[dict]],
        envelope: dict, live_mode: bool = False, live_keys_present: bool = False,
        discord_webhook_url: str | None = None,
        turnover_used_by_symbol: dict[str, float] | None = None,
    ) -> list[FireOutcome]: ...

Each item of `rules` is the DB row's `id`/`mode` merged with the parsed,
schema-valid json_doc fields (see conftest.make_stored_rule) — `mode` is the
sleeve_rules.mode DB column value (current arming state), the runtime source
of truth this function trusts as given.

MARKET-HOURS GATING IS DERIVED INTERNALLY, NEVER CALLER-SUPPLIED (the plan's
own "never hours-only, Jul-3 lesson" decision): evaluate_rules converts
now_utc to America/New_York and calls market_calendar.get_market_state(now_et)
itself — market_open = (state == "open"). There is deliberately NO
`market_open` parameter on this function; a caller cannot pass a wrong/naive
weekday-only computation because there's nowhere to pass it.

SAME-TICK PRECEDENCE (AC-10 spirit, AC-20): rules are partitioned by
structurally-derived class (schema.derive_rule_class over each rule's `then`
action types) into DEFENSIVE and ENTRY groups. ALL DEFENSIVE-class rules are
evaluated and dispatched (in ascending rule `id` order) BEFORE any ENTRY-class
rule, regardless of the input list's order or rule ids straddling both groups.

SHADOW purity (AC-6): for a rule whose `mode` is "SHADOW", the dispatched
actions execute nothing (actions.dispatch_action(..., shadow=True)) and
`sleeves.alpaca_orders` is NEVER called, but the fire — including the
envelope-clamped "would-have-ordered" snapshot — IS still recorded via
`database.insert_sleeve_rule_fire` for every action.

Fail-safe (AC-4): when conditions.evaluate_condition reports an unavailable
sense anywhere in a rule's `if` tree, OR limits.check_and_advance_pacing
reports not-fireable, the rule's FireOutcome has fired=False and NO
sleeve_rule_fires row is written for it — there is nothing to record.
"""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip(
    "sleeves.rules.runner", reason="RED phase — sleeves.rules.runner not implemented yet"
)

import database  # noqa: E402
import market_calendar  # noqa: E402
import sleeves.alpaca_orders as alpaca_orders  # noqa: E402
import sleeves.rules.runner as runner  # noqa: E402

from .conftest import ET, make_stored_rule  # noqa: E402

_ENVELOPE = {
    "allowlist": ["SPY"],
    "max_position_pct": 0.9,
    "max_order_usd": 50_000.0,
    "max_daily_turnover_usd": 100_000.0,
    "long_only": True,
}


def _base_kwargs(**overrides):
    base = dict(
        rules=[],
        sleeve_row={"id": 1, "status": "SHADOW"},
        sleeve_equity_usd=10_000.0,
        now_utc=datetime(2026, 7, 8, 15, 0, 0, tzinfo=ZoneInfo("UTC")),  # 11:00 ET, a Wednesday
        closes_by_symbol={},
        positions={},
        fred_cache={},
        envelope=_ENVELOPE,
    )
    base.update(overrides)
    return base


def _rising_closes(fixture) -> list[float]:
    return fixture["mixed_series"]["closes"]  # ends high, so sma_20 > 100.0 is TRUE


# ---------------------------------------------------------------------------
# 1. Same-tick precedence — DEFENSIVE entirely before ENTRY, ascending id within
# ---------------------------------------------------------------------------


class TestPrecedenceOrdering:
    def test_defensive_rules_processed_before_all_entry_rules_regardless_of_input_order(
        self, indicator_fixture
    ):
        closes = _rising_closes(indicator_fixture)
        entry_high_id = make_stored_rule(
            id=5,
            mode="SHADOW",
            then=[
                {
                    "type": "buy",
                    "sizing": {"mode": "shares", "shares": 1},
                    "stop_loss_pct": 0.05,
                }
            ],
        )
        entry_low_id = make_stored_rule(
            id=2,
            mode="SHADOW",
            then=[
                {
                    "type": "buy",
                    "sizing": {"mode": "shares", "shares": 1},
                    "stop_loss_pct": 0.05,
                }
            ],
        )
        defensive_rule = make_stored_rule(
            id=10,
            mode="SHADOW",
            then=[{"type": "sell", "sizing": {"mode": "shares", "shares": 1}}],
        )
        # Deliberately scrambled input order.
        rules = [entry_high_id, defensive_rule, entry_low_id]

        outcomes = runner.evaluate_rules(
            **_base_kwargs(
                rules=rules,
                closes_by_symbol={"SPY": closes},
                positions={"SPY": 10.0},
            )
        )
        fired_ids_in_order = [o.rule_id for o in outcomes if o.fired]
        assert fired_ids_in_order == [10, 2, 5], (
            f"expected defensive(10) before entry(2) before entry(5), got {fired_ids_in_order}"
        )
        assert next(o.rule_class for o in outcomes if o.rule_id == 10) == "DEFENSIVE"
        assert next(o.rule_class for o in outcomes if o.rule_id == 2) == "ENTRY"


# ---------------------------------------------------------------------------
# 2. SHADOW purity, end-to-end through the real database accessors
# ---------------------------------------------------------------------------


class TestShadowPurityEndToEnd:
    def test_shadow_entry_rule_fires_records_would_have_order_and_calls_no_broker_endpoint(
        self, indicator_fixture
    ):
        closes = _rising_closes(indicator_fixture)
        rule = make_stored_rule(
            id=101,
            mode="SHADOW",
            then=[
                {
                    "type": "buy",
                    "sizing": {"mode": "shares", "shares": 2},
                    "stop_loss_pct": 0.05,
                }
            ],
        )
        with (
            patch.object(alpaca_orders, "submit_bracket_order") as mock_bracket,
            patch.object(alpaca_orders, "submit_order") as mock_order,
            patch.object(alpaca_orders, "submit_trailing_stop_order") as mock_trail,
        ):
            outcomes = runner.evaluate_rules(
                **_base_kwargs(
                    rules=[rule], closes_by_symbol={"SPY": closes}, positions={"SPY": 0.0}
                )
            )
        mock_bracket.assert_not_called()
        mock_order.assert_not_called()
        mock_trail.assert_not_called()

        assert len(outcomes) == 1
        outcome = outcomes[0]
        assert outcome.fired is True
        assert outcome.action_results[0].executed is False
        assert outcome.action_results[0].would_have_qty is not None
        assert outcome.action_results[0].would_have_qty > 0
        assert len(outcome.fire_ids) == 1

        # The fire row genuinely landed in the DB (not just an in-memory result).
        rows = database.get_sleeve_rule_fires(rule_id=101)
        assert len(rows) == 1
        assert rows[0]["mode_at_fire"] == "SHADOW"
        assert rows[0]["order_id"] is None, "a SHADOW fire must never carry a real order_id"


# ---------------------------------------------------------------------------
# 3. Fail-safe — not-fireable ticks write NOTHING to sleeve_rule_fires
# ---------------------------------------------------------------------------


class TestFailSafeWritesNoFireRow:
    def test_insufficient_history_sense_is_not_fireable_and_writes_no_fire_row(
        self, indicator_fixture
    ):
        short_closes = indicator_fixture["insufficient_series"]["closes"]
        rule = make_stored_rule(
            id=202,
            mode="SHADOW",
            condition={"op": "compare", "sense": "sma_20", "comparator": ">", "value": 100.0},
        )
        outcomes = runner.evaluate_rules(
            **_base_kwargs(rules=[rule], closes_by_symbol={"SPY": short_closes}, positions={})
        )
        assert len(outcomes) == 1
        assert outcomes[0].fired is False
        assert outcomes[0].reason is not None
        assert database.get_sleeve_rule_fires(rule_id=202) == []


# ---------------------------------------------------------------------------
# 4. Armed path — reachable end-to-end through evaluate_rules, not just actions.py directly
# ---------------------------------------------------------------------------


class TestArmedPathReachableEndToEnd:
    def test_paper_mode_rule_reaches_alpaca_orders_through_the_clamp(self, indicator_fixture):
        closes = _rising_closes(indicator_fixture)
        rule = make_stored_rule(
            id=303,
            mode="PAPER",
            then=[{"type": "sell", "sizing": {"mode": "shares", "shares": 3}}],
        )
        with patch.object(
            alpaca_orders,
            "submit_order",
            return_value=alpaca_orders.OrderResult(order={"id": "paper-order-1"}, error=None),
        ) as mock_order:
            outcomes = runner.evaluate_rules(
                **_base_kwargs(
                    rules=[rule], closes_by_symbol={"SPY": closes}, positions={"SPY": 10.0}
                )
            )
        assert outcomes[0].fired is True
        assert mock_order.called, (
            "a PAPER-mode rule's armed action must reach sleeves.alpaca_orders.submit_order "
            "(P2 builds this capability; nothing in production calls evaluate_rules in "
            "non-SHADOW mode yet — that production wiring is P3 — but the module itself "
            "must support it structurally)."
        )
        assert outcomes[0].action_results[0].executed is True


# ---------------------------------------------------------------------------
# 5. Market-hours + NYSE-holiday gating — derived internally, real calendar
# ---------------------------------------------------------------------------


class TestMarketHoursAndHolidayGating:
    def test_evaluate_rules_imports_market_calendar_not_a_homegrown_weekday_check(self):
        import ast
        import inspect

        source = inspect.getsource(runner)
        tree = ast.parse(source)
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
        assert "market_calendar" in imported_modules, (
            "sleeves/rules/runner.py must derive market-open state via market_calendar "
            "(XNYS holiday-aware), never a hand-rolled weekday check — see the plan's "
            "'never hours-only, Jul-3 lesson' decision."
        )

    def test_a_real_nyse_observed_holiday_blocks_every_rule_even_on_a_weekday(
        self, indicator_fixture
    ):
        # 2026-07-04 (Independence Day) falls on a Saturday; NYSE observed the
        # holiday on Friday 2026-07-03 instead — a Friday, which any naive
        # "is it a weekday" check would treat as open. market_calendar (via
        # pandas_market_calendars) correctly reports it closed.
        holiday_et = datetime(2026, 7, 3, 15, 0, 0, tzinfo=ET)  # 11:00 ET
        assert market_calendar.is_trading_day(holiday_et.date()) is False, (
            "test precondition: 2026-07-03 must be the real NYSE-observed holiday "
            "(sanity-check against the live market_calendar module, not asserted blind)."
        )
        closes = _rising_closes(indicator_fixture)
        rule = make_stored_rule(id=404, mode="SHADOW")
        outcomes = runner.evaluate_rules(
            **_base_kwargs(
                rules=[rule],
                now_utc=holiday_et.astimezone(ZoneInfo("UTC")),
                closes_by_symbol={"SPY": closes},
                positions={},
            )
        )
        assert outcomes[0].fired is False
        assert "market_closed" in (outcomes[0].reason or ""), (
            f"expected a market_closed reason on an observed NYSE holiday, got {outcomes[0].reason!r}"
        )
        assert database.get_sleeve_rule_fires(rule_id=404) == []


# ---------------------------------------------------------------------------
# 6. Sensed-snapshot safety — market/portfolio values only, never secrets
# ---------------------------------------------------------------------------


class TestSensedSnapshotContainsNoSecrets:
    def test_sensed_snapshot_never_leaks_an_environment_credential(
        self, indicator_fixture, monkeypatch
    ):
        monkeypatch.setenv("ALPACA_SECRET", "totally-fake-test-secret-value-12345")
        closes = _rising_closes(indicator_fixture)
        rule = make_stored_rule(id=505, mode="SHADOW")
        outcomes = runner.evaluate_rules(
            **_base_kwargs(rules=[rule], closes_by_symbol={"SPY": closes}, positions={})
        )
        snapshot_repr = repr(outcomes[0].sensed_snapshot)
        assert "totally-fake-test-secret-value-12345" not in snapshot_repr
        assert "ALPACA_SECRET" not in snapshot_repr
