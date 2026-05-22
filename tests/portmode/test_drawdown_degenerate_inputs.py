"""
RED tests — Cluster 6 AC-2: max-drawdown computation must not raise on
zero/empty/degenerate inputs, and must not silently mis-sign a negative-peak series.

Surface: port_aggregator._compute_max_drawdown_from_series (port_aggregator.py:121).

Audit provenance:
  risk-math__2026-05-21.md HIGH "_compute_max_drawdown_from_series crashes on zero
  peak, inverts sign on negative peak" + invariant-coverage__2026-05-21.md HIGH-6.

Two confirmed defects (verified by direct execution at babd328):
  1. ZeroDivisionError when the running peak is 0.0 — reachable both when the
     FIRST port_value is 0.0 and when an INTERIOR peak update lands on 0.0.
  2. Negative-peak sign inversion: for a series like [-100, -200] the producer
     returns 0.0 (a "no drawdown" lie) instead of a correct-magnitude drawdown,
     because (v-peak)/peak with peak<0 yields a positive ratio that never updates
     max_dd.

All expected values are derived from the Pospisil-Vecer 2010 drawdown definition
and the documented None-on-degenerate policy (mirroring the sibling
_compute_simple_return_from_series initial==0.0 guard) — see the fixture
tests/fixtures/math/drawdown_degenerate_inputs.json. No producer-captured values.

Tolerance: pytest.approx with rel=1e-9 for well-formed exact ratios (these are
exact rational arithmetic, so 1e-9 is comfortably tight; abs=1e-12 for the
genuine-zero case where rel is undefined at 0.0).
"""

from __future__ import annotations

import json
import pathlib

import pytest

from port_aggregator import (
    _compute_max_drawdown_from_series,
    aggregate_to_port,
)

_FIXTURE = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math"
    / "drawdown_degenerate_inputs.json"
)


@pytest.fixture(scope="module")
def drawdown_cases() -> dict:
    """Golden fixture: degenerate-input cases for max-drawdown computation."""
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))["cases"]


# ---------------------------------------------------------------------------
# AC-2 — degenerate inputs must return the None sentinel, never raise.
# ---------------------------------------------------------------------------


class TestMaxDrawdownDoesNotRaiseOnDegenerateInputs:
    """The function must return a defined sentinel (None) for every degenerate
    input — empty, single-point, and any zero-denominator case — never raise."""

    def test_empty_series_returns_none(self, drawdown_cases):
        case = drawdown_cases["empty_series"]
        assert _compute_max_drawdown_from_series(case["input"]) is case["expected"]

    def test_single_point_returns_none(self, drawdown_cases):
        case = drawdown_cases["single_point"]
        assert _compute_max_drawdown_from_series(case["input"]) is case["expected"]

    def test_zero_first_point_then_drop_returns_none_not_raises(self, drawdown_cases):
        """A series whose FIRST port_value is 0.0 must return None, NOT raise
        ZeroDivisionError. Currently raises (peak=0.0, (v-peak)/peak)."""
        case = drawdown_cases["zero_first_point_then_drop"]
        # Will currently fail with ZeroDivisionError — that IS the RED.
        result = _compute_max_drawdown_from_series(case["input"])
        assert result is case["expected"], (
            "zero-first-value series must return None (mirror the sibling "
            "_compute_simple_return_from_series initial==0.0 guard), not raise"
        )

    def test_zero_first_point_then_rise_returns_none_not_raises(self, drawdown_cases):
        """A zero-anchored series even with a later real drawdown must return
        None — a zero starting equity is a degenerate baseline."""
        case = drawdown_cases["zero_first_point_then_rise"]
        result = _compute_max_drawdown_from_series(case["input"])
        assert result is case["expected"]

    def test_zero_interior_peak_returns_none_not_raises(self, drawdown_cases):
        """The zero-peak guard must cover the peak-UPDATE branch, not only
        values[0]: a running peak that climbs to exactly 0.0 then a later drop
        must not raise ZeroDivisionError."""
        case = drawdown_cases["zero_interior_peak_then_drop"]
        result = _compute_max_drawdown_from_series(case["input"])
        assert result is case["expected"], (
            "interior peak of 0.0 must be guarded too — the audit understated "
            "this as first-point-only; direct execution confirms the interior "
            "peak-update branch also raises"
        )

    @pytest.mark.parametrize(
        "case_name",
        [
            "empty_series",
            "single_point",
            "zero_first_point_then_drop",
            "zero_first_point_then_rise",
            "zero_interior_peak_then_drop",
        ],
    )
    def test_no_degenerate_case_raises_any_exception(self, drawdown_cases, case_name):
        """Property: NO degenerate input may propagate an exception out of the
        function. Each must resolve to the defined None sentinel."""
        case = drawdown_cases[case_name]
        try:
            result = _compute_max_drawdown_from_series(case["input"])
        except Exception as exc:  # noqa: BLE001 - the test's whole point
            pytest.fail(
                f"degenerate case '{case_name}' raised {type(exc).__name__}: {exc} "
                f"— must return the None sentinel instead"
            )
        assert result is None


# ---------------------------------------------------------------------------
# AC-2 / AC-4 — negative-peak series must not silently mis-sign to 0.0.
# ---------------------------------------------------------------------------


class TestMaxDrawdownNegativePeakSign:
    """A negative-valued series must not silently report 0.0 ('no drawdown').
    The naive (v-peak)/peak with peak<0 inverts the sign so the max_dd update
    never fires — a fail-quiet correctness defect."""

    def test_negative_monotonic_decline_not_silently_zero(self, drawdown_cases):
        """[-100, -200] is a 100% worse equity position. The producer currently
        returns 0.0 (wrong). The fix must EITHER return a correct-magnitude
        negative drawdown OR return None (treat negative equity as degenerate,
        consistent with the zero-peak None policy). It must NOT return 0.0."""
        case = drawdown_cases["negative_series_monotonic_decline"]
        result = _compute_max_drawdown_from_series(case["input"])

        rejected = case["reject"]
        assert result != pytest.approx(rejected, abs=1e-12), (
            f"negative-peak series silently reported {rejected} (a no-drawdown "
            f"lie). (v-peak)/peak inverts sign when peak<0 so the dd<max_dd "
            f"update never fires."
        )

        accepted = case["accept_either"]  # [-1.0, None]
        if result is None:
            assert None in accepted, "None is an accepted resolution"
        else:
            negative_target = next(v for v in accepted if v is not None)
            assert result == pytest.approx(negative_target, rel=1e-9), (
                f"if not None, the drawdown must be the correct-magnitude "
                f"negative value {negative_target}; got {result}"
            )


# ---------------------------------------------------------------------------
# AC-2 — control cases: the guard must not regress well-formed input.
# ---------------------------------------------------------------------------


class TestMaxDrawdownWellFormedControl:
    """Well-formed series must still compute the correct drawdown after the
    degenerate-input guards are added (anti-regression)."""

    def test_well_formed_two_excursion_drawdown(self, drawdown_cases):
        case = drawdown_cases["well_formed_drawdown_control"]
        result = _compute_max_drawdown_from_series(case["input"])
        assert result == pytest.approx(
            case["expected"], rel=case["tolerance_rel"]
        ), "well-formed two-excursion series must yield the deepest drawdown"

    def test_monotonic_rise_is_genuine_zero_not_none(self, drawdown_cases):
        """A strictly rising series has max drawdown 0.0 — a genuine zero,
        distinct from None (undefined). The guard must not collapse this to
        None."""
        case = drawdown_cases["no_drawdown_monotonic_rise"]
        result = _compute_max_drawdown_from_series(case["input"])
        assert result is not None, (
            "a monotonic-rise series has a DEFINED drawdown of 0.0; the "
            "degenerate-input guard must not return None here"
        )
        assert result == pytest.approx(case["expected"], abs=case["tolerance_abs"])

    def test_well_formed_drawdown_sign_is_non_positive(self, drawdown_cases):
        """Property: for any well-formed (strictly positive) series the result
        is <= 0.0 — the documented negative-drawdown convention."""
        for case_name in ("well_formed_drawdown_control", "no_drawdown_monotonic_rise"):
            case = drawdown_cases[case_name]
            result = _compute_max_drawdown_from_series(case["input"])
            assert result is not None and result <= 0.0, (
                f"{case_name}: drawdown must be <= 0.0 (negative convention)"
            )


# ---------------------------------------------------------------------------
# AC-2 — the public aggregate_to_port entry point must not crash either.
# ---------------------------------------------------------------------------


class TestAggregateToPortSurvivesZeroPeakEquitySeries:
    """The crash must not be reachable through the public aggregate_to_port
    surface either — a port-equity series starting at 0.0 (freshly-opened
    account / pre-fill snapshot) must aggregate without raising."""

    def test_aggregate_to_port_with_zero_first_equity_does_not_raise(self):
        symphonies = [
            {
                "symphony_id": "s1",
                "value": 10000.0,
                "cash": 0.0,
                "current_return": 0.01,
                "last_percent_change": 0.01,
                "last_dollar_change": 100.0,
                "net_deposits": 9900.0,
            }
        ]
        zero_anchored_series = [
            {"t": 0, "port_value": 0.0},
            {"t": 1, "port_value": 9000.0},
            {"t": 2, "port_value": 8000.0},
        ]
        try:
            result = aggregate_to_port(symphonies, "acct-zero-anchor", zero_anchored_series)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"aggregate_to_port raised {type(exc).__name__} on a zero-anchored "
                f"port-equity series: {exc}"
            )
        # When the drawdown is undefined the key is simply absent (aggregate_to_port
        # only sets max_drawdown when the helper returns non-None).
        assert "max_drawdown" not in result or result["max_drawdown"] is None, (
            "a zero-anchored series yields an undefined drawdown — the aggregate "
            "must omit max_drawdown (or set it None), not crash or invent a value"
        )

    def test_aggregate_to_port_with_well_formed_series_still_sets_drawdown(self):
        """Anti-regression: a well-formed series must still produce a
        max_drawdown key on the aggregate."""
        symphonies = [
            {
                "symphony_id": "s1",
                "value": 10000.0,
                "cash": 0.0,
                "current_return": 0.01,
                "last_percent_change": 0.01,
                "last_dollar_change": 100.0,
                "net_deposits": 9900.0,
            }
        ]
        series = [
            {"t": 0, "port_value": 10000.0},
            {"t": 1, "port_value": 9000.0},
            {"t": 2, "port_value": 9500.0},
        ]
        result = aggregate_to_port(symphonies, "acct-wf", series)
        assert "max_drawdown" in result and result["max_drawdown"] is not None
        # (9000-10000)/10000 = -0.1 ; subsequent 9500 does not deepen it.
        assert result["max_drawdown"] == pytest.approx(-0.1, rel=1e-9)
        assert result["max_drawdown"] <= 0.0
