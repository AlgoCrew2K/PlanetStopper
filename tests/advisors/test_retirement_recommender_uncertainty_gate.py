"""RED tests -- Retirement Recommender uncertainty gate (AC-5).

Module under test: advisors.retirement_recommender (NEW).

Contract pinned in .claude/tdd-handoff.md "retirement_recommender.py -- gates":
- evaluate_uncertainty_gate(pair: PairResult) -> GateVerdict.
- GateVerdict is a dataclass: `.passed: bool`, `.reason: str | None`,
  `.ci_lower: float | None`, `.ci_upper: float | None` (the uncertainty gate's
  own evidence fields -- these feed raw_response's ci_lower/ci_upper at
  persist time, AC-8).
- Passes iff BOTH: Fisher-z 95% CI lower bound >= CORRELATION_SCREEN_THRESHOLD
  (0.65) AND pair.n_obs >= MIN_OBS_FLOOR. Fails closed otherwise (AC-5) --
  including when pair.correlation is None or pair.n_obs is too small for the
  Fisher z formula to be defined (n <= 3).

CI-lower-bound oracle: this file's own reference_fisher_ci_lower/upper
(tests/advisors/_retirement_recommender_reference.py) is an INDEPENDENT
implementation of the textbook Fisher z-transform -- never imports or calls
back into retirement_recommender's own CI computation. A test that instead
imported the module's own value to check the module would be circular and
worthless (feedback_no_hardcoded_test_values / the PM's explicit mandate for
this cycle: 'never import the module's own CI value to check the module').
"""

from __future__ import annotations

import pytest

from advisors.correlation_diagnostic import PairResult
from tests.advisors._retirement_recommender_reference import (
    reference_fisher_ci_lower,
    reference_fisher_ci_upper,
)

# Absolute tolerance for comparing the module's computed CI bound against the
# independent oracle. Both this oracle and a house-convention implementation
# use Z_95=1.96 (see _retirement_recommender_reference.py's docstring on why
# that constant, not scipy.stats.norm.ppf(0.975)'s 1.959964, is the expected
# match) -- a generous-but-meaningful tolerance that would catch a materially
# wrong formula (e.g. a missing sqrt, a wrong n-3 vs n-1 denominator, a
# swapped +/- sign) while tolerating either critical-value convention.
_CI_TOLERANCE = 5e-3


@pytest.fixture(scope="module")
def rr():
    import advisors.retirement_recommender as _rr  # noqa: PLC0415

    return _rr


def _pair(correlation, n_obs, sym_a="alpha", sym_b="beta"):
    return PairResult(
        sym_a=sym_a,
        sym_b=sym_b,
        n_obs=n_obs,
        correlation=correlation,
        thin_data=n_obs < 30,
        window=("2026-01-01", "2026-12-31"),
    )


# ---------------------------------------------------------------------------
# Fisher-z CI value: pinned against the independent oracle
# ---------------------------------------------------------------------------


class TestUncertaintyGateCiValue:
    def test_ci_lower_matches_independent_fisher_z_oracle(self, rr):
        r, n = 0.80, 1000
        verdict = rr.evaluate_uncertainty_gate(_pair(r, n))
        expected = reference_fisher_ci_lower(r, n)
        assert verdict.ci_lower == pytest.approx(expected, abs=_CI_TOLERANCE), (
            f"evaluate_uncertainty_gate's ci_lower={verdict.ci_lower!r} does not "
            f"match the independent Fisher z-transform oracle {expected!r} for "
            f"r={r}, n={n}."
        )

    def test_ci_upper_matches_independent_fisher_z_oracle(self, rr):
        r, n = 0.80, 1000
        verdict = rr.evaluate_uncertainty_gate(_pair(r, n))
        expected = reference_fisher_ci_upper(r, n)
        assert verdict.ci_upper == pytest.approx(expected, abs=_CI_TOLERANCE)


# ---------------------------------------------------------------------------
# CI-width-driven pass/fail, isolated from the n_obs floor (both cases use a
# very large n_obs so any reasonable MIN_OBS_FLOOR is comfortably cleared;
# only the CI lower bound differs).
# ---------------------------------------------------------------------------


class TestUncertaintyGateCiWidthDriven:
    def test_strong_correlation_large_n_passes(self, rr):
        """r=0.80, n=1000 -> ci_lower ~= 0.777 (oracle-verified above), clears
        the 0.65 threshold comfortably."""
        verdict = rr.evaluate_uncertainty_gate(_pair(0.80, 1000))
        assert verdict.passed is True

    def test_correlation_just_above_screen_threshold_with_huge_n_can_still_fail_on_ci_width(
        self, rr
    ):
        """The load-bearing isolation case: r=0.66 is ABOVE the 0.65 screen
        threshold and n=1000 is far above any reasonable MIN_OBS_FLOOR, yet
        the Fisher-z CI lower bound at r=0.66 is ~0.624 (oracle-computed) --
        BELOW 0.65. This proves the gate is checking CI-lower-bound robustness,
        not just re-checking the raw correlation or the n_obs floor."""
        r, n = 0.66, 1000
        expected_lower = reference_fisher_ci_lower(r, n)
        assert expected_lower < 0.65, "fixture sanity: oracle ci_lower must be below threshold"
        verdict = rr.evaluate_uncertainty_gate(_pair(r, n))
        assert verdict.passed is False, (
            f"r=0.66 (above the 0.65 screen threshold) with n=1000 (far above "
            f"any n_obs floor) must still FAIL the uncertainty gate because "
            f"the Fisher-z CI lower bound ({expected_lower:.4f}) is below 0.65."
        )


# ---------------------------------------------------------------------------
# n_obs-floor-driven pass/fail, isolated from CI width (r=0.995 makes the CI
# lower bound clear 0.65 at essentially any n > 3, so only the floor matters).
# ---------------------------------------------------------------------------


class TestUncertaintyGateNObsFloor:
    def test_min_obs_floor_constant_exceeds_the_fisher_z_formula_minimum(self, rr):
        assert rr.MIN_OBS_FLOOR > 3, (
            "MIN_OBS_FLOOR must exceed 3 -- the Fisher z standard-error formula "
            "(1/sqrt(n-3)) is undefined at n<=3."
        )

    def test_n_obs_at_floor_passes_high_correlation_case(self, rr):
        r = 0.995  # CI lower bound clears 0.65 at any n > 3 (oracle-verified below)
        n = rr.MIN_OBS_FLOOR
        expected_lower = reference_fisher_ci_lower(r, n)
        assert expected_lower >= 0.65, (
            "fixture sanity: r=0.995 must clear 0.65 even at n=MIN_OBS_FLOOR"
        )
        verdict = rr.evaluate_uncertainty_gate(_pair(r, n))
        assert verdict.passed is True

    def test_n_obs_one_below_floor_fails_even_with_near_perfect_correlation(self, rr):
        r = 0.995
        n = rr.MIN_OBS_FLOOR - 1
        verdict = rr.evaluate_uncertainty_gate(_pair(r, n))
        assert verdict.passed is False, (
            f"n_obs={n} is one below MIN_OBS_FLOOR ({rr.MIN_OBS_FLOOR}) -- must "
            "fail-closed even though r=0.995 would otherwise clear the CI-width "
            "check easily. This isolates the n_obs floor from the CI check."
        )

    def test_very_small_n_obs_fails_closed_without_raising(self, rr):
        """n_obs=2 is far below any plausible MIN_OBS_FLOOR AND below the
        Fisher z formula's own n>3 requirement (n-3 <= 0 would otherwise raise
        ZeroDivisionError/ValueError/domain-error). The gate must check the
        floor BEFORE attempting the Fisher-z computation and fail closed
        without propagating an exception."""
        verdict = rr.evaluate_uncertainty_gate(_pair(0.99, 2))
        assert verdict.passed is False


# ---------------------------------------------------------------------------
# Fail-closed on undefined correlation
# ---------------------------------------------------------------------------


class TestUncertaintyGateUndefinedCorrelation:
    def test_none_correlation_fails_closed_without_raising(self, rr):
        verdict = rr.evaluate_uncertainty_gate(_pair(None, 100))
        assert verdict.passed is False
