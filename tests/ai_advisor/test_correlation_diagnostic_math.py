"""
M1 RED — correlation diagnostic math layer (AC-1.1 / AC-1.2 / AC-1.3).

Module under test: advisors.correlation_diagnostic
  compute_pairwise_correlations(return_series: dict[str, list[float]])
      -> list[PairResult]

PairResult contract (binding for GREEN):
    sym_a:       str   — first symphony name (lexically <=  sym_b)
    sym_b:       str   — second symphony name
    n_obs:       int   — number of overlapping (finite, non-NaN) observations
    correlation: float — Pearson r (fraction); None when n_obs < 2
    thin_data:   bool  — True when n_obs < THIN_DATA_THRESHOLD (30)

The module must also expose:
    THIN_DATA_THRESHOLD: int = 30   (same floor as _V1_BOOTSTRAP_MIN_DAYS in analytics.py)

Data-source contract (answer to the open question):
    The per-symphony return series used by callers is the ``live_ret``
    (shadow_return) column produced by analytics.compute_per_symphony_returns
    and analytics.load_post_mortem_history. The correlation diagnostic consumes
    an already-extracted {symphony_id: [float, ...]} dict; it does NOT fetch
    data itself (off-the-live-path, pure computation).

Fixture provenance (tests/fixtures/math/correlation_diagnostic_*.json):
    Inputs are synthetic. Expected correlations are hand-derived algebraic
    results for those specific series (see each fixture's _derivation key).
    Tests assert fixture-derived values, never hardcoded producer outputs.

RED status: every test here fails because advisors/correlation_diagnostic.py
does not yet exist. GREEN must implement exactly the contract above.

Math engine is NOT mocked. Network and time are not used. All fixtures are
function-scoped (no shared mutable state).
"""

from __future__ import annotations

import json
import math
import pathlib

import pytest

# ---------------------------------------------------------------------------
# Import — RED: module does not exist yet; ImportError is the first failure
# ---------------------------------------------------------------------------

try:
    from advisors.correlation_diagnostic import (
        THIN_DATA_THRESHOLD,
        compute_pairwise_correlations,
    )
    _IMPORT_OK = True
except ImportError:
    _IMPORT_OK = False


pytestmark = pytest.mark.skipif(
    not _IMPORT_OK,
    reason="advisors.correlation_diagnostic not yet implemented (RED state)",
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "math"
)


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# RED-1: THIN_DATA_THRESHOLD constant is 30 (matching the Bailey/de-Prado
# interpretability floor used elsewhere in the codebase — same value as
# analytics._V1_BOOTSTRAP_MIN_DAYS).
# ---------------------------------------------------------------------------


def test_thin_data_threshold_is_thirty():
    """THIN_DATA_THRESHOLD must be 30 — the Bailey/de-Prado 2014 floor also
    used in analytics._V1_BOOTSTRAP_MIN_DAYS. A different threshold means
    the diagnostic and the existing analytics layer disagree on what
    'sufficient data' means.
    """
    assert THIN_DATA_THRESHOLD == 30


# ---------------------------------------------------------------------------
# RED-2 / RED-3: golden-fixture basic case — 3 series, known correlations
# ---------------------------------------------------------------------------


def test_pairwise_correlations_known_positive_correlation(tmp_path):
    """symphony_a and symphony_b are identical series; Pearson r must be 1.0.

    Tolerance 1e-9: these are exact algebraic results; only IEEE-754 rounding
    contributes error (< 1e-15 for float64). 1e-9 is tight enough to catch
    any implementation divergence while allowing for platform rounding noise.
    """
    fixture = _load_fixture("correlation_diagnostic_basic.json")
    series = fixture["inputs"]["return_series"]
    tol = fixture["_correlation_tolerance"]

    results = compute_pairwise_correlations(series)
    result_map = {(r.sym_a, r.sym_b): r for r in results}

    pair = result_map[("symphony_a", "symphony_b")]
    assert pair.n_obs == 5
    assert pair.correlation == pytest.approx(1.0, abs=tol), (
        "symphony_a and symphony_b are identical series; Pearson r must be 1.0"
    )


def test_pairwise_correlations_known_negative_correlation():
    """symphony_a and symphony_c are perfect inverses; Pearson r must be -1.0."""
    fixture = _load_fixture("correlation_diagnostic_basic.json")
    series = fixture["inputs"]["return_series"]
    tol = fixture["_correlation_tolerance"]

    results = compute_pairwise_correlations(series)
    result_map = {(r.sym_a, r.sym_b): r for r in results}

    pair = result_map[("symphony_a", "symphony_c")]
    assert pair.n_obs == 5
    assert pair.correlation == pytest.approx(-1.0, abs=tol), (
        "symphony_a is the inverse of symphony_c; Pearson r must be -1.0"
    )


def test_pairwise_correlations_all_three_pairs_present():
    """With 3 symphonies the result must contain exactly 3 pairs (C(3,2)=3)."""
    fixture = _load_fixture("correlation_diagnostic_basic.json")
    series = fixture["inputs"]["return_series"]

    results = compute_pairwise_correlations(series)

    assert len(results) == 3, (
        f"3 symphonies → 3 pairs expected, got {len(results)}"
    )


def test_pairwise_correlations_pairs_are_lexically_ordered():
    """sym_a <= sym_b for every pair (lexical order) so the matrix is
    deduplicated and callers can use (sym_a, sym_b) as a canonical key.
    """
    fixture = _load_fixture("correlation_diagnostic_basic.json")
    series = fixture["inputs"]["return_series"]

    results = compute_pairwise_correlations(series)

    for r in results:
        assert r.sym_a <= r.sym_b, (
            f"pair ({r.sym_a!r}, {r.sym_b!r}) is not in lexical order; "
            "sym_a must be <= sym_b so callers have a canonical key"
        )


def test_pairwise_correlations_n_obs_matches_series_length():
    """n_obs must equal the number of overlapping (finite) observations.

    All three fixture series have 5 observations, all finite — n_obs must be 5
    for every pair.
    """
    fixture = _load_fixture("correlation_diagnostic_basic.json")
    series = fixture["inputs"]["return_series"]

    results = compute_pairwise_correlations(series)

    for r in results:
        assert r.n_obs == 5, (
            f"all series have 5 observations; expected n_obs=5, got {r.n_obs} "
            f"for pair ({r.sym_a!r}, {r.sym_b!r})"
        )


# ---------------------------------------------------------------------------
# RED-4: thin-data flag — n_obs=5 is below threshold, must be flagged
# ---------------------------------------------------------------------------


def test_thin_data_flagged_when_n_obs_below_threshold():
    """n_obs=5 < THIN_DATA_THRESHOLD(30); thin_data must be True for every pair.

    This is AC-1.2: each figure shows its sample basis so thin-data estimates
    are visible to the operator.
    """
    fixture = _load_fixture("correlation_diagnostic_basic.json")
    series = fixture["inputs"]["return_series"]

    results = compute_pairwise_correlations(series)

    for r in results:
        assert r.thin_data is True, (
            f"n_obs={r.n_obs} < {THIN_DATA_THRESHOLD}; thin_data must be True "
            f"for pair ({r.sym_a!r}, {r.sym_b!r})"
        )


def test_thin_data_flagged_at_exactly_29_obs():
    """n_obs=29 is one below the threshold; thin_data must be True.

    The boundary case: an implementation that uses strict-less-than correctly
    flags 29 as thin but not 30. Off-by-one here is a real risk.
    """
    fixture = _load_fixture("correlation_diagnostic_thin_data.json")
    series = fixture["inputs"]["return_series"]
    expected = fixture["expected_pair"]

    results = compute_pairwise_correlations(series)
    assert len(results) == 1
    r = results[0]

    assert r.n_obs == expected["n_obs"], f"expected n_obs={expected['n_obs']}, got {r.n_obs}"
    assert r.thin_data is True, "n_obs=29 is below threshold (30); thin_data must be True"


def test_thin_data_not_flagged_at_exactly_30_obs():
    """n_obs=30 meets the threshold exactly; thin_data must be False.

    Off-by-one mirror: >= 30 is not thin, < 30 is thin.
    """
    fixture = _load_fixture("correlation_diagnostic_sufficient_data.json")
    series = fixture["inputs"]["return_series"]
    expected = fixture["expected_pair"]

    results = compute_pairwise_correlations(series)
    assert len(results) == 1
    r = results[0]

    assert r.n_obs == expected["n_obs"], f"expected n_obs={expected['n_obs']}, got {r.n_obs}"
    assert r.thin_data is False, "n_obs=30 equals threshold; thin_data must be False (not strictly thin)"


# ---------------------------------------------------------------------------
# RED-5: edge cases — AC-1.3 "never crash, never NaN-leak"
# ---------------------------------------------------------------------------


def test_single_symphony_returns_empty_list():
    """One symphony means zero pairs: C(1,2) = 0. Must return an empty list,
    never raise.

    AC-1.3 degenerate-portfolio edge case.
    """
    results = compute_pairwise_correlations({"only_one": [0.01, 0.02, -0.01]})

    assert results == [], (
        f"single symphony → no pairs; expected [], got {results!r}"
    )


def test_empty_portfolio_returns_empty_list():
    """Zero symphonies → zero pairs. Must return [], never raise.

    AC-1.3: empty portfolio is a well-shaped 'insufficient data' state.
    """
    results = compute_pairwise_correlations({})

    assert results == [], "empty portfolio must return []"


def test_no_overlapping_dates_returns_none_correlation():
    """Two symphonies with zero overlapping observations: correlation must be
    None (not NaN, not a fabricated number), n_obs must be 0.

    AC-1.3 NaN-leak guard: a None result is a valid 'insufficient data' signal;
    a NaN leaking into the UI is a crash vector.
    """
    # symphony_m has only dates 1-5; symphony_n has only dates 6-10.
    # If the implementation aligns by position rather than by date, this test
    # will yield a bogus non-None r — which is the adversarial trap.
    # Implementation must align by shared date keys.
    series = {
        "symphony_m": [0.01, 0.02, -0.01, 0.03, -0.02],
        "symphony_n": [0.01, 0.02, -0.01, 0.03, -0.02],
    }
    # Both series have values but share ZERO dates if we use date-keyed dicts.
    # For list-based input without explicit dates, the contract is: if the
    # implementation accepts plain lists it aligns by index — in that case
    # the test below for date-keyed input is the adversarial check.
    # For plain-list input the overlap IS 5; pass date-keyed to trigger no-overlap.
    date_keyed_series = {
        "symphony_m": {
            "2026-01-02": 0.01,
            "2026-01-03": 0.02,
            "2026-01-06": -0.01,
            "2026-01-07": 0.03,
            "2026-01-08": -0.02,
        },
        "symphony_n": {
            "2026-01-09": 0.01,
            "2026-01-10": 0.02,
            "2026-01-13": -0.01,
            "2026-01-14": 0.03,
            "2026-01-15": -0.02,
        },
    }
    # compute_pairwise_correlations must accept both list[float] and
    # dict[str, float] per-symphony series (date-keyed for correct overlap).
    results = compute_pairwise_correlations(date_keyed_series)

    assert len(results) == 1
    r = results[0]
    assert r.n_obs == 0, f"no overlapping dates → n_obs must be 0, got {r.n_obs}"
    assert r.correlation is None, (
        f"no overlapping dates → correlation must be None (no data), got {r.correlation!r}. "
        "A fabricated or NaN value here is a data-integrity failure."
    )


def test_single_overlapping_observation_returns_none_correlation():
    """n_obs=1 is insufficient for Pearson r (need >= 2). Correlation must be
    None, n_obs must be 1, no ZeroDivisionError or NaN.
    """
    # Two series sharing exactly one date.
    date_keyed_series = {
        "sym_x": {"2026-01-02": 0.01, "2026-01-03": 0.02},
        "sym_y": {"2026-01-02": 0.01, "2026-01-09": 0.05},
    }
    results = compute_pairwise_correlations(date_keyed_series)

    assert len(results) == 1
    r = results[0]
    assert r.n_obs == 1
    assert r.correlation is None, (
        "n_obs=1 is insufficient for Pearson r; correlation must be None"
    )


def test_zero_variance_series_does_not_raise_and_returns_none():
    """A constant series has zero variance; Pearson r is undefined (division
    by zero in the denominator). Must return None, never raise ZeroDivisionError
    or produce NaN.

    This is an adversarial edge case: a naively-implemented Pearson formula
    divides by the product of standard deviations; when one is zero, the
    result is inf or nan. None is the correct sentinel.
    """
    # symphony_flat is constant at 0.01 — zero variance.
    series = {
        "symphony_flat": [0.01, 0.01, 0.01, 0.01, 0.01],
        "symphony_normal": [0.01, 0.02, -0.01, 0.03, -0.02],
    }
    results = compute_pairwise_correlations(series)

    assert len(results) == 1
    r = results[0]
    assert r.correlation is None, (
        "zero-variance series → Pearson r is undefined; must return None, not NaN or inf"
    )
    assert r.n_obs == 5  # 5 overlapping values despite undefined r


def test_nan_values_in_series_are_excluded_from_n_obs():
    """NaN values in a return series must be filtered before computing
    Pearson r. n_obs must reflect only the finite, non-NaN overlapping
    observations — not the raw series length.
    """
    # 5 values each; 2 are NaN in series_a (indices 1 and 3).
    # After filtering for finite values in BOTH series, overlap = 3.
    series = {
        "series_a": [0.01, float("nan"), -0.01, float("nan"), 0.02],
        "series_b": [0.01, 0.02, -0.01, 0.03, 0.02],
    }
    results = compute_pairwise_correlations(series)

    assert len(results) == 1
    r = results[0]
    assert r.n_obs == 3, (
        f"2 NaN values in series_a → 3 valid overlapping obs; got n_obs={r.n_obs}"
    )
    # Correlation must be a finite float or None (if < 2 obs), never NaN.
    if r.correlation is not None:
        assert math.isfinite(r.correlation), (
            "correlation must be a finite float, not NaN or inf"
        )


def test_correlation_is_always_bounded_in_minus_one_to_plus_one():
    """Pearson r ∈ [-1, 1] is a mathematical identity. Any result outside
    this range is an implementation error (e.g., wrong normalisation).

    Adversarial: if GREEN uses a non-standard formula it may produce |r| > 1
    for near-collinear series with floating-point accumulation error.
    """
    # Use the fixture series which produce exact ±1.0 values.
    fixture = _load_fixture("correlation_diagnostic_basic.json")
    series = fixture["inputs"]["return_series"]

    results = compute_pairwise_correlations(series)

    for r in results:
        if r.correlation is not None:
            assert -1.0 <= r.correlation <= 1.0, (
                f"Pearson r must be in [-1, 1]; got {r.correlation!r} "
                f"for pair ({r.sym_a!r}, {r.sym_b!r})"
            )


# ---------------------------------------------------------------------------
# RED-6: result is a list of named-attribute objects (not dicts, not tuples)
# so callers can use r.sym_a, r.sym_b, r.n_obs, r.correlation, r.thin_data
# ---------------------------------------------------------------------------


def test_pair_result_has_required_attributes():
    """PairResult objects must expose sym_a, sym_b, n_obs, correlation,
    thin_data as attributes (dataclass, namedtuple, or equivalent).
    Plain dicts or 5-tuples are NOT acceptable — callers use attribute access.
    """
    fixture = _load_fixture("correlation_diagnostic_basic.json")
    series = fixture["inputs"]["return_series"]

    results = compute_pairwise_correlations(series)
    assert results, "expected at least one pair"

    r = results[0]
    for attr in ("sym_a", "sym_b", "n_obs", "correlation", "thin_data"):
        assert hasattr(r, attr), (
            f"PairResult must have a `{attr}` attribute; dict or tuple not acceptable. "
            f"Got: {type(r).__name__!r}"
        )


def test_correlation_field_is_float_or_none():
    """correlation must be a Python float or None — never a numpy scalar,
    never a string. UI templates and JSON serialisation both require plain Python
    primitives.
    """
    fixture = _load_fixture("correlation_diagnostic_basic.json")
    series = fixture["inputs"]["return_series"]

    results = compute_pairwise_correlations(series)

    for r in results:
        assert r.correlation is None or isinstance(r.correlation, float), (
            f"correlation must be float or None, got {type(r.correlation).__name__!r} "
            f"for pair ({r.sym_a!r}, {r.sym_b!r}). numpy scalars are not acceptable."
        )


def test_n_obs_field_is_int():
    """n_obs must be a Python int, not a numpy integer or a float."""
    fixture = _load_fixture("correlation_diagnostic_basic.json")
    series = fixture["inputs"]["return_series"]

    results = compute_pairwise_correlations(series)

    for r in results:
        assert isinstance(r.n_obs, int), (
            f"n_obs must be a Python int, got {type(r.n_obs).__name__!r} "
            f"for pair ({r.sym_a!r}, {r.sym_b!r})"
        )


def test_thin_data_field_is_bool():
    """thin_data must be a Python bool, not an int 0/1 or a numpy bool."""
    fixture = _load_fixture("correlation_diagnostic_basic.json")
    series = fixture["inputs"]["return_series"]

    results = compute_pairwise_correlations(series)

    for r in results:
        assert isinstance(r.thin_data, bool), (
            f"thin_data must be a Python bool, got {type(r.thin_data).__name__!r} "
            f"for pair ({r.sym_a!r}, {r.sym_b!r})"
        )


# ---------------------------------------------------------------------------
# RED-7: no duplicate pairs (diagonal or swapped)
# ---------------------------------------------------------------------------


def test_no_self_pairs_in_result():
    """The diagonal (sym == sym) must never appear in the result list.
    Self-correlation is always 1.0 and carries no information; surfacing it
    in the matrix would confuse the operator.
    """
    fixture = _load_fixture("correlation_diagnostic_basic.json")
    series = fixture["inputs"]["return_series"]

    results = compute_pairwise_correlations(series)

    for r in results:
        assert r.sym_a != r.sym_b, (
            f"self-pair ({r.sym_a!r}, {r.sym_b!r}) must not appear in results"
        )


def test_no_duplicate_swapped_pairs():
    """With n symphonies, the result must contain exactly C(n,2) distinct pairs.
    Pairs (a,b) and (b,a) are the same pair and must appear only once.
    """
    fixture = _load_fixture("correlation_diagnostic_basic.json")
    series = fixture["inputs"]["return_series"]  # 3 symphonies → 3 pairs

    results = compute_pairwise_correlations(series)

    # Canonical key: frozenset so order doesn't matter
    seen: set = set()
    for r in results:
        key = frozenset([r.sym_a, r.sym_b])
        assert key not in seen, (
            f"pair ({r.sym_a!r}, {r.sym_b!r}) appears more than once — "
            "either as a duplicate or as a swapped duplicate"
        )
        seen.add(key)


# ---------------------------------------------------------------------------
# RED-8: property-style monotonicity / symmetry invariants
# (no hypothesis needed; use a few representative hand-crafted series)
# ---------------------------------------------------------------------------


def test_correlation_with_shifted_series_is_strictly_less_than_identical():
    """Perturbing one series must reduce the correlation below 1.0.

    This guards a weaker-than-Pearson implementation (e.g. always returning 1.0
    for same-length series, or covariance without normalisation).
    """
    base = [0.01, 0.02, -0.01, 0.03, -0.02, 0.01, 0.02, -0.01, 0.03, -0.02]
    perturbed = [v + 0.005 * (i % 3 - 1) for i, v in enumerate(base)]

    results = compute_pairwise_correlations({"base": base, "perturbed": perturbed})

    assert len(results) == 1
    r = results[0]
    assert r.correlation is not None
    # Correlation must be less than 1.0 (strictly) because the series differ.
    # We don't assert the exact value — only that it is < 1.0 and > -1.0.
    assert r.correlation < 1.0, (
        "a perturbed series is not identical to the base; correlation must be < 1.0"
    )
    assert r.correlation > -1.0, "correlation must be > -1.0 for a non-perfectly-anti-correlated pair"
