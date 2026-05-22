"""
Cluster 4 — AC-8 (audit "expected-max-Sharpe overflow"): compute_expected_max_sharpe
clamps its norm.ppf arguments below 1.0 so a very large n_trials cannot overflow
SR_0 to +inf.

RED tests, written before the implementation.

`compute_expected_max_sharpe` (math_engine.py) evaluates
    norm.ppf(1 - 1/N)  and  norm.ppf(1 - 1/(N*e)).
For N >= 1e16, `1 - 1/N` rounds to exactly 1.0 in IEEE-754 double precision
(1/1e16 = 1e-16 is below machine epsilon relative to 1.0). scipy.stats.norm.ppf(1.0)
returns +inf, so `SR_0` overflows to +inf. An infinite `SR_0` poisons the DSR
numerator `(SR_obs - SR_0)` for every trial — `SR_obs - inf = -inf` — collapsing the
re-ranking.

AC-8 requires the ppf arguments to be clamped strictly below 1.0 so the result
stays finite for any n_trials.

Fixture provenance: tests/fixtures/math/expected_max_sharpe/norm_ppf_clamp_overflow.json
— the boundary facts (1-1/N rounds to 1.0 at N>=1e16; norm.ppf(1.0)==inf) are
independently verified, not produced by the implementation. The fixture pins only
the BEHAVIOURAL contract (result is finite); the exact clamped value depends on the
implementer's clamp ceiling and is intentionally not pinned.

Reference: Bailey & López de Prado 2014, "The Deflated Sharpe Ratio", JPM 40(5),
DOI 10.3905/jpm.2014.40.5.094.
"""

from __future__ import annotations

import json
import math
import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures" / "math" / "expected_max_sharpe"
)


def _import_math_engine():
    import math_engine
    return math_engine


def _load_fixture(filename: str) -> dict:
    with open(str(_FIXTURE_DIR / filename), encoding="utf-8") as f:
        return json.load(f)


# ===========================================================================
# Boundary facts — confirm the overflow the clamp must defend against is real
# ===========================================================================


def test_overflow_boundary_facts_hold():
    """
    Sanity-check the two facts the AC-8 clamp exists to defend against. If either
    of these stops being true (e.g. a scipy change), the fixture must be revisited.

    Fact 1: `1.0 - 1.0/N == 1.0` for N == 1e16 (IEEE-754 rounding).
    Fact 2: `norm.ppf(1.0) == +inf`.

    This test does NOT exercise the implementation — it pins the environment
    assumption the clamp is built on.
    """
    from scipy.stats import norm

    fixture = _load_fixture("norm_ppf_clamp_overflow.json")
    facts = fixture["verified_facts"]

    n_round = facts["one_minus_one_over_N_rounds_to_one_at"]
    assert (1.0 - 1.0 / n_round) == 1.0, (
        f"Boundary fact broken: 1 - 1/{n_round} no longer rounds to exactly 1.0. "
        f"The AC-8 fixture must be revisited."
    )

    assert norm.ppf(1.0) == float("inf"), (
        "Boundary fact broken: scipy.stats.norm.ppf(1.0) is no longer +inf. "
        "The AC-8 fixture must be revisited."
    )

    largest_below = math.nextafter(1.0, 0.0)
    assert largest_below == facts["largest_double_below_one"], (
        f"Boundary fact broken: math.nextafter(1.0, 0.0) is {largest_below!r}, "
        f"fixture says {facts['largest_double_below_one']!r}."
    )
    assert math.isfinite(norm.ppf(largest_below)), (
        "Boundary fact broken: norm.ppf of the largest double below 1.0 is not "
        "finite — no safe clamp ceiling exists."
    )


# ===========================================================================
# AC-8 — large n_trials does NOT overflow SR_0 to +inf
# ===========================================================================


@pytest.mark.parametrize("n_trials", [1e16, 1e18, 1e100, 1e300])
def test_large_n_trials_returns_finite_sr0(n_trials):
    """
    AC-8: for an n_trials large enough that an unclamped `1 - 1/N` (or
    `1 - 1/(N*e)`) rounds to 1.0, compute_expected_max_sharpe must still return a
    FINITE float.

    Without the clamp, `norm.ppf(1.0) = +inf` makes `SR_0 = +inf`. With the clamp,
    the ppf argument is capped strictly below 1.0 and the result is finite.

    The exact finite value is not pinned — it depends on the implementer's clamp
    ceiling. Only finiteness (and non-NaN) is the contract.
    """
    me = _import_math_engine()

    result = me.compute_expected_max_sharpe(
        sr_mean=0.0,
        sr_std=0.3,
        n_trials=int(n_trials),
    )

    assert isinstance(result, float), (
        f"compute_expected_max_sharpe must return a float for n_trials={n_trials:.0e}; "
        f"got {type(result).__name__}."
    )
    assert not math.isnan(result), (
        f"compute_expected_max_sharpe returned NaN for n_trials={n_trials:.0e}. "
        f"AC-8: the norm.ppf clamp must keep SR_0 finite, not NaN."
    )
    assert math.isfinite(result), (
        f"compute_expected_max_sharpe returned a non-finite value "
        f"({result!r}) for n_trials={n_trials:.0e}. AC-8: the norm.ppf arguments "
        f"must be clamped strictly below 1.0 so SR_0 cannot overflow to +inf."
    )


def test_clamped_huge_n_is_not_smaller_than_production_default_n():
    """
    AC-8: the clamp must CAP the selection-bias benchmark, not invert it.

    Expected-max Sharpe is non-decreasing in N (more trials -> more selection
    bias). A clamped huge-N SR_0 must therefore still be >= the SR_0 at the
    production default N=500. If the clamp produced a value BELOW the N=500 value,
    the clamp would be silently *reducing* the selection-bias correction — an
    anti-conservative bug.

    sr_mean=0.0 isolates the spread term (the part the clamp touches).
    """
    me = _import_math_engine()

    sr0_n500 = me.compute_expected_max_sharpe(sr_mean=0.0, sr_std=0.3, n_trials=500)
    sr0_huge = me.compute_expected_max_sharpe(
        sr_mean=0.0, sr_std=0.3, n_trials=10**18
    )

    assert math.isfinite(sr0_n500), "SR_0 at N=500 must be finite (pre-existing)."
    assert math.isfinite(sr0_huge), (
        "SR_0 at N=1e18 must be finite — AC-8 clamp."
    )
    assert sr0_huge >= sr0_n500 - 1e-9, (
        f"Clamped huge-N SR_0 ({sr0_huge!r}) is smaller than the N=500 SR_0 "
        f"({sr0_n500!r}). The clamp must CAP selection bias at large N, never "
        f"reduce it below a smaller-N value — that would be anti-conservative."
    )


def test_overflow_fixture_cases_all_finite():
    """
    AC-8: drive every n_trials in the fixture's overflow_inputs and assert finite.

    This binds the test set to the fixture (single source of truth for which N
    values are known to overflow an unclamped implementation).
    """
    me = _import_math_engine()
    fixture = _load_fixture("norm_ppf_clamp_overflow.json")

    inputs = fixture["overflow_inputs"]
    sr_mean = inputs["sr_mean"]
    sr_std = inputs["sr_std"]

    for n in inputs["n_trials_overflow_cases"]:
        result = me.compute_expected_max_sharpe(
            sr_mean=sr_mean, sr_std=sr_std, n_trials=int(n)
        )
        assert math.isfinite(result), (
            f"compute_expected_max_sharpe overflowed for n_trials={n:.0e} "
            f"(fixture overflow case). Got {result!r}; AC-8 requires a finite SR_0."
        )
