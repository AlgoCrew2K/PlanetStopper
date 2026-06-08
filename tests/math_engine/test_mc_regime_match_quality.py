"""
RED tests for the MC regime-match-quality guard
(vision-audit Critical Rec #2 + math-reaudit Critique #1).

THE DEFECT
----------
``math_engine.run_monte_carlo`` selects the K=150 nearest historical days by
z-scored Euclidean distance on (SPY return, rolling 20d vol). It then bootstraps
the symphony return distribution from those neighbours and the caller's MC
sanity gate (``compute_exit_confirmation``) vetoes the trailing-stop exit when
``prob_underperforming >= MC_BREAKDOWN_THRESHOLD`` (60.0).

The MC gate NEVER asks whether those K neighbours are actually CLOSE to today.
On a regime break (COVID, the 2022 rates shock, late-2023) every historical day
may be far from the query point; ``run_monte_carlo`` still picks the K
"least bad fits" and the caller still treats the resulting probability as a
high-confidence opinion. The bot most confidently veto-blocks exits during a
regime break — exactly when the historical conditional CDF is least informative
(math-soundness Surface 3; vision-findings Q5; SYNTHESIS Theme 3 / B-A2 /
Recommendation 2).

THE FIX (vision-audit Critical Rec #2 — methodology adopted in this cycle)
--------------------------------------------------------------------------
Add a regime-match-quality guard using a Mahalanobis-style chi-squared threshold
on the z-scored squared Euclidean distance between today's query point and its K
nearest candidate-pool neighbours.

  * Already-z-scored features (run_monte_carlo AC-1) make the squared Euclidean
    distance an approximation to the squared Mahalanobis distance (the cross-
    correlation off-diagonal is small for SPY-return vs rolling-vol; the
    implementer may compute full inverse-covariance if preferred — both
    satisfy this RED contract).
  * Under the null that today is drawn from the same joint distribution as the
    candidate pool, the squared distance follows chi-squared with df = 2
    (number of standardized features). chi2(2)_{0.99} ~= 9.21034 is the
    Mahalanobis-style critical value for "this point is a >=99% outlier
    against the candidate-pool joint distribution."
  * The test statistic is the MEAN squared distance over the K nearest
    neighbours (not the query-to-centroid distance). Surface 3 of the
    math-reaudit specifies this construction: "if the K nearest neighbours
    have a mean distance above a threshold, the bootstrap is unrepresentative."

  References (also cited by docs/audit/vision-audit-2026-05-27/math-soundness.md
  Surface 3 and SYNTHESIS Theme 3):
    - Mahalanobis, P. C. (1936). "On the generalised distance in statistics."
      Proceedings of the National Institute of Sciences (Calcutta), 2, 49-55.
    - Aggarwal, C. C. (2017). Outlier Analysis, 2nd ed. Springer. Sec 2.3,
      sec 4.4 (kNN-distance outlier framework).
    - Knorr, E. M. & Ng, R. T. (1998). "Algorithms for mining distance-based
      outliers in large datasets." VLDB '98, 392-403.

  Named constants (the implementer GREEN step must introduce):
    MC_REGIME_MATCH_CHI2_THRESHOLD = 9.21034037197618  # chi2(2)_{0.99}
    MC_REGIME_MATCH_NEIGHBOR_K     = MC_DEFAULT_NEIGHBOR_K  # alias OK

  Env-var override is allowed and expected: ``MC_REGIME_MATCH_CHI2_THRESHOLD``
  reads from the environment if present (operator tuning per the project's
  named-constant rule), with the module-level constant as the fall-back default.

CONTRACT THIS MODULE PINS
-------------------------
1. ``compute_regime_match_quality(historical_data, spy_today_return) ->
   RegimeMatchAssessment`` is the public helper (a pure function; O(eligible
   pool size) — no I/O, no blocking work; architecture constraint #1).
2. ``RegimeMatchAssessment`` is a frozen dataclass with at minimum these fields:
       mean_sq_mahalanobis: float | None
       is_unprecedented: bool
       neighbor_k: int
       threshold_used: float
       insufficient_reason: str | None
3. For an UNPRECEDENTED query (the unprecedented-regime fixture):
       is_unprecedented is True
       mean_sq_mahalanobis > MC_REGIME_MATCH_CHI2_THRESHOLD.
4. For a TYPICAL query (the typical-regime fixture):
       is_unprecedented is False
       mean_sq_mahalanobis < MC_REGIME_MATCH_CHI2_THRESHOLD.
5. For an INSUFFICIENT eligible pool (the same boundary
   ``run_monte_carlo`` short-circuits on):
       mean_sq_mahalanobis is None
       is_unprecedented is False (fail-safe: an absent diagnostic is
       not a suppression signal; the existing MC sentinel already short-
       circuits and the protective stop fires on its own ticks-below-stop
       condition).
       insufficient_reason is a non-empty string.
6. The threshold is a NAMED module-level constant with the chi-squared 0.99
   value AND is referenced from ``compute_regime_match_quality`` (no magic
   numbers; project CLAUDE.md rule).
7. The threshold is operator-overridable via the
   ``MC_REGIME_MATCH_CHI2_THRESHOLD`` environment variable
   (the constant is the default; env overrides the value at call time).
8. The fail-safe protective stop discipline is UNCHANGED. The
   ``compute_exit_confirmation`` signature is unchanged. Suppression of the MC
   veto is implemented by the caller: when ``is_unprecedented`` is True the
   caller passes ``prob_underperforming=None`` to ``compute_exit_confirmation`` — which
   exercises the EXISTING MC-unavailable fail-safe path. The trailing stop
   then fires on the ticks-below-stop condition alone.
9. ``compute_regime_match_quality`` does NOT compute, return, or persist any
   signed CVaR-divergence quantity. The CVaR-divergence REJECT wall
   (DE-S3-005; project memory project_cvar_divergence_validation_wall) is
   preserved.
10. The MC blast-radius contract is preserved: ``run_monte_carlo``'s return
    type and signature are UNCHANGED (the regime-match guard is a SIBLING
    helper, not a re-typed MC return). Project memory
    project_mc_sentinel_consumer_blast_radius: ``run_monte_carlo``'s return
    is consumed at 7+ sites; this cycle adds zero new consumers.

PA-18 / fixture-provenance
--------------------------
No producer-computed score is hardcoded. The unprecedented assertion is the
INEQUALITY ``mean_sq_mahalanobis > MC_REGIME_MATCH_CHI2_THRESHOLD`` derived
from the chi-squared reference; the typical assertion is the converse
inequality. The insufficient-pool assertion is a None-sentinel contract.
Histories are schema-derived from ``run_monte_carlo``'s documented input
contract; the unprecedented construction (tight cluster + far query) and
the typical construction (wide cluster + centroid query) are structural,
not pinned to a specific run_monte_carlo output.

RED-VERIFICATION
----------------
Every test in this module fails today: ``compute_regime_match_quality``,
``RegimeMatchAssessment``, ``MC_REGIME_MATCH_CHI2_THRESHOLD``, and
``MC_REGIME_MATCH_NEIGHBOR_K`` do not exist on the math_engine module on
this branch. The first ``AttributeError`` / ``ImportError`` proves the
RED phase; after GREEN every test must pass without modification.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import pathlib
import re
from typing import Any

import pytest

import math_engine

FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math_engine"
    / "mc_regime_match_quality"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(filename: str) -> dict[str, Any]:
    with (FIXTURE_DIR / filename).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _date_key(i: int) -> str:
    """Synthetic ISO date string; lexicographic ordering is all the function needs."""
    base_day = 1 + i
    month = 1 + (base_day - 1) // 28
    day = 1 + (base_day - 1) % 28
    return f"2024-{month:02d}-{day:02d}"


def _build_tight_cluster_history(
    num_days: int,
    spy_center: float,
    spy_dispersion: float,
    holding_ret: float,
) -> dict[str, dict[str, dict[str, float]]]:
    """A tight candidate pool: alternating +/-dispersion around spy_center."""
    history: dict[str, dict[str, dict[str, float]]] = {}
    for i in range(num_days):
        spy_ret = spy_center + (spy_dispersion if i % 2 == 0 else -spy_dispersion)
        history[_date_key(i)] = {
            "SPY": {"daily_ret": spy_ret},
            "AAA": {"daily_ret": holding_ret},
        }
    return history


def _build_sinusoidal_wide_history(
    num_days: int,
    spy_center: float,
    spy_amplitude: float,
    spy_period_days: float,
    holding_ret: float,
) -> dict[str, dict[str, dict[str, float]]]:
    """A deterministic varied candidate pool: SPY follows a sinusoidal sweep
    of given amplitude/period around spy_center. Variation guarantees the
    rolling-vol feature has non-degenerate spread across the candidate window
    (a perfectly-alternating sequence collapses rolling vol to a constant,
    producing a zero-std vol feature and triggering the z-score divide-by-zero
    guard -- which is a separate test case, not what this fixture wants)."""
    history: dict[str, dict[str, dict[str, float]]] = {}
    for i in range(num_days):
        spy_ret = spy_center + spy_amplitude * math.sin(2.0 * math.pi * i / spy_period_days)
        history[_date_key(i)] = {
            "SPY": {"daily_ret": spy_ret},
            "AAA": {"daily_ret": holding_ret},
        }
    return history


def _build_constant_history(
    num_days: int,
    spy_ret: float,
    holding_ret: float,
) -> dict[str, dict[str, dict[str, float]]]:
    return {
        _date_key(i): {
            "SPY": {"daily_ret": spy_ret},
            "AAA": {"daily_ret": holding_ret},
        }
        for i in range(num_days)
    }


def _resolve_helper():
    """Return math_engine.compute_regime_match_quality or fail with a clear
    RED message that names the missing symbol."""
    fn = getattr(math_engine, "compute_regime_match_quality", None)
    if fn is None:
        pytest.fail(
            "math_engine.compute_regime_match_quality is not defined. "
            "GREEN must introduce a pure helper "
            "`compute_regime_match_quality(historical_data, spy_today_return) "
            "-> RegimeMatchAssessment` per this module's docstring."
        )
    return fn


# ---------------------------------------------------------------------------
# 1. The unprecedented-regime PROOF
# ---------------------------------------------------------------------------


def test_unprecedented_regime_query_is_detected() -> None:
    """
    PROOF (vision-audit Critical Rec #2; math-reaudit Surface 3).
    Today's query point is several candidate-pool standard deviations from
    every historical day. The mean squared Mahalanobis-style distance over
    the K nearest neighbours MUST exceed the chi-squared 0.99 threshold,
    and ``is_unprecedented`` MUST be True.

    Pre-fix: no helper exists; the MC gate fires blindly.
    Post-fix: the helper reports is_unprecedented=True so the caller
    suppresses the MC veto (the existing fail-safe).

    Tolerance: the assertion is the strict inequality
    ``mean_sq_mahalanobis > MC_REGIME_MATCH_CHI2_THRESHOLD``. No
    pytest.approx — the construction makes the score far above the
    threshold, the inequality is robust.
    """
    fn = _resolve_helper()
    fx = _load_fixture("01_unprecedented_regime_query_far_from_pool.json")
    spec = fx["inputs"]["historical_data_spec"]
    history = _build_tight_cluster_history(
        num_days=spec["num_days"],
        spy_center=spec["spy_center_decimal"],
        spy_dispersion=spec["spy_dispersion_decimal"],
        holding_ret=spec["holding_daily_ret_decimal"],
    )
    assessment = fn(history, fx["inputs"]["spy_today_return"])

    score = assessment.mean_sq_mahalanobis
    threshold = math_engine.MC_REGIME_MATCH_CHI2_THRESHOLD

    assert score is not None, (
        "compute_regime_match_quality returned mean_sq_mahalanobis=None for a "
        "sufficient-pool fixture. The eligible pool has 60 days (>= "
        "MC_MIN_HISTORY_DAYS + MC_VOL_WINDOW_DAYS - 1 = 39 raw days); the "
        "score must be a finite float, not the insufficient sentinel."
    )
    assert math.isfinite(score), (
        f"mean_sq_mahalanobis is non-finite ({score!r}). The z-score guard for "
        f"zero-variance features (already present in run_monte_carlo per AC-1) "
        f"must extend to this helper so the squared distance stays finite."
    )
    assert score > threshold, (
        f"compute_regime_match_quality reported mean_sq_mahalanobis={score!r} "
        f"for a query point constructed to sit several candidate-pool standard "
        f"deviations from EVERY historical day (tight cluster + far query). "
        f"The chi-squared 0.99 threshold "
        f"(MC_REGIME_MATCH_CHI2_THRESHOLD={threshold!r}) MUST be exceeded -- "
        f"this is the Mahalanobis-style multivariate-outlier criterion "
        f"(Aggarwal 2017 Outlier Analysis sec 2.3; chi2(2)_0.99). "
        f"Derivation: {fx['derivation']}"
    )
    assert assessment.is_unprecedented is True, (
        f"is_unprecedented is {assessment.is_unprecedented!r} for an "
        f"unprecedented-regime query (mean_sq_mahalanobis={score!r} > "
        f"threshold={threshold!r}). The flag MUST be True so the caller can "
        f"suppress the MC veto and let the trailing stop fire on its own "
        f"ticks-below-stop condition."
    )


def test_unprecedented_regime_threshold_used_is_recorded() -> None:
    """
    Telemetry: ``threshold_used`` records the threshold the helper actually
    applied (so an operator inspecting a suppression decision can audit which
    threshold was in force at that moment). For the unprecedented fixture
    with no env override the value MUST equal the module-level constant.
    """
    fn = _resolve_helper()
    fx = _load_fixture("01_unprecedented_regime_query_far_from_pool.json")
    spec = fx["inputs"]["historical_data_spec"]
    history = _build_tight_cluster_history(
        num_days=spec["num_days"],
        spy_center=spec["spy_center_decimal"],
        spy_dispersion=spec["spy_dispersion_decimal"],
        holding_ret=spec["holding_daily_ret_decimal"],
    )
    # No env override in this test: explicitly remove it to be deterministic.
    prior = os.environ.pop("MC_REGIME_MATCH_CHI2_THRESHOLD", None)
    try:
        assessment = fn(history, fx["inputs"]["spy_today_return"])
    finally:
        if prior is not None:
            os.environ["MC_REGIME_MATCH_CHI2_THRESHOLD"] = prior

    assert hasattr(assessment, "threshold_used"), (
        "RegimeMatchAssessment is missing the `threshold_used` field. "
        "Telemetry contract: the threshold actually applied must be a field "
        "on the assessment so an operator can audit suppression decisions."
    )
    assert assessment.threshold_used == math_engine.MC_REGIME_MATCH_CHI2_THRESHOLD, (
        f"threshold_used={assessment.threshold_used!r} does not equal "
        f"MC_REGIME_MATCH_CHI2_THRESHOLD="
        f"{math_engine.MC_REGIME_MATCH_CHI2_THRESHOLD!r}. With no env override "
        f"the module-level constant is the authoritative value."
    )


# ---------------------------------------------------------------------------
# 2. The typical-regime negative
# ---------------------------------------------------------------------------


def test_typical_regime_query_is_not_unprecedented() -> None:
    """
    Negative companion: a query point that sits at the candidate-pool
    centroid (post-z-score) MUST not be flagged unprecedented. The mean
    squared Mahalanobis-style distance is bounded well below the chi-squared
    0.99 threshold; ``is_unprecedented`` is False; the existing MC veto
    stays in force.

    Tolerance: strict inequality ``mean_sq_mahalanobis < threshold``.
    """
    fn = _resolve_helper()
    fx = _load_fixture("02_typical_regime_query_in_pool.json")
    spec = fx["inputs"]["historical_data_spec"]
    history = _build_sinusoidal_wide_history(
        num_days=spec["num_days"],
        spy_center=spec["spy_center_decimal"],
        spy_amplitude=spec["spy_amplitude_decimal"],
        spy_period_days=spec["spy_period_days"],
        holding_ret=spec["holding_daily_ret_decimal"],
    )
    assessment = fn(history, fx["inputs"]["spy_today_return"])

    score = assessment.mean_sq_mahalanobis
    threshold = math_engine.MC_REGIME_MATCH_CHI2_THRESHOLD

    assert score is not None, (
        "compute_regime_match_quality returned None for a 60-day pool well "
        "above the eligible-pool boundary; expected a finite score."
    )
    assert math.isfinite(score) and score >= 0.0, (
        f"mean_sq_mahalanobis must be a non-negative finite float; got {score!r}."
    )
    assert score < threshold, (
        f"compute_regime_match_quality reported mean_sq_mahalanobis={score!r} "
        f">= threshold={threshold!r} for a query point constructed to sit at "
        f"the candidate-pool centroid (post-z-score). The score must stay "
        f"below the chi-squared 0.99 threshold; otherwise the suppression gate "
        f"will fire on TYPICAL regimes and over-suppress the MC veto -- the "
        f"failure mode at the other end of the spec. Derivation: {fx['derivation']}"
    )
    assert assessment.is_unprecedented is False, (
        f"is_unprecedented is {assessment.is_unprecedented!r} for a typical "
        f"query (score={score!r} < threshold={threshold!r}). False is required "
        f"so the MC veto stays in force in normal regimes."
    )


def test_unprecedented_and_typical_scores_are_strictly_ordered() -> None:
    """
    Property: the unprecedented-regime fixture's score MUST be greater than
    the typical-regime fixture's score. Two histories that bracket the
    threshold cannot rank in the wrong order; if they do, the score is not
    a monotonic distance measure and the suppression flag is unsound.
    """
    fn = _resolve_helper()

    fx_unp = _load_fixture("01_unprecedented_regime_query_far_from_pool.json")
    spec_unp = fx_unp["inputs"]["historical_data_spec"]
    hist_unp = _build_tight_cluster_history(
        num_days=spec_unp["num_days"],
        spy_center=spec_unp["spy_center_decimal"],
        spy_dispersion=spec_unp["spy_dispersion_decimal"],
        holding_ret=spec_unp["holding_daily_ret_decimal"],
    )
    assess_unp = fn(hist_unp, fx_unp["inputs"]["spy_today_return"])

    fx_typ = _load_fixture("02_typical_regime_query_in_pool.json")
    spec_typ = fx_typ["inputs"]["historical_data_spec"]
    hist_typ = _build_sinusoidal_wide_history(
        num_days=spec_typ["num_days"],
        spy_center=spec_typ["spy_center_decimal"],
        spy_amplitude=spec_typ["spy_amplitude_decimal"],
        spy_period_days=spec_typ["spy_period_days"],
        holding_ret=spec_typ["holding_daily_ret_decimal"],
    )
    assess_typ = fn(hist_typ, fx_typ["inputs"]["spy_today_return"])

    s_unp = assess_unp.mean_sq_mahalanobis
    s_typ = assess_typ.mean_sq_mahalanobis
    assert s_unp is not None and s_typ is not None, (
        "Both fixtures have sufficient eligible pools; both scores must be "
        "finite floats, got unp={s_unp!r} typ={s_typ!r}."
    )
    assert s_unp > s_typ, (
        f"Score ordering inverted: unprecedented={s_unp!r} not > "
        f"typical={s_typ!r}. mean_sq_mahalanobis must be a monotonic distance "
        f"measure -- a far query must score above a centroid query."
    )


# ---------------------------------------------------------------------------
# 3. Insufficient-pool fail-safe
# ---------------------------------------------------------------------------


def test_insufficient_pool_returns_score_none_and_not_unprecedented() -> None:
    """
    Fail-safe: when the eligible pool is below MC_MIN_HISTORY_DAYS the
    regime-match-quality assessment cannot be computed.

    Contract:
      - mean_sq_mahalanobis is None (a distinct out-of-band sentinel,
        mirroring MC_INSUFFICIENT_HISTORY_SENTINEL).
      - is_unprecedented is False (an absent diagnostic is not a
        suppression signal; the EXISTING MC sentinel path already short-
        circuits prob_underperforming to None and the protective stop fires on
        its own condition; flagging unprecedented here would double-count
        the same path).
      - insufficient_reason is a non-empty human-readable string so an
        operator inspecting the telemetry row sees why the diagnostic is
        absent.
    """
    fn = _resolve_helper()
    fx = _load_fixture("03_insufficient_pool_returns_score_none.json")
    spec = fx["inputs"]["historical_data_spec"]
    history = _build_constant_history(
        num_days=spec["num_days"],
        spy_ret=spec["spy_daily_ret_decimal"],
        holding_ret=spec["holding_daily_ret_decimal"],
    )
    assessment = fn(history, fx["inputs"]["spy_today_return"])

    assert assessment.mean_sq_mahalanobis is None, (
        f"compute_regime_match_quality returned mean_sq_mahalanobis="
        f"{assessment.mean_sq_mahalanobis!r} for an insufficient eligible "
        f"pool ({spec['num_days']} raw days < eligible-pool boundary). The "
        f"contract is a None sentinel; a numeric score here would be a "
        f"least-bad-fit measurement on a degenerate pool."
    )
    assert assessment.is_unprecedented is False, (
        f"is_unprecedented is {assessment.is_unprecedented!r} for an "
        f"insufficient eligible pool. Fail-safe contract: False. The "
        f"existing MC sentinel already short-circuits prob_underperforming; an "
        f"unprecedented=True here would double-count the same fail-safe "
        f"path in telemetry."
    )
    assert isinstance(assessment.insufficient_reason, str) and assessment.insufficient_reason, (
        f"insufficient_reason must be a non-empty string when the score is "
        f"None; got {assessment.insufficient_reason!r}. Operator telemetry "
        f"needs the reason text to distinguish 'insufficient pool' from "
        f"'sufficient pool, score below threshold'."
    )


def test_protective_stop_still_fires_when_regime_unprecedented() -> None:
    """
    Fail-safe end-to-end PROOF: even when the regime is unprecedented and
    the helper would suppress the MC veto, the protective trailing stop
    MUST fire on its own ticks-below-stop condition. The caller wires the
    suppression by passing prob_underperforming=None to compute_exit_confirmation
    on the unprecedented branch; this test exercises that wiring by
    asserting compute_exit_confirmation(..., prob_underperforming=None, ...) drives
    the count to EXIT_CONFIRM_TICKS over consecutive qualifying ticks.

    This guards architecture constraint #1 from the project CLAUDE.md
    ("never an action surface for live trades" is symmetric: never a
    veto surface either, when MC has no statistical authority).
    """
    fx = _load_fixture("01_unprecedented_regime_query_far_from_pool.json")
    scenario = fx["exit_confirmation_scenario"]

    # Magnitude condition genuinely satisfied so a non-firing stop can only
    # be the MC gate vetoing.
    magnitude_satisfied = scenario["current_return"] <= (
        scenario["stop_trigger_level"] - math_engine.MAGNITUDE_FLOOR_PCT
    )
    assert magnitude_satisfied, (
        "Fixture scenario invariant broken: current_return must be at least "
        "MAGNITUDE_FLOOR_PCT below stop_trigger_level so the magnitude gate "
        "passes -- otherwise a non-firing stop could be due to a too-shallow "
        "drawdown rather than the MC gate."
    )

    # When the caller suppresses the MC veto (because is_unprecedented=True)
    # it passes prob_underperforming=None -- the EXISTING MC-unavailable fail-safe
    # path. Drive the confirm machine for EXIT_CONFIRM_TICKS qualifying
    # ticks and assert the stop fires.
    count = scenario["starting_below_stop_count"]
    hit = False
    for _ in range(math_engine.EXIT_CONFIRM_TICKS):
        count, hit = math_engine.compute_exit_confirmation(
            armed=scenario["armed"],
            is_triggered=scenario["is_triggered"],
            current_return=scenario["current_return"],
            stop_trigger_level=scenario["stop_trigger_level"],
            prob_underperforming=None,
            current_below_stop_count=count,
        )
    assert hit is True, (
        f"The protective trailing stop did NOT fire after "
        f"{math_engine.EXIT_CONFIRM_TICKS} ticks below stop with the MC veto "
        f"suppressed (prob_underperforming=None). The unprecedented-regime gate must "
        f"reuse the existing fail-safe -- passing prob_underperforming=None drives "
        f"compute_exit_confirmation through its MC-unavailable branch and the "
        f"trailing stop confirms on its own."
    )
    assert count >= math_engine.EXIT_CONFIRM_TICKS, (
        f"below_stop_count reached {count} after "
        f"{math_engine.EXIT_CONFIRM_TICKS} ticks; with the MC veto suppressed "
        f"the count must reach at least EXIT_CONFIRM_TICKS."
    )


# ---------------------------------------------------------------------------
# 4. Named-constant and threshold-override contract
# ---------------------------------------------------------------------------


def test_chi2_threshold_constant_is_named_and_has_correct_value() -> None:
    """
    The chi-squared threshold MUST be a named module-level constant.
    Project CLAUDE.md rule (math_engine.py): every constant named + source
    comment. The expected value is chi2(2)_{0.99} ~= 9.21034037197618.

    Tolerance: pytest.approx(rel=1e-6). The chi-squared 0.99 quantile is a
    well-defined mathematical constant; rounding to ~6 significant figures
    is acceptable but the implementer should not silently pin a different
    value (e.g. 9.5, 10.0 -- a magic-number violation in disguise).
    """
    assert hasattr(math_engine, "MC_REGIME_MATCH_CHI2_THRESHOLD"), (
        "math_engine.MC_REGIME_MATCH_CHI2_THRESHOLD missing. The chi-squared "
        "0.99 threshold (df=2; Mahalanobis-style multivariate-outlier "
        "criterion) must be a named module-level constant."
    )
    threshold = math_engine.MC_REGIME_MATCH_CHI2_THRESHOLD
    assert isinstance(threshold, float), (
        f"MC_REGIME_MATCH_CHI2_THRESHOLD is type {type(threshold).__name__!r}; "
        f"must be float."
    )
    # chi2(2)_{0.99} = 9.21034037197618 (scipy.stats.chi2.ppf(0.99, df=2)).
    # rel=1e-6 because the published value is precise to ~12 sig figs and any
    # difference at the 6th sig fig is a deliberate threshold change, not a
    # rounding artifact.
    assert threshold == pytest.approx(9.21034037197618, rel=1e-6), (
        f"MC_REGIME_MATCH_CHI2_THRESHOLD={threshold!r} does not match the "
        f"chi-squared 0.99 quantile (df=2) ~= 9.21034037197618. If a "
        f"different operating threshold is intended it must be DOCUMENTED "
        f"with the reference, not silently pinned. References: Mahalanobis "
        f"1936; Aggarwal 2017 Outlier Analysis sec 2.3; chi2(2)_0.99."
    )


def test_neighbor_k_constant_is_named() -> None:
    """
    The K used in the mean-squared-distance test statistic MUST be a named
    module-level constant. Either ``MC_REGIME_MATCH_NEIGHBOR_K`` (new) or
    an alias to the existing ``MC_DEFAULT_NEIGHBOR_K=150`` is acceptable.
    """
    direct = getattr(math_engine, "MC_REGIME_MATCH_NEIGHBOR_K", None)
    fallback = getattr(math_engine, "MC_DEFAULT_NEIGHBOR_K", None)
    assert direct is not None or fallback is not None, (
        "Neither MC_REGIME_MATCH_NEIGHBOR_K nor MC_DEFAULT_NEIGHBOR_K is "
        "defined on math_engine. The mean-squared-distance K must be a "
        "named constant."
    )
    k = direct if direct is not None else fallback
    assert isinstance(k, int) and k > 0, (
        f"Resolved K is {k!r}; must be a positive int."
    )


def test_helper_source_contains_zero_magic_numbers() -> None:
    """
    Project CLAUDE.md rule (math_engine.py): no magic numbers; every
    constant named + source comment. The helper must not contain bare
    numeric literals for the threshold (9.21, 0.99, etc.) or for K (150).

    AST scan of compute_regime_match_quality; structural whitelist
    matches the rule used by test_mc_gating_magic_numbers.py.
    """
    import ast as _ast

    structural_whitelist = {0, 1, -1, 0.0, 2}
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = _ast.parse(source)
    target: _ast.FunctionDef | None = None
    for node in _ast.walk(tree):
        if isinstance(node, _ast.FunctionDef) and node.name == "compute_regime_match_quality":
            target = node
            break
    if target is None:
        pytest.fail(
            "compute_regime_match_quality is not defined in math_engine.py; "
            "GREEN must introduce it before this test can run."
        )

    offenders: list[tuple[int, Any]] = []
    for sub in _ast.walk(target):
        if not isinstance(sub, _ast.Constant):
            continue
        val = sub.value
        if isinstance(val, bool):
            continue
        if not isinstance(val, (int, float)):
            continue
        if val in structural_whitelist:
            continue
        offenders.append((sub.lineno, val))

    assert not offenders, (
        f"Bare numeric literals in compute_regime_match_quality: {offenders}. "
        f"Project CLAUDE.md rule: extract each to a named module-level "
        f"constant with a source-comment provenance. Likely offenders here "
        f"are the chi-squared threshold (must be MC_REGIME_MATCH_CHI2_"
        f"THRESHOLD) and the K (must be MC_REGIME_MATCH_NEIGHBOR_K or "
        f"MC_DEFAULT_NEIGHBOR_K)."
    )


def test_chi2_threshold_is_env_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Operator override: the threshold MUST be overridable at call time via
    the ``MC_REGIME_MATCH_CHI2_THRESHOLD`` environment variable. With an
    override SMALLER than the typical-regime score, the typical fixture
    must FLIP to is_unprecedented=True (proving the env override is the
    value actually applied).

    Why an env override (not a function arg): the project's named-constant
    rule explicitly allows operator-overridable env vars (see
    feedback_no_hardcoded_test_values + project CLAUDE.md). Pushing this
    through every call site as a parameter would bloat every consumer.

    Behavioural assertion only -- no producer score is pinned.
    """
    fn = _resolve_helper()

    # Step 1: typical fixture under the default threshold -> is_unprecedented False.
    fx = _load_fixture("02_typical_regime_query_in_pool.json")
    spec = fx["inputs"]["historical_data_spec"]
    history = _build_sinusoidal_wide_history(
        num_days=spec["num_days"],
        spy_center=spec["spy_center_decimal"],
        spy_amplitude=spec["spy_amplitude_decimal"],
        spy_period_days=spec["spy_period_days"],
        holding_ret=spec["holding_daily_ret_decimal"],
    )
    monkeypatch.delenv("MC_REGIME_MATCH_CHI2_THRESHOLD", raising=False)
    baseline = fn(history, fx["inputs"]["spy_today_return"])
    assert baseline.is_unprecedented is False and baseline.mean_sq_mahalanobis is not None, (
        f"Pre-condition failed: typical fixture is_unprecedented="
        f"{baseline.is_unprecedented!r} score={baseline.mean_sq_mahalanobis!r}."
    )

    # Step 2: set an override threshold strictly below the typical fixture's
    # score; the same fixture must flip to is_unprecedented=True.
    override = baseline.mean_sq_mahalanobis - 1e-9
    assert override > 0, "Score is zero or negative; override math is undefined."
    monkeypatch.setenv("MC_REGIME_MATCH_CHI2_THRESHOLD", repr(override))
    overridden = fn(history, fx["inputs"]["spy_today_return"])
    assert overridden.is_unprecedented is True, (
        f"With MC_REGIME_MATCH_CHI2_THRESHOLD={override!r} (strictly below "
        f"the score), is_unprecedented is {overridden.is_unprecedented!r}; "
        f"must be True. The env override is not being read."
    )
    assert overridden.threshold_used == pytest.approx(override, rel=1e-12, abs=1e-12), (
        f"threshold_used={overridden.threshold_used!r} does not equal the env "
        f"override {override!r}. The telemetry field must reflect the value "
        f"actually applied so an operator can audit the suppression decision."
    )


# ---------------------------------------------------------------------------
# 5. Dataclass shape + immutability contract
# ---------------------------------------------------------------------------


def test_regime_match_assessment_is_a_frozen_dataclass() -> None:
    """
    RegimeMatchAssessment must be a frozen dataclass (consistent with
    CVaREstimate / CVaRAssessment / project pattern). Frozenness prevents
    a consumer from mutating the telemetry struct after the producer
    returns it, which would silently break the cycle-by-cycle audit trail.
    """
    cls = getattr(math_engine, "RegimeMatchAssessment", None)
    assert cls is not None, (
        "math_engine.RegimeMatchAssessment is not defined. GREEN must "
        "introduce a frozen dataclass with fields documented in this "
        "module's docstring."
    )
    assert dataclasses.is_dataclass(cls), (
        "RegimeMatchAssessment is not a dataclass. The project pattern "
        "(CVaREstimate / CVaRAssessment) uses @dataclasses.dataclass(frozen=True)."
    )
    params = getattr(cls, "__dataclass_params__", None)
    assert params is not None and params.frozen, (
        "RegimeMatchAssessment is a dataclass but is NOT frozen. Frozen is "
        "required so a consumer cannot mutate the telemetry struct after the "
        "producer returns it (same rule as CVaRAssessment)."
    )


def test_regime_match_assessment_has_required_fields() -> None:
    """
    Minimum schema for the operator-telemetry struct (see module docstring
    Contract item 2). Additional fields are allowed; these MUST exist.
    """
    cls = getattr(math_engine, "RegimeMatchAssessment", None)
    if cls is None:
        pytest.fail(
            "RegimeMatchAssessment missing; see test_regime_match_assessment_is_a_frozen_dataclass."
        )
    required = {
        "mean_sq_mahalanobis",
        "is_unprecedented",
        "neighbor_k",
        "threshold_used",
        "insufficient_reason",
    }
    fields = {f.name for f in dataclasses.fields(cls)}
    missing = required - fields
    assert not missing, (
        f"RegimeMatchAssessment missing fields {sorted(missing)}; declared "
        f"fields {sorted(fields)}. The operator-telemetry contract requires "
        f"the listed fields (see module docstring Contract item 2)."
    )


# ---------------------------------------------------------------------------
# 6. CVaR-divergence REJECT wall preservation (project-binding)
# ---------------------------------------------------------------------------


def test_helper_does_not_compute_signed_cvar_divergence() -> None:
    """
    Project binding: the CVaR-divergence REJECT wall (DE-S3-005; project
    memory project_cvar_divergence_validation_wall) bans any signed-divergence
    quantity from the producer surface. compute_regime_match_quality must
    NOT compute, return, or persist a signed CVaR divergence as part of the
    regime-match guard.

    Scanner-style check: the RegimeMatchAssessment dataclass MUST NOT
    declare a field whose name contains 'divergence' or 'cvar_div'. The
    regime-match guard is a kNN-distance diagnostic, not a CVaR signal.
    """
    cls = getattr(math_engine, "RegimeMatchAssessment", None)
    if cls is None:
        pytest.fail("RegimeMatchAssessment missing; cannot scan its fields.")
    forbidden_pattern = re.compile(r"(?:divergence|cvar_div)", re.IGNORECASE)
    offenders = [
        f.name for f in dataclasses.fields(cls) if forbidden_pattern.search(f.name)
    ]
    assert not offenders, (
        f"RegimeMatchAssessment declares fields {offenders!r} that look like "
        f"CVaR-divergence quantities. The CVaR-divergence REJECT wall "
        f"(DE-S3-005) bans signed-divergence quantities on producer surfaces. "
        f"The regime-match guard is a kNN-distance diagnostic only."
    )


# ---------------------------------------------------------------------------
# 7. run_monte_carlo signature unchanged (blast-radius contract)
# ---------------------------------------------------------------------------


def test_run_monte_carlo_signature_unchanged_by_this_cycle() -> None:
    """
    Project memory project_mc_sentinel_consumer_blast_radius: run_monte_carlo's
    return type is consumed at 7+ sites; changing its signature is never a
    math_engine-local change. The regime-match guard is a SIBLING helper, not
    a re-typed MC return.

    This test pins the parameter names + their order so a GREEN implementer
    cannot smuggle a new parameter onto run_monte_carlo (e.g. a
    ``regime_match_threshold`` keyword). Any new behaviour belongs on
    compute_regime_match_quality.
    """
    import inspect

    sig = inspect.signature(math_engine.run_monte_carlo)
    actual_params = tuple(sig.parameters.keys())
    expected_params = (
        "holdings",
        "historical_data",
        "spy_today_return",
        "simulation_paths",
        "neighbor_k",
        "seed",
    )
    assert actual_params == expected_params, (
        f"run_monte_carlo signature changed: {actual_params!r}. Expected "
        f"{expected_params!r}. The regime-match guard belongs in a sibling "
        f"helper (compute_regime_match_quality), not in a new parameter on "
        f"run_monte_carlo -- the latter's return is consumed at 7+ sites "
        f"(project memory project_mc_sentinel_consumer_blast_radius)."
    )
