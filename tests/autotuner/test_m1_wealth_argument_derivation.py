"""
RED tests — M1 Phase 1: wealth-argument derivation (W-H2 + W-H4 floor).

Pins the W-H2 derivation formula:

    W_i = max(WEALTH_ARG_FLOOR, 1 + r_i_policy_fraction)
    where r_i_policy_fraction = (guard_alpha_i_pct + eod_return_i_pct) / RETURN_PCT_TO_FRACTION

AND the W-H4 contract: floor applied to input W, NEVER to output U.

Source-of-truth references:
  - decision-science-council-synthesis.md §3.9 W-H2, §4 S-2.
  - decision-science-v3-and-divergence-evaluation.md §A.1 H-1 (W-H4 floor).
  - tests/fixtures/m1-wealth-argument/derivation-fixture.json (critic-ratified
    W-H2 golden fixture; THIS test file consumes it).

Fixture provenance (D-2):
  tests/fixtures/m1-wealth-argument/derivation-fixture.json is the W-H2
  derivation memo itself and is the canonical producer. This test file is the
  consumer. No values are derived from the SUT under test.

Three named module-scope constants required by the SUT (no-magic-numbers rule):
  WEALTH_ARG_FLOOR         = 0.001
  RETURN_PCT_TO_FRACTION   = 100.0
  CRRA_LOG_UTILITY_GAMMA_TOL  = 1e-9  (tolerance for gamma == 1 branch)
"""
from __future__ import annotations

import json
import math
import pathlib
import statistics

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_W_H2_FIXTURE = (
    _WORKTREE_ROOT / "tests" / "fixtures" / "m1-wealth-argument" / "derivation-fixture.json"
)


def _import_autotuner():
    import autotuner
    return autotuner


def _load_wh2_fixture() -> dict:
    with open(str(_W_H2_FIXTURE), encoding="utf-8") as fh:
        return json.load(fh)


def _get_wealth_arg_floor():
    """Return WEALTH_ARG_FLOOR from whichever module exports it.

    The plan §Deliverables says 'Module-scope constants in math_engine.py:
    WEALTH_ARG_FLOOR'. A transitional or alternative placement in autotuner.py
    is also accepted. This accessor checks both, preferring math_engine to
    match the plan's canonical location, and raises clearly if neither exports it.
    """
    import autotuner
    import math_engine
    if hasattr(math_engine, "WEALTH_ARG_FLOOR"):
        return math_engine.WEALTH_ARG_FLOOR
    if hasattr(autotuner, "WEALTH_ARG_FLOOR"):
        return autotuner.WEALTH_ARG_FLOOR
    raise AttributeError(
        "WEALTH_ARG_FLOOR not found in math_engine or autotuner. "
        "Plan §Deliverables: 'Module-scope constants in math_engine.py: WEALTH_ARG_FLOOR'. "
        "The constant must be defined in at least one of these modules."
    )


# ---------------------------------------------------------------------------
# Constant-provenance check: fixture constants match SUT at import time
# (binding requirement from red-test-2 plan §Definition of Done bullet 4)
# ---------------------------------------------------------------------------


def test_fixture_constants_match_sut_constants():
    """Pins that the SUT exports WEALTH_ARG_FLOOR, RETURN_PCT_TO_FRACTION, and
    CRRA_LOG_UTILITY_GAMMA_TOL at module scope AND that the values match the
    W-H2 fixture's recorded values.

    Red-test-2 plan §DoD bullet 4: 'fixture-load asserts presence of gamma,
    WEALTH_ARG_FLOOR, AND RETURN_PCT_TO_FRACTION'.

    If the SUT changes any constant the fixture becomes stale and this test
    catches the drift before the fixture's pre-computed U values are consumed
    by the downstream tests.
    """
    autotuner = _import_autotuner()
    import math_engine
    fixture = _load_wh2_fixture()

    # WEALTH_ARG_FLOOR — plan §Deliverables: 'Module-scope constants in math_engine.py:
    # WEALTH_ARG_FLOOR'. A placement in autotuner.py is also accepted (transition).
    has_floor = hasattr(math_engine, "WEALTH_ARG_FLOOR") or hasattr(autotuner, "WEALTH_ARG_FLOOR")
    assert has_floor, (
        "WEALTH_ARG_FLOOR not found in math_engine or autotuner. "
        "Plan §Deliverables requires this constant in math_engine.py (or autotuner.py "
        "as a transitional placement). W-H4 + project no-magic-numbers rule."
    )
    sut_floor = _get_wealth_arg_floor()
    fixture_floor = fixture["WEALTH_ARG_FLOOR"]["value"]
    assert sut_floor == pytest.approx(fixture_floor, rel=1e-12), (
        f"WEALTH_ARG_FLOOR = {sut_floor!r} differs from "
        f"the W-H2 fixture value {fixture_floor!r}. Update the fixture or the constant."
    )

    # RETURN_PCT_TO_FRACTION
    assert hasattr(autotuner, "RETURN_PCT_TO_FRACTION"), (
        "autotuner.py must export RETURN_PCT_TO_FRACTION as a named module-scope constant "
        "(W-H2 unit-conversion boundary; critic C-1 blocker)."
    )
    fixture_rptf = fixture["selected_wealth_argument_formula"]["unit_conversion_constant"]["value"]
    assert autotuner.RETURN_PCT_TO_FRACTION == pytest.approx(fixture_rptf, rel=1e-12), (
        f"autotuner.RETURN_PCT_TO_FRACTION = {autotuner.RETURN_PCT_TO_FRACTION!r} differs "
        f"from the W-H2 fixture value {fixture_rptf!r}."
    )

    # CRRA_LOG_UTILITY_GAMMA_TOL (may live in math_engine instead of autotuner)
    has_tol = hasattr(autotuner, "CRRA_LOG_UTILITY_GAMMA_TOL") or hasattr(
        math_engine, "CRRA_LOG_UTILITY_GAMMA_TOL"
    )
    assert has_tol, (
        "CRRA_LOG_UTILITY_GAMMA_TOL must be defined as a named module-scope constant "
        "in autotuner.py or math_engine.py (project no-magic-numbers rule; W-H2 fixture "
        "log_branch_tolerance_constant_name = 'CRRA_LOG_UTILITY_GAMMA_TOL')."
    )


# ---------------------------------------------------------------------------
# Scenario 1 (W-H2 pin): derive_wealth_argument matches formula on ex1-ex4
# ---------------------------------------------------------------------------


def test_wealth_argument_matches_design_formula():
    """Pins derive_wealth_argument against W-H2 fixture examples ex1-ex4.

    For each worked example where floor was NOT applied (floor_applied == False),
    asserts W_i == 1.0 + r_policy_fraction element-wise at tolerance 1e-12.

    The formula: W_i = 1 + r_i_policy_fraction where
        r_i_policy_fraction = r_policy_pct / RETURN_PCT_TO_FRACTION.
    This is the W-H2 closed-form derivation (per_period_gross_wealth_ratio).

    Tolerance rel=1e-12: both sides are pure double-precision arithmetic on a
    fixed input; only last-bit rounding is acceptable.
    """
    autotuner = _import_autotuner()
    fixture = _load_wh2_fixture()

    examples = [ex for ex in fixture["worked_examples"] if not ex["floor_applied"]]
    assert examples, "Fixture must have at least one non-floor worked example."

    for ex in examples:
        r_policy_pct = ex["inputs"]["r_policy"]  # decimal-fraction frame in fixture
        expected_W_raw = ex["W_raw"]

        # Call the SUT's wealth-argument deriver.
        # Contract: derive_wealth_argument(r_policy_fraction) -> float > 0
        # The fixture already provides r_policy in decimal-fraction units
        # (not percent); W = 1 + r_policy_fraction.
        W = autotuner.derive_wealth_argument(r_policy_pct)

        assert W == pytest.approx(expected_W_raw, rel=1e-12), (
            f"Wealth argument mismatch for fixture example {ex['id']!r}.\n"
            f"  r_policy (decimal-fraction) = {r_policy_pct!r}\n"
            f"  expected W_raw = {expected_W_raw!r}\n"
            f"  got W = {W!r}\n"
            f"  Formula: W = 1 + r_policy_fraction (W-H2 per_period_gross_wealth_ratio)."
        )
        # Positivity invariant: raw W is positive for any realistic r_policy.
        assert W > 0, (
            f"derive_wealth_argument returned non-positive W = {W!r} for "
            f"r_policy = {r_policy_pct!r}. CRRA domain requires W > 0."
        )


# ---------------------------------------------------------------------------
# Scenario 2 (W-H4 positive identity): floor applied at WEALTH_ARG_FLOOR exactly
# ---------------------------------------------------------------------------


def test_wealth_argument_floors_input_W_at_constant():
    """Pins that a floor-hitting input is clamped to exactly WEALTH_ARG_FLOOR.

    Uses fixture ex5 (r_policy = -0.9999, W_raw = 0.0001 < WEALTH_ARG_FLOOR = 0.001).
    Asserts W_floored == WEALTH_ARG_FLOOR as exact equality (not approx) because
    the floor is an exact conditional assignment, not floating-point arithmetic.

    Also asserts non-floor examples are unchanged by the floor call (no always-on floor).
    """
    autotuner = _import_autotuner()
    fixture = _load_wh2_fixture()
    wealth_arg_floor = _get_wealth_arg_floor()

    # Ex5: floor-hitting day
    ex5 = next(ex for ex in fixture["worked_examples"] if ex["floor_applied"])
    r_policy_ex5 = ex5["inputs"]["r_policy"]  # decimal-fraction
    expected_W_floored = ex5["W_floored"]

    W_floored = autotuner.derive_floored_wealth_argument(r_policy_ex5)

    assert W_floored == wealth_arg_floor, (
        f"derive_floored_wealth_argument for a floor-hitting day must return exactly "
        f"WEALTH_ARG_FLOOR = {wealth_arg_floor!r}.\n"
        f"  r_policy (decimal-fraction) = {r_policy_ex5!r}\n"
        f"  expected W_floored = {expected_W_floored!r}\n"
        f"  got W_floored = {W_floored!r}\n"
        f"  Exact equality (not approx): floor is an assignment, not arithmetic."
    )

    # Verify fixture ex5 value matches WEALTH_ARG_FLOOR constant.
    assert expected_W_floored == wealth_arg_floor, (
        f"Fixture ex5.W_floored ({expected_W_floored!r}) != WEALTH_ARG_FLOOR "
        f"({wealth_arg_floor!r}). The fixture is stale; re-generate."
    )

    # Non-floor examples: derive_floored_wealth_argument must equal derive_wealth_argument.
    for ex in [e for e in fixture["worked_examples"] if not e["floor_applied"]]:
        r_policy = ex["inputs"]["r_policy"]
        W_raw = autotuner.derive_wealth_argument(r_policy)
        W_floored_ex = autotuner.derive_floored_wealth_argument(r_policy)
        assert W_floored_ex == pytest.approx(W_raw, rel=1e-12), (
            f"derive_floored_wealth_argument({r_policy!r}) = {W_floored_ex!r} but "
            f"derive_wealth_argument = {W_raw!r}. For W > WEALTH_ARG_FLOOR the floor "
            f"must leave W unchanged."
        )


# ---------------------------------------------------------------------------
# Scenario 3 (W-H4 anti-conservatism guard): floor on W, not U
# ---------------------------------------------------------------------------


def test_floor_is_applied_to_W_not_to_U():
    """Pins that a floor-hitting day produces U = crra(WEALTH_ARG_FLOOR, gamma),
    NOT a clipped-at-higher-level U. This is the W-H4 discriminating test.

    Method: for ex5, compute the CRRA U from the floored W directly.
    Assert U_floor_day == crra(WEALTH_ARG_FLOOR, gamma) exactly.
    A wrong U-side floor (e.g. clip U at -10 or at 0) would produce a different
    value and shrink sd(U) by ~100x -- the anti-conservative inflation.

    Also cross-check: compute_crra_utility(WEALTH_ARG_FLOOR, gamma) is imported
    from the SUT and equals the fixture's expected value.
    """
    autotuner = _import_autotuner()
    import math_engine
    fixture = _load_wh2_fixture()
    wealth_arg_floor = _get_wealth_arg_floor()

    ex5 = next(ex for ex in fixture["worked_examples"] if ex["floor_applied"])
    gamma_str = "gamma_2.0"
    gamma = 2.0
    expected_U_ex5 = ex5["U_by_gamma"][gamma_str]["value"]
    tol = ex5["U_by_gamma"][gamma_str]["tolerance_for_test"]

    # U must equal crra(WEALTH_ARG_FLOOR, gamma) when floor was applied.
    u_at_floor = math_engine.compute_crra_utility(wealth_arg_floor, gamma)

    assert u_at_floor == pytest.approx(expected_U_ex5, abs=tol), (
        f"compute_crra_utility(WEALTH_ARG_FLOOR={wealth_arg_floor!r}, gamma=2.0) = {u_at_floor!r}, "
        f"expected (W-H2 fixture ex5) = {expected_U_ex5!r} (tol abs={tol!r}).\n"
        f"Formula: u(W; 2.0) = (W^(-1) - 1)/(-1) = 1 - 1/W. "
        f"At W=0.001: u = 1 - 1000 = -999.0."
    )

    # Confirm: u(WEALTH_ARG_FLOOR) != 0.0 and != -10.0 (common wrong floor levels)
    assert u_at_floor != pytest.approx(0.0, abs=1.0), (
        f"compute_crra_utility(WEALTH_ARG_FLOOR, 2.0) = {u_at_floor!r}; "
        f"expected ~-999.0. A U-side floor at 0 would be detected here."
    )
    assert u_at_floor != pytest.approx(-10.0, abs=1.0), (
        f"compute_crra_utility(WEALTH_ARG_FLOOR, 2.0) = {u_at_floor!r}; "
        f"expected ~-999.0. A U-side floor at -10 would be detected here."
    )


# ---------------------------------------------------------------------------
# Scenario 4 (W-H4 downstream): t-stat finite with floor-hitting day
# ---------------------------------------------------------------------------


def test_crra_tstat_is_finite_with_floor_hitting_day():
    """Pins that the downstream t-stat is finite when a series contains a floor-hitting day.

    Uses all 5 fixture examples (ex1-ex4 normal, ex5 floor-hitting) to form a
    5-element U-series. The t-stat via compute_crra_eu_tstat must be finite.
    Also asserts compute_haircut_pvalue(t) is in the open interval
    (_HAIRCUT_PVALUE_EPSILON, 1 - _HAIRCUT_PVALUE_EPSILON) -- not silently saturated.
    This proves the floor short-circuited the H-1 NaN-poisoning path.
    """
    autotuner = _import_autotuner()
    import math_engine
    fixture = _load_wh2_fixture()

    gamma = 2.0
    gamma_key = "gamma_2.0"

    U_series = [ex["U_by_gamma"][gamma_key]["value"] for ex in fixture["worked_examples"]]
    assert len(U_series) == 5

    t = autotuner.compute_crra_eu_tstat(U_series)
    assert math.isfinite(t), (
        f"compute_crra_eu_tstat on a floor-containing U-series returned non-finite {t!r}.\n"
        f"U_series = {U_series}\n"
        f"This indicates the floor failed to prevent NaN/inf propagation (H-1)."
    )

    mean_U = sum(U_series) / len(U_series)
    sd_U = statistics.stdev(U_series)
    assert math.isfinite(mean_U), f"mean(U) is non-finite: {mean_U!r}"
    assert math.isfinite(sd_U), f"sd(U) is non-finite: {sd_U!r}"

    p = autotuner.compute_haircut_pvalue(t)
    eps = autotuner._HAIRCUT_PVALUE_EPSILON
    assert eps < p < (1.0 - eps), (
        f"compute_haircut_pvalue({t!r}) = {p!r} is outside the open interval "
        f"({eps!r}, {1.0 - eps!r}). A NaN/inf t would saturate the clamp; "
        f"the floor must have prevented it."
    )


# ---------------------------------------------------------------------------
# Scenario 5 (no-floor negative identity): floor passive when all W > WEALTH_ARG_FLOOR
# ---------------------------------------------------------------------------


def test_no_floor_applied_when_all_W_above_floor():
    """Pins that derive_floored_wealth_argument == derive_wealth_argument when
    no day breaches the floor.

    A buggy 'always replace with floor' implementation would fail this scenario.
    Uses ex1-ex4 which all have floor_applied == False.

    Tolerance rel=1e-12: deterministic arithmetic.
    """
    autotuner = _import_autotuner()
    fixture = _load_wh2_fixture()

    normal_examples = [ex for ex in fixture["worked_examples"] if not ex["floor_applied"]]
    assert len(normal_examples) >= 4, "Fixture must have >= 4 non-floor examples."

    wealth_arg_floor = _get_wealth_arg_floor()

    for ex in normal_examples:
        r_policy = ex["inputs"]["r_policy"]
        W_raw = autotuner.derive_wealth_argument(r_policy)
        W_floored = autotuner.derive_floored_wealth_argument(r_policy)

        assert W_raw > wealth_arg_floor, (
            f"Test construction: ex {ex['id']!r} should have W_raw > WEALTH_ARG_FLOOR. "
            f"W_raw = {W_raw!r}"
        )

        assert W_floored == pytest.approx(W_raw, rel=1e-12), (
            f"derive_floored_wealth_argument({r_policy!r}) = {W_floored!r} differs "
            f"from derive_wealth_argument = {W_raw!r} even though W > WEALTH_ARG_FLOOR.\n"
            f"  ex = {ex['id']!r}\n"
            f"  An always-on floor would set W = WEALTH_ARG_FLOOR = "
            f"{wealth_arg_floor!r} regardless."
        )


# ---------------------------------------------------------------------------
# Closed-form verification: fixture ex1-ex5 U values match crra(W_floored, gamma)
# ---------------------------------------------------------------------------


def test_fixture_u_values_match_crra_formula():
    """Cross-checks the fixture's U_by_gamma values against compute_crra_utility.

    The W-H2 fixture is the canonical spec; the SUT must match it. This test
    verifies the CRRA formula implementation (not just the floor) by cross-checking
    all 5 examples at gamma in {0.5, 1.0, 2.0}.

    Tolerance per fixture's tolerance_for_test field (1e-12 to 1e-14).
    """
    import math_engine
    fixture = _load_wh2_fixture()

    gamma_map = {
        "gamma_0.5": 0.5,
        "gamma_1.0": 1.0,
        "gamma_2.0": 2.0,
    }

    for ex in fixture["worked_examples"]:
        W_floored = ex["W_floored"]
        for gamma_key, gamma_val in gamma_map.items():
            if gamma_key not in ex["U_by_gamma"]:
                continue
            expected_U = ex["U_by_gamma"][gamma_key]["value"]
            tol = ex["U_by_gamma"][gamma_key]["tolerance_for_test"]
            computed_U = math_engine.compute_crra_utility(W_floored, gamma_val)

            assert computed_U == pytest.approx(expected_U, abs=tol), (
                f"compute_crra_utility({W_floored!r}, gamma={gamma_val!r}) mismatch.\n"
                f"  ex = {ex['id']!r} ({ex['scenario']!r})\n"
                f"  expected U = {expected_U!r} (formula: {ex['U_by_gamma'][gamma_key]['formula']!r})\n"
                f"  got U = {computed_U!r}\n"
                f"  tolerance abs={tol!r}"
            )


# ---------------------------------------------------------------------------
# Caller-discipline documentation (spec-m1 Finding 2, scoped per rev-m1)
# ---------------------------------------------------------------------------


def test_compute_crra_utility_unfloored_near_zero_returns_large_negative():
    """Documents caller discipline: compute_crra_utility does NOT re-floor W.

    An unfloored near-zero W (e.g. W=0.0001, below WEALTH_ARG_FLOOR=0.001) must
    return a large-magnitude finite negative value — NOT -inf and NOT the same
    value as crra(WEALTH_ARG_FLOOR, gamma).

    Scope clarification per rev-m1: -inf only occurs at exactly W=0.0. At
    W=0.0001 and gamma=2.0, the result is (0.0001^(-1) - 1)/(-1) = 1 - 10000
    = -9999.0 (finite, large-magnitude). The test asserts:
    1. result is finite (not -inf, not NaN)
    2. result < -100.0 (large-magnitude negative — the expected CRRA penalty)
    3. result != crra(WEALTH_ARG_FLOOR, gamma) (proves the function does NOT
       silently re-floor W to WEALTH_ARG_FLOOR before computing)

    If compute_crra_utility silently re-floored W=0.0001 to WEALTH_ARG_FLOOR=0.001,
    it would return crra(0.001, 2.0) = -999.0. Assertion 3 catches this.
    The caller MUST apply WEALTH_ARG_FLOOR before calling this function.

    This test is in RED because the NaN-guard series (test_compute_crra_utility_
    rejects_nan_input etc.) only verifies that NaN/Inf inputs are rejected.
    This test verifies that a small-but-valid W produces the right large-negative
    value (the unsafe caller discipline path — intentionally unsafe, not guarded).
    """
    import math_engine

    W_unfloored = 0.0001  # below WEALTH_ARG_FLOOR=0.001; valid float > 0
    gamma = 2.0

    result = math_engine.compute_crra_utility(W_unfloored, gamma)

    # 1. Must be finite (not -inf — W=0.0001 > 0 so CRRA is defined and finite).
    assert math.isfinite(result), (
        f"compute_crra_utility({W_unfloored!r}, gamma={gamma!r}) = {result!r}; "
        f"expected a finite large-negative value. "
        f"W=0.0001 is a valid positive float; -inf only occurs at W=0.0. "
        f"A non-finite result here means the function incorrectly extrapolates to W<=0."
    )

    # 2. Must be a large-magnitude negative (CRRA penalty for near-zero wealth).
    # At W=0.0001, gamma=2.0: u = (0.0001^(-1) - 1)/(-1) = 1 - 10000 = -9999.0
    assert result < -100.0, (
        f"compute_crra_utility({W_unfloored!r}, gamma={gamma!r}) = {result!r}; "
        f"expected < -100.0 (specifically ~ -9999.0). "
        f"Formula: u = (W^(-1) - 1)/(-1) = 1 - 1/W = 1 - 10000 = -9999.0. "
        f"A U-side floor clipping at -10 would return -10.0 and fail this assertion."
    )

    # 3. Must NOT equal crra(WEALTH_ARG_FLOOR, gamma): proves no silent re-flooring.
    # crra(0.001, 2.0) = -999.0; crra(0.0001, 2.0) = -9999.0. These differ by 10x.
    wealth_arg_floor = _get_wealth_arg_floor()
    floor_U = math_engine.compute_crra_utility(wealth_arg_floor, gamma)
    assert result != pytest.approx(floor_U, rel=1e-3), (
        f"compute_crra_utility({W_unfloored!r}, gamma={gamma!r}) = {result!r} "
        f"matches crra(WEALTH_ARG_FLOOR={wealth_arg_floor!r}, gamma={gamma!r}) = {floor_U!r}.\n"
        f"These should differ by ~10x. A result matching floor_U means the function "
        f"silently re-floors W to WEALTH_ARG_FLOOR before computing (caller discipline violated).\n"
        f"The caller MUST apply the floor; compute_crra_utility must NOT."
    )
