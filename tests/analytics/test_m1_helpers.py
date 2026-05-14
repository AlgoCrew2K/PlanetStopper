"""
RED-phase tests for analytics.py — M1 data-layer helpers.

Covers:
  - get_portfolio_today_change(symphonies) -> {"if_held": float, "dry_run": float}
  - get_portfolio_cumulative_return(symphonies) -> {"if_held": float, "dry_run": float}
  - get_portfolio_max_drawdown(symphonies) -> {"if_held": float, "dry_run": float}
  - get_symphony_today_change(sym_dict, bot_state_entry) -> {"if_held": float, "dry_run": float}
  - get_symphony_cumulative_return(sym_dict, bot_state_entry) -> {"if_held": float, "dry_run": float}
  - get_symphony_max_drawdown(sym_dict, bot_state_entry) -> {"if_held": float, "dry_run": float}

Data-source contract (from team-lead mandate):
  If-held side:
    - today_change  <- last_percent_change (present on all symphonies)
    - CR (if-held)  <- simple_return UNLESS (simple_return==0.0 AND net_deposits==0.0)
                       then fall back to time_weighted_return  [TWR fallback]
    - MDD (if-held) <- max_drawdown (Composer convention: positive float in [0,1])

  Dry-run (shadow) side:
    - For non-triggered symphonies: dry_run == if_held (AlphaBot did nothing)
    - For triggered symphonies: dry_run comes from bot_state_entry
      - today_change  <- bot_state_entry["current_return"] (already *100 from engine)
      - CR dry_run    <- Not stored in bot_state per recon; falls back to if_held
      - MDD dry_run   <- Not stored in bot_state per recon; falls back to if_held

  Portfolio-level aggregates are value-weighted across all symphonies in the
  Composer response; portfolio dry_run is value-weighted across per-symphony dry_run.

All fixtures used here are read from the pre-placed captured-from-producer fixture
at tests/fixtures/composer/symphony_stats_meta.json (11 real symphonies, 160KB).
No network calls are made in this module — fetch_symphony_stats is not invoked.

Live contract test is in test_live_m1_helpers.py.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "composer"
_SYMPHONY_STATS_META_FIXTURE = _FIXTURE_DIR / "symphony_stats_meta.json"


@pytest.fixture(scope="module")
def symphony_stats_meta():
    """Load the pre-placed captured-from-producer fixture (11 symphonies)."""
    with open(_SYMPHONY_STATS_META_FIXTURE, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload.get("symphonies", [])


@pytest.fixture(scope="module")
def first_symphony(symphony_stats_meta):
    """
    The first symphony in the fixture — the TWR-fallback case:
      simple_return == 0.0, net_deposits == 0.0, time_weighted_return == 3.13212
    """
    return symphony_stats_meta[0]


@pytest.fixture(scope="module")
def normal_symphony(symphony_stats_meta):
    """
    A normal symphony (index 1) with simple_return != 0 and net_deposits != 0.
    simple_return=0.65976, net_deposits=658.5, last_percent_change=-0.0229, max_drawdown=0.1495
    """
    return symphony_stats_meta[1]


# ---------------------------------------------------------------------------
# Helper: minimal bot_state entry for a triggered symphony
# ---------------------------------------------------------------------------

def _triggered_bot_state_entry(current_return: float = -5.0) -> dict:
    """Minimal triggered bot_state entry from the engine's persisted shape."""
    return {
        "triggered": True,
        "current_return": current_return,   # engine stores pct already *100
        "high_water_mark": 10.0,
        "shadow_hwm": 12.0,
    }


def _untriggered_bot_state_entry(current_return: float = 2.0) -> dict:
    return {
        "triggered": False,
        "current_return": current_return,
        "high_water_mark": 3.0,
        "shadow_hwm": 3.0,
    }


# ---------------------------------------------------------------------------
# TIER 1 — Per-symphony helpers: get_symphony_today_change
# ---------------------------------------------------------------------------

class TestGetSymphonyTodayChange:

    def test_if_held_equals_last_percent_change_times_100(self, normal_symphony):
        """
        if_held today's change must be last_percent_change * 100.
        Composer returns last_percent_change as a decimal (e.g. -0.0229 = -2.29%).
        """
        from analytics import get_symphony_today_change

        result = get_symphony_today_change(normal_symphony, bot_state_entry=None)

        assert isinstance(result, dict), (
            f"get_symphony_today_change must return a dict; got {type(result)}"
        )
        assert "if_held" in result, "result must have 'if_held' key"
        assert "dry_run" in result, "result must have 'dry_run' key"

        expected_if_held = normal_symphony["last_percent_change"] * 100
        assert result["if_held"] == pytest.approx(expected_if_held, abs=1e-9), (
            f"if_held must be last_percent_change*100; "
            f"expected {expected_if_held}, got {result['if_held']}"
        )

    def test_dry_run_equals_if_held_when_not_triggered(self, normal_symphony):
        """
        Non-triggered symphony: AlphaBot did nothing. dry_run == if_held.
        """
        from analytics import get_symphony_today_change

        result = get_symphony_today_change(
            normal_symphony,
            bot_state_entry=_untriggered_bot_state_entry(),
        )

        assert result["dry_run"] == pytest.approx(result["if_held"], abs=1e-9), (
            f"non-triggered symphony: dry_run must equal if_held; "
            f"if_held={result['if_held']}, dry_run={result['dry_run']}"
        )

    def test_dry_run_equals_if_held_when_no_bot_state(self, normal_symphony):
        """
        No bot_state entry at all (symphony not in bot_state dict): dry_run == if_held.
        """
        from analytics import get_symphony_today_change

        result = get_symphony_today_change(normal_symphony, bot_state_entry=None)

        assert result["dry_run"] == pytest.approx(result["if_held"], abs=1e-9), (
            f"absent bot_state entry: dry_run must equal if_held; "
            f"if_held={result['if_held']}, dry_run={result['dry_run']}"
        )

    def test_dry_run_comes_from_bot_state_current_return_when_triggered(self, normal_symphony):
        """
        Triggered symphony: dry_run today's change comes from bot_state_entry["current_return"].
        The engine stores current_return already multiplied by 100, so no conversion needed.
        """
        from analytics import get_symphony_today_change

        triggered_entry = _triggered_bot_state_entry(current_return=-7.5)
        result = get_symphony_today_change(normal_symphony, bot_state_entry=triggered_entry)

        assert result["dry_run"] == pytest.approx(-7.5, abs=1e-9), (
            f"triggered symphony: dry_run must equal bot_state current_return (-7.5); "
            f"got {result['dry_run']}"
        )
        # if_held is still from Composer, not bot_state
        expected_if_held = normal_symphony["last_percent_change"] * 100
        assert result["if_held"] == pytest.approx(expected_if_held, abs=1e-9), (
            f"triggered symphony: if_held must still derive from last_percent_change; "
            f"got {result['if_held']}"
        )

    def test_dry_run_and_if_held_are_finite_floats(self, symphony_stats_meta):
        """All 11 fixture symphonies must produce finite-float results."""
        from analytics import get_symphony_today_change

        for sym in symphony_stats_meta:
            result = get_symphony_today_change(sym, bot_state_entry=None)
            for key in ("if_held", "dry_run"):
                v = result[key]
                assert isinstance(v, float), (
                    f"symphony {sym.get('id')}: {key} must be float; got {type(v)}"
                )
                assert math.isfinite(v), (
                    f"symphony {sym.get('id')}: {key} must be finite; got {v}"
                )


# ---------------------------------------------------------------------------
# TIER 1 — Per-symphony helpers: get_symphony_cumulative_return
# ---------------------------------------------------------------------------

class TestGetSymphonyCumulativeReturn:

    def test_twr_fallback_when_simple_return_zero_and_net_deposits_zero(self, first_symphony):
        """
        TWR fallback contract: when simple_return==0.0 AND net_deposits==0.0,
        if_held CR must equal time_weighted_return from the fixture.

        Fixture anchor (captured-from-producer):
          first symphony: simple_return=0.0, net_deposits=0.0, time_weighted_return=3.13212
        """
        from analytics import get_symphony_cumulative_return

        assert first_symphony["simple_return"] == 0.0, (
            "fixture assumption violated: first_symphony.simple_return must be 0.0"
        )
        assert first_symphony["net_deposits"] == 0.0, (
            "fixture assumption violated: first_symphony.net_deposits must be 0.0"
        )

        result = get_symphony_cumulative_return(first_symphony, bot_state_entry=None)

        assert isinstance(result, dict), (
            f"get_symphony_cumulative_return must return a dict; got {type(result)}"
        )
        assert "if_held" in result and "dry_run" in result, (
            f"result must have 'if_held' and 'dry_run'; got {list(result.keys())}"
        )

        expected_twr = first_symphony["time_weighted_return"]  # 3.13212
        assert result["if_held"] == pytest.approx(expected_twr, abs=1e-9), (
            f"TWR fallback: if_held CR must equal time_weighted_return={expected_twr}; "
            f"got {result['if_held']} — check fallback branch for simple_return==0, net_deposits==0"
        )

    def test_normal_symphony_uses_simple_return(self, normal_symphony):
        """
        Normal case: simple_return != 0, so if_held CR must equal simple_return.
        Fixture: normal_symphony.simple_return = 0.65976
        """
        from analytics import get_symphony_cumulative_return

        assert normal_symphony["simple_return"] != 0.0, (
            "fixture assumption violated: normal_symphony.simple_return must be non-zero"
        )
        assert normal_symphony["net_deposits"] != 0.0, (
            "fixture assumption violated: normal_symphony.net_deposits must be non-zero"
        )

        result = get_symphony_cumulative_return(normal_symphony, bot_state_entry=None)

        expected = normal_symphony["simple_return"]
        assert result["if_held"] == pytest.approx(expected, abs=1e-9), (
            f"normal CR: if_held must equal simple_return={expected}; "
            f"got {result['if_held']}"
        )

    def test_dry_run_equals_if_held_when_not_triggered(self, normal_symphony):
        """Non-triggered: dry_run CR == if_held (no divergence)."""
        from analytics import get_symphony_cumulative_return

        result = get_symphony_cumulative_return(
            normal_symphony,
            bot_state_entry=_untriggered_bot_state_entry(),
        )

        assert result["dry_run"] == pytest.approx(result["if_held"], abs=1e-9), (
            f"non-triggered: dry_run CR must equal if_held; "
            f"if_held={result['if_held']}, dry_run={result['dry_run']}"
        )

    def test_dry_run_equals_if_held_when_no_bot_state(self, normal_symphony):
        """No bot_state entry: dry_run CR == if_held."""
        from analytics import get_symphony_cumulative_return

        result = get_symphony_cumulative_return(normal_symphony, bot_state_entry=None)

        assert result["dry_run"] == pytest.approx(result["if_held"], abs=1e-9), (
            f"absent bot_state: dry_run CR must equal if_held; "
            f"if_held={result['if_held']}, dry_run={result['dry_run']}"
        )

    def test_dry_run_equals_if_held_when_triggered_no_cr_in_bot_state(self, normal_symphony):
        """
        Triggered, but bot_state does not store CR (recon confirmed).
        dry_run CR falls back to if_held.
        """
        from analytics import get_symphony_cumulative_return

        triggered = _triggered_bot_state_entry()
        result = get_symphony_cumulative_return(normal_symphony, bot_state_entry=triggered)

        assert result["dry_run"] == pytest.approx(result["if_held"], abs=1e-9), (
            f"triggered, no CR in bot_state: dry_run must fall back to if_held; "
            f"if_held={result['if_held']}, dry_run={result['dry_run']}"
        )

    def test_twr_fallback_dry_run_also_uses_twr(self, first_symphony):
        """
        TWR fallback symphony, non-triggered: both if_held and dry_run use TWR.
        """
        from analytics import get_symphony_cumulative_return

        result = get_symphony_cumulative_return(first_symphony, bot_state_entry=None)

        expected_twr = first_symphony["time_weighted_return"]
        assert result["dry_run"] == pytest.approx(expected_twr, abs=1e-9), (
            f"TWR fallback symphony: dry_run CR must also equal TWR={expected_twr}; "
            f"got {result['dry_run']}"
        )

    def test_all_fixture_symphonies_produce_finite_results(self, symphony_stats_meta):
        """All 11 fixture symphonies must produce finite-float CR results."""
        from analytics import get_symphony_cumulative_return

        for sym in symphony_stats_meta:
            result = get_symphony_cumulative_return(sym, bot_state_entry=None)
            for key in ("if_held", "dry_run"):
                v = result[key]
                assert isinstance(v, float), (
                    f"symphony {sym.get('id')}: CR {key} must be float; got {type(v)}"
                )
                assert math.isfinite(v), (
                    f"symphony {sym.get('id')}: CR {key} must be finite; got {v}"
                )


# ---------------------------------------------------------------------------
# TIER 1 — Per-symphony helpers: get_symphony_max_drawdown
# ---------------------------------------------------------------------------

class TestGetSymphonyMaxDrawdown:

    def test_if_held_equals_max_drawdown_from_composer(self, normal_symphony):
        """
        if_held MDD must equal max_drawdown from Composer (positive float, convention: [0,1]).
        Fixture: normal_symphony.max_drawdown = 0.1495
        """
        from analytics import get_symphony_max_drawdown

        result = get_symphony_max_drawdown(normal_symphony, bot_state_entry=None)

        assert isinstance(result, dict), (
            f"get_symphony_max_drawdown must return a dict; got {type(result)}"
        )
        assert "if_held" in result and "dry_run" in result, (
            f"result must have 'if_held' and 'dry_run'; got {list(result.keys())}"
        )

        expected = normal_symphony["max_drawdown"]
        assert result["if_held"] == pytest.approx(expected, abs=1e-9), (
            f"if_held MDD must equal Composer max_drawdown={expected}; "
            f"got {result['if_held']}"
        )

    def test_if_held_mdd_is_non_negative(self, symphony_stats_meta):
        """
        MDD is a non-negative float (Composer convention: 0.0 = no drawdown,
        positive value = drawdown magnitude). Must not be negative.
        """
        from analytics import get_symphony_max_drawdown

        for sym in symphony_stats_meta:
            result = get_symphony_max_drawdown(sym, bot_state_entry=None)
            assert result["if_held"] >= 0.0, (
                f"symphony {sym.get('id')}: if_held MDD must be >= 0; "
                f"got {result['if_held']} — check sign convention"
            )

    def test_dry_run_equals_if_held_when_not_triggered(self, normal_symphony):
        """Non-triggered: dry_run MDD == if_held."""
        from analytics import get_symphony_max_drawdown

        result = get_symphony_max_drawdown(
            normal_symphony,
            bot_state_entry=_untriggered_bot_state_entry(),
        )

        assert result["dry_run"] == pytest.approx(result["if_held"], abs=1e-9), (
            f"non-triggered: dry_run MDD must equal if_held; "
            f"if_held={result['if_held']}, dry_run={result['dry_run']}"
        )

    def test_dry_run_equals_if_held_when_triggered_no_mdd_in_bot_state(self, normal_symphony):
        """
        Triggered, but bot_state does not store MDD (recon confirmed).
        dry_run MDD falls back to if_held.
        """
        from analytics import get_symphony_max_drawdown

        triggered = _triggered_bot_state_entry()
        result = get_symphony_max_drawdown(normal_symphony, bot_state_entry=triggered)

        assert result["dry_run"] == pytest.approx(result["if_held"], abs=1e-9), (
            f"triggered, no MDD in bot_state: dry_run must fall back to if_held; "
            f"if_held={result['if_held']}, dry_run={result['dry_run']}"
        )

    def test_all_fixture_symphonies_produce_finite_results(self, symphony_stats_meta):
        """All 11 fixture symphonies must produce finite-float MDD results."""
        from analytics import get_symphony_max_drawdown

        for sym in symphony_stats_meta:
            result = get_symphony_max_drawdown(sym, bot_state_entry=None)
            for key in ("if_held", "dry_run"):
                v = result[key]
                assert isinstance(v, float), (
                    f"symphony {sym.get('id')}: MDD {key} must be float; got {type(v)}"
                )
                assert math.isfinite(v), (
                    f"symphony {sym.get('id')}: MDD {key} must be finite; got {v}"
                )


# ---------------------------------------------------------------------------
# TIER 1 — Portfolio-level helpers: get_portfolio_today_change
# ---------------------------------------------------------------------------

class TestGetPortfolioTodayChange:

    def test_returns_dict_with_if_held_and_dry_run(self, symphony_stats_meta):
        """Portfolio today-change must return a dict with 'if_held' and 'dry_run'."""
        from analytics import get_portfolio_today_change

        result = get_portfolio_today_change(symphony_stats_meta, bot_state={})

        assert isinstance(result, dict), (
            f"get_portfolio_today_change must return a dict; got {type(result)}"
        )
        assert "if_held" in result, "result must have 'if_held' key"
        assert "dry_run" in result, "result must have 'dry_run' key"

    def test_if_held_is_value_weighted_average_of_last_percent_change(self, symphony_stats_meta):
        """
        Portfolio if_held today-change must be value-weighted mean of
        (symphony.last_percent_change * 100) across all symphonies.

        Weight is symphony["value"] (current portfolio value).
        """
        from analytics import get_portfolio_today_change

        result = get_portfolio_today_change(symphony_stats_meta, bot_state={})

        # Compute expected value-weighted avg from the fixture
        total_weight = 0.0
        weighted_sum = 0.0
        for sym in symphony_stats_meta:
            w = float(sym.get("value", 0.0))
            if w > 0:
                weighted_sum += sym["last_percent_change"] * 100.0 * w
                total_weight += w

        if total_weight > 0:
            expected = weighted_sum / total_weight
            assert result["if_held"] == pytest.approx(expected, abs=1e-6), (
                f"portfolio if_held today-change must be value-weighted avg; "
                f"expected {expected:.6f}, got {result['if_held']:.6f}"
            )

    def test_if_held_and_dry_run_are_finite_floats(self, symphony_stats_meta):
        """Both portfolio metrics must be finite floats."""
        from analytics import get_portfolio_today_change

        result = get_portfolio_today_change(symphony_stats_meta, bot_state={})

        for key in ("if_held", "dry_run"):
            v = result[key]
            assert isinstance(v, float), (
                f"portfolio today-change {key} must be float; got {type(v)}"
            )
            assert math.isfinite(v), (
                f"portfolio today-change {key} must be finite; got {v}"
            )

    def test_empty_symphonies_returns_zeros(self):
        """Empty input must not raise; returns 0.0 for both sides."""
        from analytics import get_portfolio_today_change

        result = get_portfolio_today_change([], bot_state={})

        assert result["if_held"] == pytest.approx(0.0, abs=1e-9), (
            f"empty symphonies: if_held must be 0.0; got {result['if_held']}"
        )
        assert result["dry_run"] == pytest.approx(0.0, abs=1e-9), (
            f"empty symphonies: dry_run must be 0.0; got {result['dry_run']}"
        )

    def test_dry_run_equals_if_held_when_no_triggered_symphonies(self, symphony_stats_meta):
        """
        With no triggered symphonies in bot_state, portfolio dry_run == if_held.
        """
        from analytics import get_portfolio_today_change

        # All symphonies untriggered
        bot_state = {
            sym["id"]: _untriggered_bot_state_entry() for sym in symphony_stats_meta
        }
        result = get_portfolio_today_change(symphony_stats_meta, bot_state=bot_state)

        assert result["dry_run"] == pytest.approx(result["if_held"], abs=1e-6), (
            f"all-untriggered: portfolio dry_run must equal if_held; "
            f"if_held={result['if_held']}, dry_run={result['dry_run']}"
        )

    def test_triggered_symphony_shifts_dry_run_portfolio(self, symphony_stats_meta):
        """
        When one symphony is triggered with a very different current_return,
        the portfolio dry_run must differ from if_held.

        We force the first symphony's dry_run to +100% (current_return=100.0)
        while its if_held (last_percent_change*100) is small and negative.
        The portfolio dry_run must shift toward the triggered value.
        """
        from analytics import get_portfolio_today_change

        triggered_symphony = symphony_stats_meta[0]
        triggered_id = triggered_symphony["id"]

        bot_state = {
            triggered_id: {
                "triggered": True,
                "current_return": 100.0,  # wildly different from if_held
            }
        }
        result = get_portfolio_today_change(symphony_stats_meta, bot_state=bot_state)

        # dry_run must differ from if_held due to the triggered symphony's contribution
        assert result["dry_run"] != pytest.approx(result["if_held"], abs=0.001), (
            f"portfolio dry_run must reflect triggered symphony's current_return; "
            f"dry_run={result['dry_run']} should differ from if_held={result['if_held']}"
        )


# ---------------------------------------------------------------------------
# TIER 1 — Portfolio-level helpers: get_portfolio_cumulative_return
# ---------------------------------------------------------------------------

class TestGetPortfolioCumulativeReturn:

    def test_returns_dict_with_if_held_and_dry_run(self, symphony_stats_meta):
        """Portfolio CR must return a dict with 'if_held' and 'dry_run'."""
        from analytics import get_portfolio_cumulative_return

        result = get_portfolio_cumulative_return(symphony_stats_meta, bot_state={})

        assert isinstance(result, dict), (
            f"get_portfolio_cumulative_return must return a dict; got {type(result)}"
        )
        assert "if_held" in result, "result must have 'if_held' key"
        assert "dry_run" in result, "result must have 'dry_run' key"

    def test_twr_fallback_influences_portfolio_cr(self, symphony_stats_meta, first_symphony):
        """
        The first symphony uses TWR fallback (simple_return==0, net_deposits==0).
        Portfolio if_held CR must incorporate TWR for that symphony, not 0.0.

        Verify: portfolio CR != value-weighted of simple_returns (naive case that ignores fallback).
        """
        from analytics import get_portfolio_cumulative_return

        result = get_portfolio_cumulative_return(symphony_stats_meta, bot_state={})

        # Naive wrong calculation that ignores the TWR fallback:
        total_weight = 0.0
        naive_weighted_sum = 0.0
        for sym in symphony_stats_meta:
            w = float(sym.get("value", 0.0))
            if w > 0:
                naive_weighted_sum += sym["simple_return"] * w  # wrong for sym[0]
                total_weight += w

        naive_cr = naive_weighted_sum / total_weight if total_weight > 0 else 0.0

        # The first symphony's simple_return is 0.0 but TWR is 3.13212.
        # These are materially different — the portfolio CR must not use 0.0 for sym[0].
        twr = first_symphony["time_weighted_return"]
        w0 = float(first_symphony.get("value", 0.0))
        if w0 > 0 and total_weight > 0:
            # The correct if_held must account for TWR on sym[0]
            # rather than 0.0 (simple_return). The difference is twr * w0 / total_weight.
            expected_correction = (twr - 0.0) * w0 / total_weight
            if abs(expected_correction) > 0.0001:
                assert result["if_held"] != pytest.approx(naive_cr, abs=0.0001), (
                    f"portfolio CR must use TWR fallback for first symphony; "
                    f"naive (wrong) CR={naive_cr:.6f}, got {result['if_held']:.6f}"
                )

    def test_if_held_and_dry_run_are_finite_floats(self, symphony_stats_meta):
        """Portfolio CR must be finite floats."""
        from analytics import get_portfolio_cumulative_return

        result = get_portfolio_cumulative_return(symphony_stats_meta, bot_state={})

        for key in ("if_held", "dry_run"):
            v = result[key]
            assert isinstance(v, float), (
                f"portfolio CR {key} must be float; got {type(v)}"
            )
            assert math.isfinite(v), (
                f"portfolio CR {key} must be finite; got {v}"
            )

    def test_empty_symphonies_returns_zeros(self):
        """Empty input must not raise; returns 0.0 for both sides."""
        from analytics import get_portfolio_cumulative_return

        result = get_portfolio_cumulative_return([], bot_state={})

        assert result["if_held"] == pytest.approx(0.0, abs=1e-9)
        assert result["dry_run"] == pytest.approx(0.0, abs=1e-9)

    def test_dry_run_equals_if_held_when_no_triggered_symphonies(self, symphony_stats_meta):
        """All-untriggered: portfolio CR dry_run == if_held."""
        from analytics import get_portfolio_cumulative_return

        bot_state = {
            sym["id"]: _untriggered_bot_state_entry() for sym in symphony_stats_meta
        }
        result = get_portfolio_cumulative_return(symphony_stats_meta, bot_state=bot_state)

        assert result["dry_run"] == pytest.approx(result["if_held"], abs=1e-6), (
            f"all-untriggered: portfolio dry_run CR must equal if_held; "
            f"if_held={result['if_held']}, dry_run={result['dry_run']}"
        )


# ---------------------------------------------------------------------------
# TIER 1 — Portfolio-level helpers: get_portfolio_max_drawdown
# ---------------------------------------------------------------------------

class TestGetPortfolioMaxDrawdown:

    def test_returns_dict_with_if_held_and_dry_run(self, symphony_stats_meta):
        """Portfolio MDD must return a dict with 'if_held' and 'dry_run'."""
        from analytics import get_portfolio_max_drawdown

        result = get_portfolio_max_drawdown(symphony_stats_meta, bot_state={})

        assert isinstance(result, dict), (
            f"get_portfolio_max_drawdown must return a dict; got {type(result)}"
        )
        assert "if_held" in result, "result must have 'if_held' key"
        assert "dry_run" in result, "result must have 'dry_run' key"

    def test_if_held_is_value_weighted_average_of_max_drawdown(self, symphony_stats_meta):
        """
        Portfolio if_held MDD must be value-weighted mean of symphony.max_drawdown.
        Composer max_drawdown is a positive float (convention: magnitude, not signed).
        """
        from analytics import get_portfolio_max_drawdown

        result = get_portfolio_max_drawdown(symphony_stats_meta, bot_state={})

        total_weight = 0.0
        weighted_sum = 0.0
        for sym in symphony_stats_meta:
            w = float(sym.get("value", 0.0))
            if w > 0:
                weighted_sum += sym["max_drawdown"] * w
                total_weight += w

        if total_weight > 0:
            expected = weighted_sum / total_weight
            assert result["if_held"] == pytest.approx(expected, abs=1e-6), (
                f"portfolio if_held MDD must be value-weighted avg; "
                f"expected {expected:.6f}, got {result['if_held']:.6f}"
            )

    def test_if_held_mdd_is_non_negative(self, symphony_stats_meta):
        """Portfolio if_held MDD must be non-negative (Composer convention)."""
        from analytics import get_portfolio_max_drawdown

        result = get_portfolio_max_drawdown(symphony_stats_meta, bot_state={})

        assert result["if_held"] >= 0.0, (
            f"portfolio if_held MDD must be >= 0 (Composer positive convention); "
            f"got {result['if_held']}"
        )

    def test_if_held_and_dry_run_are_finite_floats(self, symphony_stats_meta):
        """Portfolio MDD must be finite floats."""
        from analytics import get_portfolio_max_drawdown

        result = get_portfolio_max_drawdown(symphony_stats_meta, bot_state={})

        for key in ("if_held", "dry_run"):
            v = result[key]
            assert isinstance(v, float), (
                f"portfolio MDD {key} must be float; got {type(v)}"
            )
            assert math.isfinite(v), (
                f"portfolio MDD {key} must be finite; got {v}"
            )

    def test_empty_symphonies_returns_zeros(self):
        """Empty input must not raise; returns 0.0 for both sides."""
        from analytics import get_portfolio_max_drawdown

        result = get_portfolio_max_drawdown([], bot_state={})

        assert result["if_held"] == pytest.approx(0.0, abs=1e-9)
        assert result["dry_run"] == pytest.approx(0.0, abs=1e-9)

    def test_dry_run_equals_if_held_when_no_triggered_symphonies(self, symphony_stats_meta):
        """All-untriggered: portfolio MDD dry_run == if_held."""
        from analytics import get_portfolio_max_drawdown

        bot_state = {
            sym["id"]: _untriggered_bot_state_entry() for sym in symphony_stats_meta
        }
        result = get_portfolio_max_drawdown(symphony_stats_meta, bot_state=bot_state)

        assert result["dry_run"] == pytest.approx(result["if_held"], abs=1e-6), (
            f"all-untriggered: portfolio dry_run MDD must equal if_held; "
            f"if_held={result['if_held']}, dry_run={result['dry_run']}"
        )


# ---------------------------------------------------------------------------
# TIER 1 — Property invariant: TWR fallback only triggers on BOTH conditions
# ---------------------------------------------------------------------------

class TestTwrFallbackConditions:

    def test_fallback_not_triggered_when_only_net_deposits_is_zero(self):
        """
        If simple_return != 0.0 but net_deposits == 0.0 — NOT a fallback case.
        Must use simple_return, not time_weighted_return.
        """
        from analytics import get_symphony_cumulative_return

        sym = {
            "id": "test-sym",
            "simple_return": 0.5,       # non-zero
            "net_deposits": 0.0,        # zero (only one condition)
            "time_weighted_return": 99.0,  # sentinel — must NOT be used
            "last_percent_change": 0.01,
            "max_drawdown": 0.1,
            "value": 1000.0,
        }
        result = get_symphony_cumulative_return(sym, bot_state_entry=None)

        assert result["if_held"] == pytest.approx(0.5, abs=1e-9), (
            f"simple_return=0.5 with net_deposits=0 must use simple_return, not TWR; "
            f"got {result['if_held']} (TWR sentinel=99.0 would indicate fallback triggered)"
        )

    def test_fallback_not_triggered_when_only_simple_return_is_zero(self):
        """
        If simple_return == 0.0 but net_deposits != 0.0 — NOT a fallback case.
        Must use simple_return (0.0), not time_weighted_return.
        """
        from analytics import get_symphony_cumulative_return

        sym = {
            "id": "test-sym",
            "simple_return": 0.0,       # zero
            "net_deposits": 500.0,      # non-zero (only one condition)
            "time_weighted_return": 99.0,  # sentinel — must NOT be used
            "last_percent_change": 0.01,
            "max_drawdown": 0.1,
            "value": 1000.0,
        }
        result = get_symphony_cumulative_return(sym, bot_state_entry=None)

        assert result["if_held"] == pytest.approx(0.0, abs=1e-9), (
            f"simple_return=0.0 with net_deposits=500 must use simple_return (0.0), not TWR; "
            f"got {result['if_held']} (TWR sentinel=99.0 would indicate fallback triggered)"
        )

    def test_fallback_triggered_when_both_conditions_met(self):
        """
        Both simple_return==0.0 AND net_deposits==0.0: must use time_weighted_return.
        """
        from analytics import get_symphony_cumulative_return

        sym = {
            "id": "test-sym",
            "simple_return": 0.0,
            "net_deposits": 0.0,
            "time_weighted_return": 3.13212,
            "last_percent_change": -0.02,
            "max_drawdown": 0.25,
            "value": 1000.0,
        }
        result = get_symphony_cumulative_return(sym, bot_state_entry=None)

        assert result["if_held"] == pytest.approx(3.13212, abs=1e-9), (
            f"both conditions met: must use time_weighted_return=3.13212; "
            f"got {result['if_held']}"
        )


# ---------------------------------------------------------------------------
# TIER 1 — Missing-field contract (reviewer advisory, encoded as RED/GREEN pin)
#
# Current implementation uses bare dict key access (sym_dict["field"]).
# These tests document that contract: a missing required field raises KeyError.
# The live contract test (test_live_m1_helpers.py) is the drift guard.
# If a future implementer changes to graceful degradation, these tests must be
# updated deliberately — they must not silently pass on both behaviors.
# ---------------------------------------------------------------------------

class TestMissingFieldContract:

    def test_today_change_raises_on_missing_last_percent_change(self):
        """
        get_symphony_today_change raises KeyError when last_percent_change is absent.
        Documents the current bare-key-access contract — not silent degradation.
        """
        from analytics import get_symphony_today_change

        sym = {
            "id": "test-sym",
            # last_percent_change intentionally omitted
            "simple_return": 0.1,
            "net_deposits": 100.0,
            "time_weighted_return": 0.1,
            "max_drawdown": 0.05,
            "value": 1000.0,
        }
        with pytest.raises(KeyError):
            get_symphony_today_change(sym, bot_state_entry=None)

    def test_cumulative_return_raises_on_missing_simple_return(self):
        """
        get_symphony_cumulative_return raises KeyError when simple_return is absent.
        """
        from analytics import get_symphony_cumulative_return

        sym = {
            "id": "test-sym",
            "last_percent_change": -0.01,
            # simple_return intentionally omitted
            "net_deposits": 100.0,
            "time_weighted_return": 0.1,
            "max_drawdown": 0.05,
            "value": 1000.0,
        }
        with pytest.raises(KeyError):
            get_symphony_cumulative_return(sym, bot_state_entry=None)

    def test_cumulative_return_raises_on_missing_net_deposits(self):
        """
        get_symphony_cumulative_return raises KeyError when net_deposits is absent.
        The TWR-fallback branch reads net_deposits unconditionally.
        """
        from analytics import get_symphony_cumulative_return

        sym = {
            "id": "test-sym",
            "last_percent_change": -0.01,
            "simple_return": 0.1,
            # net_deposits intentionally omitted
            "time_weighted_return": 0.1,
            "max_drawdown": 0.05,
            "value": 1000.0,
        }
        with pytest.raises(KeyError):
            get_symphony_cumulative_return(sym, bot_state_entry=None)

    def test_cumulative_return_raises_on_missing_twr_when_fallback_needed(self):
        """
        get_symphony_cumulative_return raises KeyError when time_weighted_return is
        absent AND the TWR fallback is triggered (simple_return==0, net_deposits==0).
        """
        from analytics import get_symphony_cumulative_return

        sym = {
            "id": "test-sym",
            "last_percent_change": -0.01,
            "simple_return": 0.0,
            "net_deposits": 0.0,
            # time_weighted_return intentionally omitted — fallback path would need it
            "max_drawdown": 0.05,
            "value": 1000.0,
        }
        with pytest.raises(KeyError):
            get_symphony_cumulative_return(sym, bot_state_entry=None)

    def test_max_drawdown_raises_on_missing_max_drawdown(self):
        """
        get_symphony_max_drawdown raises KeyError when max_drawdown is absent.
        """
        from analytics import get_symphony_max_drawdown

        sym = {
            "id": "test-sym",
            "last_percent_change": -0.01,
            "simple_return": 0.1,
            "net_deposits": 100.0,
            "time_weighted_return": 0.1,
            # max_drawdown intentionally omitted
            "value": 1000.0,
        }
        with pytest.raises(KeyError):
            get_symphony_max_drawdown(sym, bot_state_entry=None)

    def test_portfolio_skips_symphony_missing_value_field(self):
        """
        _value_weighted_portfolio skips symphonies where 'value' key is absent.
        A symphony list with one valid and one missing-value entry must still
        return a result derived from the valid symphony only.
        """
        from analytics import get_portfolio_today_change

        symphonies = [
            {
                "id": "sym-good",
                "last_percent_change": 0.02,
                "simple_return": 0.1,
                "net_deposits": 100.0,
                "time_weighted_return": 0.1,
                "max_drawdown": 0.05,
                "value": 1000.0,
            },
            {
                "id": "sym-no-value",
                "last_percent_change": 0.99,   # sentinel — must not contribute
                "simple_return": 0.9,
                "net_deposits": 100.0,
                "time_weighted_return": 0.9,
                "max_drawdown": 0.5,
                # "value" key intentionally absent
            },
        ]
        result = get_portfolio_today_change(symphonies, bot_state={})

        # Only sym-good contributes — its last_percent_change*100 = 2.0
        assert result["if_held"] == pytest.approx(2.0, abs=1e-9), (
            f"symphony missing 'value' must be skipped by portfolio kernel; "
            f"expected 2.0 (sym-good only), got {result['if_held']}"
        )
