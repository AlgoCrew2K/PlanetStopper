"""
R5 — Full Bailey & López de Prado 2014 Expected-Max-SR Correction for DSR: RED tests.

These tests are written BEFORE the implementation. They will ALL FAIL until:
  1. math_engine.py exposes compute_expected_max_sharpe(sr_mean, sr_std, n_trials)
  2. autotuner.py calls compute_expected_max_sharpe to derive SR_0 instead of SR_0=0.0

Reference:
  Bailey, D.H. & López de Prado, M. (2014). "The Deflated Sharpe Ratio:
  Correcting for Selection Bias, Backtest Overfitting, and Non-Normality."
  Journal of Portfolio Management, 40(5), 94-107.
  DOI: 10.3905/jpm.2014.40.5.094. Equation 9 + expected-maximum-SR appendix.

  Expected-maximum Sharpe formula (paper appendix):
    SR_0 = mu + sigma * ((1 - gamma_E) * Phi^-1(1 - 1/N) + gamma_E * Phi^-1(1 - 1/(N*e)))
  where:
    mu      — mean of trial Sharpe ratios
    sigma   — std dev of trial Sharpe ratios
    gamma_E — Euler-Mascheroni constant (~0.5772156649...)
    Phi^-1  — inverse normal CDF (scipy.stats.norm.ppf)
    N       — number of trials
    e       — Euler's number (math.e)

Fixture provenance: tests/fixtures/math/expected_max_sharpe/
All expected values derived via reference implementation (numpy + scipy.stats.norm.ppf)
with step-by-step derivation comments in each JSON. NO hardcoded implementation outputs
(PA-18).

AC summary:
  AC1  — compute_expected_max_sharpe exists in math_engine with correct formula
  AC2  — _GAMMA_EULER_MASCHERONI constant named and present in math_engine
  AC3  — scipy.stats.norm.ppf used for inverse-normal CDF
  AC4  — autotuner.py DSR sites call compute_expected_max_sharpe for SR_0
  AC5  — DSR formula structure (Eq. 9 denominator) unchanged
  AC6  — fixture round-trips pin formula to reference values
  AC7  — DSR(full correction) < DSR(SR_0=0) for N=100 (more conservative)
  AC8  — N=1 edge case: collapses to sr_mean (guard against Phi^-1(0)=-inf)
  AC9  — autotune_runs DB column still receives deflated_sharpe
  AC10 — O2 fixture cases recomputed with full SR_0 correction
  AC11 — source comment cites paper with DOI + Eq. 9 + appendix
  AC12 — Discord/dashboard surface unchanged (value is now correctly corrected)
"""

from __future__ import annotations

import inspect
import json
import math
import pathlib
import sys

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures" / "math" / "expected_max_sharpe"
)
_DSR_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures" / "autotuner" / "dsr_examples"
)

sys.path.insert(0, str(_WORKTREE_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fixture(fixture_dir: pathlib.Path, filename: str) -> dict:
    path = fixture_dir / filename
    with open(str(path), encoding="utf-8") as f:
        return json.load(f)


def _import_math_engine():
    import math_engine
    return math_engine


def _import_autotuner():
    import autotuner
    return autotuner


# ---------------------------------------------------------------------------
# AC2 + AC3 + AC11 — constant presence and source citation
# ---------------------------------------------------------------------------


def test_gamma_euler_mascheroni_constant_present():
    """
    AC2: math_engine must define _GAMMA_EULER_MASCHERONI as a named module-level
    constant. Source comment must cite Bailey & López de Prado 2014.

    Project rule: no magic numbers in math_engine.py — every constant named +
    source comment (project CLAUDE.md). The Euler-Mascheroni constant is a
    load-bearing math parameter; it MUST be named, not inlined as 0.5772...
    """
    me = _import_math_engine()

    assert hasattr(me, "_GAMMA_EULER_MASCHERONI"), (
        "math_engine must expose _GAMMA_EULER_MASCHERONI at module scope. "
        "Project rule: no magic numbers — every constant named + source comment. "
        "AC2: R5 implementation has not been merged yet (expected RED)."
    )

    val = me._GAMMA_EULER_MASCHERONI
    assert isinstance(val, float), (
        f"_GAMMA_EULER_MASCHERONI must be a float; got {type(val).__name__}"
    )
    # Euler-Mascheroni constant to 15 significant figures: 0.5772156649015329
    # Tolerance: 1e-10 — using full double precision is mandatory for DSR math.
    assert val == pytest.approx(0.5772156649015329, abs=1e-10), (
        f"_GAMMA_EULER_MASCHERONI must equal 0.5772156649015329 (Euler-Mascheroni constant, "
        f"15 sig figs). Got {val!r}. "
        f"Reference: OEIS A001620. Used in Bailey & López de Prado 2014 expected-max-SR formula."
    )


def test_source_comment_cites_paper():
    """
    AC11: The compute_expected_max_sharpe implementation (or its module constant block)
    must cite Bailey & López de Prado 2014 with at minimum the paper title, DOI, and
    a reference to Eq. 9 and/or the expected-max-SR appendix.

    Method: read math_engine.py source text and verify key citation strings appear
    within reasonable proximity of the function definition or the constant definition.
    """
    src_path = _WORKTREE_ROOT / "math_engine.py"
    src = src_path.read_text(encoding="utf-8")

    required_strings = [
        "Bailey",
        "López de Prado",
        "2014",
        "10.3905",  # DOI prefix (10.3905/jpm.2014.40.5.094)
    ]

    for s in required_strings:
        assert s in src, (
            f"math_engine.py source must contain citation string '{s}' near "
            f"compute_expected_max_sharpe or _GAMMA_EULER_MASCHERONI. "
            f"AC11: source comment must cite Bailey & López de Prado 2014 with "
            f"paper title, DOI, Eq. 9, and expected-max-SR appendix reference."
        )


# ---------------------------------------------------------------------------
# AC1 — function exists with correct signature
# ---------------------------------------------------------------------------


def test_compute_expected_max_sharpe_exists():
    """
    AC1: math_engine must expose compute_expected_max_sharpe at module scope
    with signature (sr_mean: float, sr_std: float, n_trials: int) -> float.

    This is a pure function: no side effects, no I/O, no mutation.
    """
    me = _import_math_engine()

    assert hasattr(me, "compute_expected_max_sharpe"), (
        "math_engine must expose compute_expected_max_sharpe at module scope. "
        "R5 implementation has not been merged yet (expected RED)."
    )

    sig = inspect.signature(me.compute_expected_max_sharpe)
    param_names = set(sig.parameters.keys())

    required_params = {"sr_mean", "sr_std", "n_trials"}
    missing = required_params - param_names
    assert not missing, (
        f"compute_expected_max_sharpe missing required parameters: {missing}. "
        f"Required signature: (sr_mean: float, sr_std: float, n_trials: int) -> float. "
        f"Current params: {param_names}"
    )


# ---------------------------------------------------------------------------
# AC6 — fixture round-trips (formula pinned against reference implementation)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_file,scenario_label", [
    ("n10_typical.json", "N=10_sr_mean=0.5_sr_std=0.3"),
    ("n100_typical.json", "N=100_sr_mean=0.5_sr_std=0.3"),
    ("n500_optuna_default.json", "N=500_sr_mean=0.5_sr_std=0.3_production_default"),
    ("n1_edge_case.json", "N=1_guard_against_minus_inf"),
    ("std_zero_edge_case.json", "sr_std=0_degenerate_all_trials_identical"),
])
def test_compute_expected_max_sharpe_fixture_round_trip(fixture_file, scenario_label):
    """
    AC6: Pin compute_expected_max_sharpe against reference values computed via
    numpy/scipy reference implementation (see fixture JSON derivation blocks).

    Formula: E[max SR] = sr_mean + sr_std * ((1-gamma_E)*Phi^-1(1-1/N) + gamma_E*Phi^-1(1-1/(N*e)))

    Tolerance: 1e-9 absolute. The formula is deterministic; larger error indicates
    a wrong formula, not floating-point rounding. scipy.stats.norm.ppf returns
    ~15 significant figures for these argument ranges.

    Fixture provenance: tests/fixtures/math/expected_max_sharpe/<filename>
    All expected values independently derived — see derivation keys in each JSON.
    """
    fixture = _load_fixture(_FIXTURE_DIR, fixture_file)
    me = _import_math_engine()

    inputs = fixture["inputs"]
    expected = fixture["expected_result"]

    result = me.compute_expected_max_sharpe(
        sr_mean=inputs["sr_mean"],
        sr_std=inputs["sr_std"],
        n_trials=inputs["n_trials"],
    )

    assert isinstance(result, float), (
        f"compute_expected_max_sharpe must return a float; got {type(result).__name__}. "
        f"Scenario: {scenario_label}"
    )
    assert result == pytest.approx(expected, abs=1e-9), (
        f"compute_expected_max_sharpe formula mismatch on scenario '{scenario_label}'.\n"
        f"  Fixture: {fixture_file}\n"
        f"  inputs: sr_mean={inputs['sr_mean']}, sr_std={inputs['sr_std']}, "
        f"n_trials={inputs['n_trials']}\n"
        f"  expected={expected!r}\n"
        f"  got={result!r}\n"
        f"  Formula: E[max SR] = sr_mean + sr_std * ((1-gamma_E)*Phi^-1(1-1/N) + gamma_E*Phi^-1(1-1/(N*e)))\n"
        f"  Reference: Bailey & López de Prado 2014, DOI: 10.3905/jpm.2014.40.5.094, "
        f"Eq. 9 expected-max-SR appendix.\n"
        f"  Note: Derivation in fixture JSON 'derivation' key."
    )


# ---------------------------------------------------------------------------
# AC7 — DSR with full correction < DSR with SR_0=0 (more conservative)
# ---------------------------------------------------------------------------


def test_full_correction_dsr_is_more_conservative_than_sr0_zero():
    """
    AC7: For N=100 trials with typical sr_mean/sr_std, the DSR computed with
    the full expected-max-SR correction must be strictly less than the DSR
    computed with SR_0=0 (the old PSR-style simplification).

    This is the key economic property of R5: the selection-bias correction
    raises the null baseline (SR_0 > 0), which compresses the numerator
    (SR_obs - SR_0) and produces a more conservative (lower) DSR.

    Fixture: tests/fixtures/math/expected_max_sharpe/dsr_conservatism_n100.json
    Scenario: SR_obs=1.2, gamma3=0.5, gamma4=4.0, T=252, sr_mean=0.5, sr_std=0.3, N=100.

    Tolerance on the fixture values: 1e-9 absolute.
    """
    fixture = _load_fixture(_FIXTURE_DIR, "dsr_conservatism_n100.json")
    me = _import_math_engine()
    at = _import_autotuner()

    inputs = fixture["inputs"]

    # Compute SR_0 via the full expected-max correction
    SR_0_full = me.compute_expected_max_sharpe(
        sr_mean=inputs["sr_mean"],
        sr_std=inputs["sr_std"],
        n_trials=inputs["n_trials"],
    )

    # SR_0_full should match the fixture's stored SR_0 derivation
    assert SR_0_full == pytest.approx(fixture["derivation"]["SR_0_full"], abs=1e-9), (
        f"SR_0 (full correction) mismatch.\n"
        f"  expected={fixture['derivation']['SR_0_full']!r}, got={SR_0_full!r}.\n"
        f"  See fixture derivation key for step-by-step calculation."
    )

    # DSR with full SR_0
    dsr_full = at.compute_deflated_sharpe_ratio(
        SR_obs=inputs["SR_obs"],
        SR_0=SR_0_full,
        gamma3=inputs["gamma3"],
        gamma4=inputs["gamma4"],
        T=inputs["T"],
    )

    # DSR with SR_0=0 (old PSR-style)
    dsr_zero = at.compute_deflated_sharpe_ratio(
        SR_obs=inputs["SR_obs"],
        SR_0=0.0,
        gamma3=inputs["gamma3"],
        gamma4=inputs["gamma4"],
        T=inputs["T"],
    )

    # Pin both against fixture derivation values
    assert dsr_full == pytest.approx(fixture["expected_dsr_full"], abs=1e-9), (
        f"DSR (full correction) mismatch.\n"
        f"  expected={fixture['expected_dsr_full']!r}, got={dsr_full!r}."
    )
    assert dsr_zero == pytest.approx(fixture["expected_dsr_zero"], abs=1e-9), (
        f"DSR (SR_0=0) mismatch.\n"
        f"  expected={fixture['expected_dsr_zero']!r}, got={dsr_zero!r}."
    )

    # The core R5 invariant: full correction is strictly more conservative
    assert dsr_full < dsr_zero, (
        f"INVARIANT VIOLATED: DSR with full expected-max-SR correction must be "
        f"strictly less than DSR with SR_0=0 (R5 selection-bias conservatism).\n"
        f"  dsr_full={dsr_full!r} (SR_0={SR_0_full!r})\n"
        f"  dsr_zero={dsr_zero!r} (SR_0=0)\n"
        f"  Scenario: SR_obs={inputs['SR_obs']}, gamma3={inputs['gamma3']}, "
        f"gamma4={inputs['gamma4']}, T={inputs['T']}, N={inputs['n_trials']}, "
        f"sr_mean={inputs['sr_mean']}, sr_std={inputs['sr_std']}.\n"
        f"  The expected-max correction raises SR_0, compressing (SR_obs - SR_0)."
    )


# ---------------------------------------------------------------------------
# AC8 — N=1 edge case collapses to sr_mean without raising
# ---------------------------------------------------------------------------


def test_n1_edge_case_collapses_to_sr_mean():
    """
    AC8: For n_trials=1, Phi^-1(1 - 1/1) = Phi^-1(0) = -inf. The implementation
    must guard this case: with a single trial there is no selection bias, so
    the expected-max-SR collapses to sr_mean.

    Required behavior:
      - No exception raised
      - Returns sr_mean (not -inf, not NaN, not +inf)

    Tolerance: 1e-12 (exact equality expected; sr_mean is returned unmodified).
    """
    me = _import_math_engine()

    sr_mean_value = 0.5
    sr_std_value = 0.3

    result = None
    try:
        result = me.compute_expected_max_sharpe(
            sr_mean=sr_mean_value,
            sr_std=sr_std_value,
            n_trials=1,
        )
    except Exception as exc:
        pytest.fail(
            f"compute_expected_max_sharpe raised {type(exc).__name__} for n_trials=1. "
            f"Must guard against Phi^-1(0)=-inf and return sr_mean without raising.\n"
            f"Exception: {exc}"
        )

    assert result is not None, (
        "compute_expected_max_sharpe returned None for n_trials=1; expected sr_mean."
    )
    assert isinstance(result, float), (
        f"Return type must be float for n_trials=1; got {type(result).__name__}"
    )
    assert math.isfinite(result), (
        f"compute_expected_max_sharpe must return a finite value for n_trials=1; "
        f"got {result!r}. The guard must prevent -inf from propagating."
    )
    assert result == pytest.approx(sr_mean_value, abs=1e-12), (
        f"For n_trials=1, compute_expected_max_sharpe must return sr_mean={sr_mean_value!r}. "
        f"No selection-bias correction applies with a single trial. Got {result!r}.\n"
        f"Fixture: tests/fixtures/math/expected_max_sharpe/n1_edge_case.json"
    )


# ---------------------------------------------------------------------------
# AC4 — autotuner.py DSR sites call compute_expected_max_sharpe for SR_0
# ---------------------------------------------------------------------------


def test_autotuner_dsr_sites_use_expected_max_sharpe_not_zero():
    """
    AC4: Both DSR computation sites in autotuner.py must derive SR_0 via
    compute_expected_max_sharpe, NOT pass SR_0=0.0.

    Method: read autotuner.py source and assert:
      1. 'SR_0=0.0' does NOT appear in DSR call sites (the old PSR-style form).
      2. 'compute_expected_max_sharpe' appears at the DSR computation sites.

    Note: SR_0=0.0 is legitimately allowed ONLY in the function signature
    default/docstring context — NOT as a call-site argument after R5.

    We verify by scanning lines that contain 'compute_deflated_sharpe_ratio' calls
    to confirm they do not pass SR_0=0.0 as a keyword argument.
    """
    src_path = _WORKTREE_ROOT / "autotuner.py"
    src = src_path.read_text(encoding="utf-8")

    lines = src.splitlines()

    # Find lines in DSR call blocks: look for SR_0=0.0 as a keyword argument
    # in compute_deflated_sharpe_ratio call blocks.
    # A multi-line call looks like:
    #   dsr = compute_deflated_sharpe_ratio(
    #       SR_obs=t.value,
    #       SR_0=0.0,   <-- this is the pattern to forbid after R5
    #       ...
    #   )
    # We detect this by scanning for 'SR_0=0.0' in close proximity to
    # 'compute_deflated_sharpe_ratio' (within 10 lines of any call site).

    dsr_call_line_indices = [
        i for i, line in enumerate(lines)
        if "compute_deflated_sharpe_ratio(" in line
    ]

    for call_idx in dsr_call_line_indices:
        # Scan the next 10 lines for SR_0=0.0 (multi-line call args)
        window = lines[call_idx : call_idx + 10]
        window_text = "\n".join(window)
        assert "SR_0=0.0" not in window_text, (
            f"autotuner.py line ~{call_idx+1}: compute_deflated_sharpe_ratio still "
            f"passes SR_0=0.0 (PSR-style simplification). "
            f"R5 requires SR_0 to be derived from compute_expected_max_sharpe(...). "
            f"Found in:\n{window_text}"
        )

    # compute_expected_max_sharpe must appear in autotuner.py
    assert "compute_expected_max_sharpe" in src, (
        "autotuner.py must call compute_expected_max_sharpe to derive SR_0. "
        "AC4: R5 has not been implemented yet (expected RED)."
    )


# ---------------------------------------------------------------------------
# AC9 — autotune_runs DB column still receives deflated_sharpe after R5
# ---------------------------------------------------------------------------


def test_autotune_runs_still_receives_deflated_sharpe_column():
    """
    AC9: R5 changes the VALUE of deflated_sharpe (now more conservative) but
    must NOT change the DB schema. autotune_runs.deflated_sharpe column must
    still be written.

    Method: inspect autotuner.py for the save_autotune_run call pattern and
    verify 'deflated_sharpe' kwarg is present. This is a source-level structural
    test — it cannot regress if R5 accidentally drops the persistence call.
    """
    src_path = _WORKTREE_ROOT / "autotuner.py"
    src = src_path.read_text(encoding="utf-8")

    assert "deflated_sharpe" in src, (
        "autotuner.py must still reference 'deflated_sharpe' after R5. "
        "AC9: the DB column must continue to receive the (now corrected) DSR value."
    )
    assert "save_autotune_run" in src, (
        "autotuner.py must call save_autotune_run. "
        "AC9: persistence of deflated_sharpe must not be dropped by R5."
    )


# ---------------------------------------------------------------------------
# AC10 — O2 fixture cases: recomputed with full SR_0 correction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_file,scenario_label,n_trials", [
    ("01_normal_distribution.json",  "normal_N500", 500),
    ("02_positive_skew.json",        "pos_skew_N500", 500),
    ("03_negative_skew.json",        "neg_skew_N500", 500),
    ("04_fat_tail.json",             "fat_tail_N500", 500),
    ("05_normal_low_sharpe.json",    "low_sharpe_N500", 500),
])
def test_o2_fixtures_dsr_is_more_conservative_with_full_sr0(
    fixture_file, scenario_label, n_trials
):
    """
    AC10: The O2 fixtures represent the same 5 DSR scenarios but with the old
    SR_0=0.0 simplification. For each scenario, compute the DSR with:
      (a) SR_0 = compute_expected_max_sharpe(sr_mean, sr_std, n_trials)
      (b) SR_0 = 0.0 (the O2 baseline, stored in each fixture as expected_dsr)

    Assert that result (a) < result (b) for all 5 scenarios.

    sr_mean/sr_std for these fixtures: we use the population mean and std of
    trial Sharpe values implied by each scenario. Since the O2 fixtures don't
    store cross-trial moments (they only store SR_obs + distribution moments
    gamma3/gamma4), we use a canonical reference distribution:
      sr_mean=0.5, sr_std=0.3
    These are representative of typical AlphaBot Optuna studies.

    Rationale: the conservative direction (full < zero) must hold regardless
    of the specific distribution shape; the correction only depends on sr_mean,
    sr_std, and N.

    Note: this test does NOT re-pin the exact corrected DSR values in the O2
    fixtures (that is the implementer's job per AC10 instructions). It only
    asserts the direction is correct.
    """
    me = _import_math_engine()
    at = _import_autotuner()

    fixture = _load_fixture(_DSR_FIXTURE_DIR, fixture_file)
    inputs = fixture["inputs"]

    # Canonical trial distribution moments for SR_0 derivation
    sr_mean_canonical = 0.5
    sr_std_canonical = 0.3

    SR_0_full = me.compute_expected_max_sharpe(
        sr_mean=sr_mean_canonical,
        sr_std=sr_std_canonical,
        n_trials=n_trials,
    )

    dsr_full = at.compute_deflated_sharpe_ratio(
        SR_obs=inputs["SR_obs"],
        SR_0=SR_0_full,
        gamma3=inputs["gamma3"],
        gamma4=inputs["gamma4"],
        T=inputs["T"],
    )
    dsr_zero = at.compute_deflated_sharpe_ratio(
        SR_obs=inputs["SR_obs"],
        SR_0=0.0,
        gamma3=inputs["gamma3"],
        gamma4=inputs["gamma4"],
        T=inputs["T"],
    )

    # The stored expected_dsr in the O2 fixture used SR_0=0.0; verify that baseline
    expected_dsr_zero = fixture["calculation"]["expected_dsr"]
    assert dsr_zero == pytest.approx(expected_dsr_zero, abs=1e-9), (
        f"Scenario {scenario_label}: DSR with SR_0=0 must match O2 fixture baseline. "
        f"expected={expected_dsr_zero!r}, got={dsr_zero!r}. "
        f"If this fails the O2 implementation is broken (not an R5 regression)."
    )

    # R5 invariant: full correction is more conservative
    assert dsr_full < dsr_zero, (
        f"INVARIANT: DSR with full expected-max-SR correction must be strictly "
        f"less than O2 baseline (SR_0=0) for scenario '{scenario_label}'.\n"
        f"  dsr_full={dsr_full!r} (SR_0={SR_0_full!r})\n"
        f"  dsr_zero={dsr_zero!r}\n"
        f"  Inputs: SR_obs={inputs['SR_obs']}, gamma3={inputs['gamma3']}, "
        f"gamma4={inputs['gamma4']}, T={inputs['T']}, N={n_trials}."
    )


# ---------------------------------------------------------------------------
# AC12 — Discord / dashboard surface unchanged (value still flows)
# ---------------------------------------------------------------------------


def test_dsr_surfacing_structure_unchanged_after_r5():
    """
    AC12: R5 must not alter the DSR surfacing path to Discord or the dashboard.
    The value is now more conservative but the plumbing is the same.

    Method: read reporting.py and alpha_bot_execution.py source and verify that
    references to 'deflated_sharpe' that existed in R4 still exist in R5.
    This is a non-regression structural test.
    """
    reporting_path = _WORKTREE_ROOT / "reporting.py"
    execution_path = _WORKTREE_ROOT / "alpha_bot_execution.py"

    reporting_src = reporting_path.read_text(encoding="utf-8")
    execution_src = execution_path.read_text(encoding="utf-8")

    assert "deflated_sharpe" in reporting_src, (
        "reporting.py must reference 'deflated_sharpe' after R5. "
        "AC12: R5 changes the DSR value but must not drop the Discord surfacing path."
    )
    assert "deflated_sharpe" in execution_src, (
        "alpha_bot_execution.py must reference 'deflated_sharpe' after R5. "
        "AC12: DSR value flows through alpha_bot_execution.py to the operator surfaces."
    )


# ---------------------------------------------------------------------------
# AC3 — scipy.stats.norm.ppf used (not a custom approximation)
# ---------------------------------------------------------------------------


def test_scipy_norm_ppf_used_for_inverse_normal():
    """
    AC3: The implementation must use scipy.stats.norm.ppf for the inverse normal
    CDF, not a hand-rolled approximation. Hand-rolled approximations to Phi^-1
    have limited accuracy and introduce divergence from the paper.

    Method: read math_engine.py source and assert 'scipy.stats' or 'norm.ppf'
    appears in the module (either in an import or a direct call).

    Acceptable patterns:
      from scipy.stats import norm  (then norm.ppf)
      import scipy.stats            (then scipy.stats.norm.ppf)

    The function compute_expected_max_sharpe must use the scipy ppf, not math.erfinv
    or any series expansion.
    """
    src_path = _WORKTREE_ROOT / "math_engine.py"
    src = src_path.read_text(encoding="utf-8")

    has_scipy_import = "scipy" in src or "norm.ppf" in src
    assert has_scipy_import, (
        "math_engine.py must import scipy.stats.norm (or equivalent) for the "
        "inverse-normal CDF in compute_expected_max_sharpe. "
        "AC3: scipy.stats.norm.ppf is the citation-grounded reference implementation. "
        "Hand-rolled approximations to Phi^-1 are not acceptable."
    )


# ---------------------------------------------------------------------------
# Monotonicity — increasing N increases E[max SR] (selection bias grows)
# ---------------------------------------------------------------------------


def test_expected_max_sharpe_monotone_in_n():
    """
    Property: for fixed sr_mean and sr_std > 0, compute_expected_max_sharpe must
    be strictly monotonically increasing in n_trials.

    Economic meaning: more trials → more selection bias → higher expected-maximum
    Sharpe → higher SR_0 → more conservative DSR.

    Test cases: N in {2, 5, 10, 50, 100, 500} with sr_mean=0.5, sr_std=0.3.
    Each step must strictly increase.

    Tolerance: we assert strict inequality (not approx) since the formula is
    analytically monotone in N for N >= 2 and sigma > 0.
    """
    me = _import_math_engine()

    ns = [2, 5, 10, 50, 100, 500]
    sr_mean = 0.5
    sr_std = 0.3

    values = [me.compute_expected_max_sharpe(sr_mean, sr_std, n) for n in ns]

    for i in range(len(ns) - 1):
        assert values[i] < values[i + 1], (
            f"Monotonicity violated between N={ns[i]} and N={ns[i+1]}:\n"
            f"  compute_expected_max_sharpe({sr_mean}, {sr_std}, {ns[i]}) = {values[i]!r}\n"
            f"  compute_expected_max_sharpe({sr_mean}, {sr_std}, {ns[i+1]}) = {values[i+1]!r}\n"
            f"  Expected strictly increasing. Economic: more trials → larger selection bias."
        )


# ---------------------------------------------------------------------------
# Non-negativity: sr_std >= 0 enforced
# ---------------------------------------------------------------------------


def test_compute_expected_max_sharpe_rejects_negative_std():
    """
    Property: compute_expected_max_sharpe must raise ValueError (or similar)
    for negative sr_std. A negative standard deviation is not physically
    meaningful and must be rejected at the function boundary.

    Any exception type is acceptable; a silent wrong result is not.
    """
    me = _import_math_engine()

    with pytest.raises(Exception) as exc_info:
        me.compute_expected_max_sharpe(sr_mean=0.5, sr_std=-0.1, n_trials=100)

    assert exc_info.type in (ValueError, TypeError, AssertionError), (
        f"Expected ValueError/TypeError/AssertionError for negative sr_std; "
        f"got {exc_info.type.__name__}: {exc_info.value}"
    )


# ---------------------------------------------------------------------------
# n_trials=0 guard
# ---------------------------------------------------------------------------


def test_compute_expected_max_sharpe_rejects_zero_trials():
    """
    Property: n_trials=0 is not meaningful (no trials ran). The function must
    raise rather than silently return sr_mean or attempt Phi^-1 of an undefined
    argument (1 - 1/0 = undefined).
    """
    me = _import_math_engine()

    with pytest.raises(Exception) as exc_info:
        me.compute_expected_max_sharpe(sr_mean=0.5, sr_std=0.3, n_trials=0)

    assert exc_info.type in (ValueError, TypeError, ZeroDivisionError, AssertionError), (
        f"Expected ValueError/TypeError/ZeroDivisionError for n_trials=0; "
        f"got {exc_info.type.__name__}: {exc_info.value}"
    )


# ---------------------------------------------------------------------------
# R/G/R Gap — fallback DSR path must use SR_0=0.0, not compute_expected_max_sharpe
# ---------------------------------------------------------------------------


def test_fallback_dsr_path_produces_nonzero_signal():
    """
    R/G/R gap found in GREEN review: the fallback DSR path (fewer than 2 completed
    trials) must produce a meaningful non-zero DSR signal, not always 0.0.

    The GREEN implementation incorrectly routes the fallback through
    compute_expected_max_sharpe(sr_mean=naive_sharpe_value, sr_std=0.0, n_trials=1),
    which returns sr_mean=naive_sharpe_value via the sr_std=0 guard. This makes
    SR_0 = SR_obs, so DSR numerator = (SR_obs - SR_0) = 0.0 always.

    Correct behavior: the fallback path has fewer than 2 trials, so there is no
    cross-trial distribution to derive expected-max from. SR_0 must be 0.0 (PSR-style
    null) — the same value used in the original O2 implementation. There is no
    selection bias to correct for when only one trial ran.

    This test asserts that for a naive_sharpe_value > 0, the fallback DSR is
    strictly positive (not zero). It will FAIL on the current GREEN implementation
    and PASS once the fallback site is corrected to use SR_0=0.0.

    Verification: compute_deflated_sharpe_ratio(SR_obs=1.5, SR_0=0.0, gamma3=0, gamma4=3, T=252)
    = 1.5 * sqrt(251) / sqrt(1.5) ≈ 16.30 — strictly positive, non-zero.
    """
    import contextlib
    import io
    from unittest.mock import MagicMock, patch

    autotuner = _import_autotuner()

    save_autotune_run_calls: list[dict] = []

    def capturing_save_autotune_run(**kwargs):
        save_autotune_run_calls.append(kwargs)

    default_params = {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }

    # Fewer than 2 completed trials forces the fallback DSR path
    fake_study = MagicMock()
    fake_study.best_params = default_params.copy()
    fake_study.best_value = 1.5
    fake_study.optimize = MagicMock(return_value=None)
    # Only 1 completed trial — triggers fallback path (len(completed_trials) < 2)
    single_trial = MagicMock()
    single_trial.value = 1.5
    single_trial.params = default_params.copy()
    fake_study.trials = [single_trial]

    bot_state = {"sym-A": {"name": "Fallback DSR Test", "account_uuid": "acc-1"}}
    history = {
        "sym-A": {
            "2026-04-01": [{"return": 2.0, "mc_prob": 50.0, "vol": 1.0,
                            "vwap_diff": -2.0, "base_atr_pct": 1.0,
                            "valid_vwap_weight": 1.0}],
            "2026-04-02": [{"return": 2.0, "mc_prob": 50.0, "vol": 1.0,
                            "vwap_diff": -2.0, "base_atr_pct": 1.0,
                            "valid_vwap_weight": 1.0}],
        }
    }

    def vwap_always_triggers(**kwargs):
        return (0, 0, True, False)

    buf = io.StringIO()
    with (
        patch("autotuner.optuna.create_study", return_value=fake_study),
        patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()),
        patch("autotuner.synthetic_history.generate_synthetic_history",
              return_value=history),
        patch("autotuner.database.load_chart_history", return_value={}),
        patch("autotuner.database.save_chart_archive"),
        patch("autotuner.database.get_symphony_strategy",
              return_value={"params": default_params.copy(), "locked_vars": []}),
        patch("autotuner.database.save_symphony_strategy"),
        patch("autotuner.database.DEFAULT_STRATEGY", default_params),
        patch("autotuner.database.save_autotune_run",
              side_effect=capturing_save_autotune_run),
        patch("autotuner.math_engine.compute_para_arm_decision",
              side_effect=lambda **kw: (0.0, False)),
        patch("autotuner.math_engine.compute_time_squeeze_decay",
              side_effect=lambda tr: (1.5, 0.5)),
        patch("autotuner.math_engine.compute_active_trailing_stop",
              side_effect=lambda *a, **kw: 5.0),
        patch("autotuner.math_engine.compute_breakeven_update",
              side_effect=lambda *a, **kw: (a[3], a[4], a[2])),
        patch("autotuner.math_engine.compute_vwap_bleed_arm_threshold",
              side_effect=lambda *a, **kw: -10.0),
        patch("autotuner.math_engine.compute_vwap_breakdown_update",
              side_effect=vwap_always_triggers),
        contextlib.redirect_stdout(buf),
    ):
        autotuner.run_autotuner(bot_state, "2026-05-10", ["acc-1"])

    assert len(save_autotune_run_calls) == 1, (
        f"Expected exactly one save_autotune_run call; got {len(save_autotune_run_calls)}"
    )

    deflated = save_autotune_run_calls[0].get("deflated_sharpe")

    assert deflated is not None, (
        "deflated_sharpe must not be NULL in the fallback path."
    )
    assert math.isfinite(deflated), (
        f"deflated_sharpe must be finite in the fallback path; got {deflated!r}"
    )
    assert deflated != pytest.approx(0.0, abs=1e-9), (
        f"FALLBACK DSR GAP: deflated_sharpe is 0.0 in the fallback path. "
        f"This means SR_0 = SR_obs (the GREEN implementation routes fallback through "
        f"compute_expected_max_sharpe(sr_mean=naive_sharpe_value, sr_std=0.0, n_trials=1) "
        f"which returns sr_mean=naive_sharpe_value, collapsing the numerator to zero). "
        f"Correct fix: fallback DSR path must use SR_0=0.0 directly — no selection bias "
        f"correction for a single-trial study.\n"
        f"  naive_sharpe_value=1.5, deflated_sharpe={deflated!r}"
    )
    # Also assert strictly positive for a positive naive SR
    assert deflated > 0, (
        f"Fallback DSR must be positive when naive_sharpe_value > 0 and SR_0=0.0. "
        f"Got {deflated!r}. Expected ~16.3 (SR_obs=1.5, T≥2, gamma3=0, gamma4=3)."
    )
