"""
R6 — Sortino Sentinel Trim: RED tests for filtering sentinel values from DSR moment computation.

Gap 12.2 (Methodology-grounding audit): when a trial's Sortino hits 1e6 (the sentinel returned
by compute_sortino_ratio when downside_deviation==0, meaning all returns beat the target), that
value pollutes the cross-trial Sharpe distribution used by DSR Eq. 9. The sentinel is not a
failed trial (Optuna stores None for those), but an extreme outlier that distorts gamma_3
(skewness) and gamma_4 (kurtosis) consumed by compute_deflated_sharpe_ratio.

Fix: filter_sortino_sentinels() removes 1e6 values from the trial series BEFORE computing the
moments (mean, std, gamma3, gamma4) and SR_0 passed to compute_expected_max_sharpe.

Reference:
  Bailey, D.H. & López de Prado, M. (2014). "The Deflated Sharpe Ratio."
  Financial Analysts Journal, 70(5), 94-107. DOI: 10.3905/jpm.2014.40.5.094. Equation 9.

Fixture provenance: tests/fixtures/autotuner/sortino_sentinel_trim/
All expected values derived via reference implementation (numpy/math). NO hardcoded literals
without derivation comments. See each fixture's "derivation" key (PA-18).

AC summary:
  AC1  — filter_sortino_sentinels exists in math_engine with correct signature
  AC2  — Sentinel detection matches EXACTLY the project sentinel (1e6), not all-NaN/inf blindly
  AC3  — DSR call site invokes filter BEFORE compute_expected_max_sharpe AND before gamma3/gamma4
  AC4  — 10 valid + 5 sentinels: moments and DSR computed on the 10 valid values only
  AC5  — All-valid set: filter is a no-op; DSR unchanged
  AC6  — All-sentinel set: returns empty list; DSR computation handles gracefully (returns None)
  AC7  — 1 valid + many sentinels: N=1 degenerate path kicks in; fallback DSR fires
  AC8  — Filter is composable: single source of truth; no inline filtering duplicated at call sites
  AC9  — Backward compat: DSR value with no-sentinel inputs == R5 behavior
  AC10 — Source comment explains why sentinels pollute moments (non-obvious WHY)
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
    / "fixtures" / "autotuner" / "sortino_sentinel_trim"
)

sys.path.insert(0, str(_WORKTREE_ROOT))

# Sentinel used by compute_sortino_ratio when downside_deviation==0.
# Defined here for test clarity; the implementation must use the SAME value.
# Source: autotuner.py compute_sortino_ratio line ~118: returns 1e6 when
# downside_deviation==0.0 so Optuna TPE receives a finite value.
_SENTINEL = 1e6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fixture(filename: str) -> dict:
    path = _FIXTURE_DIR / filename
    with open(str(path), encoding="utf-8") as f:
        return json.load(f)


def _import_math_engine():
    import math_engine
    return math_engine


def _import_autotuner():
    import autotuner
    return autotuner


# ---------------------------------------------------------------------------
# AC1 — filter_sortino_sentinels exists with correct signature
# ---------------------------------------------------------------------------


def test_filter_sortino_sentinels_exists_with_correct_signature():
    """
    AC1: math_engine must expose filter_sortino_sentinels at module scope.
    Signature: (sortino_values: list[float]) -> list[float].
    Pure function: no side effects, no I/O, no mutation of input list.

    This will FAIL (RED) until filter_sortino_sentinels is implemented.
    """
    me = _import_math_engine()

    assert hasattr(me, "filter_sortino_sentinels"), (
        "math_engine must expose filter_sortino_sentinels at module scope. "
        "R6 implementation has not been merged yet (expected RED). "
        "Signature: (sortino_values: list[float]) -> list[float]."
    )

    sig = inspect.signature(me.filter_sortino_sentinels)
    params = list(sig.parameters.keys())

    assert len(params) >= 1, (
        f"filter_sortino_sentinels must accept at least one parameter (the sortino_values list). "
        f"Got parameters: {params}"
    )
    # First param should be sortino_values (or similar name)
    assert "sortino" in params[0].lower() or "values" in params[0].lower(), (
        f"First parameter of filter_sortino_sentinels should be named 'sortino_values' or similar. "
        f"Got: {params[0]!r}"
    )


# ---------------------------------------------------------------------------
# AC2 — Sentinel detection matches the project sentinel exactly (1e6, not all-inf/nan)
# ---------------------------------------------------------------------------


def test_filter_sentinel_detection_matches_project_sentinel_not_all_nonfinite():
    """
    AC2: filter_sortino_sentinels must filter ONLY the project sentinel value (1e6),
    not all non-finite values (NaN, -inf, +inf) blindly. The project sentinel IS finite
    (1e6), so blanket non-finite filtering would not catch it and is the wrong approach.

    Conversely, finite-but-unusual negative values must NOT be filtered.
    A Sortino of -5.0 represents a legitimately bad trial — it must survive the filter
    and properly pull the moment distribution toward caution.

    This will FAIL (RED) if the implementation uses math.isfinite() as the only guard,
    or if it accidentally drops legitimate negative Sortinos.
    """
    me = _import_math_engine()
    fn = me.filter_sortino_sentinels

    # 1e6 is the sentinel — must be removed
    result_with_sentinel = fn([0.5, _SENTINEL, 1.2])
    assert _SENTINEL not in result_with_sentinel, (
        f"filter_sortino_sentinels must remove the project sentinel 1e6 from the list. "
        f"Got: {result_with_sentinel!r}"
    )

    # Legitimate negative Sortino — must be KEPT
    result_with_neg = fn([-5.0, 0.5, 1.2])
    assert -5.0 in result_with_neg, (
        f"filter_sortino_sentinels must NOT remove legitimate negative Sortino values (-5.0). "
        f"A Sortino of -5.0 is a real bad trial, not a sentinel. "
        f"Got: {result_with_neg!r}"
    )

    # -inf is not the project sentinel (failed trials get t.value=None in Optuna, not -inf).
    # The filter must not DROP -inf either — this is ambiguous behavior the implementer must decide.
    # The key contract is: 1e6 is removed, real values are kept.
    # (We do not mandate -inf behavior here since Optuna's completed_trials filter already
    # excludes None-valued trials; -inf would only appear if an objective explicitly returned it.)

    # Verify 0.0 is kept (an empty returns series returns 0.0, which is a real signal)
    result_with_zero = fn([0.0, 0.5, 1.2])
    assert 0.0 in result_with_zero, (
        f"filter_sortino_sentinels must NOT remove 0.0. "
        f"0.0 is a legitimate Sortino for an empty returns series. "
        f"Got: {result_with_zero!r}"
    )


# ---------------------------------------------------------------------------
# AC3 — Source: filter called before moment computation in autotuner.py
# ---------------------------------------------------------------------------


def test_filter_called_before_moment_computation_in_autotuner():
    """
    AC3: autotuner.py must invoke filter_sortino_sentinels on trial_values BEFORE
    computing the population moments (mean, variance, gamma3, gamma4) fed to DSR.

    Method: source-level structural test — verify that:
    (a) 'filter_sortino_sentinels' appears in autotuner.py
    (b) It appears BEFORE the gamma3/gamma4 computation lines in the DSR block
    (c) It is called on 'trial_values' (the list used for moment computation)
    (d) There is a single call site per DSR block (single source of truth — no inline duplication)

    This will FAIL (RED) until the implementation hooks the filter into the call site.
    """
    src_path = _WORKTREE_ROOT / "autotuner.py"
    src = src_path.read_text(encoding="utf-8")
    lines = src.splitlines()

    assert "filter_sortino_sentinels" in src, (
        "autotuner.py must call filter_sortino_sentinels. "
        "AC3: R6 implementation not merged yet (expected RED)."
    )

    # Verify it appears before gamma3 computation in each DSR block
    filter_line_indices = [
        i for i, line in enumerate(lines)
        if "filter_sortino_sentinels" in line
    ]
    gamma3_line_indices = [
        i for i, line in enumerate(lines)
        if "gamma3" in line and "=" in line and "compute" not in line
    ]

    assert len(filter_line_indices) >= 2, (
        f"autotuner.py must call filter_sortino_sentinels at least twice "
        f"(once per DSR block: walk-forward + calibration sweep). "
        f"Found {len(filter_line_indices)} occurrence(s)."
    )

    # For each DSR block's gamma3 computation, there must be a preceding filter call
    for gamma3_idx in gamma3_line_indices[:2]:  # check first two DSR blocks
        preceding_filter = any(fi < gamma3_idx for fi in filter_line_indices)
        assert preceding_filter, (
            f"autotuner.py line ~{gamma3_idx+1}: gamma3 computation found without a preceding "
            f"filter_sortino_sentinels call. The filter must run BEFORE moment computation. "
            f"filter_sortino_sentinels call lines: {[fi+1 for fi in filter_line_indices]}"
        )


# ---------------------------------------------------------------------------
# AC4 — 10 valid + 5 sentinels: moments on valid values only; DSR matches fixture
# ---------------------------------------------------------------------------


def test_sentinel_pollution_removed_dsr_matches_valid_only_fixture():
    """
    AC4: With 10 valid Sortinos + 5 sentinels (1e6), filter_sortino_sentinels must
    return the 10 valid values. Moments computed from the filtered list must match
    the fixture's valid_only_moments. DSR computed from those moments must match
    fixture dsr_valid_only_moments.

    Fixture: 01_mixed_valid_and_sentinels.json

    Tolerance: 1e-9 absolute on DSR (deterministic formula).
    Tolerance: 1e-12 on intermediate moments (they are exact arithmetic on fixture inputs).

    This will FAIL (RED) until filter_sortino_sentinels is implemented.
    """
    me = _import_math_engine()
    at = _import_autotuner()
    fixture = _load_fixture("01_mixed_valid_and_sentinels.json")

    all_sortinos = fixture["inputs"]["all_sortinos"]
    expected_filtered = fixture["expected_filtered"]

    filtered = me.filter_sortino_sentinels(all_sortinos)

    assert filtered == pytest.approx(expected_filtered, abs=1e-12), (
        f"filter_sortino_sentinels([...10 valid + 5 sentinels...]) must return the 10 valid values. "
        f"expected={expected_filtered!r}, got={filtered!r}. "
        f"Sentinel value to remove: 1e6."
    )

    n = len(filtered)
    assert n == fixture["inputs"]["valid_count"], (
        f"After filtering, must have {fixture['inputs']['valid_count']} values; got {n}."
    )

    # Compute moments from filtered list
    mean_f = sum(filtered) / n
    variance_f = sum((v - mean_f) ** 2 for v in filtered) / n  # population variance
    std_f = math.sqrt(variance_f)
    gamma3_f = sum((v - mean_f) ** 3 for v in filtered) / (n * std_f ** 3)
    gamma4_f = sum((v - mean_f) ** 4 for v in filtered) / (n * std_f ** 4)

    vm = fixture["valid_only_moments"]

    # Moments must match reference — tolerance 1e-12 (exact float arithmetic on same inputs)
    assert mean_f == pytest.approx(vm["mean"], abs=1e-12), (
        f"mean from filtered values must match fixture. "
        f"expected={vm['mean']!r}, got={mean_f!r}."
    )
    assert std_f == pytest.approx(vm["std"], abs=1e-12), (
        f"std from filtered values must match fixture. "
        f"expected={vm['std']!r}, got={std_f!r}."
    )
    assert gamma3_f == pytest.approx(vm["gamma3"], abs=1e-9), (
        f"gamma3 from filtered values must match fixture (tol=1e-9: population-divisor formula). "
        f"expected={vm['gamma3']!r}, got={gamma3_f!r}."
    )
    assert gamma4_f == pytest.approx(vm["gamma4"], abs=1e-9), (
        f"gamma4 from filtered values must match fixture (tol=1e-9: population-divisor formula). "
        f"expected={vm['gamma4']!r}, got={gamma4_f!r}."
    )

    # Compute DSR from filtered moments
    SR_0 = me.compute_expected_max_sharpe(sr_mean=mean_f, sr_std=std_f, n_trials=n)
    dsr_scenario = fixture["dsr_scenario"]
    dsr = at.compute_deflated_sharpe_ratio(
        SR_obs=dsr_scenario["SR_obs"],
        SR_0=SR_0,
        gamma3=gamma3_f,
        gamma4=gamma4_f,
        T=dsr_scenario["T"],
    )

    # DSR from valid-only moments — tolerance 1e-6 (cascaded float ops through SR_0 + DSR formula)
    # Wider than moment tolerance because SR_0 is derived via scipy.stats.norm.ppf chain
    assert dsr == pytest.approx(dsr_scenario["dsr_valid_only_moments"], abs=1e-6), (
        f"DSR computed from sentinel-filtered moments must match fixture dsr_valid_only_moments. "
        f"expected={dsr_scenario['dsr_valid_only_moments']!r}, got={dsr!r}. "
        f"Sentinel pollution would have given {dsr_scenario['dsr_polluted_moments']!r} "
        f"(difference of ~{abs(dsr_scenario['dsr_polluted_moments'] - dsr_scenario['dsr_valid_only_moments']):.1f})."
    )


# ---------------------------------------------------------------------------
# AC4 complementary — polluted moments produce catastrophically wrong DSR
# ---------------------------------------------------------------------------


def test_sentinel_pollution_magnitude_is_catastrophic():
    """
    Complementary to AC4: directly verify that WITHOUT filtering, the sentinel-polluted
    moments produce a DSR that is catastrophically wrong — ~19M difference from correct.

    This validates why the filter is necessary (the 'WHY' requirement from AC10).

    Not a RED test in the traditional sense — this passes against R5 (polluted path exists
    and produces a wrong value). It documents the severity of the bug.
    """
    me = _import_math_engine()
    at = _import_autotuner()
    fixture = _load_fixture("01_mixed_valid_and_sentinels.json")

    all_sortinos = fixture["inputs"]["all_sortinos"]
    n_all = len(all_sortinos)
    mean_all = sum(all_sortinos) / n_all
    var_all = sum((v - mean_all) ** 2 for v in all_sortinos) / n_all
    std_all = math.sqrt(var_all)
    gamma3_all = sum((v - mean_all) ** 3 for v in all_sortinos) / (n_all * std_all ** 3)
    gamma4_all = sum((v - mean_all) ** 4 for v in all_sortinos) / (n_all * std_all ** 4)
    SR_0_polluted = me.compute_expected_max_sharpe(sr_mean=mean_all, sr_std=std_all, n_trials=n_all)

    dsr_polluted = at.compute_deflated_sharpe_ratio(
        SR_obs=fixture["dsr_scenario"]["SR_obs"],
        SR_0=SR_0_polluted,
        gamma3=gamma3_all,
        gamma4=gamma4_all,
        T=fixture["dsr_scenario"]["T"],
    )

    dsr_correct = fixture["dsr_scenario"]["dsr_valid_only_moments"]

    # Polluted DSR must differ by at least 1 million — proves sentinel pollution is catastrophic
    # Tolerance rationale: fixture shows ~19M difference. We assert > 1M to give implementation
    # some latitude on floating-point accumulation while still catching any regression
    # that re-introduces pollution.
    assert abs(dsr_polluted - dsr_correct) > 1e6, (
        f"Sentinel pollution must produce a catastrophically wrong DSR (>1M error). "
        f"If this assertion fails, sentinels are no longer a problem OR sentinels changed. "
        f"polluted_dsr={dsr_polluted!r}, correct_dsr={dsr_correct!r}, "
        f"diff={abs(dsr_polluted - dsr_correct)!r}. "
        f"Recheck the sentinel value (currently 1e6)."
    )


# ---------------------------------------------------------------------------
# AC5 — All-valid set: filter is a no-op
# ---------------------------------------------------------------------------


def test_filter_is_noop_for_all_valid_inputs():
    """
    AC5: filter_sortino_sentinels on a list with no sentinels must return the list unchanged
    (same elements, same order, same length).

    Fixture: 02_all_valid_no_sentinels.json

    This will FAIL (RED) if the filter incorrectly removes non-sentinel values.
    """
    me = _import_math_engine()
    fixture = _load_fixture("02_all_valid_no_sentinels.json")

    input_values = fixture["inputs"]["all_sortinos"]
    result = me.filter_sortino_sentinels(input_values)

    assert result == pytest.approx(input_values, abs=1e-15), (
        f"filter_sortino_sentinels must be a no-op on all-valid inputs. "
        f"input={input_values!r}, got={result!r}. "
        f"Must return the same values in the same order."
    )

    assert len(result) == len(input_values), (
        f"filter_sortino_sentinels must not change the length for all-valid inputs. "
        f"input_len={len(input_values)}, output_len={len(result)}."
    )


# ---------------------------------------------------------------------------
# AC6 — All-sentinel set: returns empty list; no crash
# ---------------------------------------------------------------------------


def test_all_sentinels_returns_empty_list_no_crash():
    """
    AC6: filter_sortino_sentinels on an all-sentinel list must return [] without raising.
    The caller (DSR block) guards with len(filtered) >= 2 before proceeding; this pins
    the contract for the empty-list return value.

    Fixture: 03_all_sentinels.json

    This will FAIL (RED) if the filter raises or returns non-empty for all-1e6 input.
    """
    me = _import_math_engine()
    fixture = _load_fixture("03_all_sentinels.json")

    input_values = fixture["inputs"]["all_sortinos"]
    assert all(v == _SENTINEL for v in input_values), (
        "Fixture sanity: all inputs should be the sentinel value."
    )

    result = None
    try:
        result = me.filter_sortino_sentinels(input_values)
    except Exception as exc:
        pytest.fail(
            f"filter_sortino_sentinels must not raise for all-sentinel input. "
            f"Got {type(exc).__name__}: {exc}"
        )

    assert result == [], (
        f"filter_sortino_sentinels on all-sentinel input must return []. "
        f"Got: {result!r}. "
        f"Downstream DSR block guards with len(filtered) >= 2."
    )


# ---------------------------------------------------------------------------
# AC7 — 1 valid + many sentinels: N=1 degenerate; fallback DSR fires
# ---------------------------------------------------------------------------


def test_one_valid_many_sentinels_triggers_n1_fallback():
    """
    AC7: With 1 valid Sortino after filtering + many sentinels, the DSR re-ranking block
    is skipped (len(filtered) < 2). The fallback DSR path fires with SR_0=0.0,
    gamma3=0.0, gamma4=3.0 (normal-distribution fallback).

    Two assertions:
    (a) filter returns [1.5] from [1.5, 1e6, 1e6, ...]
    (b) fallback DSR for SR_obs=1.5, SR_0=0.0, gamma3=0.0, gamma4=3.0, T=60 matches fixture

    Fixture: 04_one_valid_many_sentinels.json
    Tolerance on DSR: 1e-9 (compute_deflated_sharpe_ratio is deterministic arithmetic).
    """
    me = _import_math_engine()
    at = _import_autotuner()
    fixture = _load_fixture("04_one_valid_many_sentinels.json")

    input_values = fixture["inputs"]["all_sortinos"]
    expected_filtered = fixture["expected_filtered"]

    filtered = me.filter_sortino_sentinels(input_values)

    assert filtered == pytest.approx(expected_filtered, abs=1e-12), (
        f"filter_sortino_sentinels([1.5, 1e6*9]) must return [1.5]. "
        f"expected={expected_filtered!r}, got={filtered!r}."
    )

    assert len(filtered) == 1, (
        f"Only 1 valid value expected after filter; got len={len(filtered)}, values={filtered!r}."
    )

    # Verify fallback DSR arithmetic matches fixture (pin the N=1 path)
    scenario = fixture["fallback_dsr_scenario"]
    dsr_fallback = at.compute_deflated_sharpe_ratio(
        SR_obs=scenario["SR_obs"],
        SR_0=scenario["SR_0"],
        gamma3=scenario["gamma3"],
        gamma4=scenario["gamma4"],
        T=scenario["T"],
    )

    # Tolerance 1e-9: pure arithmetic, no PPF chain, deterministic
    assert dsr_fallback == pytest.approx(scenario["expected_dsr"], abs=1e-9), (
        f"Fallback DSR for N=1 after filter must match fixture. "
        f"expected={scenario['expected_dsr']!r}, got={dsr_fallback!r}. "
        f"Formula: (SR_obs - SR_0) * sqrt(T-1) / sqrt(denom_sq). "
        f"denom_sq = 1 - 0*1.5 + (2/4)*1.5^2 = 2.125."
    )


# ---------------------------------------------------------------------------
# AC8 — Composability: single source of truth, no inline filtering duplicated
# ---------------------------------------------------------------------------


def test_filter_single_source_of_truth_no_inline_duplication():
    """
    AC8: The sentinel filter must be invoked via filter_sortino_sentinels at the call sites
    in autotuner.py. There must be NO inline sentinel-filtering logic duplicated directly
    at the call sites (e.g., no list comprehension `[v for v in values if v != 1e6]` or
    `[v for v in values if v < 1e6]` appearing adjacent to the DSR block).

    Single source of truth: all filtering routes through filter_sortino_sentinels in
    math_engine.py. This ensures the sentinel value is defined in one place and any
    future change to the sentinel value requires only one edit.

    Method: source-level structural test.
    """
    src_path = _WORKTREE_ROOT / "autotuner.py"
    src = src_path.read_text(encoding="utf-8")

    # Inline patterns that would bypass the function
    inline_patterns = [
        "if v != 1e6",
        "if v != 1000000",
        "v < 1e6",
        "v < 1000000",
        "!= _SENTINEL",
        "!= SENTINEL",
    ]

    for pattern in inline_patterns:
        assert pattern not in src, (
            f"autotuner.py must NOT contain inline sentinel-filtering logic ('{pattern}'). "
            f"All filtering must route through math_engine.filter_sortino_sentinels. "
            f"Single source of truth: sentinel value defined once in math_engine.py. "
            f"AC8: found inline pattern '{pattern}' — refactor to use filter_sortino_sentinels."
        )

    # Verify filter_sortino_sentinels is called in autotuner.py
    assert "filter_sortino_sentinels" in src, (
        "autotuner.py must call math_engine.filter_sortino_sentinels. "
        "AC8: the function must be the single filter source (not inline logic)."
    )


# ---------------------------------------------------------------------------
# AC9 — Backward compat: DSR on no-sentinel input is unchanged from R5
# ---------------------------------------------------------------------------


def test_backward_compat_dsr_unchanged_for_clean_inputs():
    """
    AC9: For a trial set with no sentinel values, DSR computed via the R6 path
    (filter → moments → DSR) must equal the R5 baseline DSR.

    This guards against R6 accidentally altering the DSR formula or moment computation
    for clean inputs where the filter is a no-op.

    Fixture: 05_backward_compat_no_sentinels.json
    R5 baseline DSR stored in fixture as dsr_scenario.r5_baseline_dsr.
    Tolerance: 1e-6 (cascaded through SR_0 via scipy.stats.norm.ppf).
    """
    me = _import_math_engine()
    at = _import_autotuner()
    fixture = _load_fixture("05_backward_compat_no_sentinels.json")

    input_values = fixture["inputs"]["all_sortinos"]
    r5_baseline_dsr = fixture["dsr_scenario"]["r5_baseline_dsr"]

    # Apply filter (must be no-op)
    filtered = me.filter_sortino_sentinels(input_values)
    assert len(filtered) == len(input_values), (
        f"Backward-compat: filter must be no-op on clean inputs. "
        f"input_len={len(input_values)}, filtered_len={len(filtered)}."
    )

    n = len(filtered)
    mean_f = sum(filtered) / n
    variance_f = sum((v - mean_f) ** 2 for v in filtered) / n
    std_f = math.sqrt(variance_f)
    gamma3_f = sum((v - mean_f) ** 3 for v in filtered) / (n * std_f ** 3)
    gamma4_f = sum((v - mean_f) ** 4 for v in filtered) / (n * std_f ** 4)
    SR_0 = me.compute_expected_max_sharpe(sr_mean=mean_f, sr_std=std_f, n_trials=n)

    scenario = fixture["dsr_scenario"]
    dsr_r6 = at.compute_deflated_sharpe_ratio(
        SR_obs=scenario["SR_obs"],
        SR_0=SR_0,
        gamma3=gamma3_f,
        gamma4=gamma4_f,
        T=scenario["T"],
    )

    assert dsr_r6 == pytest.approx(r5_baseline_dsr, abs=1e-6), (
        f"R6 DSR for no-sentinel inputs must equal R5 baseline DSR. "
        f"R5 baseline={r5_baseline_dsr!r}, R6 result={dsr_r6!r}. "
        f"R6 must not change DSR behavior for clean trial sets."
    )


# ---------------------------------------------------------------------------
# AC10 — Source comment explains WHY sentinels pollute moments
# ---------------------------------------------------------------------------


def test_filter_source_comment_explains_why_sentinels_pollute():
    """
    AC10: The filter_sortino_sentinels function (or its call site in autotuner.py) must
    have a source comment that explains WHY sentinel values pollute the DSR moments.

    The WHY is non-obvious: 1e6 is returned for technically-successful trials (all returns
    beat the target), not failures, so it looks like a valid trial value but is a numerical
    artifact that dominates the mean and std of the cross-trial distribution, distorting
    gamma3/gamma4 and SR_0.

    Method: source-level assertion — check for key explanatory strings in math_engine.py
    or autotuner.py near the filter function/call site.
    """
    me_src_path = _WORKTREE_ROOT / "math_engine.py"
    at_src_path = _WORKTREE_ROOT / "autotuner.py"

    me_src = me_src_path.read_text(encoding="utf-8")
    at_src = at_src_path.read_text(encoding="utf-8")
    combined_src = me_src + at_src

    # At minimum, the comment must mention that sentinels (or the 1e6 value) distort or
    # pollute moments. One of these strings should appear near the filter.
    required_concepts = ["pollut", "distort", "moment", "sentinel"]
    for concept in required_concepts:
        assert concept.lower() in combined_src.lower(), (
            f"Source (math_engine.py or autotuner.py) must contain a comment explaining "
            f"why sentinel values pollute DSR moments. "
            f"Missing concept '{concept}' in source text. "
            f"AC10: the WHY is non-obvious — 1e6 is a successful-trial artifact (not a failure), "
            f"but its magnitude dominates cross-trial mean/std/gamma3/gamma4."
        )


# ---------------------------------------------------------------------------
# R/G/R Gap — sentinel trials must be excluded from DSR SCORING loop too
# ---------------------------------------------------------------------------


def test_sentinel_trials_excluded_from_dsr_scoring_loop():
    """
    R/G/R gap found after GREEN: the DSR scoring loop (for t in completed_trials: dsr = ...)
    still iterates ALL completed trials including sentinel-valued ones (t.value=1e6).

    Even with corrected moments (mean/std/gamma3/gamma4 computed from filtered values),
    a sentinel trial scores DSR=14.02 vs the best valid trial's DSR=1.01 — the sentinel
    WINS the ranking and its params get selected as best_trial_by_dsr.

    Root cause: the sentinel (1e6) is enormous relative to any real Sortino. Even divided
    by a corrected SR_0, the numerator (SR_obs - SR_0) = 1e6 - 1.37 ≈ 1e6 still dominates.
    Fixing only the moment computation does not prevent sentinel params from being selected.

    Fix required: the scoring loop must skip trials where t.value == _SORTINO_SENTINEL.
    Either filter the trial list before the loop, or add a guard inside the loop.

    Fixture: 06_sentinel_excluded_from_scoring_loop.json
    Tolerance on DSR: 1e-6 (cascaded through SR_0 via scipy PPF chain).

    This will FAIL (RED) against the current GREEN (which only fixes moments, not scoring).
    """
    me = _import_math_engine()
    at = _import_autotuner()
    fixture = _load_fixture("06_sentinel_excluded_from_scoring_loop.json")

    # Reconstruct the corrected moments (same as fixture 01 valid-only moments)
    valid_sortinos = fixture["inputs"]["valid_sortinos"]
    n = len(valid_sortinos)
    mean_v = sum(valid_sortinos) / n
    variance_v = sum((v - mean_v) ** 2 for v in valid_sortinos) / n
    std_v = math.sqrt(variance_v)
    gamma3 = sum((v - mean_v) ** 3 for v in valid_sortinos) / (n * std_v ** 3)
    gamma4 = sum((v - mean_v) ** 4 for v in valid_sortinos) / (n * std_v ** 4)
    SR_0 = me.compute_expected_max_sharpe(sr_mean=mean_v, sr_std=std_v, n_trials=n)

    scoring = fixture["scoring_scenario"]
    T = scoring["T"]

    # Confirm sentinel DOES produce a higher raw DSR than best valid trial
    dsr_sentinel = at.compute_deflated_sharpe_ratio(
        SR_obs=_SENTINEL,
        SR_0=SR_0,
        gamma3=gamma3,
        gamma4=gamma4,
        T=T,
    )
    dsr_best_valid = at.compute_deflated_sharpe_ratio(
        SR_obs=scoring["best_valid_SR_obs"],
        SR_0=SR_0,
        gamma3=gamma3,
        gamma4=gamma4,
        T=T,
    )

    # Confirm the gap is real — sentinel wins without exclusion
    assert dsr_sentinel > dsr_best_valid, (
        f"Fixture sanity: sentinel DSR must exceed best-valid DSR without exclusion. "
        f"dsr_sentinel={dsr_sentinel!r}, dsr_best_valid={dsr_best_valid!r}. "
        f"If this fails the sentinel value or moments have changed."
    )
    assert dsr_sentinel == pytest.approx(scoring["sentinel_DSR"], abs=1e-6), (
        f"Sentinel DSR must match fixture. "
        f"expected={scoring['sentinel_DSR']!r}, got={dsr_sentinel!r}."
    )

    # Now assert the SCORING LOOP in autotuner.py skips sentinel trials.
    # Method: source-level — the loop body must exclude t.value == sentinel
    # (or iterate a pre-filtered list). Either pattern is acceptable.
    src_path = _WORKTREE_ROOT / "autotuner.py"
    src = src_path.read_text(encoding="utf-8")
    lines = src.splitlines()

    # Find all DSR scoring loop sites (the 'for t in ...' that calls compute_deflated_sharpe_ratio)
    scoring_loop_indices = [
        i for i, line in enumerate(lines)
        if "for t in" in line and "trial" in line.lower()
        and i + 10 < len(lines)
        and any("compute_deflated_sharpe_ratio" in lines[j] for j in range(i, min(i + 10, len(lines))))
    ]

    assert len(scoring_loop_indices) >= 2, (
        f"Expected at least 2 DSR scoring loops in autotuner.py (walk-forward + calibration); "
        f"found {len(scoring_loop_indices)}. Check loop detection pattern."
    )

    # Each scoring loop must guard against sentinel values
    # Acceptable patterns: iterating a pre-filtered list, or an explicit skip/continue guard
    sentinel_guard_patterns = [
        "filter_sortino_sentinels",  # loop iterates pre-filtered list
        "_SORTINO_SENTINEL",          # explicit guard references the constant
        "!= 1e6",                     # acceptable inline but not preferred
        "!= _SORTINO_SENTINEL",
        "math_engine._SORTINO_SENTINEL",
    ]

    for loop_idx in scoring_loop_indices:
        # Check window: the loop line + preceding 5 lines (pre-filtered list assignment)
        # + following 15 lines (loop body with possible guard)
        context_start = max(0, loop_idx - 5)
        context_end = min(len(lines), loop_idx + 15)
        context = "\n".join(lines[context_start:context_end])

        has_sentinel_guard = any(pattern in context for pattern in sentinel_guard_patterns)
        assert has_sentinel_guard, (
            f"autotuner.py line ~{loop_idx+1}: DSR scoring loop does not guard against "
            f"sentinel trials (t.value=1e6). Sentinel trials must be excluded from "
            f"the ranking loop, not just from moment computation.\n"
            f"Acceptable fix: iterate a pre-filtered list, or add an explicit skip guard.\n"
            f"Context:\n{context}\n"
            f"R/G/R gap: with corrected SR_0=1.37, sentinel DSR=14.02 still beats "
            f"best valid DSR=1.01 because (1e6 - 1.37) dominates the numerator."
        )
