"""
RED tests — Cluster 6 AC-4: drawdown sign convention must be consistent and
documented across every producer and consumer.

Audit provenance:
  risk-math__2026-05-21.md MEDIUM "Two opposing max-drawdown sign conventions
  across the codebase" + invariant-coverage__2026-05-21.md MEDIUM-6.

THE FINDING. Three drawdown surfaces use sign conventions that are each
internally correct but mutually inconsistent, with no test pinning them
side-by-side:

  - port_aggregator._compute_max_drawdown_from_series  -> NEGATIVE float
    (a 20% drawdown is -0.20; port_aggregator.py:146 docstring).
  - analytics.compute_quantstats_metrics["max_drawdown"] -> NEGATIVE (<= 0)
    (analytics.py:396-397; quantstats convention).
  - analytics.get_symphony_max_drawdown["if_held"/"dry_run"] -> POSITIVE
    magnitude (a 20% drawdown is +20.0; analytics.py:587 docstring).

A future accidental sign flip in any one module, or a consumer that mixes the
two (an abs() applied on one path but not another), passes every per-module
test today. This suite freezes the contract: each surface must agree with its
OWN documented sign, and the two families must be cross-referenced in source so
the inconsistency is visible to any future reader.

All expecteds are derived from the documented conventions themselves — there
are no producer-captured magic numbers. The equivalent-loss scenario is
hand-constructed so the magnitude is analytically known.

Tolerance: pytest.approx with rel=1e-9 — the drawdown ratios here are exact
rational arithmetic so a tight tolerance is appropriate; floating-point
representation of e.g. -0.2 is the only source of error.
"""

from __future__ import annotations

import inspect

import pytest

import analytics
import port_aggregator
from analytics import compute_quantstats_metrics, get_symphony_max_drawdown
from port_aggregator import _compute_max_drawdown_from_series


# A single equity decline of exactly 20%: peak 100 -> trough 80.
# Drawdown MAGNITUDE = 0.20 (20%). The three surfaces must each express this
# same loss in their OWN documented sign.
_PEAK = 100.0
_TROUGH = 80.0
_DRAWDOWN_MAGNITUDE = (_PEAK - _TROUGH) / _PEAK  # 0.20, derived not hardcoded


class TestPortAggregatorUsesNegativeConvention:
    """port_aggregator._compute_max_drawdown_from_series documents a negative
    drawdown (a 20% drawdown is -0.20)."""

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
            "(port_aggregator.py:146); a real loss must produce a negative value"
        )
        # Magnitude must equal the 20% loss, expressed negative.
        assert result == pytest.approx(-_DRAWDOWN_MAGNITUDE, rel=1e-9)


class TestQuantstatsMetricsUsesNegativeConvention:
    """analytics.compute_quantstats_metrics emits max_drawdown <= 0 (negative
    convention) — the same family as port_aggregator."""

    def test_quantstats_max_drawdown_is_non_positive_for_a_real_loss(self):
        # A return series with a clear 25% drawdown excursion.
        returns_pct = [1.0, -25.0, 5.0, 3.0]
        metrics = compute_quantstats_metrics(returns_pct)
        assert metrics["max_drawdown"] is not None
        assert metrics["max_drawdown"] <= 0.0, (
            "compute_quantstats_metrics documents max_drawdown <= 0 (negative "
            "convention); a real loss must not produce a positive value"
        )

    def test_quantstats_and_port_aggregator_agree_on_sign_family(self):
        """Both NEGATIVE-convention surfaces must agree: neither emits a
        positive drawdown for a loss. This is the cross-module guard against
        one of the two silently flipping."""
        series = [
            {"t": 0, "port_value": _PEAK},
            {"t": 1, "port_value": _TROUGH},
        ]
        pa_dd = _compute_max_drawdown_from_series(series)
        qs_dd = compute_quantstats_metrics([1.0, -20.0, 2.0])["max_drawdown"]
        assert pa_dd is not None and qs_dd is not None
        assert pa_dd <= 0.0 and qs_dd <= 0.0, (
            "port_aggregator and compute_quantstats_metrics are the same sign "
            "family (negative); a flip in either is a cross-module contract break"
        )


class TestGetSymphonyMaxDrawdownUsesPositiveMagnitudeConvention:
    """analytics.get_symphony_max_drawdown documents a POSITIVE magnitude
    convention (a 20% drawdown is +20.0; analytics.py:587). app.py templates
    consume this sign — it must not silently flip."""

    def test_if_held_drawdown_is_positive_magnitude(self):
        # Composer max_drawdown is a positive decimal magnitude; the helper
        # returns it * 100 (percent-scale), still positive.
        sym_dict = {
            "id": "sym-sign",
            "max_drawdown": 0.20,  # Composer: 20% drawdown as positive decimal
        }
        result = get_symphony_max_drawdown(sym_dict, bot_state_entry=None)
        assert result["if_held"] is not None
        assert result["if_held"] > 0.0, (
            "get_symphony_max_drawdown documents the POSITIVE magnitude "
            "convention (analytics.py:587); a real drawdown must be positive"
        )
        # 0.20 decimal * 100 = 20.0 percent — magnitude derived from the input.
        assert result["if_held"] == pytest.approx(
            _DRAWDOWN_MAGNITUDE * 100.0, rel=1e-9
        )

    def test_if_held_and_port_aggregator_use_OPPOSITE_signs_for_the_same_loss(self):
        """The defining cross-module assertion: feed the SAME 20% loss to both
        families and assert they express it with OPPOSITE signs. This pins the
        documented inconsistency so any future accidental unification or flip
        in either module fails loudly here.

        port_aggregator: -0.20 (negative decimal)
        get_symphony_max_drawdown: +20.0 (positive percent)
        """
        # Negative-convention surface: 20% loss as a series.
        pa_dd = _compute_max_drawdown_from_series(
            [{"t": 0, "port_value": _PEAK}, {"t": 1, "port_value": _TROUGH}]
        )
        # Positive-magnitude surface: same 20% loss as a Composer field.
        sym_dd = get_symphony_max_drawdown(
            {"id": "sym-sign", "max_drawdown": _DRAWDOWN_MAGNITUDE},
            bot_state_entry=None,
        )["if_held"]

        assert pa_dd is not None and sym_dd is not None
        assert pa_dd < 0.0, "port_aggregator side must be negative"
        assert sym_dd > 0.0, "get_symphony_max_drawdown side must be positive"
        # The magnitudes describe the same loss (modulo the percent/decimal scale).
        assert abs(pa_dd) * 100.0 == pytest.approx(abs(sym_dd), rel=1e-9), (
            "the two surfaces must describe the SAME loss magnitude — only the "
            "sign and the percent/decimal scale differ. If this fails, one "
            "surface's magnitude computation drifted."
        )


class TestDrawdownConventionsAreDocumentedAndCrossReferenced:
    """AC-4 requires ONE documented convention story: each function's docstring
    must state its sign, and the opposing-family functions must cross-reference
    each other so a future reader cannot miss the inconsistency."""

    def test_port_aggregator_docstring_states_negative_convention(self):
        doc = inspect.getdoc(port_aggregator._compute_max_drawdown_from_series) or ""
        assert "negative" in doc.lower(), (
            "_compute_max_drawdown_from_series docstring must state its drawdown "
            "is NEGATIVE-convention"
        )

    def test_get_symphony_max_drawdown_docstring_states_positive_convention(self):
        doc = inspect.getdoc(analytics.get_symphony_max_drawdown) or ""
        assert (
            "positive" in doc.lower() or "magnitude" in doc.lower()
        ), (
            "get_symphony_max_drawdown docstring must state its drawdown is "
            "POSITIVE-magnitude convention"
        )

    def test_opposing_drawdown_functions_cross_reference_each_other(self):
        """At least one of the two opposing-convention functions must name the
        other in its docstring/source, so the deliberate inconsistency is
        discoverable. A future refactor that 'fixes' one without the other
        then has a visible breadcrumb."""
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
            "each other (docstring or comment) so the deliberate convention "
            "split is documented and a future sign flip is caught by a reader"
        )
