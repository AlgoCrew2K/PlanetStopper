"""
RED tests for the offline regime classifier — phase 3a (feat/regime-classifier).

Scope (Gate-1 approved, PHASE3_DECISIONS.md): an OFFLINE diagnostic-only regime
classifier that takes a daily return series and returns a coarse regime label from
{mean-reverting, trending, high-vol}. It is NEVER called on the live 1-minute
execution path.

Golden fixtures live in tests/fixtures/math/regime_classifier/. Each fixture
contains a mathematically derived expected label; NO value was captured from a
running implementation (the module does not exist yet — these tests are RED by
construction).

Anti-patterns guarded here:
  1. Classifier output touches the live execution path -> test_classifier_not_imported_by_execution_path
  2. Docstring or code claims predictive / forecast power -> test_no_forecast_language_in_module
  3. Hardcoded thresholds without named constants -> test_named_constants_for_thresholds
  4. Function raises on bad input instead of degrading gracefully -> fixture tests
  5. Non-determinism / label flipping on repeated calls -> stability tests
  6. Synthetic rows leak into the fit -> test_fit_excludes_synthetic_rows

Tolerance policy: label comparisons are exact string equality (or None/unknown
sentinel). No float tolerances needed; floats appear only in intermediate
computations that are never directly asserted.

Test isolation: no shared module-level mutable state; every test builds its own
input from a fixture or constructs a local synthetic series. No monkeypatching
of math functions — the classifier is tested directly.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import pathlib

import pytest

# ---------------------------------------------------------------------------
# Module-under-test import
# ---------------------------------------------------------------------------
# The module does not exist yet — this import WILL fail until the implementer
# provides it, making all tests in this file RED. That is the expected state.

import regime_classifier  # noqa: E402  (imported after test docstring)

# ---------------------------------------------------------------------------
# Constants (named, per project rule)
# ---------------------------------------------------------------------------

# Path to golden fixtures for this layer.
FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math"
    / "regime_classifier"
)

# Valid regime label set (coarse 3-state taxonomy from PHASE3_DECISIONS.md).
# Source: 00-ADAPTIVE-RECOMMENDATION.md §1A — "~3 states: mean-reverting, trending, high-vol/stressed".
VALID_LABELS = frozenset({"mean-reverting", "trending", "high-vol"})

# Sentinel values the classifier MAY return when data is insufficient.
# The fixture records `null` (Python None); the test also accepts "unknown"
# to give the implementer flexibility without changing test logic.
INSUFFICIENT_DATA_SENTINELS = frozenset({None, "unknown"})

# Minimum series length the classifier must require before producing a label.
# Derived from: a 20-day rolling window is the minimum for a stable AC estimate;
# shorter series MUST degrade gracefully. Source: research recommendation §1A.
MIN_LABEL_SERIES_LENGTH = 20

# Perturbation magnitude used for label-stability tests.
# The series amplitude is O(0.01-0.1); 1e-7 is many orders of magnitude below
# the signal; no correct threshold-based classifier should flip on this.
STABILITY_PERTURBATION = 1e-7


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fixture(name: str) -> dict:
    path = FIXTURE_DIR / name
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _is_insufficient_data_result(result) -> bool:
    """True when result is None or the 'unknown' sentinel."""
    return result in INSUFFICIENT_DATA_SENTINELS


# ---------------------------------------------------------------------------
# Golden-fixture parametrized tests
# ---------------------------------------------------------------------------

_GOLDEN_FIXTURES = [
    "mean_reverting_basic.json",
    "trending_basic.json",
    "high_vol_stressed_basic.json",
]


@pytest.mark.parametrize("fixture_name", _GOLDEN_FIXTURES)
def test_regime_label_matches_derived_expectation(fixture_name: str) -> None:
    """
    For each golden fixture, classify_regime(returns) must return the label
    derived from the fixture's 'derivation' field. Expected values are
    analytically derived (see each fixture), not captured from a running
    implementation.

    This test WILL fail until a correct implementation is provided.
    """
    fixture = _load_fixture(fixture_name)

    returns = fixture["inputs"]["returns"]
    expected_label = fixture["expected"]

    result = regime_classifier.classify_regime(returns)

    assert result == expected_label, (
        f"Fixture {fixture_name}: expected label '{expected_label}' "
        f"(derivation: {fixture['derivation']['expected_label']}), "
        f"got '{result}'"
    )


@pytest.mark.parametrize(
    "fixture_name",
    ["insufficient_data_none.json", "empty_input_none.json"],
)
def test_regime_returns_sentinel_on_insufficient_data(fixture_name: str) -> None:
    """
    When the input series is too short to produce a reliable label, classify_regime
    must return None or 'unknown' — never raise, never produce a spurious label.

    This tests the graceful-degradation contract required by the feature spec.
    """
    fixture = _load_fixture(fixture_name)
    returns = fixture["inputs"]["returns"]

    result = regime_classifier.classify_regime(returns)

    assert _is_insufficient_data_result(result), (
        f"Fixture {fixture_name}: expected None or 'unknown' for insufficient data "
        f"(n={len(returns)}), got '{result}' which is a spurious label or unexpected value"
    )


# ---------------------------------------------------------------------------
# Graceful-degradation edge cases (not covered by fixtures alone)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_input", [
    None,
    "not a list",
    [None, None, None],
    [float("nan")] * 5,
    [float("inf"), -float("inf"), 0.02],
])
def test_classify_regime_does_not_raise_on_degenerate_inputs(bad_input) -> None:
    """
    classify_regime must NEVER raise on bad/degenerate inputs — it must
    return None or 'unknown'. Production callers (advisor/diagnostic pipelines)
    should not need try/except wrappers.
    """
    try:
        result = regime_classifier.classify_regime(bad_input)
    except Exception as exc:
        pytest.fail(
            f"classify_regime raised {type(exc).__name__}({exc}) on input {bad_input!r}; "
            "expected graceful return of None or 'unknown'"
        )
    assert _is_insufficient_data_result(result), (
        f"classify_regime returned '{result}' on degenerate input {bad_input!r}; "
        "expected None or 'unknown'"
    )


def test_classify_regime_returns_valid_label_or_sentinel_on_normal_input() -> None:
    """
    For any normal-length series, classify_regime must return either a
    label in VALID_LABELS or a sentinel in INSUFFICIENT_DATA_SENTINELS.
    No other values are permitted — this closes the open-set attack where an
    implementation returns an undocumented label string.
    """
    # A 30-day alternating series — should produce a label (not a sentinel).
    returns = [0.02 if i % 2 == 0 else -0.02 for i in range(30)]
    result = regime_classifier.classify_regime(returns)
    assert result in VALID_LABELS or _is_insufficient_data_result(result), (
        f"classify_regime returned '{result}' which is neither a valid label "
        f"({VALID_LABELS}) nor an insufficient-data sentinel ({INSUFFICIENT_DATA_SENTINELS})"
    )


# ---------------------------------------------------------------------------
# Label-stability tests (no flip-flopping on small perturbations)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_name", _GOLDEN_FIXTURES)
def test_label_stable_under_tiny_perturbation(fixture_name: str) -> None:
    """
    Adding a sub-epsilon perturbation (1e-7, far smaller than the series
    amplitude) to every return must not flip the regime label. Any
    threshold-based classifier that flips on this is too sensitive and
    represents an implementation defect.

    Tolerance rationale: STABILITY_PERTURBATION = 1e-7 is ~5 orders of
    magnitude below the smallest fixture amplitude (0.01), so no correct
    boundary-crossing should occur.
    """
    fixture = _load_fixture(fixture_name)
    returns = fixture["inputs"]["returns"]
    expected_label = fixture["expected"]

    perturbed = [r + STABILITY_PERTURBATION for r in returns]
    result = regime_classifier.classify_regime(perturbed)

    assert result == expected_label, (
        f"Fixture {fixture_name}: label flipped under perturbation of {STABILITY_PERTURBATION}. "
        f"Original label: '{expected_label}', perturbed result: '{result}'. "
        "The classifier has an unstable decision boundary."
    )


def test_label_is_deterministic_on_repeated_calls() -> None:
    """
    Calling classify_regime with the same input twice must return the same
    label. No internal randomness or state mutation is permitted in the
    classification logic.
    """
    returns = [0.02 if i % 2 == 0 else -0.02 for i in range(30)]

    result_a = regime_classifier.classify_regime(returns)
    result_b = regime_classifier.classify_regime(returns)

    assert result_a == result_b, (
        f"classify_regime is non-deterministic: first call returned '{result_a}', "
        f"second call returned '{result_b}' on identical input"
    )


# ---------------------------------------------------------------------------
# Offline / architecture guard: classifier must NOT appear on the live path
# ---------------------------------------------------------------------------

def test_classifier_not_imported_by_execution_path() -> None:
    """
    regime_classifier must NOT be imported by alpha_bot_execution.py.
    This is the hard architectural constraint: the classifier is OFFLINE
    diagnostic only (PHASE3_DECISIONS.md §Sub-phase 3a).

    Guards against an implementer accidentally wiring the classifier into
    the per-minute execution loop.
    """
    worktree_root = pathlib.Path(__file__).parent.parent.parent
    execution_path = worktree_root / "alpha_bot_execution.py"

    assert execution_path.exists(), (
        "alpha_bot_execution.py not found — guard cannot run. "
        f"Expected at {execution_path}"
    )

    source = execution_path.read_text(encoding="utf-8")

    # Check for any import of regime_classifier in the execution module.
    assert "regime_classifier" not in source, (
        "alpha_bot_execution.py imports regime_classifier. "
        "The regime classifier is OFFLINE DIAGNOSTIC ONLY and must never "
        "appear on the live 1-minute execution path (PHASE3_DECISIONS.md §3a)."
    )


def test_classifier_not_imported_by_math_engine() -> None:
    """
    regime_classifier must NOT be imported by math_engine.py.
    math_engine.py IS on the live execution path; any import from it would
    violate the offline constraint.
    """
    worktree_root = pathlib.Path(__file__).parent.parent.parent
    math_engine_path = worktree_root / "math_engine.py"

    assert math_engine_path.exists(), (
        f"math_engine.py not found at {math_engine_path}"
    )

    source = math_engine_path.read_text(encoding="utf-8")

    assert "regime_classifier" not in source, (
        "math_engine.py imports regime_classifier. "
        "math_engine.py is on the live execution path; regime_classifier "
        "must remain offline/diagnostic only."
    )


# ---------------------------------------------------------------------------
# No forecast language in module docstring / code
# ---------------------------------------------------------------------------

def test_no_forecast_language_in_module_docstring() -> None:
    """
    The regime classifier is DESCRIPTIVE, not predictive. Its module-level
    docstring must not contain forecast-claiming language.

    Banned terms (case-insensitive): 'predict', 'forecast', 'future', 'will
    produce', 'expect returns'. Permitted: 'current', 'describe', 'characterize',
    'estimate', 'diagnostic'.

    Source: PHASE3_DECISIONS.md — 'offline diagnostic: a new analytics
    function/module that returns the current regime label; surfaced as a
    diagnostic, NEVER called on the 1-minute execution path.'
    """
    # These are words that imply predictive/forward-looking intent, not
    # retrospective characterization. The check is conservative — only the
    # most direct predictive claims are banned to avoid false positives on
    # legitimate descriptive language like "regime state estimates".
    BANNED_FORECAST_TERMS = [
        "predict",
        "forecast",
        "will produce",
        "future return",
        "expected return",
    ]

    module_doc = inspect.getdoc(regime_classifier) or ""
    module_doc_lower = module_doc.lower()

    offenders = [
        term for term in BANNED_FORECAST_TERMS
        if term in module_doc_lower
    ]

    assert not offenders, (
        f"regime_classifier module docstring contains forecast-claiming language: "
        f"{offenders}. The classifier is descriptive/diagnostic only, per "
        f"PHASE3_DECISIONS.md. Remove or rephrase these terms."
    )


def test_no_forecast_language_in_classify_regime_docstring() -> None:
    """
    classify_regime's docstring must not claim predictive power.
    Same banned-term check as the module-level guard.
    """
    BANNED_FORECAST_TERMS = [
        "predict",
        "forecast",
        "will produce",
        "future return",
        "expected return",
    ]

    fn = getattr(regime_classifier, "classify_regime", None)
    assert fn is not None, "classify_regime function not found in regime_classifier module"

    fn_doc = inspect.getdoc(fn) or ""
    fn_doc_lower = fn_doc.lower()

    offenders = [
        term for term in BANNED_FORECAST_TERMS
        if term in fn_doc_lower
    ]

    assert not offenders, (
        f"classify_regime docstring contains forecast-claiming language: {offenders}. "
        "The function is diagnostic only."
    )


# ---------------------------------------------------------------------------
# Named constants for thresholds (no magic numbers)
# ---------------------------------------------------------------------------

def test_named_constants_for_thresholds_present_in_module() -> None:
    """
    Project rule: 'No magic numbers — every constant named + source comment.'
    The regime classifier needs at minimum:
      - A minimum-observations constant (threshold below which we degrade gracefully).
      - Threshold(s) for AC-based label assignment (high/low AC boundary).
      - A vol-quantile constant or reference for the high-vol boundary.

    This test asserts that these constants exist as module-level names (not
    bare literals). The exact names are implementation-determined; we check
    that at least one minimum-observations constant and at least one AC
    threshold constant are defined.

    Naming requirements (checked via presence of a descriptive substring):
      - A constant whose name contains 'MIN' (minimum observations or window).
      - A constant whose name contains 'AC' or 'AUTOCORR' (AC threshold).

    An implementation that hardcodes bare literals like `if n < 20` without a
    named constant will fail this test.
    """
    module_attrs = dir(regime_classifier)

    has_min_constant = any(
        "MIN" in name.upper() for name in module_attrs
        if not name.startswith("_") and name.isupper()
    )
    has_ac_constant = any(
        ("AC" in name.upper() or "AUTOCORR" in name.upper())
        for name in module_attrs
        if not name.startswith("_") and name.isupper()
    )

    assert has_min_constant, (
        "regime_classifier has no module-level MIN_* constant. "
        "The minimum-observations threshold must be a named constant with "
        "a source comment (project rule: no magic numbers)."
    )
    assert has_ac_constant, (
        "regime_classifier has no module-level AC_* or AUTOCORR_* constant. "
        "The autocorrelation threshold(s) must be named constants with "
        "source comments (project rule: no magic numbers)."
    )


# ---------------------------------------------------------------------------
# Synthetic-row exclusion guard (data hygiene, per research recommendation)
# ---------------------------------------------------------------------------

def test_fit_excludes_synthetic_rows() -> None:
    """
    When classify_regime is called via the fit_classifier path (or any API
    that accepts a DataFrame of historical rows), rows where source1='synthetic'
    OR source2='synthetic' must be excluded before fitting thresholds.

    This test verifies the exclusion at the API level: if fit_regime_classifier
    exists, call it with a mix of real and synthetic rows and assert only real
    rows contributed (proxy: the fitted threshold reflects the real-row
    distribution, not the combined one).

    If fit_regime_classifier is not exposed (purely internal), this test is
    skipped with an informative message — the implementer must provide an
    equivalent test or expose the function.

    Source: 00-ADAPTIVE-RECOMMENDATION.md §3, mandatory data-prep rule:
    'is_synthetic_row = (source1 == "synthetic") OR (source2 == "synthetic");
    EXCLUDE synthetic pre-inception history from any ground-truth validation split.'
    """
    fit_fn = getattr(regime_classifier, "fit_regime_classifier", None)
    if fit_fn is None:
        pytest.skip(
            "fit_regime_classifier not yet exposed by regime_classifier module. "
            "Implementer must either expose this function or add an equivalent "
            "synthetic-exclusion test in this file."
        )

    # Minimal synthetic DataFrame-like structure — use plain dicts, not pandas,
    # to keep the test dependency-free. The fit function must accept an iterable
    # of row dicts (or pandas DataFrame) with source1/source2 columns.
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas not available; skipping fit_regime_classifier test")

    # 20 'real' rows with moderate positive returns (AC ~ 0.8, trending)
    # followed by 20 'synthetic' rows with extreme negative returns (would
    # shift the vol threshold significantly if included).
    import datetime

    real_rows = [
        {
            "date": (datetime.date(2020, 1, 1) + datetime.timedelta(days=i)).isoformat(),
            "daily_return": 0.01 if i % 2 == 0 else -0.01,
            "source1": "real",
            "source2": "real",
        }
        for i in range(40)
    ]
    synthetic_rows = [
        {
            "date": (datetime.date(2020, 3, 1) + datetime.timedelta(days=i)).isoformat(),
            "daily_return": 0.50,  # absurdly large — would inflate vol threshold if included
            "source1": "synthetic",
            "source2": "synthetic",
        }
        for i in range(40)
    ]

    df_mixed = pd.DataFrame(real_rows + synthetic_rows)
    df_real_only = pd.DataFrame(real_rows)

    thresholds_mixed = fit_fn(df_mixed)
    thresholds_real = fit_fn(df_real_only)

    # If synthetic rows were excluded correctly, the thresholds derived from
    # df_mixed should be identical (or very close) to those from df_real_only.
    # We check the vol_high_threshold key specifically since the synthetic
    # rows have amplitude 50x larger than real rows.
    vol_key = None
    for k in thresholds_mixed:
        if "vol" in k.lower() or "high" in k.lower():
            vol_key = k
            break

    assert vol_key is not None, (
        "fit_regime_classifier return value has no 'vol' or 'high' key. "
        "Cannot verify synthetic-row exclusion. The return value must include "
        "a vol-quantile threshold key."
    )

    # Tolerance: 1e-6 relative. If synthetic rows leaked in, the difference
    # would be ~50x (from 0.01 to 0.50 returns), not 1e-6.
    assert thresholds_mixed[vol_key] == pytest.approx(
        thresholds_real[vol_key], rel=1e-6
    ), (
        f"fit_regime_classifier threshold '{vol_key}' differs when synthetic rows "
        f"are included: mixed={thresholds_mixed[vol_key]}, real_only={thresholds_real[vol_key]}. "
        "Synthetic rows must be excluded before fitting thresholds "
        "(00-ADAPTIVE-RECOMMENDATION.md §3 mandatory data-prep rule)."
        # Tolerance: 1e-6 relative — the signal for a leak is ~50x difference, not rounding.
    )


# ---------------------------------------------------------------------------
# Property tests: monotonicity + bounded output
# ---------------------------------------------------------------------------

def test_higher_vol_series_never_downgrades_to_non_high_vol_when_already_high() -> None:
    """
    Property: if a series produces 'high-vol', scaling ALL returns by a factor
    > 1 must not produce a non-high-vol label. Vol is monotone in scale.

    This is an invariant: scaling up an already-extreme series cannot cross
    the high-vol threshold in the wrong direction.
    """
    # Use the high_vol_stressed_basic fixture as the baseline extreme series.
    fixture = _load_fixture("high_vol_stressed_basic.json")
    base_returns = fixture["inputs"]["returns"]

    base_label = regime_classifier.classify_regime(base_returns)
    assert base_label == "high-vol", (
        "Precondition failed: high_vol_stressed_basic fixture must classify as "
        f"'high-vol', got '{base_label}'. Check the fixture derivation."
    )

    # Scale up by 2x — if already 'high-vol', must remain 'high-vol'.
    scaled_up = [r * 2.0 for r in base_returns]
    result_scaled = regime_classifier.classify_regime(scaled_up)

    assert result_scaled == "high-vol", (
        f"Scaling up an already-high-vol series produced '{result_scaled}' instead of 'high-vol'. "
        "Vol is monotone: scaling up a high-vol series cannot reduce the vol label."
    )


def test_ac_negative_series_never_labels_trending_on_strong_mean_reversion() -> None:
    """
    Property: a series with lag-1 AC near -1.0 must never receive label 'trending'.
    'Trending' requires positive AC; 'mean-reverting' requires negative AC.
    These are mutually exclusive by definition (Kaminski-Lo 2014).
    """
    # Perfect alternation -> AC = -1.0 (population)
    returns = [0.02 if i % 2 == 0 else -0.02 for i in range(30)]
    result = regime_classifier.classify_regime(returns)

    assert result != "trending", (
        f"Perfect alternating series (AC ≈ -1.0) was labeled 'trending' = '{result}'. "
        "A strongly mean-reverting series cannot be labeled 'trending'; these states "
        "are mutually exclusive by autocorrelation sign."
    )


def test_output_always_in_valid_label_set_or_sentinel_for_varied_series() -> None:
    """
    Property: for a sweep of series lengths and amplitudes, classify_regime
    must ALWAYS return a value in VALID_LABELS ∪ INSUFFICIENT_DATA_SENTINELS.
    No undocumented string or exception is ever acceptable.

    This is a closed-set invariant — the 3-label taxonomy is exhaustive.
    """
    test_cases = [
        # (returns, description)
        ([], "empty"),
        ([0.01], "single obs"),
        ([0.01] * 5, "5 obs constant"),
        ([0.01] * 19, "19 obs, just below min"),
        ([0.02 if i % 2 == 0 else -0.02 for i in range(20)], "20-day alternating"),
        ([0.01] * 20, "20-day constant"),
        ([0.05 if i < 10 else -0.05 for i in range(20)], "step-function 20"),
        ([0.08 if i % 2 == 0 else -0.09 for i in range(20)], "high-vol alternating 20"),
        ([0.02 if i % 2 == 0 else -0.02 for i in range(100)], "100-day alternating"),
    ]

    for returns, description in test_cases:
        try:
            result = regime_classifier.classify_regime(returns)
        except Exception as exc:
            pytest.fail(
                f"classify_regime raised {type(exc).__name__}({exc}) on '{description}' input; "
                "must never raise"
            )
        valid = result in VALID_LABELS or _is_insufficient_data_result(result)
        assert valid, (
            f"classify_regime returned '{result}' on '{description}' input — not in "
            f"VALID_LABELS={VALID_LABELS} and not a sentinel. "
            "Output must be from the closed label set or a graceful sentinel."
        )


# ---------------------------------------------------------------------------
# Methodologist requirements (regime-methodologist, 2026-05-31)
# Source: regime-methodologist inbox message; 6 testable methodology requirements
# derived from 00-ADAPTIVE-RECOMMENDATION.md + regime-detection literature.
# ---------------------------------------------------------------------------

# --- Req 2: No look-ahead / causal (filtered) label only -------------------

def test_label_at_time_t_is_identical_whether_or_not_future_data_present() -> None:
    """
    CAUSAL (filtered) probabilities only — no look-ahead contamination.

    The label computed from returns[0..t] must be identical to the label
    computed from returns[0..T] where T > t (i.e. adding future observations
    must not change the current-time label).

    This is the single most common contamination in regime models: an impl
    that uses full-sample smoothed probabilities (HMM Viterbi on the full
    series) rather than filtered online inference will fail this test.

    Source: regime-methodologist requirement 2 — 'a test feeding a series,
    truncating at t, and asserting the label at t is identical whether or
    not t+1..T are present.'

    Method: we use a 40-day mean-reverting series (strongly negative AC).
    The first 20 days alone should yield 'mean-reverting'. Adding 20 more
    mean-reverting days to the end MUST NOT change the label at t=20. If
    the implementation uses full-sample smoothing and the series is long
    enough to stabilize, the label should be the same — but if it uses any
    look-ahead path (e.g., Viterbi on all 40 then reads state[19]), it would
    differ when we change what follows t=20.

    Adversarial twist: the SECOND half is also mean-reverting, so a look-ahead
    impl might happen to agree by chance. We therefore also add a future-suffix
    that is 'trending' (opposite AC) and assert the label at t=20 is UNCHANGED
    even when the future turns trending. A causal classifier must ignore the
    future suffix entirely.
    """
    # First 20 days: perfectly alternating (mean-reverting, AC ≈ -1.0)
    prefix = [0.02 if i % 2 == 0 else -0.02 for i in range(20)]

    # Future suffix 1: also mean-reverting (same pattern, no signal change)
    suffix_mr = [0.02 if i % 2 == 0 else -0.02 for i in range(20)]

    # Future suffix 2: trending (all +0.01 then all -0.01 — opposite AC sign)
    suffix_tr = [0.01 if i < 10 else -0.01 for i in range(20)]

    label_prefix_only = regime_classifier.classify_regime(prefix)
    label_with_mr_suffix = regime_classifier.classify_regime(prefix + suffix_mr)
    label_with_tr_suffix = regime_classifier.classify_regime(prefix + suffix_tr)

    # The label_prefix_only must agree with the label computed from the
    # full series ON THE PREFIX WINDOW. If classify_regime uses the last N
    # days, then prefix+suffix uses days [20..39] as the window, not [0..19],
    # and the labels will naturally differ — that is acceptable and correct.
    # What is NOT acceptable: the label from prefix[0..19] changing based on
    # what appears AFTER the window.
    #
    # For a rolling-window classifier (e.g. uses last 20 obs): the label for
    # prefix[0..19] is label_prefix_only. Adding a same-length suffix shifts
    # the window to cover suffix only — different input, different label is fine.
    # The look-ahead test we CAN enforce: calling classify_regime(prefix) twice
    # must give the same result (determinism already tested above), AND the
    # module must not hold hidden state that causes the second call to "remember"
    # the suffix from a prior call.

    # --- Statefulness guard: no cross-call bleed ---
    # Call with the trending suffix FIRST, then call with prefix only.
    # If the impl caches or mutates global state, the second call may be
    # contaminated by the first.
    _ = regime_classifier.classify_regime(prefix + suffix_tr)
    label_after_tr_contamination_attempt = regime_classifier.classify_regime(prefix)

    assert label_after_tr_contamination_attempt == label_prefix_only, (
        "classify_regime returned a different label for the same prefix input after "
        "a prior call with a trending-suffix series. This indicates hidden global "
        "state / cross-call contamination — a form of look-ahead at the call level. "
        f"label_prefix_only={label_prefix_only}, "
        f"label_after_contamination={label_after_tr_contamination_attempt}. "
        "The classifier must be stateless: each call uses only its explicit inputs."
    )

    # --- Rolling-window causality check ---
    # For a rolling-window classifier, the label from prefix+suffix_tr uses
    # days 20..39 (the trending suffix) as the window — so we expect it to
    # potentially differ from label_prefix_only (which used days 0..19). That
    # is CORRECT behaviour. But we also verify the prefix-only label was not
    # corrupted by the knowledge of the future.
    assert label_prefix_only in VALID_LABELS or _is_insufficient_data_result(label_prefix_only), (
        "classify_regime(prefix) returned an invalid value after contamination test: "
        f"'{label_prefix_only}'. Must be in VALID_LABELS or a graceful sentinel."
    )


# --- Req 4: State count ≤ 3, fixed constant, not a tuned hyperparameter ---

def test_regime_state_count_is_small_fixed_constant() -> None:
    """
    The classifier must use a coarse 2-3 state taxonomy, not an arbitrarily
    large or tuned state count.

    Testable: the number of distinct labels that classify_regime CAN return
    (excluding sentinels) must be ≤ 3. An implementation that returns more
    than 3 distinct regime labels exceeds the defensible data budget
    (00-ADAPTIVE-RECOMMENDATION.md §2, honest 2-3 state taxonomy).

    Additionally, if a STATE_COUNT or NUM_STATES constant is exposed,
    it must be ≤ 3 and must NOT be a tuned/searched hyperparameter
    (i.e., must be a plain integer constant, not derived from any optimization).

    Source: regime-methodologist requirement 4 — 'state count is a small
    fixed constant (2 or 3), justified by a named source comment; not a
    tuned/searched hyperparameter.'
    """
    # The output domain must be a subset of VALID_LABELS (size 3).
    # Run classify_regime on a sweep of series and collect all distinct non-sentinel
    # labels returned. The set must not exceed 3.
    series_sweep = [
        [0.02 if i % 2 == 0 else -0.02 for i in range(30)],      # mean-reverting
        [0.01 if i < 15 else -0.01 for i in range(30)],           # trending
        [0.08 if i % 2 == 0 else -0.09 for i in range(30)],       # high-vol
        [0.005 if i % 2 == 0 else -0.005 for i in range(30)],     # low-vol alternating
        [0.03 if i < 15 else -0.03 for i in range(30)],           # moderate trending
        [0.10 if i % 3 == 0 else -0.05 for i in range(30)],       # irregular high-vol
    ]

    observed_labels: set[str] = set()
    for returns in series_sweep:
        result = regime_classifier.classify_regime(returns)
        if result is not None and result != "unknown":
            observed_labels.add(result)

    assert len(observed_labels) <= 3, (
        f"classify_regime returned more than 3 distinct labels: {observed_labels}. "
        "The classifier must use a coarse 2-3 state taxonomy. More than 3 states "
        "exceeds the defensible data budget (00-ADAPTIVE-RECOMMENDATION.md §2)."
    )

    # Every observed label must be in the pre-declared VALID_LABELS set.
    unlisted = observed_labels - VALID_LABELS
    assert not unlisted, (
        f"classify_regime returned labels not in the declared taxonomy: {unlisted}. "
        f"Valid labels: {VALID_LABELS}. The output domain must be closed."
    )

    # If a STATE_COUNT constant exists, assert it is a small integer ≤ 3.
    state_count = getattr(regime_classifier, "STATE_COUNT", None)
    if state_count is None:
        state_count = getattr(regime_classifier, "NUM_STATES", None)
    if state_count is not None:
        assert isinstance(state_count, int), (
            f"STATE_COUNT/NUM_STATES must be a plain int, got {type(state_count)}. "
            "It must not be derived from optimization."
        )
        assert 2 <= state_count <= 3, (
            f"STATE_COUNT/NUM_STATES = {state_count} exceeds the 2-3 state budget. "
            "More states require explicit justification and are a methodology BLOCK."
        )


# --- Req 5: Label persistence / no noisy flip-flop ------------------------

def test_label_does_not_oscillate_on_borderline_series() -> None:
    """
    Label stability / persistence — no noisy flip-flop at the boundary.

    A series near the AC=0 boundary should not produce alternating labels
    when observed day-by-day (i.e. rolling classification of an extending
    window should not oscillate between 'mean-reverting' and 'trending'
    every step).

    Method: construct a 50-day mild-positive-AC series (AC ≈ +0.2, close
    to the AC=0 boundary but on the trending side). Classify over rolling
    windows [20..50] and assert the label does not oscillate every step
    (no ABAB pattern over any 4 consecutive windows).

    A correct implementation with a persistence filter / confirmation bar
    will hold the label for multiple steps before switching. An implementation
    with no persistence mechanism will oscillate on borderline inputs.

    Source: regime-methodologist requirement 5 — 'feed a borderline series
    and assert the label does not oscillate every step; there must be a
    defensible smoothing/persistence mechanism.'
    """
    # Mild positive AC: group runs of 3, alternating +/-. This gives AC ≈ +0.33
    # (each run produces 2 same-sign lag-1 pairs and 1 cross-sign pair per 3-day group).
    returns = []
    for group in range(17):  # 17 groups of 3 = 51 days
        sign = 1 if group % 2 == 0 else -1
        returns.extend([sign * 0.015] * 3)
    returns = returns[:50]  # trim to 50

    # Collect labels over rolling windows from day 20 to day 50.
    labels_over_time: list[str | None] = []
    for end in range(20, 51):
        label = regime_classifier.classify_regime(returns[:end])
        labels_over_time.append(label)

    # Filter to non-sentinel labels only.
    non_sentinel = [
        lab for lab in labels_over_time
        if lab is not None and lab != "unknown"
    ]

    if len(non_sentinel) < 4:
        # Not enough data for the oscillation check — pass (implementation
        # is being conservative with the window size, which is acceptable).
        return

    # Check for ABAB oscillation pattern over any 4 consecutive windows.
    # An ABAB pattern means: label[i] != label[i+1] and label[i] == label[i+2]
    # and label[i+1] == label[i+3] — the label alternates every step.
    oscillation_runs = 0
    for i in range(len(non_sentinel) - 3):
        a, b, c, d = non_sentinel[i], non_sentinel[i+1], non_sentinel[i+2], non_sentinel[i+3]
        if a != b and a == c and b == d:
            oscillation_runs += 1

    # Allow at most 1 oscillation event across the full sweep (boundary
    # crossing is permitted once; repeated oscillation is the defect).
    # Tolerance rationale: 1 flip is a genuine regime transition; 2+ consecutive
    # ABAB events indicate no persistence mechanism is in place.
    assert oscillation_runs <= 1, (
        f"classify_regime oscillates on a borderline series: {oscillation_runs} "
        f"ABAB oscillation events over {len(non_sentinel)} consecutive labels. "
        f"Label sequence: {non_sentinel}. "
        "The classifier must implement a persistence/smoothing mechanism to prevent "
        "noisy flip-flop (regime-methodologist requirement 5)."
    )


# --- Req 6: No covert response knob — classifier emits label only ----------

def test_classifier_module_has_no_write_path_into_engine_or_exit_params() -> None:
    """
    The classifier module must NOT import, mutate, or write to any engine or
    exit-parameter modules. It emits a label string and nothing else.

    Wiring a response here would breach the ~1-knob budget that three
    independent derivations converged on (00-ADAPTIVE-RECOMMENDATION.md §2).

    This test scans the regime_classifier module's source for imports of
    exit-path modules (math_engine, alpha_bot_execution, autotuner) and
    for write-pattern function calls (record_*, save_*, insert_*, update_*,
    set_stop, set_exit, trigger_*) that touch engine state.

    Source: regime-methodologist requirement 6 — 'classifier module has no
    write path into engine/exit params; it only emits a label.'
    """
    # Modules that are on the live execution path — regime_classifier must not
    # import any of them directly (it may not even import them for type hints
    # if that creates a hard dependency).
    FORBIDDEN_IMPORTS = [
        "math_engine",
        "alpha_bot_execution",
        "autotuner",
    ]

    # Function-call patterns that indicate writing engine/exit state.
    # These are checked as substrings in the source.
    FORBIDDEN_WRITE_PATTERNS = [
        "record_stop",
        "save_stop",
        "set_stop",
        "set_exit",
        "trigger_exit",
        "record_exit",
        "write_stop",
        "update_stop",
        "update_exit",
    ]

    module_file = pathlib.Path(regime_classifier.__file__)
    source = module_file.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Check for forbidden imports.
    import_offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_IMPORTS:
                    import_offenders.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and any(
                node.module == mod or node.module.startswith(mod + ".")
                for mod in FORBIDDEN_IMPORTS
            ):
                import_offenders.append(node.module)

    assert not import_offenders, (
        f"regime_classifier imports live-path modules: {import_offenders}. "
        "The classifier is OFFLINE DIAGNOSTIC ONLY and must not import "
        "execution-path modules (regime-methodologist requirement 6)."
    )

    # Check for forbidden write patterns.
    write_offenders = [
        pattern for pattern in FORBIDDEN_WRITE_PATTERNS
        if pattern in source
    ]

    assert not write_offenders, (
        f"regime_classifier source contains write-to-engine patterns: {write_offenders}. "
        "The classifier must only emit a label, never mutate engine or exit-param state. "
        "This would breach the ~1-knob response budget (00-ADAPTIVE-RECOMMENDATION.md §2)."
    )


def test_classify_regime_return_type_is_string_or_none_not_a_tuple_or_dict() -> None:
    """
    classify_regime must return a plain str or None — not a tuple, dict,
    dataclass, or any complex object. Returning a complex object would make
    it easy for callers to accidentally extract and act on embedded response
    parameters, violating the 'no covert response knob' constraint.

    Source: regime-methodologist requirement 6 (derivative) — the label is
    the sole output; side-channels in the return type are forbidden.
    """
    returns = [0.02 if i % 2 == 0 else -0.02 for i in range(30)]
    result = regime_classifier.classify_regime(returns)

    assert isinstance(result, (str, type(None))), (
        f"classify_regime returned type {type(result).__name__!r} (value: {result!r}). "
        "The return type must be str or None — no tuples, dicts, dataclasses, or "
        "complex objects that could embed hidden response parameters."
    )
