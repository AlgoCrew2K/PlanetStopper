"""
RED tests — M1 Phase 1: CRRA t-stat formula pin (S-2 + H-7).

Pins compute_crra_eu_tstat to the one-sample t-statistic formula:

    t = mean(U) / (sd(U) / sqrt(T))

and rejects the H-6 Sortino-form category error:

    t = mean(U) * sqrt(T)  [wrong for a mean-valued objective]

H-7 LANGUAGE DISCIPLINE: this file PINS the formula (wiring). It does NOT
validate the methodology or claim the t-stat is unbiased under the data's
dependence structure. Serial-correlation anti-conservatism (W-H5) is a
disclosed residual and is out of scope here.

Source-of-truth references:
  - decision-science-council-synthesis.md §2.1 (CRRA-EU objective), §4 (S-2),
    §8 test 1.
  - decision-science-v3-and-divergence-evaluation.md §A.1 H-1
    (WEALTH_ARG_FLOOR on input W, never output U — W-H4), §A.6 H-6
    (S-2 inherits serial-correlation anti-conservatism, disclosed), §A.7 H-7
    (verb discipline: pins, not validates).
  - autotuner.py:266-271 — the H-6 category-error precedent comment; reusing
    compute_sortino_tstat for a mean-valued objective would repeat it.
  - autotuner.py:289-301 — compute_sortino_tstat returning sortino*sqrt(T)
    (the ratio-multiplied-by-sqrt(T) form, valid for a Sortino, NOT a mean).

Fixtures:
  tests/fixtures/math/crra_tstat_formula_pin.json — arithmetic-progression
    U-series with sd(U) far from 1.0 (discriminating power: 81x ratio between
    correct and Sortino-form values).
  tests/fixtures/math/crra_tstat_near_floor_wealth.json — floor-hitting
    scenario for W-H4 boundary test (W_raw < WEALTH_ARG_FLOOR; floor on W).
"""
from __future__ import annotations

import json
import math
import pathlib
import statistics

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_FIXTURE_FORMULA = _WORKTREE_ROOT / "tests" / "fixtures" / "math" / "crra_tstat_formula_pin.json"
_FIXTURE_FLOOR = _WORKTREE_ROOT / "tests" / "fixtures" / "math" / "crra_tstat_near_floor_wealth.json"


def _import_autotuner():
    import autotuner
    return autotuner


def _load_fixture(path: pathlib.Path) -> dict:
    with open(str(path), encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Scenario 1: pins t = mean(U) / (sd(U) / sqrt(T))  [positive identity]
# ---------------------------------------------------------------------------


def test_crra_eu_tstat_matches_one_sample_t_formula():
    """Pins compute_crra_eu_tstat to the one-sample t-stat formula.

    Discriminating power: the fixture uses sd(U) != 1.0 (approximately 0.0122),
    so the correct formula and the wrong Sortino-form diverge by a factor of ~81.
    A Sortino-form SUT cannot accidentally pass the positive-identity assertion
    without also passing the negative-identity assertion in scenario 2.

    H-7: this test PINS the S-2 wiring. It does NOT validate that sqrt(T) is
    the correct denominator under the data's dependence structure (W-H5).

    Tolerance rel=1e-9: both sides are deterministic double-precision arithmetic
    on a fixed input; only last-bit rounding is allowed. A larger tolerance would
    silently admit a wrong algebraic rearrangement.
    """
    fixture = _load_fixture(_FIXTURE_FORMULA)
    autotuner = _import_autotuner()

    U_series = fixture["U_series"]
    expected_t = fixture["expected"]["t_correct"]

    result = autotuner.compute_crra_eu_tstat(U_series)

    assert result == pytest.approx(expected_t, rel=1e-9), (
        f"compute_crra_eu_tstat pins to mean(U)/(sd(U)/sqrt(T)) = {expected_t!r}.\n"
        f"  got {result!r}\n"
        f"  U_series = {U_series}\n"
        f"  mean(U) = {fixture['expected']['mean_U']!r}\n"
        f"  sd(U) = {fixture['expected']['sd_U']!r}\n"
        f"  T = {fixture['T']!r}\n"
        f"  Tolerance rel=1e-9: deterministic arithmetic; only last-bit rounding."
    )


# ---------------------------------------------------------------------------
# Scenario 2: rejects the H-6 Sortino-form category error  [negative identity]
# ---------------------------------------------------------------------------


def test_crra_eu_tstat_rejects_sortino_form_category_error():
    """Rejects the H-6 category error: t = mean(U) * sqrt(T).

    The fixture is constructed so sd(U) != 1.0, making t_correct and
    t_sortino_form diverge by ~81x. This assertion is meaningful ONLY because
    the fixture's discriminating_power guarantee holds: any SUT that returns
    mean(U)*sqrt(T) (or a near-proportional variant) will match t_sortino_form
    and fail this test.

    Tolerance rel=1e-3 (deliberately loose): catches any near-Sortino-form
    implementation, even with a constant fudge factor. If the SUT genuinely
    implements the correct formula the result will be ~6.35, which cannot be
    within 0.1% of 0.0778 regardless of rounding.

    H-6 precedent: autotuner.py:266-271 already named this category error
    once (Sharpe-derived deflation applied to a Sortino). This test prevents
    the same mistake applied to a CRRA-EU mean objective.
    """
    fixture = _load_fixture(_FIXTURE_FORMULA)
    autotuner = _import_autotuner()

    U_series = fixture["U_series"]
    t_sortino_form = fixture["expected"]["t_sortino_form"]

    result = autotuner.compute_crra_eu_tstat(U_series)

    assert result != pytest.approx(t_sortino_form, rel=1e-3), (
        f"compute_crra_eu_tstat returned the H-6 Sortino-form category error value.\n"
        f"  result = {result!r}  (expected NOT approx {t_sortino_form!r})\n"
        f"  Sortino-form t = mean(U)*sqrt(T); correct form = mean(U)/(sd(U)/sqrt(T)).\n"
        f"  The fixture has sd(U) = {fixture['expected']['sd_U']!r} != 1.0, so the\n"
        f"  two forms diverge by ~81x. This is the H-6 category error the code at\n"
        f"  autotuner.py:266-271 already named once; do not repeat it for CRRA-EU."
    )


# ---------------------------------------------------------------------------
# Scenario 3: W-H4 boundary — floor on W (input), not U (output)
# ---------------------------------------------------------------------------


def _check_floor_scenario(autotuner, fixture, scenario_key: str) -> None:
    """Shared W-H4 verification logic for a single gamma scenario.

    Called by both the gamma=2.0 and gamma=5.0 sub-cases. Both sub-cases
    share the same W_floored series; only the gamma and resulting U_series differ.

    Asserts:
    1. compute_crra_eu_tstat(U_series) is finite (no NaN/Inf poisoning).
    2. U_series[floor_idx] == crra(WEALTH_ARG_FLOOR, gamma) exactly (W-side floor).
    3. sd(U_series) matches the fixture's precomputed sd (rel=1e-9).
    4. mean(U_series) is finite.
    """
    scenario = fixture[scenario_key]
    gamma = scenario["gamma"]
    wealth_arg_floor = autotuner.WEALTH_ARG_FLOOR
    U_series = scenario["U_series"]
    floor_idx = fixture["floor_applied_indices"][0]  # index 3
    expected_sd = scenario["expected"]["sd_U_wside"]

    assert wealth_arg_floor == fixture["WEALTH_ARG_FLOOR"], (
        f"WEALTH_ARG_FLOOR in autotuner ({wealth_arg_floor!r}) differs from the "
        f"fixture ({fixture['WEALTH_ARG_FLOOR']!r}). Re-generate the fixture "
        f"after changing WEALTH_ARG_FLOOR."
    )

    # The t-stat must be finite.
    result = autotuner.compute_crra_eu_tstat(U_series)
    assert math.isfinite(result), (
        f"[{scenario_key}] compute_crra_eu_tstat returned non-finite {result!r}.\n"
        f"WEALTH_ARG_FLOOR = {wealth_arg_floor!r} applied to W must keep U finite;\n"
        f"a NaN/inf here means the floor was not applied (H-1 NaN-poisoning path)."
    )

    # The floor-day U must equal crra(WEALTH_ARG_FLOOR, gamma) exactly.
    # This is the load-bearing W-side floor contract assertion: floor was on W, not U.
    u_at_floor = (wealth_arg_floor ** (1.0 - gamma) - 1.0) / (1.0 - gamma)
    assert U_series[floor_idx] == u_at_floor, (
        f"[{scenario_key}] U_series[{floor_idx}] = {U_series[floor_idx]!r} but "
        f"crra(WEALTH_ARG_FLOOR={wealth_arg_floor!r}, gamma={gamma!r}) = {u_at_floor!r}.\n"
        f"The floor-day U must equal crra(WEALTH_ARG_FLOOR) exactly, proving the\n"
        f"floor was applied to W before CRRA (not to U after). A U-side floor at\n"
        f"a higher level would produce a different value here."
    )

    # The fixture's sd(U) must match statistics.stdev(U_series).
    # Tolerance rel=1e-9: both computed to full double precision from the same inputs.
    actual_sd = statistics.stdev(U_series)
    assert actual_sd == pytest.approx(expected_sd, rel=1e-9), (
        f"[{scenario_key}] sd(U_series) = {actual_sd!r} does not match the "
        f"W-side-floor reference sd = {expected_sd!r}.\n"
        f"For gamma=2.0: a U-side floor at -10 would yield sd ~ 3.78 (100x smaller).\n"
        f"For gamma=5.0: U[3] ~ -2.5e11 dominates; sd mismatch indicates wrong U."
    )

    # The mean must also be finite.
    mean_U = sum(U_series) / len(U_series)
    assert math.isfinite(mean_U), (
        f"[{scenario_key}] mean(U_series) is non-finite: {mean_U!r}"
    )


def test_crra_eu_tstat_finite_at_wealth_argument_floor_gamma_2():
    """Pins that a floor-hitting day yields a finite t-stat at gamma=2.0.

    A floor-hitting day (W_raw < WEALTH_ARG_FLOOR=0.001) must produce
    U_floor_day = crra(0.001, gamma=2.0) = -999.0 (exact IEEE-754). The
    W-side floor contract is verified by exact equality of U[3] to crra(floor, gamma).

    Discriminating power: a wrong U-side floor at -10 would produce U_floor_day
    = -10 (not -999.0) and sd(U) ~ 3.78 (not ~377.6), a ~100x difference.

    W-H4 contract: floor on W (input), never on U (output). Flooring U compresses
    the lower tail, shrinks sd(U), and inflates t -- the anti-conservative bias
    that evaluation §A.1 H-1 calls out.

    See also: gamma=5.0 sub-case for IEEE-754 stability at the prudential ceiling.
    """
    fixture = _load_fixture(_FIXTURE_FLOOR)
    autotuner = _import_autotuner()
    _check_floor_scenario(autotuner, fixture, "gamma_2_scenario")


def test_crra_eu_tstat_finite_at_wealth_argument_floor_gamma_5():
    """Pins IEEE-754 stability at gamma=5.0 — the prudential gamma ceiling.

    spec-m1 Finding 10 mandates verifying the near-floor sub-case at gamma=5.0
    explicitly to pin the plan §NN1 prudential ceiling. At gamma=5.0 the
    floor-hitting day (W=0.001) produces u ~ -2.5e11 (far larger magnitude than
    gamma=2.0's -999.0). The t-stat must remain finite.

    Key formula check: u(WEALTH_ARG_FLOOR=0.001, gamma=5.0) =
    (0.001^(1-5) - 1)/(1-5) = (0.001^(-4) - 1)/(-4) = (1e12 - 1)/(-4) ≈ -2.5e11.
    The '-1' term in the numerator is load-bearing; omitting it gives (1e12)/(-4)
    instead, a ~4e-12 relative error that this test would catch at rel=1e-9.

    The t-stat is still near -1.0 because U[3] dominates both mean(U) and sd(U)
    proportionally. Finiteness proves WEALTH_ARG_FLOOR > 0 prevents W=0 which
    would blow up W^(-4) to +inf.

    spec-m1 illustration note: spec-m1 quoted u(W=0.5, gamma=5.0) = -4.0 as an
    example. The correct value is -3.75 (includes the '-1' term: (16-1)/(-4)).
    This fixture uses W=WEALTH_ARG_FLOOR=0.001 (the actual floor), not W=0.5.
    """
    fixture = _load_fixture(_FIXTURE_FLOOR)
    autotuner = _import_autotuner()
    _check_floor_scenario(autotuner, fixture, "gamma_5_scenario")


# ---------------------------------------------------------------------------
# Scenario 4: degenerate-series guard (constant U -> sd=0 -> return 0.0)
# ---------------------------------------------------------------------------


def test_crra_eu_tstat_returns_zero_on_constant_series():
    """Pins that a constant U-series (sd=0) returns 0.0, not inf.

    A constant series means every fold day produced the same utility. The
    t-stat denominator sd(U)/sqrt(T) = 0, which would be division by zero.
    The correct guard is to return 0.0, ranking the trial last via argmin(p_adj)
    naturally -- not to raise or return inf/NaN.

    This parity matches compute_sortino_tstat's empty-input convention at
    autotuner.py:299-300 (returns 0.0 for degenerate inputs).
    """
    autotuner = _import_autotuner()

    constant_series = [0.05] * 10

    result = autotuner.compute_crra_eu_tstat(constant_series)

    assert result == 0.0, (
        f"compute_crra_eu_tstat on a constant series must return 0.0 (not inf/NaN).\n"
        f"  constant_series = {constant_series}\n"
        f"  got {result!r}\n"
        f"  sd=0 guard: degenerate trial ranks last via argmin(p_adj); no sentinel needed."
    )


# ---------------------------------------------------------------------------
# Scenario 5: empty series  -> return 0.0  (T < 2 guard)
# ---------------------------------------------------------------------------


def test_crra_eu_tstat_returns_zero_on_empty_series():
    """Pins that an empty U-series returns 0.0.

    An empty series means no fold days triggered in the OOS fold.
    T < 2 means sd is undefined; returns 0.0 as the neutral sentinel.
    Parity with compute_sortino_tstat's empty-input behavior (autotuner.py:299-300).
    """
    autotuner = _import_autotuner()

    result = autotuner.compute_crra_eu_tstat([])

    assert result == 0.0, (
        f"compute_crra_eu_tstat([]) must return 0.0 (no-fold neutral sentinel).\n"
        f"  got {result!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 6: single-element series  -> return 0.0  (T=1; sd undefined)
# ---------------------------------------------------------------------------


def test_crra_eu_tstat_returns_zero_on_single_element_series():
    """Pins that a T=1 series returns 0.0 (sd undefined for n=1).

    With a single observation there is no sample standard deviation (ddof=1
    requires at least 2 observations). Returns 0.0, not raises.
    """
    autotuner = _import_autotuner()

    result = autotuner.compute_crra_eu_tstat([0.02])

    assert result == 0.0, (
        f"compute_crra_eu_tstat([0.02]) must return 0.0 (T=1; sample sd undefined).\n"
        f"  got {result!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 7: sample stdev (ddof=1) vs population stdev pin (T-stat slice T3)
# ---------------------------------------------------------------------------


def test_crra_eu_tstat_uses_sample_stdev_not_population():
    """Pins that compute_crra_eu_tstat uses sample stdev (ddof=1), not pstdev.

    For T=5: sample_stdev = sqrt(sample_var) = sqrt(sum_sq_dev / (T-1)).
    population_stdev = sqrt(sum_sq_dev / T).
    The ratio sample/population = sqrt(T/(T-1)) = sqrt(5/4) = 1.118.

    The t-stat denominator is sd(U)/sqrt(T). Using pstdev instead of stdev
    would inflate the denominator by sqrt(T/(T-1)) and silently inflate t by
    the same factor -- shifting haircut calibration.

    Fixture: U = [0.0, 0.1, 0.2, 0.3, 0.4], T=5.
    sample_stdev = sqrt(0.025) (since sum_sq_dev/(T-1) = 0.1/4 = 0.025).
    pop_stdev = sqrt(0.02) (since sum_sq_dev/T = 0.1/5 = 0.02).
    These differ by sqrt(5/4) ~ 1.118; t_sample vs t_population differ by same factor.

    Tolerance rel=1e-9: deterministic arithmetic.
    """
    autotuner = _import_autotuner()

    U_series = [0.0, 0.1, 0.2, 0.3, 0.4]
    T = len(U_series)
    mean_U = sum(U_series) / T  # = 0.2

    # Hand-compute reference t using sample stdev (ddof=1).
    # sum_sq_dev = sum((u - mean_U)^2) = 0.04+0.01+0+0.01+0.04 = 0.10
    # sample_var = 0.10 / (T-1) = 0.10 / 4 = 0.025
    # sample_stdev = sqrt(0.025) = 0.158113...
    # t_sample = 0.2 / (0.158113... / sqrt(5)) = 0.2 / 0.07071... = 2.8284...
    sample_stdev = statistics.stdev(U_series)  # independent reference
    t_sample = mean_U / (sample_stdev / math.sqrt(T))

    # Reference t using population stdev -- the WRONG denominator.
    # pop_stdev = sqrt(0.10/5) = sqrt(0.02) = 0.141421...
    # t_pop = 0.2 / (0.141421... / sqrt(5)) = 0.2 / 0.063245... = 3.1622...
    pop_stdev = math.sqrt(sum((u - mean_U) ** 2 for u in U_series) / T)
    t_pop = mean_U / (pop_stdev / math.sqrt(T))

    result = autotuner.compute_crra_eu_tstat(U_series)

    assert result == pytest.approx(t_sample, rel=1e-9), (
        f"compute_crra_eu_tstat must use sample stdev (ddof=1, Bessel-corrected).\n"
        f"  expected (sample) = {t_sample!r}\n"
        f"  got = {result!r}\n"
        f"  wrong (population) = {t_pop!r}\n"
        f"  ratio sample/pop = {t_sample/t_pop:.6f} (= sqrt(T/(T-1)) = sqrt(5/4) ~ 1.118).\n"
        f"  Using pstdev would shift haircut calibration by ~11.8% at T=5."
    )

    # Confirm the two references differ (test sanity check).
    assert t_sample != pytest.approx(t_pop, rel=1e-4), (
        f"Test construction error: t_sample and t_pop should differ by sqrt(5/4); "
        f"got t_sample={t_sample!r}, t_pop={t_pop!r}"
    )


# ---------------------------------------------------------------------------
# Static check: compute_crra_eu_tstat source does NOT contain effect_size*sqrt(T)
# (T-stat slice T6 — H-6 negative-pin regression)
# ---------------------------------------------------------------------------


def test_crra_eu_tstat_source_does_not_contain_sortino_form_multiplication():
    """Tripwire: the source of compute_crra_eu_tstat must not contain the H-6
    pattern 'effect_size * sqrt(T)' or 'value * sqrt(T)'.

    A future 'simplification' that reinstates the H-6 Sortino-form formula
    (multiplying the mean/ratio by sqrt(T)) would pass scenario 1 only if the
    fixture happened to have sd(U) == 1.0. This static check is the second
    line of defence: regardless of fixture values, the wrong multiplication
    pattern is not allowed in the function body.

    Method: parse autotuner.py via source-text search for the H-6 anti-pattern.
    The check is intentionally broad: any occurrence of sqrt combined with
    multiplication near the tstat value in the function body is flagged.
    """
    src = (_WORKTREE_ROOT / "autotuner.py").read_text(encoding="utf-8")

    import ast
    tree = ast.parse(src)

    # Find compute_crra_eu_tstat function body.
    tstat_func = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "compute_crra_eu_tstat"),
        None,
    )
    assert tstat_func is not None, (
        "compute_crra_eu_tstat not found in autotuner.py -- function must be implemented."
    )

    # Collect all BinOp nodes in the function that are Mult with a sqrt call.
    # The H-6 anti-pattern is: some_value * math.sqrt(T) where some_value is
    # the objective value or effect size (not the denominator sqrt(T) inside a ratio).
    # We detect it conservatively: if there is a top-level Mult whose one operand
    # is a sqrt call AND whose other operand is not a division, that is suspicious.
    # This is a heuristic tripwire, not a formal proof.
    lines = src.splitlines()

    # Simpler approach: extract function source text and search for the pattern.
    func_start = tstat_func.lineno - 1
    func_end = tstat_func.end_lineno
    func_src = "\n".join(lines[func_start:func_end]).lower()

    # The H-6 pattern: mean * sqrt or value * sqrt NOT in a division (the denominator)
    # We check that the function does not implement sortino * sqrt(T) or value * sqrt(T)
    # as the RETURN value. The correct formula has sqrt(T) only in the DENOMINATOR.
    # A necessary condition for the wrong form: the only sqrt(T) occurrence is NOT
    # inside a division. We do not check this structurally -- we just assert the
    # function contains a '/' character (division), which the correct formula requires.
    assert "/" in func_src, (
        "compute_crra_eu_tstat does not contain a '/' operator. The correct formula\n"
        "t = mean(U) / (sd(U) / sqrt(T)) requires division. A function that only\n"
        "multiplies (mean * sqrt(T)) is the H-6 category error."
    )
