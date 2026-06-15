"""
RED tests — F4 TWR-outlier exclusion from portfolio aggregate.

AUDIT FINDING (DATA-CORRECTNESS-AUDIT F4 / CA-3, MEDIUM):
  n2ooAZTvBR has simple_return=0, net_deposits=0 -> time_weighted_return*100 = 318%.
  analytics.py:763-770 (now ~:789) uses TWR as the if_held fallback for zero-deposit
  symphonies. At ~12.6% weight, this 318% figure lifts the VW portfolio "Bot" from
  ~26.9% to ~67.1% — an outlier domination artefact, not a real guard signal.

THE CONTRACT THESE TESTS PIN (the fix):
  A symphony that uses the TWR fallback (simple_return == 0 AND net_deposits == 0)
  MUST be EXCLUDED from the portfolio VW aggregate (get_portfolio_cumulative_return).

  The per-symphony card value (get_symphony_cumulative_return.if_held) is UNCHANGED
  — the TWR fallback remains correct for per-card display. Only the portfolio-level
  aggregation must exclude these entries.

  Invariant: with one outlier TWR symphony (TWR >> normal) plus N normal symphonies,
  the portfolio CR must be within the normal symphonies' range — not pulled toward
  the outlier.

  Implementation hint: get_symphony_cumulative_return should mark TWR-fallback
  entries so _value_weighted_portfolio can identify and skip them from the
  portfolio aggregate. A "_twr_fallback": True key in the return dict is one way;
  the implementer decides the mechanism so long as the portfolio aggregate excludes
  the outlier and the per-symphony value is preserved.

Fixtures: inline — no live API calls.
"""

from __future__ import annotations

import pytest

import analytics


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _normal_sym(sym_id: str, value: float, simple_return: float) -> dict:
    """A normal symphony with a standard simple_return."""
    return {
        "id": sym_id,
        "value": value,
        "last_percent_change": simple_return / 100.0,
        "simple_return": simple_return / 100.0,
        "net_deposits": value * 0.8,  # non-zero => simple_return path
        "time_weighted_return": simple_return / 100.0,
        "max_drawdown": 0.05,
        "trading_day": "2026-06-04",
    }


def _twr_fallback_sym(sym_id: str, value: float, twr: float) -> dict:
    """A zero-deposit symphony that uses the TWR fallback.

    Replicates n2ooAZTvBR: simple_return=0.0, net_deposits=0.0.
    """
    return {
        "id": sym_id,
        "value": value,
        "last_percent_change": 0.0,
        "simple_return": 0.0,
        "net_deposits": 0.0,
        "time_weighted_return": twr,  # e.g. 3.18 => 318%
        "max_drawdown": 0.05,
        "trading_day": "2026-06-04",
    }


# ---------------------------------------------------------------------------
# TestPerSymphonyTWRFallbackPreserved
# ---------------------------------------------------------------------------


class TestPerSymphonyTWRFallbackPreserved:
    """Per-symphony TWR fallback must be PRESERVED — only portfolio aggregate changes."""

    def test_twr_fallback_if_held_is_twr_times_100(self):
        """
        get_symphony_cumulative_return for a zero-deposit symphony must still
        return if_held = time_weighted_return * 100 (per-card value unchanged).
        """
        sym = _twr_fallback_sym("n2ooA", 1609.36, 3.18)
        result = analytics.get_symphony_cumulative_return(sym, None)

        assert result["if_held"] is not None
        assert result["if_held"] == pytest.approx(3.18 * 100.0, rel=1e-6), (
            "Per-symphony if_held for TWR-fallback must still be TWR * 100. "
            f"Got {result['if_held']}. Per-card display must be unaffected by the fix."
        )

    def test_normal_symphony_if_held_is_simple_return_times_100(self):
        """
        Normal symphony (net_deposits > 0) must use simple_return * 100, unchanged.
        """
        sym = _normal_sym("sym-A", 1000.0, 45.0)
        result = analytics.get_symphony_cumulative_return(sym, None)

        assert result["if_held"] == pytest.approx(45.0, rel=1e-6), (
            "Normal symphony if_held must equal simple_return * 100. "
            f"Expected 45.0, got {result['if_held']}."
        )


# ---------------------------------------------------------------------------
# TestTWROutlierExcludedFromPortfolioAggregate
# ---------------------------------------------------------------------------


class TestTWROutlierExcludedFromPortfolioAggregate:
    """The TWR-fallback outlier must NOT dominate the portfolio CR aggregate."""

    def _three_normal_syms(self) -> list[dict]:
        return [
            _normal_sym("sym-A", 2000.0, 30.0),
            _normal_sym("sym-B", 2000.0, 35.0),
            _normal_sym("sym-C", 2000.0, 40.0),
        ]

    def _bot_state(self, syms: list[dict]) -> dict:
        return {s["id"]: s for s in syms}

    def test_outlier_does_not_dominate_portfolio_cr(self):
        """
        With 3 normal symphonies (~30-40% CR) plus one TWR-fallback outlier (318%),
        the portfolio CR must be within the normal symphonies' range — not pulled
        toward 318%.

        Pre-fix: the outlier at ~25% weight lifts the aggregate above 100%.
        Post-fix: the outlier is excluded; aggregate stays within normal range.
        """
        normal_syms = self._three_normal_syms()
        outlier = _twr_fallback_sym("n2ooA", 2000.0, 3.18)  # 318%, equal weight
        all_syms = normal_syms + [outlier]
        bot_state = self._bot_state(all_syms)

        from unittest.mock import patch

        with patch("analytics._get_shadow_divergence_trajectory", return_value=None):
            result = analytics.get_portfolio_cumulative_return(all_syms, bot_state)

        assert result["if_held"] is not None, (
            "Portfolio CR must not be None when normal symphonies are present."
        )

        # Normal symphonies are 30%, 35%, 40% — equal weight => portfolio ~35%.
        # The outlier at 318% must not pull the result above the normal range.
        normal_max = 40.0
        normal_min = 30.0
        assert result["if_held"] <= normal_max + 1.0, (
            f"Portfolio CR ({result['if_held']:.2f}%) must not be dominated by the "
            f"318% TWR outlier — must stay within normal range (~{normal_min}–{normal_max}%). "
            f"The TWR-fallback symphony must be excluded from the portfolio VW aggregate."
        )
        assert result["if_held"] >= normal_min - 1.0, (
            f"Portfolio CR ({result['if_held']:.2f}%) must be at least as large as "
            f"the minimum normal symphony ({normal_min}%)."
        )

    def test_portfolio_cr_without_outlier_equals_portfolio_cr_with_excluded_outlier(self):
        """
        Computing portfolio CR with only the normal symphonies must give the same
        result as computing with the outlier included (but excluded from aggregate).

        This is the strong invariant: excluding the TWR-fallback from the aggregate
        is equivalent to not having it in the portfolio at all.
        """
        normal_syms = self._three_normal_syms()
        outlier = _twr_fallback_sym("n2ooA", 2000.0, 3.18)
        bot_state_no_outlier = self._bot_state(normal_syms)
        bot_state_with_outlier = self._bot_state(normal_syms + [outlier])

        from unittest.mock import patch

        with patch("analytics._get_shadow_divergence_trajectory", return_value=None):
            result_without = analytics.get_portfolio_cumulative_return(
                normal_syms, bot_state_no_outlier
            )
            result_with = analytics.get_portfolio_cumulative_return(
                normal_syms + [outlier], bot_state_with_outlier
            )

        assert result_without["if_held"] is not None
        assert result_with["if_held"] is not None

        assert result_with["if_held"] == pytest.approx(result_without["if_held"], rel=1e-4), (
            f"Portfolio CR WITH outlier excluded ({result_with['if_held']:.4f}%) must equal "
            f"portfolio CR WITHOUT outlier ({result_without['if_held']:.4f}%). "
            f"The TWR-fallback symphony must contribute zero weight to the VW aggregate."
        )

    def test_twr_fallback_is_zero_weight_in_portfolio(self):
        """
        A portfolio of ONLY the TWR-fallback symphony must return if_held=None
        (empty aggregate after exclusion) — confirming zero-weight contribution.
        """
        outlier_only = [_twr_fallback_sym("n2ooA", 2000.0, 3.18)]
        bot_state = self._bot_state(outlier_only)

        from unittest.mock import patch

        with patch("analytics._get_shadow_divergence_trajectory", return_value=None):
            result = analytics.get_portfolio_cumulative_return(outlier_only, bot_state)

        # With none_on_empty=True (the CR path), excluded outlier => empty aggregate => None
        assert result["if_held"] is None, (
            f"Portfolio CR with ONLY a TWR-fallback symphony must return if_held=None "
            f"(none_on_empty=True path: no contributing symphonies). "
            f"Got {result['if_held']}. The outlier must contribute zero weight."
        )

    def test_n2ooa_shape_fixture_dominated_pre_fix(self):
        """
        Regression pin: with the n2ooAZTvBR-shaped outlier at its live weight
        (~12.6% of portfolio) alongside 10 normal symphonies (~10% each, ~30% CR),
        the pre-fix aggregate is pulled ABOVE the normal range.

        This test documents the BUG — it should FAIL on the unfixed codebase and
        PASS once the fix lands (the outlier is excluded, aggregate stays in range).
        """
        # 10 normal symphonies, ~1173 value each (from v3-audit fixture ratios)
        normal_syms = [_normal_sym(f"sym-{i}", 1173.0, 30.0) for i in range(10)]
        # n2ooA-shaped outlier at 1609.36 (its actual value from the fixture)
        outlier = _twr_fallback_sym("n2ooA", 1609.36, 3.18)
        all_syms = normal_syms + [outlier]
        bot_state = self._bot_state(all_syms)

        from unittest.mock import patch

        with patch("analytics._get_shadow_divergence_trajectory", return_value=None):
            result = analytics.get_portfolio_cumulative_return(all_syms, bot_state)

        assert result["if_held"] is not None
        # Post-fix: must stay near normal (~30%), not inflated toward 318%
        assert result["if_held"] == pytest.approx(30.0, abs=5.0), (
            f"Portfolio CR with n2ooA-shaped outlier (post-fix) must be near the "
            f"normal symphonies' CR (~30%); got {result['if_held']:.2f}%. "
            f"The TWR-fallback outlier must be excluded from the VW aggregate."
        )
