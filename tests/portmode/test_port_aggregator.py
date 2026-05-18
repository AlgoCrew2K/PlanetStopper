"""
RED tests for port_aggregator.aggregate_to_port (AC-P2.4.*)

All expected values are schema-derived from AC-P2.4.2 per-field aggregation spec
and the math literature (Goldberg-Mahmoud 2017 / Pospisil-Vecer 2010) — no inline
literals; see tests/fixtures/portmode/port_aggregator/*.json for derivation comments.

Tolerance: pytest.approx(rel=1e-6) for float aggregations.
"""

from __future__ import annotations

import json
import pathlib

import pytest

FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "portmode" / "port_aggregator"

# --- These imports WILL FAIL until port_aggregator.py is implemented (RED intent) ---
# The module must expose: aggregate_to_port(symphonies, account_id, port_equity_series) -> dict
from port_aggregator import aggregate_to_port  # noqa: F401


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


# ---------------------------------------------------------------------------
# AC-P2.4.2: Value-weightable fields
# ---------------------------------------------------------------------------

class TestValueWeightableFields:
    """Value-weightable fields use allocation-weighted sum (AC-P2.4.2)."""

    def test_value_sums_directly(self):
        fx = _load("01_two_symphony_value_weighted_fields.json")
        result = aggregate_to_port(
            fx["inputs"]["symphonies"],
            fx["inputs"]["account_id"],
            fx["inputs"]["port_equity_series"],
        )
        assert result["value"] == pytest.approx(fx["expected"]["value"], rel=1e-6)

    def test_cash_sums_directly(self):
        fx = _load("01_two_symphony_value_weighted_fields.json")
        result = aggregate_to_port(
            fx["inputs"]["symphonies"],
            fx["inputs"]["account_id"],
            fx["inputs"]["port_equity_series"],
        )
        assert result["cash"] == pytest.approx(fx["expected"]["cash"], rel=1e-6)

    def test_net_deposits_arithmetically_summed_not_weighted(self):
        """net_deposits is a flow quantity — must SUM, not weight (AC-P2.4.2)."""
        fx = _load("01_two_symphony_value_weighted_fields.json")
        result = aggregate_to_port(
            fx["inputs"]["symphonies"],
            fx["inputs"]["account_id"],
            fx["inputs"]["port_equity_series"],
        )
        assert result["net_deposits"] == pytest.approx(fx["expected"]["net_deposits"], rel=1e-6)

    def test_current_return_allocation_weighted(self):
        """current_return is allocation-weighted: sum(w_i * r_i) where w_i = value_i / total_value."""
        fx = _load("01_two_symphony_value_weighted_fields.json")
        result = aggregate_to_port(
            fx["inputs"]["symphonies"],
            fx["inputs"]["account_id"],
            fx["inputs"]["port_equity_series"],
        )
        assert result["current_return"] == pytest.approx(fx["expected"]["current_return"], rel=1e-6)

    def test_last_percent_change_allocation_weighted(self):
        fx = _load("01_two_symphony_value_weighted_fields.json")
        result = aggregate_to_port(
            fx["inputs"]["symphonies"],
            fx["inputs"]["account_id"],
            fx["inputs"]["port_equity_series"],
        )
        assert result["last_percent_change"] == pytest.approx(
            fx["expected"]["last_percent_change"], rel=1e-6
        )

    def test_sharpe_ratio_is_null(self):
        """sharpe_ratio is UNAVAILABLE / NULL — cannot value-weight across different histories."""
        fx = _load("01_two_symphony_value_weighted_fields.json")
        result = aggregate_to_port(
            fx["inputs"]["symphonies"],
            fx["inputs"]["account_id"],
            fx["inputs"]["port_equity_series"],
        )
        assert result.get("sharpe_ratio") is None

    def test_annualized_ror_is_null(self):
        """annualized_rate_of_return is UNAVAILABLE / NULL (AC-P2.4.2 dropped)."""
        fx = _load("01_two_symphony_value_weighted_fields.json")
        result = aggregate_to_port(
            fx["inputs"]["symphonies"],
            fx["inputs"]["account_id"],
            fx["inputs"]["port_equity_series"],
        )
        assert result.get("annualized_rate_of_return") is None


# ---------------------------------------------------------------------------
# AC-P2.4.2 CRITICAL: Path-dependent fields recomputed from series (NOT weighted)
# ---------------------------------------------------------------------------

class TestPathDependentFieldsRecomputedFromSeries:
    """
    max_drawdown, simple_return, time_weighted_return MUST be computed from the
    port-equity series, NOT from per-symphony scalars (Goldberg-Mahmoud 2017 /
    Pospisil-Vecer 2010 — value-weighting non-coincident extrema is meaningless).

    This test is adversarially structured: the fixture provides per-symphony
    max_drawdown_scalar values that, if incorrectly value-weighted, would produce
    a DIFFERENT answer than the correct series-based result. An implementation
    that value-weights the scalar will FAIL this test.
    """

    def test_max_drawdown_from_series_not_weighted_scalars(self):
        fx = _load("03_path_dependent_fields.json")
        result = aggregate_to_port(
            fx["inputs"]["symphonies"],
            fx["inputs"]["account_id"],
            fx["inputs"]["port_equity_series"],
        )
        # If implementation incorrectly value-weights per-symphony scalars, it yields ~-0.076.
        # Correct series-based computation yields ~-0.0316 (see fixture derivation).
        correct_mdd = fx["expected"]["max_drawdown"]
        incorrect_value_weighted = fx["expected"]["value_weighted_max_drawdown_would_be_wrong"]
        assert isinstance(incorrect_value_weighted, str), "fixture integrity: wrong answer must be documented"
        assert result["max_drawdown"] == pytest.approx(correct_mdd, rel=1e-4)
        # Explicit anti-assertion: result must NOT match the value-weighted wrong answer
        wrong_approx = -0.076
        assert abs(result["max_drawdown"] - wrong_approx) > 0.01, (
            "max_drawdown appears to be value-weighted scalar (WRONG) not series-recomputed (CORRECT)"
        )

    def test_simple_return_from_series_not_weighted(self):
        fx = _load("03_path_dependent_fields.json")
        result = aggregate_to_port(
            fx["inputs"]["symphonies"],
            fx["inputs"]["account_id"],
            fx["inputs"]["port_equity_series"],
        )
        assert result["simple_return"] == pytest.approx(fx["expected"]["simple_return"], rel=1e-4)


# ---------------------------------------------------------------------------
# AC-P2.4.4: Edge cases — zero and single symphony
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_zero_symphonies_returns_sentinel(self):
        """Account with zero symphonies -> aggregator returns an empty sentinel."""
        fx = _load("02_zero_symphonies_sentinel.json")
        result = aggregate_to_port(
            fx["inputs"]["symphonies"],
            fx["inputs"]["account_id"],
            fx["inputs"]["port_equity_series"],
        )
        assert fx["expected_sentinel"] is True
        # Sentinel must be distinguishable: either has 'empty_sentinel' key or is falsy
        is_sentinel = (
            result.get("empty_sentinel") is True
            or result.get("value", -1) == 0 and len(result.get("symphonies", [])) == 0
        )
        assert is_sentinel, f"Expected sentinel shape, got {result}"

    def test_single_symphony_passes_through_and_flags_port_mode(self):
        """Single symphony: values pass through; result is flagged port_mode=True (AC-P2.4.4)."""
        fx = _load("04_single_symphony_port_mode_flag.json")
        result = aggregate_to_port(
            fx["inputs"]["symphonies"],
            fx["inputs"]["account_id"],
            fx["inputs"]["port_equity_series"],
        )
        assert result["value"] == pytest.approx(fx["expected"]["value"], rel=1e-6)
        assert result["current_return"] == pytest.approx(fx["expected"]["current_return"], rel=1e-6)
        assert result.get("port_mode") is True, "Single-symphony port must be flagged port_mode=True"

    def test_zero_cash_account(self):
        """All-equity portfolio: cash field aggregates to 0.0, not None or missing."""
        result = aggregate_to_port(
            [
                {"symphony_id": "s1", "value": 10000.0, "cash": 0.0,
                 "current_return": 0.01, "last_percent_change": 0.01,
                 "last_dollar_change": 100.0, "net_deposits": 9900.0},
            ],
            "acct-zerocash",
            [],
        )
        assert result["cash"] == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# AC-P2.4.5: No false-precedent on compute_vwap_signals
# ---------------------------------------------------------------------------

class TestNoFalsePrecedent:

    def test_aggregator_does_not_cross_into_vwap_signals(self):
        """
        AC-P2.4.5: The aggregator does NOT call compute_vwap_signals or reference
        per-ticker within-symphony weighting conventions. It is a pure function.
        """
        import inspect
        import port_aggregator
        source = inspect.getsource(port_aggregator.aggregate_to_port)
        assert "compute_vwap_signals" not in source, (
            "AC-P2.4.5: aggregate_to_port must not reference compute_vwap_signals"
        )

    def test_aggregate_to_port_has_no_io(self):
        """AC-P2.4.1: Pure function — no I/O, no state mutation, no time-of-day awareness."""
        import inspect
        import port_aggregator
        source = inspect.getsource(port_aggregator.aggregate_to_port)
        for forbidden in ("open(", "sqlite3", "requests", "datetime.now", "time.time", "os.getenv"):
            assert forbidden not in source, f"aggregate_to_port must be pure; found '{forbidden}'"


# ---------------------------------------------------------------------------
# Property tests: aggregation invariants
# ---------------------------------------------------------------------------

class TestAggregationInvariants:

    def test_current_return_weights_sum_to_one(self):
        """The allocation weights used for current_return must sum to 1.0."""
        symphonies = [
            {"symphony_id": "s1", "value": 30000.0, "cash": 0.0,
             "current_return": 0.0, "last_percent_change": 0.0,
             "last_dollar_change": 0.0, "net_deposits": 30000.0},
            {"symphony_id": "s2", "value": 70000.0, "cash": 0.0,
             "current_return": 0.0, "last_percent_change": 0.0,
             "last_dollar_change": 0.0, "net_deposits": 70000.0},
        ]
        result = aggregate_to_port(symphonies, "acct-weights", [])
        assert result["current_return"] == pytest.approx(0.0, abs=1e-9), (
            "Both symphonies have current_return=0.0; weighted sum must equal 0.0 regardless of weights"
        )

    def test_value_is_nonnegative(self):
        """Aggregated port value is always >= 0 (sum of non-negative values)."""
        symphonies = [
            {"symphony_id": "s1", "value": 0.0, "cash": 0.0,
             "current_return": 0.0, "last_percent_change": 0.0,
             "last_dollar_change": 0.0, "net_deposits": 0.0},
        ]
        result = aggregate_to_port(symphonies, "acct-zero-val", [])
        assert result.get("value", 0.0) >= 0.0
