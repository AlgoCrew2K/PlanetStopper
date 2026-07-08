"""
RED tests -- Managed Sleeves epic-done-bar gap (task #33).

The PM's live paper-account smoke (2026-07-08) found that
run_sleeve_tick_for_all_sleeves passes closes_by_symbol={} and fred_cache={}
to sleeves.rules.runner.evaluate_rules (sleeves/tick_orchestrator.py, the two
hardcoded empty-dict literals just before the runner.evaluate_rules(...)
call). With no real closes, sleeves.rules.runner._evaluate_one_rule's own
`price = closes[-1] if closes else 0.0` always yields price=0.0, so
sleeves.sizing.size_order refuses every entry with error="invalid_price" --
an armed PAPER sleeve can genuinely never place a trade. This is the
epic-done-bar blocker, not a code-cycle regression (the P3 cycle itself was
independently reviewed/approved at f8ca848/0c365ff).

CONTRACT this file specifies for the GREEN implementer (s3-engine):

    sleeves/tick_orchestrator.py's run_sleeve_tick_for_all_sleeves must, once
    per tick (never once per sleeve):
      - Determine every symbol referenced by an ENABLED rule across ALL
        sleeves being ticked this cycle (_load_enabled_rules already exists
        and correctly filters by the `enabled` column per sleeve -- this is
        purely an aggregation-across-sleeves + real-fetch concern, not a new
        filtering concern).
      - Fetch daily closes for that symbol set via the SAME daily-bar path
        P1/P2 already use for history: synthetic_history.fetch_bars. Do not
        invent a second HTTP client or a new Alpaca-bars wrapper. Called
        EXACTLY once for the whole tick (mirrors the aggregate-reconciliation
        get_account/get_positions once-per-tick precedent already in this
        module) -- never once per sleeve, even when multiple sleeves
        reference the same or different symbols.
      - Thread the resulting {symbol: [close, ...]} into every sleeve's own
        evaluate_rules(closes_by_symbol=...) call, replacing the current
        hardcoded {} literal.
      - A symbol with NO bars available (fetch_bars omitted it, returned an
        empty list for it, or the whole fetch call raised) must fail SAFE via
        the EXISTING empty-closes contract already implemented in
        sleeves.rules.senses/conditions (unavailable sense -> not fireable,
        no fire recorded) -- never crash the tick, never invent/backfill a
        synthetic price.
      - Populate fred_cache from database.get_latest_market_lens_cache()'s
        cached bundle -- no live FRED call from the tick (D-1/cache-only,
        matching ai_advisor.py's own cache-serve precedent). This file only
        pins that the tick actually CONSULTS that cache accessor; it does
        NOT pin the internal {series_id: [observation, ...]} mapping shape,
        since the exact lens-bundle-to-fred_cache translation is an
        implementation detail for s3-engine to work out against the real
        MARKET_LENS_CACHE payload shape (tests/database/
        test_market_lens_cache_accessor.py), not something this file can
        responsibly dictate sight-unseen.

    The exact internal helper name(s)/shape used to assemble the per-symbol
    closes dict, or to convert fetch_bars' {"c": ...}-keyed bar dicts into
    plain close floats, are NOT pinned here (implementation detail --
    advisors/lens_technicals.py's own `[bar["c"] for bar in bar_list]`
    pattern is available for reuse but not mandated by name). Only the
    observable contract is pinned: synthetic_history.fetch_bars is the sole
    daily-bar source, called once per tick, and evaluate_rules receives real,
    non-empty data for a referenced symbol when bars are available for it.

Zero live network: synthetic_history.fetch_bars hits requests.get directly
and is ALWAYS mocked here (house rule). alpaca_orders.submit_bracket_order/
get_account/get_positions are likewise always mocked -- this file is a
sleeve-tick unit/integration surface, not a live-broker surface.

Fixture-derivation rule: the sma_20-crossing close series reused here
(indicator_fixture's mixed_series) is the SAME golden fixture P2's own
sleeves/rules/runner.py tests already use and already proved sma_20 > 100.0
against (tests/sleeves/rules/test_runner_shadow_and_precedence.py) --
re-deriving a fresh series here would just be re-asserting arithmetic this
suite already owns; reusing it keeps this file scoped to the WIRING gap.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip(
    "sleeves.tick_orchestrator", reason="RED phase -- sleeves.tick_orchestrator not implemented yet"
)

import database  # noqa: E402
import sleeves.tick_orchestrator as tick_orchestrator  # noqa: E402
import synthetic_history  # noqa: E402
from sleeves import alpaca_orders  # noqa: E402
from tests.sleeves.rules.conftest import make_rule_doc  # noqa: E402

_NOW_UTC = datetime(2026, 7, 8, 14, 31, 0, tzinfo=UTC)  # Wednesday, 10:31 ET -- market open

_ENVELOPE_JSON = json.dumps(
    {
        "allowlist": ["SPY"],
        "max_position_pct": 0.9,
        "max_order_usd": 50_000.0,
        "max_daily_turnover_usd": 100_000.0,
        "long_only": True,
    }
)

_INDICATOR_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "math"
    / "sleeves_rule_indicator_senses_basic.json"
)


def _rising_closes() -> list[float]:
    """The exact 20-close series tests/sleeves/rules/test_runner_shadow_and_precedence.py
    already proved sma_20 > 100.0 against -- ends high (113.50), a genuine
    non-degenerate SMA crossing, not a hand-tuned value invented for this file."""
    fixture = json.loads(_INDICATOR_FIXTURE_PATH.read_text(encoding="utf-8"))
    return fixture["mixed_series"]["closes"]


def _bars_payload(symbol: str, closes: list[float]) -> dict:
    """synthetic_history.fetch_bars' own return shape: {symbol: [bar, ...]}
    where each bar is an Alpaca-style dict keyed "c" for close (matches
    advisors/lens_technicals.py's own `bar["c"]` extraction convention)."""
    return {symbol: [{"c": c} for c in closes]}


def _make_matching_account(cash_usd: float) -> dict:
    """A clean (non-breaching) broker-truth double -- avoids the aggregate
    reconciliation step pausing the sleeve for reasons unrelated to what
    this file actually tests (never live network; house rule)."""
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


def _seed_armed_entry_sleeve(
    *, capital_usd: float = 10_000.0, symbol: str = "SPY", mode: str = "PAPER"
) -> tuple[int, int]:
    """One sleeve, armed to `mode`, with exactly one ENABLED entry rule
    referencing `symbol` (default risk_pct buy + sma_20 > 100.0 condition,
    the same default make_rule_doc's own P2-proven shape). Returns
    (sleeve_id, rule_id)."""
    sleeve_id = database.create_sleeve(
        f"bars-wiring-test-{uuid.uuid4().hex}", capital_usd, envelope_json=_ENVELOPE_JSON
    )
    database.update_sleeve_status(sleeve_id, mode)
    doc = make_rule_doc(sleeve_id=sleeve_id, when={"symbol": symbol}, mode=mode)
    rule_id = database.create_sleeve_rule(
        sleeve_id, f"bars-wiring-rule-{uuid.uuid4().hex}", json_doc=json.dumps(doc), mode=mode
    )
    return sleeve_id, rule_id


# ---------------------------------------------------------------------------
# 1. A referenced symbol gets real, non-empty closes + a >0 price
# ---------------------------------------------------------------------------


class TestReferencedSymbolGetsRealBars:
    def test_symbol_referenced_by_an_enabled_rule_gets_nonempty_closes_and_positive_price(self):
        sleeve_id, _rule_id = _seed_armed_entry_sleeve()
        closes = _rising_closes()

        captured_kwargs: dict = {}

        def _capture_evaluate_rules(**kwargs):
            captured_kwargs.update(kwargs)
            return []

        with (
            patch.object(
                synthetic_history, "fetch_bars", return_value=_bars_payload("SPY", closes)
            ) as mock_fetch_bars,
            patch.object(
                tick_orchestrator.runner, "evaluate_rules", side_effect=_capture_evaluate_rules
            ),
            patch.object(
                alpaca_orders,
                "get_account",
                return_value=alpaca_orders.OrderResult(
                    order=_make_matching_account(10_000.0), error=None
                ),
            ),
            patch.object(
                alpaca_orders,
                "get_positions",
                return_value=alpaca_orders.OrderResult(order=[], error=None),
            ),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)

        assert mock_fetch_bars.called, (
            "run_sleeve_tick_for_all_sleeves must call synthetic_history.fetch_bars "
            "at least once when an enabled rule references a symbol -- currently "
            "closes_by_symbol is a hardcoded {} literal and fetch_bars is never called."
        )
        assert "closes_by_symbol" in captured_kwargs, (
            "evaluate_rules must be called with a closes_by_symbol kwarg"
        )
        received_closes = captured_kwargs["closes_by_symbol"].get("SPY", [])
        assert received_closes, (
            f"evaluate_rules received an empty closes list for SPY "
            f"({captured_kwargs['closes_by_symbol']!r}) despite fetch_bars being "
            f"mocked to return {len(closes)} real closes for it -- the tick is "
            f"not threading fetched bars through to the rule engine."
        )
        assert received_closes[-1] > 0, (
            f"the most recent close for SPY must be a real positive price, got "
            f"{received_closes[-1]!r} -- a >0 price is what allows sizing.size_order "
            f"to proceed past its own 'price <= 0' -> invalid_price refusal."
        )

    def test_a_symbol_with_no_bars_available_fails_safe_not_crash(self):
        """fetch_bars omitting the symbol entirely (broker had nothing to
        return, or the requested symbol was simply absent from the response)
        must degrade to the EXISTING empty-closes fail-safe contract senses.py
        already implements -- never propagate an exception out of the tick,
        never fabricate a synthetic price to keep the rule 'working'."""
        sleeve_id, rule_id = _seed_armed_entry_sleeve(symbol="SPY")

        with (
            patch.object(synthetic_history, "fetch_bars", return_value={}),
            patch.object(alpaca_orders, "submit_bracket_order") as mock_submit,
            patch.object(
                alpaca_orders,
                "get_account",
                return_value=alpaca_orders.OrderResult(
                    order=_make_matching_account(10_000.0), error=None
                ),
            ),
            patch.object(
                alpaca_orders,
                "get_positions",
                return_value=alpaca_orders.OrderResult(order=[], error=None),
            ),
        ):
            # Must not raise.
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)

        assert not mock_submit.called, (
            "with zero bars available for the only referenced symbol, no order "
            "may reach the broker -- the missing-data case must fail safe "
            "(not-fireable), not silently invent a price and submit anyway."
        )
        assert database.get_sleeve_rule_fires(rule_id=rule_id) == [], (
            "a rule that could not sense its condition (no bars available for "
            "its symbol) must record NO fire row this tick -- matches the "
            "existing fail-safe contract for every other unavailable sense."
        )
        # Sleeve status must be untouched -- a data-unavailable tick is not a
        # reconciliation breach and must never itself pause the sleeve.
        assert database.get_sleeve(sleeve_id)["status"] == "PAPER"


# ---------------------------------------------------------------------------
# 2. An indicator sense genuinely evaluates on the real fetched bars
# ---------------------------------------------------------------------------


class TestIndicatorSenseEvaluatesOnRealBars:
    def test_sma_20_condition_fires_true_against_real_fetched_closes(self):
        """Does NOT mock evaluate_rules itself -- the real P2 rule engine
        (already independently reviewed/approved) must run against bars the
        TICK fetched, proving the wiring is genuinely end-to-end rather than
        just threading an empty-but-technically-present dict through."""
        sleeve_id, rule_id = _seed_armed_entry_sleeve()
        closes = _rising_closes()

        with (
            patch.object(
                synthetic_history, "fetch_bars", return_value=_bars_payload("SPY", closes)
            ),
            patch.object(
                alpaca_orders,
                "submit_bracket_order",
                return_value=alpaca_orders.OrderResult(
                    order={"id": "bars-wiring-order-1"}, error=None
                ),
            ),
            patch.object(
                alpaca_orders,
                "get_account",
                return_value=alpaca_orders.OrderResult(
                    order=_make_matching_account(10_000.0), error=None
                ),
            ),
            patch.object(
                alpaca_orders,
                "get_positions",
                return_value=alpaca_orders.OrderResult(order=[], error=None),
            ),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)

        fires = database.get_sleeve_rule_fires(rule_id=rule_id)
        assert fires, (
            "the rule's sma_20 > 100.0 condition must fire TRUE against the real "
            "fetched closes (mixed_series ends at 113.50, a genuine non-degenerate "
            "crossing already proven by tests/sleeves/rules/"
            "test_runner_shadow_and_precedence.py) -- zero fire rows means the "
            "indicator sense never actually saw the fetched bars."
        )


# ---------------------------------------------------------------------------
# 3. An armed entry rule reaches the broker layer -- no invalid_price refusal
# ---------------------------------------------------------------------------


class TestArmedEntryNoLongerRefusesInvalidPrice:
    def test_armed_paper_entry_rule_submits_a_bracket_order_not_invalid_price_refusal(self):
        """This is the EXACT live failure the PM's paper smoke reproduced:
        rule fires (shadow=False) but sizing.size_order refuses with
        error='invalid_price' because price was 0.0. With real bars wired
        in, the same armed rule must reach alpaca_orders.submit_bracket_order
        (mocked -- zero live network) instead of being refused at sizing."""
        sleeve_id, rule_id = _seed_armed_entry_sleeve()
        closes = _rising_closes()

        with (
            patch.object(
                synthetic_history, "fetch_bars", return_value=_bars_payload("SPY", closes)
            ),
            patch.object(
                alpaca_orders,
                "submit_bracket_order",
                return_value=alpaca_orders.OrderResult(
                    order={"id": "bars-wiring-order-2"}, error=None
                ),
            ) as mock_submit_bracket,
            patch.object(
                alpaca_orders,
                "get_account",
                return_value=alpaca_orders.OrderResult(
                    order=_make_matching_account(10_000.0), error=None
                ),
            ),
            patch.object(
                alpaca_orders,
                "get_positions",
                return_value=alpaca_orders.OrderResult(order=[], error=None),
            ),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)

        assert mock_submit_bracket.called, (
            "an armed (PAPER-mode) entry rule whose condition fires true must "
            "reach sleeves.alpaca_orders.submit_bracket_order -- with real bars "
            "wired in, sizing must no longer refuse with error='invalid_price' "
            "(the exact live failure the PM's paper-account smoke reproduced)."
        )

        fires = database.get_sleeve_rule_fires(rule_id=rule_id)
        assert fires, "the fire must be recorded"
        assert fires[0]["order_id"] is not None, (
            "an armed (non-SHADOW) fire that actually reached the broker must "
            "link a real sleeve_orders.id via order_id -- None here would mean "
            "the action was refused (e.g. still invalid_price) rather than "
            "genuinely dispatched."
        )
        placed_orders = {row["id"]: row for row in database.get_sleeve_orders(sleeve_id=sleeve_id)}
        placed_order = placed_orders.get(fires[0]["order_id"])
        assert placed_order is not None, (
            f"fires[0]['order_id']={fires[0]['order_id']!r} does not correspond to any "
            f"real sleeve_orders row for sleeve {sleeve_id} -- the fire claims an order "
            f"was placed but no such row exists."
        )
        assert placed_order["status"] != "invalid_price", (
            "the placed order's status must never literally be the sizing "
            "refusal reason -- if this fails, the order was never really placed."
        )


# ---------------------------------------------------------------------------
# 4. Bars are fetched ONCE per tick, never once per sleeve
# ---------------------------------------------------------------------------


class TestBarsFetchedOncePerTickNotPerSleeve:
    def test_multiple_sleeves_referencing_symbols_trigger_exactly_one_fetch_bars_call(self):
        """Mirrors this module's own precedent for the aggregate-reconciliation
        get_account/get_positions calls (test_tick_orchestrator.py's
        test_broker_account_and_positions_are_fetched_once_per_tick_not_per_sleeve):
        fetching bars per-sleeve was the same class of bug as the original
        per-sleeve reconciliation defect -- wasteful and, at scale, a real
        rate-limit risk against the daily-bar endpoint."""
        closes = _rising_closes()
        sleeve_ids = [
            _seed_armed_entry_sleeve(capital_usd=5_000.0, symbol=sym)[0]
            for sym in ("SPY", "SPY", "SPY")  # 3 sleeves, SAME symbol -- still one fetch
        ]

        with (
            patch.object(
                synthetic_history, "fetch_bars", return_value=_bars_payload("SPY", closes)
            ) as mock_fetch_bars,
            patch.object(
                alpaca_orders,
                "submit_bracket_order",
                return_value=alpaca_orders.OrderResult(
                    order={"id": "bars-wiring-order-3"}, error=None
                ),
            ),
            patch.object(
                alpaca_orders,
                "get_account",
                return_value=alpaca_orders.OrderResult(
                    order=_make_matching_account(sum(5_000.0 for _ in sleeve_ids)), error=None
                ),
            ),
            patch.object(
                alpaca_orders,
                "get_positions",
                return_value=alpaca_orders.OrderResult(order=[], error=None),
            ),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)

        assert mock_fetch_bars.call_count == 1, (
            f"synthetic_history.fetch_bars must be called exactly ONCE per tick "
            f"for the whole symbol set across all {len(sleeve_ids)} sleeves, not "
            f"once per sleeve; got {mock_fetch_bars.call_count} calls"
        )


# ---------------------------------------------------------------------------
# 5. fred_cache is actually wired from the cached lens store, not hardcoded {}
# ---------------------------------------------------------------------------


class TestFredCacheReadFromLensStore:
    def test_tick_consults_the_latest_market_lens_cache_when_a_rule_is_evaluated(self):
        """Lighter pin than the bars coverage above: this file does not
        dictate the internal fred_cache {series_id: [...]} shape (that
        mapping is s3-engine's implementation call against the real
        MARKET_LENS_CACHE payload) -- it only proves the tick actually reads
        from the cache-only accessor (database.get_latest_market_lens_cache,
        the SAME accessor ai_advisor.py's own cache-serve path already uses)
        rather than leaving fred_cache permanently hardcoded to {}."""
        sleeve_id, rule_id = _seed_armed_entry_sleeve()
        closes = _rising_closes()

        with (
            patch.object(
                synthetic_history, "fetch_bars", return_value=_bars_payload("SPY", closes)
            ),
            patch.object(
                database, "get_latest_market_lens_cache", return_value=None
            ) as mock_get_lens_cache,
            patch.object(
                alpaca_orders,
                "submit_bracket_order",
                return_value=alpaca_orders.OrderResult(
                    order={"id": "bars-wiring-order-4"}, error=None
                ),
            ),
            patch.object(
                alpaca_orders,
                "get_account",
                return_value=alpaca_orders.OrderResult(
                    order=_make_matching_account(10_000.0), error=None
                ),
            ),
            patch.object(
                alpaca_orders,
                "get_positions",
                return_value=alpaca_orders.OrderResult(order=[], error=None),
            ),
        ):
            tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=_NOW_UTC)

        assert mock_get_lens_cache.called, (
            "run_sleeve_tick_for_all_sleeves must consult "
            "database.get_latest_market_lens_cache() to populate fred_cache -- "
            "currently fred_cache is a hardcoded {} literal and this cache "
            "accessor is never called from the tick at all. No live FRED call "
            "is required or permitted here (cache-only, D-1)."
        )
