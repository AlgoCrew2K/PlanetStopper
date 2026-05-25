"""
RED tests — Cluster 6 AC-4: drawdown sign convention must be consistent on the
operator-facing surface, regardless of cache warmth.

Audit provenance:
  risk-math__2026-05-21.md MEDIUM "Two opposing max-drawdown sign conventions
  across the codebase" + invariant-coverage__2026-05-21.md MEDIUM-6, AND a
  load-bearing live sign-flip found by risk-engine-specialist during the
  Cluster 6 pre-review (not in the original audit).

THE FINDING. Three drawdown surfaces use different sign conventions:
  - port_aggregator._compute_max_drawdown_from_series  -> NEGATIVE float
    (internal; consumed only by aggregate_to_port).
  - analytics.compute_quantstats_metrics["max_drawdown"] -> NEGATIVE (<= 0)
    (internal quant-metrics function).
  - analytics.get_symphony_max_drawdown["if_held"/"dry_run"] -> POSITIVE
    magnitude (operator-facing).

THE LIVE DEFECT (risk-engine-specialist). app._compute_portfolio_strip emits
portfolio_strip["max_drawdown"]["if_held"] from TWO branches:
  - WARM cache: if_held = _account_totals_cache["portfolio_mdd"], which app.py
    sets from `float(compute_quantstats_metrics["max_drawdown"]) * 100` — the
    NEGATIVE convention.
  - COLD cache: if_held flows from analytics.get_portfolio_max_drawdown ->
    get_symphony_max_drawdown -> POSITIVE magnitude.
=> The SAME operator-facing dashboard field flips sign purely on cache warmth.

D8 RULING (team-lead 2026-05-22): positive magnitude is the canonical
convention for the dashboard / operator-facing drawdown surface. The fix is
option (a): the app.py warm-cache branch abs()-converts the quantstats negative
value AT THE BOUNDARY. compute_quantstats_metrics KEEPS its internal negative
convention (it is a standard quant-metrics function — convert at the consumer,
not the producer). port_aggregator stays negative internally. The canonical
convention is documented in code.

This suite:
  - pins the per-module conventions each surface documents (anti-flip guard);
  - DRIVES both the warm-cache and cold-cache app._compute_portfolio_strip
    branches and asserts they agree on POSITIVE magnitude — the D8 contract;
  - requires the canonical convention to be documented + cross-referenced.

All expecteds are derived from the documented conventions and a hand-built loss
scenario — no producer-captured magic numbers.

Tolerance: pytest.approx rel=1e-9 — the drawdown ratios are exact rational
arithmetic; floating-point representation is the only error source.
"""

from __future__ import annotations

import inspect

import pytest

import analytics
import app as app_module
import port_aggregator
from analytics import compute_quantstats_metrics, get_symphony_max_drawdown
from port_aggregator import _compute_max_drawdown_from_series


# A single equity decline of exactly 20%: peak 100 -> trough 80.
_PEAK = 100.0
_TROUGH = 80.0
_DRAWDOWN_MAGNITUDE = (_PEAK - _TROUGH) / _PEAK  # 0.20, derived not hardcoded


# ---------------------------------------------------------------------------
# Cache isolation — _compute_portfolio_strip reads a module-level cache.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_account_totals_cache():
    """Reset app._account_totals_cache before and after each test so cache
    warmth is fully controlled per test."""
    app_module._account_totals_cache.clear()
    yield
    app_module._account_totals_cache.clear()


def _minimal_bot_state_with_drawdown() -> dict:
    """A bot_state with two symphonies each carrying a real Composer
    max_drawdown so the cold-cache analytics path produces a non-zero MDD."""
    return {
        f"sym-{i}": {
            "name": f"Symphony {i}",
            "current_value": 1000.0,
            "current_return": 1.5,
            "simple_return": 0.05,
            "net_deposits": 800.0,
            "time_weighted_return": 0.06,
            "max_drawdown": _DRAWDOWN_MAGNITUDE,  # Composer positive decimal
        }
        for i in range(2)
    }


# ---------------------------------------------------------------------------
# Per-module convention pins (anti-flip guards).
# ---------------------------------------------------------------------------


class TestPortAggregatorUsesNegativeConventionInternally:
    """port_aggregator._compute_max_drawdown_from_series documents a negative
    drawdown internally — it is consumed only by aggregate_to_port, not the
    dashboard strip directly."""

    def test_drawdown_is_negative_for_a_real_loss(self):
        series = [
            {"t": 0, "port_value": _PEAK},
            {"t": 1, "port_value": _TROUGH},
            {"t": 2, "port_value": 90.0},
        ]
        result = _compute_max_drawdown_from_series(series)
        assert result is not None
        assert result < 0.0, (
            "port_aggregator documents the NEGATIVE drawdown convention "
            "internally; a real loss must produce a negative value"
        )
        assert result == pytest.approx(-_DRAWDOWN_MAGNITUDE, rel=1e-9)


class TestQuantstatsMetricsKeepsNegativeConventionInternally:
    """compute_quantstats_metrics keeps max_drawdown <= 0 — D8 explicitly says
    NOT to flip this producer; it is converted at the consumer boundary."""

    def test_quantstats_max_drawdown_is_non_positive_for_a_real_loss(self):
        pytest.importorskip("quantstats", reason="quantstats is an optional dep — skip when absent")
        returns_pct = [1.0, -25.0, 5.0, 3.0]
        metrics = compute_quantstats_metrics(returns_pct)
        assert metrics["max_drawdown"] is not None
        assert metrics["max_drawdown"] <= 0.0, (
            "compute_quantstats_metrics keeps the internal negative convention "
            "(D8: convert at the app.py consumer boundary, not the producer)"
        )


class TestGetSymphonyMaxDrawdownUsesPositiveMagnitude:
    """analytics.get_symphony_max_drawdown is operator-facing and uses the
    canonical POSITIVE magnitude convention (D8)."""

    def test_if_held_drawdown_is_positive_magnitude(self):
        sym_dict = {"id": "sym-sign", "max_drawdown": _DRAWDOWN_MAGNITUDE}
        result = get_symphony_max_drawdown(sym_dict, bot_state_entry=None)
        assert result["if_held"] is not None
        assert result["if_held"] > 0.0, (
            "get_symphony_max_drawdown is operator-facing — D8 canonical is "
            "POSITIVE magnitude"
        )
        assert result["if_held"] == pytest.approx(
            _DRAWDOWN_MAGNITUDE * 100.0, rel=1e-9
        )


# ---------------------------------------------------------------------------
# THE D8 CONTRACT — the operator-facing portfolio-strip MDD must be the SAME
# positive-magnitude sign regardless of cache warmth.
# ---------------------------------------------------------------------------


class TestPortfolioStripDrawdownSignIsCacheWarmthInvariant:
    """app._compute_portfolio_strip must emit portfolio_strip["max_drawdown"]
    ["if_held"] as a POSITIVE magnitude whether the account-totals cache is
    warm or cold. Currently the warm branch is negative (quantstats) and the
    cold branch is positive (analytics) — a live sign flip on one operator
    field."""

    def test_warm_cache_portfolio_mdd_if_held_is_positive_magnitude(self):
        """WARM cache: app.py sets portfolio_mdd from the quantstats NEGATIVE
        value. After the D8 fix the strip must still expose it as a POSITIVE
        magnitude (abs() at the boundary). Currently RED — the warm value is
        negative."""
        bot_state = _minimal_bot_state_with_drawdown()
        # Simulate what app.py:272 writes: quantstats max_drawdown is negative.
        # A 20% drawdown -> compute_quantstats_metrics-style negative * 100.
        quantstats_negative_mdd_pct = -_DRAWDOWN_MAGNITUDE * 100.0  # -20.0
        app_module._account_totals_cache["portfolio_mdd"] = quantstats_negative_mdd_pct
        app_module._account_totals_cache["portfolio_value"] = 2000.0

        strip = app_module._compute_portfolio_strip(bot_state)
        mdd = strip.get("max_drawdown")
        assert mdd is not None and mdd.get("if_held") is not None, (
            "warm-cache portfolio strip must produce a max_drawdown.if_held"
        )
        assert mdd["if_held"] > 0.0, (
            "D8: the operator-facing portfolio MDD if_held must be a POSITIVE "
            "magnitude even on a warm cache. The warm branch currently passes "
            "the quantstats NEGATIVE value straight through — the fix must "
            "abs()-convert it at the app.py boundary."
        )
        # Magnitude must be preserved by the abs() conversion.
        assert mdd["if_held"] == pytest.approx(
            abs(quantstats_negative_mdd_pct), rel=1e-9
        ), "abs() conversion must preserve the magnitude, only drop the sign"

    def test_cold_cache_portfolio_mdd_if_held_is_positive_magnitude(self):
        """COLD cache: if_held flows through analytics.get_portfolio_max_drawdown
        which is already positive magnitude. Anti-regression — the D8 fix must
        not disturb the already-correct cold path."""
        bot_state = _minimal_bot_state_with_drawdown()
        # cache is empty (cleared by autouse fixture) -> cold path
        strip = app_module._compute_portfolio_strip(bot_state)
        mdd = strip.get("max_drawdown")
        assert mdd is not None and mdd.get("if_held") is not None, (
            "cold-cache portfolio strip must produce a max_drawdown.if_held"
        )
        assert mdd["if_held"] > 0.0, (
            "cold-cache portfolio MDD if_held is already positive magnitude — "
            "must remain so after the D8 fix"
        )

    def test_warm_and_cold_portfolio_mdd_agree_on_sign(self):
        """THE defining D8 assertion: the SAME loss must yield the SAME sign on
        both cache branches. Drive each branch with an equivalent 20% loss and
        assert both if_held values are positive — no sign flip on cache
        warmth."""
        bot_state = _minimal_bot_state_with_drawdown()

        # Cold branch.
        app_module._account_totals_cache.clear()
        cold = app_module._compute_portfolio_strip(bot_state)["max_drawdown"]["if_held"]

        # Warm branch — same 20% loss, expressed in the quantstats negative form
        # that app.py:272 caches.
        app_module._account_totals_cache.clear()
        app_module._account_totals_cache["portfolio_mdd"] = -_DRAWDOWN_MAGNITUDE * 100.0
        app_module._account_totals_cache["portfolio_value"] = 2000.0
        warm = app_module._compute_portfolio_strip(bot_state)["max_drawdown"]["if_held"]

        assert cold is not None and warm is not None
        assert (cold >= 0.0) == (warm >= 0.0), (
            f"D8 VIOLATION: portfolio MDD if_held flips sign on cache warmth — "
            f"cold={cold}, warm={warm}. The same operator-facing field must "
            f"have one convention. Fix: abs() the warm-cache branch."
        )
        # Per D8 the agreed convention is POSITIVE magnitude.
        assert cold > 0.0 and warm > 0.0, (
            "D8 canonical convention is POSITIVE magnitude on both branches"
        )


# ---------------------------------------------------------------------------
# The canonical convention must be documented in code.
# ---------------------------------------------------------------------------


class TestDrawdownConventionIsDocumentedAndCrossReferenced:
    """AC-4 / D8: the canonical positive-magnitude convention for the
    operator-facing surface must be documented in code, and the opposing
    internal-negative producers must be cross-referenced so a future reader
    cannot miss the deliberate split."""

    def test_port_aggregator_docstring_states_negative_convention(self):
        doc = inspect.getdoc(port_aggregator._compute_max_drawdown_from_series) or ""
        assert "negative" in doc.lower(), (
            "_compute_max_drawdown_from_series docstring must state its drawdown "
            "is the internal NEGATIVE convention"
        )

    def test_get_symphony_max_drawdown_docstring_states_positive_convention(self):
        doc = inspect.getdoc(analytics.get_symphony_max_drawdown) or ""
        assert "positive" in doc.lower() or "magnitude" in doc.lower(), (
            "get_symphony_max_drawdown docstring must state the canonical "
            "POSITIVE-magnitude convention"
        )

    def test_app_portfolio_strip_documents_the_canonical_convention(self):
        """The D8 fix at the app.py warm-cache boundary must carry a comment
        naming the canonical positive-magnitude convention, so the abs()
        conversion is not mistaken for a bug by a future reader."""
        src = inspect.getsource(app_module._compute_portfolio_strip)
        lowered = src.lower()
        # The fix introduces an abs() on the warm-cache mdd branch.
        assert "abs(" in src, (
            "the D8 fix must abs()-convert the warm-cache portfolio_mdd at the "
            "app.py boundary — no abs() found in _compute_portfolio_strip"
        )
        assert "magnitude" in lowered or "convention" in lowered or "sign" in lowered, (
            "the D8 abs() conversion must be documented with a comment naming "
            "the canonical positive-magnitude drawdown convention"
        )

    def test_opposing_drawdown_functions_cross_reference_each_other(self):
        """At least one of the two opposing-convention drawdown functions must
        name the other (docstring/comment), so the deliberate internal-negative
        vs operator-positive split is discoverable."""
        pa_src = inspect.getsource(port_aggregator._compute_max_drawdown_from_series)
        an_src = inspect.getsource(analytics.get_symphony_max_drawdown)
        pa_refs_analytics = (
            "get_symphony_max_drawdown" in pa_src or "analytics" in pa_src
        )
        an_refs_pa = (
            "_compute_max_drawdown_from_series" in an_src
            or "port_aggregator" in an_src
        )
        assert pa_refs_analytics or an_refs_pa, (
            "AC-4: the two opposing-sign drawdown functions must cross-reference "
            "each other so the deliberate convention split is documented"
        )
