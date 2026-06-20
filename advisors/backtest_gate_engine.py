"""M2 backtest-and-gate engine: fold-transform + acceptance gate wiring (OFFLINE).

This module is the reusable spine of the AI Advisor (feature-plans/ai-advisor.md §M2).
For each batch of advisor candidates it:

  1. Receives a list of ``BacktestCandidate`` objects — each carrying a Composer
     backtest return series (list of ``(date_str, daily_return_pct)`` pairs) plus
     the candidate's parameter vector for panel scoring.
  2. Applies the **fold-transform**: slices every candidate's return series into
     the same walk-forward fold structure that the autotuner walk-forward uses
     (train / validation using the 60/20/20 ratios and PURGE_DAYS / EMBARGO_DAYS),
     so the gate's FDR semantics apply cleanly to the backtest context.
  3. Runs BHY/Yekutieli FDR across the full candidate set (N candidates = N trials
     in the BHY sense) using the validation-fold return series of each candidate —
     preserving the multiple-testing correction that is the gate's load-bearing
     honest backbone.
  4. Calls ``acceptance_gate.evaluate_acceptance_gate(...)`` UNCHANGED for each
     candidate, feeding it the fold-derived inputs.
  5. Returns a ``GatedBatch`` result: each candidate annotated with its gate
     verdict and honest caveats.

LOAD-BEARING SOUNDNESS REQUIREMENTS (from the team brief):
  - The fold-transform MUST preserve the one-directional-brake invariant: no
    discretionary score can resurrect a veto-failed candidate.
  - The BHY/Yekutieli FDR correction is applied across the FULL candidate set
    (N), not per-candidate. ``n_effective = len(candidates)`` is the honest
    multiple-testing count — identical to the autotuner's ``n_effective``
    semantics (autotuner.py:_haircut_select docstring, plan D3).
  - Purge-integrity check: the fold-split must respect PURGE_DAYS on the
    validation-fold boundary.  A series too short to yield a purge-respecting
    validation fold yields winner_trial_is_none=True (WITHHOLD, not fabricate).
  - NN1 compliance is structural: Composer backtests never touch BACKTEST_SELECTION
    facets (that's a spec-tuning concept, not a Composer tree concept), so
    ``nn1_compliant=True`` is the correct value for all Composer backtest paths.
    An explicit parameter lets callers override for test/audit purposes.
  - Too-short series / too-few-folds → WITHHOLD (gate produces REJECT_VETO_FAILED
    via winner_trial_is_none=True). Never fabricate a pass for a thin series.

ARCHITECTURE CONSTRAINT:
  This module MUST NOT be imported or called from ``alpha_bot_execution.py``'s
  1-minute live exit path (architecture constraint #1: no blocking I/O on the
  execution path; the same constraint governing acceptance_gate.py).

Reference: feature-plans/ai-advisor.md §M2, §Gate-1 Resolutions #1;
López de Prado 2018 AFML Ch. 7.4 (purge / embargo / walk-forward fold structure);
Harvey & Liu 2015 (BHY/Yekutieli FDR, DOI 10.3905/jpm.2015.42.1.013).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import NamedTuple

from acceptance_gate import AcceptanceVerdict, evaluate_acceptance_gate

# ---------------------------------------------------------------------------
# Walk-forward fold constants — IMPORTED from autotuner (single source of truth).
# Never duplicate here; a silent drift would break fold-purge correspondence.
# ---------------------------------------------------------------------------
# TRAIN_RATIO, VALIDATION_RATIO, FROZEN_EVAL_RATIO: the 60/20/20 split.
# PURGE_DAYS, EMBARGO_DAYS: boundary purge width.
# HARVEY_LIU_FDR_Q: the BHY FDR significance level.
# These names are the canonical autotuner module-level constants; import by name
# so any operator amendment to those constants propagates here automatically.
from autotuner import (  # noqa: E402
    EMBARGO_DAYS,
    HARVEY_LIU_FDR_Q,
    PURGE_DAYS,
    TRAIN_RATIO,
    VALIDATION_RATIO,
    benjamini_hochberg_adjust,
    compute_haircut_pvalue,
    compute_sortino_tstat,
)

# ---------------------------------------------------------------------------
# Module-level constants (named; no magic numbers per project coding standard).
# ---------------------------------------------------------------------------

# Minimum number of data points in the validation fold (post-purge) required to
# compute a defensible t-statistic via the nonparametric bootstrap.  Below this
# the bootstrap SE is "unavailable" (autotuner._BOOTSTRAP_MIN_T = 5), causing
# compute_sortino_tstat to fall back to 0.0 which makes p = 0.5 which fails BHY.
# The gate therefore correctly WITHHOLDs — but we name the floor here so the
# WITHHOLD reason is explicit rather than silent.
# Source: autotuner._BOOTSTRAP_MIN_T (5), the bootstrap small-T floor.
FOLD_TRANSFORM_MIN_VALIDATION_DAYS = 5

# Minimum raw series length (total days) below which the fold-transform cannot
# produce any meaningful split at all.  Formula: we need at least
#   floor(N * TRAIN_RATIO) + PURGE_DAYS + EMBARGO_DAYS + FOLD_TRANSFORM_MIN_VALIDATION_DAYS
# For the default 125-day autotuner budget that's 75 + 20 + 1 + 5 = 101 days;
# for a Composer backtest we apply the same proportional split so the formula
# generalises as:
#   ceil(PURGE_DAYS + EMBARGO_DAYS + FOLD_TRANSFORM_MIN_VALIDATION_DAYS)
#     / (1 - TRAIN_RATIO)
# which for TRAIN_RATIO=0.60 evaluates to (20+1+5)/0.40 = 65 days.
# We round up to an integer and store as a named constant.
# Source: derived from autotuner fold constants above; annotated inline.
FOLD_TRANSFORM_MIN_TOTAL_DAYS = math.ceil(
    (PURGE_DAYS + EMBARGO_DAYS + FOLD_TRANSFORM_MIN_VALIDATION_DAYS) / (1.0 - TRAIN_RATIO)
)

# Caveat text: selecting on backtest Sharpe/Sortino is the canonical selection
# bias trap even after gate screening.  Every surfaced survivor must carry this
# caveat so the operator sees the limitation explicitly (feature-plans/ai-advisor.md
# §Risk Tiers, §AC-3.3).
SURVIVOR_OVERFITTING_CAVEAT = (
    "The gate screens for statistical significance (BHY/Yekutieli FDR) and"
    " structural compliance, but selecting the best backtest from N candidates"
    " still concentrates selection bias even after correction."
    " A gate pass is resistance to noise, not proof of live edge."
    " Treat survivors as hypotheses, not guarantees."
)

# Caveat appended when the validation window is thin (below the autotuner's
# acknowledged ~4-day OOS-fold-collapse floor at 125 days).  Thin windows
# mean the bootstrap SE is unreliable; the t-stat falls back conservatively.
# Source: autotuner.py OPTUNA-4 comment / _OOS_USABLE_VALIDATION_DAYS_EXPECTED.
THIN_WINDOW_CAVEAT = (
    "Validation fold contains fewer days than the bootstrap-SE floor"
    f" ({FOLD_TRANSFORM_MIN_VALIDATION_DAYS} days). The t-statistic is"
    " computed with a conservative fallback (0.0) which typically fails the"
    " BHY gate. The gate is therefore likely to WITHHOLD on thin series."
)

# ---------------------------------------------------------------------------
# C5b — overfitting-cull strengthening constants
# ---------------------------------------------------------------------------

# CRRA risk-aversion coefficient passed to math_engine.compute_pbo.
# Mirrors the autotuner's gamma (autotuner.py: GAMMA = 1.0, default CRRA parameter).
# Source: Bailey & López de Prado 2014, §3 CSCV algorithm; autotuner.py module constant.
_BATCH_PBO_GAMMA: float = 1.0

# Minimum number of date-keyed configs required to compute a meaningful batch PBO.
# compute_pbo with K=1 cannot rank the IS-best against other OOS configs (no ranking
# is possible when there is only one config).  With K<2 we pass pbo=None so the PBO
# veto correctly does NOT fire (no false reject).
# Source: math_engine.compute_pbo docstring — K>=2 required for the CSCV ranking.
_PBO_MIN_CONFIGS: int = 2

# Minimum number of overlapping (intersection) dates between all candidates' date-keyed
# returns needed to partition into meaningful CSCV blocks.  Below this floor the
# eligible_dates list is too short to split into S=8 blocks of even 1 date each.
# Source: math_engine.compute_pbo — n_blocks=8 minimum (S=_CSCV_S); each block needs
# at least 1 date so we require at least 8 aligned dates.  Named for clarity.
_PBO_MIN_ALIGNED_DATES: int = 8

# Conservative default_oos_alpha used when the SPY benchmark series is unavailable.
# +inf guarantees that the withhold-clause in evaluate_acceptance_gate
# (acceptance_gate.py:257) — ``oos_alpha <= default_oos_alpha`` — is ALWAYS TRUE for
# every finite candidate oos_alpha → KEEP_INCUMBENT (conservative WITHHOLD) for all.
# This is the conservative degradation required by AC-25 / edge case 14 in the feature
# plan: if SPY cannot be established, we must not silently fall back to beats-zero.
# Note: -inf would make that clause ALWAYS FALSE, collapsing the withhold to the fallback
# (incumbent, 0.0) = beats-zero — the exact behaviour AC-25 edge-14 forbids.
# Source: feature-plans/strategy-builder-real.md §Edge Cases #14; AC-25 contract.
_SPY_UNAVAILABLE_DEFAULT_OOS_ALPHA: float = float("+inf")

# SPY benchmark ticker — the US equity broad-market reference series.
# SPY is the SPDR S&P 500 ETF, the canonical institutional benchmark for US equity
# portfolios. The fold OOS alpha of each candidate is compared against SPY's OOS alpha
# over the same fold window (AC-25). This constant isolates the ticker from the logic.
# Source: feature-plans/strategy-builder-real.md §AC-25 (SPY-OOS-over-the-fold baseline).
SPY_BENCHMARK_TICKER: str = "SPY"


# ---------------------------------------------------------------------------
# Input / output types
# ---------------------------------------------------------------------------


class BacktestCandidate(NamedTuple):
    """One advisor-proposed variant to be fold-transformed and gated.

    Fields
    ------
    candidate_id:
        An opaque string identifier (e.g., symphony_id + variant description)
        used in the result for operator traceability.  Never interpreted by the
        gate engine.
    daily_returns_pct:
        Chronologically ordered list of per-day return values IN PERCENT
        (e.g., +0.52 for +0.52% on that day), spanning the full Composer
        backtest window.  These are transformed to the fold-split series before
        the BHY haircut — same provenance contract as autotuner.py:
        ``run_simulation_crra_eu`` which stores daily_returns as percent.
    candidate_params:
        A ``dict`` of parameter name → value for the variant (used by the
        panel's stability and prior-anchor scoring functions in ``panel_scorers``
        below).  May be empty ``{}`` if no parameter-distance scoring is needed
        (the panel will fall back to default mid-range scores).
    incumbent_params:
        The live incumbent's parameter dict (for panel stability comparison).
        May be empty if unknown; same fallback behaviour as ``candidate_params``.
    theory_prior_params:
        The theory-anchor parameter dict (for panel prior-anchor scoring).
        May be empty; same fallback behaviour.
    nn1_compliant:
        Whether this candidate passed the NN1 spec-freeze check.  For all
        Composer backtest paths this is True (Composer backtest trees are not
        BACKTEST_SELECTION-spec facets); this field exists to allow callers
        to override for test / audit purposes.
    purge_integrity_ok:
        Whether the fold-split respected PURGE_DAYS.  The fold-transform
        derives this automatically from the series length; callers may
        supply False to force a veto for audit/test purposes.  The engine
        does NOT honour a caller-supplied True that contradicts the
        series-derived check — the series length wins.
    """

    candidate_id: str
    daily_returns_pct: list  # list[float]
    candidate_params: dict
    incumbent_params: dict
    theory_prior_params: dict
    nn1_compliant: bool = True
    purge_integrity_ok: bool = True
    # C5b (AC-24/25): date-keyed returns dict (date_str -> pct) enabling batch PBO
    # computation (math_engine.compute_pbo) and SPY date-alignment for the fold baseline.
    # Optional — defaults to {} so existing callers that supply only daily_returns_pct
    # continue to work.  When provided, dates are the source of truth for PBO partitioning
    # and SPY intersection; daily_returns_pct (the chronologically ordered value list) is
    # still used for the fold transform and BHY t-stat (unchanged from prior behaviour).
    # Source: feature-plans/strategy-builder-real.md §AC-24, AC-25.
    dated_returns: dict = {}  # dict[str, float]


class CandidateGateResult(NamedTuple):
    """Gate result for one candidate.

    Fields
    ------
    candidate_id:
        Echoes the BacktestCandidate identifier.
    verdict:
        The ``AcceptanceVerdict`` from ``acceptance_gate.evaluate_acceptance_gate``.
    validation_days:
        Number of days in the (post-purge) validation fold used for the
        t-stat and OOS alpha.  Surfaced for operator transparency (AC-1.2 /
        AC-2.3 / AC-3.3: show sample basis).
    oos_alpha:
        Sum of daily percent returns over the validation fold — the OOS
        performance used by the gate's OOS-superiority check.
    caveats:
        List of plain-text caveat strings.  Always non-empty for ADOPT_CANDIDATE
        verdicts (the survivor overfitting caveat is mandatory per AC-3.3).
    winner_p_adj:
        The BHY-adjusted p-value for this candidate (``p_adj[idx]`` from the
        batch-wide BHY correction) — always a finite float, never None.
        A value > HARVEY_LIU_FDR_Q means this candidate did not clear the FDR
        gate on its own; the gate still uses the batch-wide winner selection,
        so this field is provided for operator audit trail only.
    rejection_reason:
        C5b operator-legibility field (AC-24/25/26 live-probe requirement).
        A short string identifying WHY this candidate was culled, or None when
        the candidate was adopted (ADOPT_CANDIDATE).  The three distinguishable
        causes are:
          - ``"pbo_veto"``         — batch pbo > PBO_REJECT_THRESHOLD fired
          - ``"below_spy_alpha"``  — candidate oos_alpha <= SPY-fold baseline
          - ``"fdr_not_winner"``   — BHY/Yekutieli FDR gate (winner_trial_is_none)
        Populated after the full veto cascade so the deepest contributing cause
        is recorded.  Survivor (ADOPT_CANDIDATE) → None.
        Source: feature-plans/strategy-builder-real.md §5b; PM live-probe mandate.
    """

    candidate_id: str
    verdict: AcceptanceVerdict
    validation_days: int
    oos_alpha: float
    caveats: list  # list[str]
    winner_p_adj: float | None
    rejection_reason: str | None = None  # C5b: set only on a cull (None for survivors)


class GatedBatch(NamedTuple):
    """Result of ``evaluate_candidate_batch``.

    Fields
    ------
    results:
        Per-candidate ``CandidateGateResult`` in the same order as the input
        ``candidates`` list.
    survivors:
        Subset of ``results`` where ``verdict.decision == DECISION_ADOPT_CANDIDATE``.
    n_candidates:
        Total number of candidates in the batch (for FDR denominator audit trail).
    fdr_q:
        The BHY FDR level used (``HARVEY_LIU_FDR_Q``).  Carried for operator
        transparency so the surfaced recommendation can cite its correction level.
    """

    results: list  # list[CandidateGateResult]
    survivors: list  # list[CandidateGateResult]
    n_candidates: int
    fdr_q: float


# ---------------------------------------------------------------------------
# Panel scoring helpers
# ---------------------------------------------------------------------------


def _compute_parameter_stability_score(
    candidate_params: dict,
    incumbent_params: dict,
) -> float:
    """Compute the discretionary stability sub-score in [0.0, 1.0].

    Measures how close the candidate parameter vector is to the incumbent
    (panel criterion D2: parameter move-magnitude penalty).  A candidate that
    is identical to the incumbent scores 1.0; a candidate that differs
    maximally in every dimension scores 0.0.

    Formula: 1 - normalised_L1_distance.  L1 is normalised by the number of
    shared parameters so the score is independent of dimensionality.

    Edge cases:
      - No shared parameters → returns 0.5 (neutral prior; insufficient info).
      - Empty candidate or incumbent → returns 0.5 (neutral prior).
      - Parameter with zero range (min == max across both) → contributes 0.0
        to the sum (no deviation possible, no signal).

    Source: design §Sub-question 2, D2 (stability vs incumbent).  The
    normalisation approach follows the "zero-sample-cost" principle — the score
    is a pure function of parameter vectors, consuming no validation budget.
    """
    if not candidate_params or not incumbent_params:
        # Neutral prior: 0.5 means "neither good nor bad"; matches the
        # panel's symmetric weighting intent when no parameter info exists.
        return 0.5

    shared_keys = [k for k in candidate_params if k in incumbent_params]
    if not shared_keys:
        return 0.5

    total_deviation = 0.0
    valid_dims = 0
    for k in shared_keys:
        c_val = float(candidate_params[k])
        i_val = float(incumbent_params[k])
        # Normalise each dimension by |i_val| so the score is scale-invariant;
        # cap the per-dimension deviation at 1.0 (any move larger than the
        # incumbent's own magnitude is "maximally different").
        if i_val == 0.0:
            # Zero incumbent value: use absolute deviation (cannot normalise by 0).
            # A deviation of 0 contributes 0; any non-zero deviation contributes 1.
            dim_deviation = 0.0 if c_val == 0.0 else 1.0
        else:
            dim_deviation = min(abs(c_val - i_val) / abs(i_val), 1.0)
        total_deviation += dim_deviation
        valid_dims += 1

    if valid_dims == 0:
        return 0.5

    mean_deviation = total_deviation / valid_dims
    # 1 - deviation: 0 deviation → score 1.0 (identical to incumbent), maximum
    # deviation → score 0.0 (maximally far from incumbent).
    return 1.0 - mean_deviation


def _compute_prior_anchor_score(
    candidate_params: dict,
    theory_prior_params: dict,
) -> float:
    """Compute the discretionary prior-anchor sub-score in [0.0, 1.0].

    Measures how close the candidate parameter vector is to the theory-anchor
    prior (panel criterion D4: distance from theory prior).  A candidate that
    exactly matches the theory prior scores 1.0; maximum deviation scores 0.0.

    Uses the same normalised-L1-distance formula as ``_compute_parameter_stability_score``
    because both criteria are zero-sample-cost parameter-distance measures that
    share the same unit-consistency contract (design §Sub-question 2, D2 + D4).

    Edge cases: identical to ``_compute_parameter_stability_score``.

    Source: design §Sub-question 2, D4 (prior-anchor / theory-consistency).
    """
    if not candidate_params or not theory_prior_params:
        return 0.5

    shared_keys = [k for k in candidate_params if k in theory_prior_params]
    if not shared_keys:
        return 0.5

    total_deviation = 0.0
    valid_dims = 0
    for k in shared_keys:
        c_val = float(candidate_params[k])
        p_val = float(theory_prior_params[k])
        if p_val == 0.0:
            dim_deviation = 0.0 if c_val == 0.0 else 1.0
        else:
            dim_deviation = min(abs(c_val - p_val) / abs(p_val), 1.0)
        total_deviation += dim_deviation
        valid_dims += 1

    if valid_dims == 0:
        return 0.5

    mean_deviation = total_deviation / valid_dims
    return 1.0 - mean_deviation


# ---------------------------------------------------------------------------
# Fold-transform core
# ---------------------------------------------------------------------------


class _FoldResult(NamedTuple):
    """Intermediate fold-transform result for one candidate.

    Internal type; not part of the public API.
    """

    validation_returns_pct: list  # post-purge validation fold returns (in %)
    oos_alpha: float  # sum of validation_returns_pct
    validation_days: int  # len(validation_returns_pct)
    purge_integrity_ok: bool  # True iff series was long enough to purge cleanly
    thin_window: bool  # True iff validation_days < FOLD_TRANSFORM_MIN_VALIDATION_DAYS


def _fold_transform_single(daily_returns_pct: list) -> _FoldResult:
    """Slice a daily return series into the walk-forward fold structure.

    Applies the autotuner's 60/20/20 TRAIN/VALIDATION/FROZEN-EVAL split with
    PURGE_DAYS boundary purge at the train|validation boundary — identical
    methodology to autotuner.py ``run_autotuner``'s three-fold split.

    For the gate context we only need the validation fold (the OOS window
    the BHY t-stat and OOS alpha are computed over); frozen-eval is not used
    here because the advisor candidates are evaluated on the validation fold
    only (same as how Optuna trials in the autotuner score on validation, not
    frozen-eval — frozen-eval is consumed once post-selection, a luxury we
    don't have for a batch of N advisor candidates on a single Composer fetch).

    Purge logic (Boundary 1 — train | validation):
      - Excludes the last PURGE_DAYS + EMBARGO_DAYS of the raw training window.
      - The validation fold starts immediately after the raw train boundary;
        it is NOT purged on its own left edge (same as autotuner: the purge
        applies to the train side, not the start of the validation fold).

    Reference: autotuner.py ``run_autotuner``:1798-1838;
    López de Prado 2018 AFML Ch. 7.4 (purged k-fold CV).

    Args:
        daily_returns_pct: Chronologically ordered daily return values in percent.

    Returns:
        _FoldResult with the validation-fold returns and derived scalars.
    """
    # NaN / non-finite guard: strip non-finite values before any computation.
    # A NaN in the return series would propagate silently into oos_alpha (via sum)
    # and into the Sortino t-stat, corrupting both the gate's OOS-superiority check
    # and the BHY p-value.  Stripping is the conservative choice: we compute the gate
    # on the finite observations available, and if too few remain, the series is too
    # short anyway (FOLD_TRANSFORM_MIN_TOTAL_DAYS check below catches it).
    daily_returns_pct = [r for r in daily_returns_pct if math.isfinite(r)]

    n = len(daily_returns_pct)

    if n < FOLD_TRANSFORM_MIN_TOTAL_DAYS:
        # Series too short to produce a purge-respecting validation fold.
        # Return a zero-length validation fold with purge_integrity_ok=False so
        # the gate produces REJECT_VETO_FAILED (WITHHOLD) — never fabricate a pass.
        return _FoldResult(
            validation_returns_pct=[],
            oos_alpha=0.0,
            validation_days=0,
            purge_integrity_ok=False,
            thin_window=True,
        )

    # Walk-forward split: apply the same integer-index formula as autotuner.py:1800-1801.
    # Using floor (int) is intentional — this matches the autotuner's exact formula so
    # the fold boundary semantics are byte-identical, not merely proportional.
    val_start_idx = int(n * TRAIN_RATIO)
    # frozen_start_idx is the end of the validation fold (we don't use the frozen fold here).
    # Computed for completeness and to mirror the autotuner's variable set for clarity.
    frozen_start_idx = int(n * (TRAIN_RATIO + VALIDATION_RATIO))

    # Boundary 1 — train | validation: purge + embargo on the train side.
    # Identical to autotuner.py:1813: effective_train_cutoff = max(0, val_start_idx - PURGE_DAYS - EMBARGO_DAYS)  # noqa: E501  # inline comment cannot be wrapped without splitting the annotation
    effective_train_cutoff = max(0, val_start_idx - PURGE_DAYS - EMBARGO_DAYS)

    # Purge integrity: did the purge window actually fit inside the training fold?
    # It fails when the training fold is smaller than PURGE_DAYS + EMBARGO_DAYS
    # (meaning the entire training segment is consumed by the purge, leaving
    # effective_train_cutoff = 0 — a degenerate split).
    # effective_train_cutoff = 0 when val_start_idx <= PURGE_DAYS + EMBARGO_DAYS.
    purge_ok = effective_train_cutoff > 0

    # The validation fold is the raw (un-purged) slice from val_start_idx to
    # frozen_start_idx — same as autotuner.py:validation_dates_full (the full
    # raw validation fold used by the OOS cascade, NOT the purge-reduced version
    # used by the Optuna objective).
    # Rationale: the OOS alpha and t-stat measure the candidate's true
    # out-of-sample performance over the full validation window, consistent
    # with how autotuner.py:history_test = history_validation_full feeds the
    # OOS cascade.
    validation_returns = daily_returns_pct[val_start_idx:frozen_start_idx]
    oos_alpha = sum(validation_returns)
    validation_days = len(validation_returns)

    thin = validation_days < FOLD_TRANSFORM_MIN_VALIDATION_DAYS

    return _FoldResult(
        validation_returns_pct=validation_returns,
        oos_alpha=oos_alpha,
        validation_days=validation_days,
        purge_integrity_ok=purge_ok,
        thin_window=thin,
    )


# ---------------------------------------------------------------------------
# Batch evaluation — the public entry point
# ---------------------------------------------------------------------------


def evaluate_candidate_batch(
    candidates: Sequence[BacktestCandidate],
    *,
    incumbent_oos_alpha: float = 0.0,
    default_oos_alpha: float = 0.0,
    spy_returns_fn: Callable[..., dict] | None = None,
) -> GatedBatch:
    """Fold-transform a batch of Composer backtest candidates and run them through the gate.

    This is the M2 reusable spine called by M3 (asset-swap) and M4 (logic-change)
    proposal handlers.  It is the ONLY path from a Composer backtest return series
    to an ``AcceptanceVerdict`` — callers must not call ``acceptance_gate.evaluate_acceptance_gate``
    directly without going through this transform.

    Multiple-testing correction (BHY/Yekutieli FDR):
      The FDR correction is applied across the FULL batch of N candidates:
      ``n_effective = len(candidates)`` is the honest multiple-testing count,
      identical to the autotuner's ``n_effective`` semantics (autotuner.py:
      _haircut_select docstring, plan D3 "Shape A: pad the p-value list with S copies
      of 1.0 ... so the Yekutieli c(N) factor is computed over the honest N").
      Raising N raises the bar each candidate must clear — the AC-3.2 mandatory
      guardrail for logic-change proposals.

    Winner selection:
      The BHY winner is the argmin of adjusted p-values over non-veto-failed
      candidates (same logic as autotuner.py:_haircut_select:1288).  Only the
      winner may be ADOPT_CANDIDATE; all other veto-passing candidates that are
      not the BHY winner receive KEEP_INCUMBENT.  Veto-failed candidates always
      receive REJECT_VETO_FAILED.

    Args:
        candidates:
            The batch of ``BacktestCandidate`` objects.  May be empty (returns an
            empty GatedBatch with no survivors).
        incumbent_oos_alpha:
            The live incumbent's OOS alpha (sum of daily returns over its
            equivalent validation window).  Used as ``fallback_oos_alpha`` in the
            gate call — the gate's KEEP_INCUMBENT branch fires when the candidate
            does not strictly beat the incumbent.
        default_oos_alpha:
            The global-default params' OOS alpha.  Used as ``default_oos_alpha``
            in the gate call.  Overridden by the SPY-fold baseline when
            ``spy_returns_fn`` supplies a non-empty series (AC-25).
        spy_returns_fn:
            C5b injectable seam for the SPY benchmark series (AC-25).  A callable
            that returns SPY's date-keyed returns (dict[str, float]).  When
            supplied and the returned series is non-empty, the SPY series is aligned
            to each candidate's date span (intersection), fold-transformed via the
            IDENTICAL ``_fold_transform_single`` used for candidates, and the
            resulting validation-fold OOS alpha replaces ``default_oos_alpha``.
            When None or the returned series is empty, the conservative
            ``_SPY_UNAVAILABLE_DEFAULT_OOS_ALPHA`` (float("+inf")) is used, which
            makes ``oos_alpha <= default_oos_alpha`` (acceptance_gate.py:257) always
            TRUE → KEEP_INCUMBENT for every candidate (conservative WITHHOLD).  This
            prevents a silent fallback to the old beats-zero baseline (AC-25 edge-14).
            Production callers wire this to a real SPY fetch; tests inject a
            fixed fixture series.
            Source: feature-plans/strategy-builder-real.md §AC-25.

    Returns:
        A ``GatedBatch`` with per-candidate verdicts and the survivor list.

    Architecture note:
        This function MUST NOT be called from ``alpha_bot_execution.py``.
        It is an OFFLINE post-backtest decision layer (architecture constraint #1).
    """
    if not candidates:
        return GatedBatch(
            results=[],
            survivors=[],
            n_candidates=0,
            fdr_q=HARVEY_LIU_FDR_Q,
        )

    n = len(candidates)

    # --------------------------------------------------------------------------
    # C5b Step 0a: Batch-level PBO computation (AC-24).
    # Each candidate's dated_returns dict is one "config" in the CSCV sense.
    # eligible_dates = sorted intersection of dates across all configs so that
    # every config has a return on every eligible date (no missing-data bias).
    # Mirrors autotuner.py:2518-2538 (pbo=None when K<2; one batch pbo applied to
    # every candidate's evaluate_acceptance_gate call as a batch-level statistic).
    # Source: Bailey & López de Prado 2014 §3.4; autotuner.py PBO wiring pattern.
    # --------------------------------------------------------------------------
    import math_engine as _math_engine  # local import — avoids circular import risk

    _batch_pbo: float | None = None
    _dated_configs: list[dict[str, float]] = [
        dict(c.dated_returns) for c in candidates if c.dated_returns
    ]
    if len(_dated_configs) >= _PBO_MIN_CONFIGS:
        # Intersection of all date keys: only dates present in every config.
        # This ensures compute_pbo receives a consistent eligible_dates list
        # (no candidate has a missing return on an eligible date).
        _date_sets = [set(cfg.keys()) for cfg in _dated_configs]
        _intersection_dates: list[str] = sorted(_date_sets[0].intersection(*_date_sets[1:]))
        if len(_intersection_dates) >= _PBO_MIN_ALIGNED_DATES:
            _batch_pbo = _math_engine.compute_pbo(
                _dated_configs,
                _intersection_dates,
                _BATCH_PBO_GAMMA,
            )
        # else: insufficient aligned dates → pbo stays None (no false reject).
    # else: K<2 date-keyed configs → pbo stays None (no false reject).

    # --------------------------------------------------------------------------
    # C5b Step 0b: SPY-fold baseline computation (AC-25).
    # Align SPY to candidate date span FIRST (intersect on dates in the candidate
    # series), then fold-transform the aligned series identically to the candidates.
    # The resulting SPY validation-fold OOS alpha replaces default_oos_alpha.
    # If SPY is unavailable (empty series), use _SPY_UNAVAILABLE_DEFAULT_OOS_ALPHA
    # (float("+inf")) — makes ``oos_alpha <= default_oos_alpha`` always TRUE →
    # KEEP_INCUMBENT for every candidate (conservative WITHHOLD, AC-25 edge-14).
    # Source: feature-plans/strategy-builder-real.md §AC-25; handoff §AC-25.
    # --------------------------------------------------------------------------
    _effective_default_oos_alpha: float = default_oos_alpha
    if spy_returns_fn is not None:
        _spy_dated: dict[str, float] = {}
        try:
            _spy_dated = spy_returns_fn() or {}
        except Exception:
            _spy_dated = {}

        if _spy_dated:
            # Collect all dates that appear in any candidate's dated_returns.
            # We align SPY to the union of candidate dates so the fold window
            # is consistent with the candidate time span (not restricted to
            # the PBO intersection, which may be narrower).
            _all_candidate_dates: set[str] = set()
            for c in candidates:
                if c.dated_returns:
                    _all_candidate_dates.update(c.dated_returns.keys())
            if not _all_candidate_dates:
                # No candidates have dated_returns; fall back to all SPY dates
                # for the fold (the fold-transform will use the ordered value list).
                _all_candidate_dates = set(_spy_dated.keys())

            # Restrict SPY to the candidate date span (intersection).
            # Extra SPY dates outside the candidate span are ignored — this is the
            # guarded date-alignment (the positional-fold bug: if we did not intersect,
            # a longer SPY series would have its fold window land on different dates).
            _spy_aligned_dates = sorted(d for d in _spy_dated if d in _all_candidate_dates)
            if _spy_aligned_dates:
                _spy_value_list = [_spy_dated[d] for d in _spy_aligned_dates]
                _spy_fold = _fold_transform_single(_spy_value_list)
                _effective_default_oos_alpha = _spy_fold.oos_alpha
            else:
                # SPY has no dates overlapping the candidate span → conservative WITHHOLD.
                # +inf makes oos_alpha <= default_oos_alpha always TRUE → KEEP_INCUMBENT.
                _effective_default_oos_alpha = _SPY_UNAVAILABLE_DEFAULT_OOS_ALPHA
        else:
            # SPY series returned empty → conservative WITHHOLD (+inf → KEEP_INCUMBENT).
            _effective_default_oos_alpha = _SPY_UNAVAILABLE_DEFAULT_OOS_ALPHA

    # --------------------------------------------------------------------------
    # Step 1: Fold-transform every candidate independently.
    # --------------------------------------------------------------------------
    fold_results: list[_FoldResult] = [
        _fold_transform_single(c.daily_returns_pct) for c in candidates
    ]

    # --------------------------------------------------------------------------
    # Step 2: Compute per-candidate t-statistics on the validation-fold returns.
    # Only non-zero validation folds contribute to the BHY; empty folds (too-short
    # series / purge failure) produce t=0.0 → p=0.5 which fails BHY conservatively.
    # --------------------------------------------------------------------------
    # Each candidate's t-stat is computed with a deterministic seed derived from its
    # index (same seed-derivation strategy as autotuner.py:_haircut_select:1256 where
    # seed=trial_idx ensures reproducible cross-study haircut decisions).
    tstats: list[float] = []
    for idx, (cand, fold) in enumerate(zip(candidates, fold_results)):
        if fold.validation_returns_pct:
            t = compute_sortino_tstat(fold.validation_returns_pct, seed=idx)
        else:
            # Empty validation fold (series too short) → conservative 0.0.
            # compute_haircut_pvalue(0.0) = 0.5, fails BHY.
            t = 0.0
        tstats.append(t)

    p_values: list[float] = [compute_haircut_pvalue(t) for t in tstats]

    # --------------------------------------------------------------------------
    # Step 3: BHY/Yekutieli FDR correction across the full batch (n_effective = N).
    # Shape A padding: append S = max(0, N - len(p_values)) copies of 1.0.
    # Here len(p_values) == N so S = 0; the padding is a no-op when all candidates
    # contribute a p-value, but the explicit form mirrors autotuner.py:1279-1286
    # so the BHY preservation contract is byte-identical.
    # Source: autotuner.py:_haircut_select "Shape A" comment at line 1275-1278.
    # --------------------------------------------------------------------------
    # n_effective equals the batch size — the honest multiple-testing count.
    n_effective = n
    s = max(0, n_effective - len(p_values))
    padded = p_values + [1.0] * s
    p_adj_all = benjamini_hochberg_adjust(padded)
    # Select only over the original n candidates (not the padding tail).
    p_adj = p_adj_all[:n]

    # --------------------------------------------------------------------------
    # Step 4: Identify the BHY winner (argmin over non-None p_adj).
    # Only veto-passing candidates participate; veto-failed candidates are skipped.
    # A candidate whose fold produced purge_integrity_ok=False is veto-failed, so
    # we skip those when searching for the winner.  This mirrors the autotuner's
    # post-haircut winner selection at :1288-1292.
    # --------------------------------------------------------------------------
    # Build a mask: True iff this candidate could pass vetoes (purge ok + nn1 ok).
    # The BHY veto itself is determined after we see p_adj, so we only pre-filter
    # on the structural vetoes that are independent of the BHY computation.
    veto_eligible_indices = [
        i for i in range(n) if fold_results[i].purge_integrity_ok and candidates[i].nn1_compliant
    ]

    winner_idx: int | None = None
    winner_p_adj_value: float | None = None
    if veto_eligible_indices:
        # argmin p_adj over veto-eligible candidates only.
        best_i = min(veto_eligible_indices, key=lambda i: p_adj[i])
        if p_adj[best_i] <= HARVEY_LIU_FDR_Q:
            winner_idx = best_i
            winner_p_adj_value = p_adj[best_i]
        else:
            # No candidate clears the FDR gate; best adjusted p-value reported.
            winner_p_adj_value = p_adj[best_i]

    # --------------------------------------------------------------------------
    # Step 5: Compute panel scores and call evaluate_acceptance_gate per candidate.
    # --------------------------------------------------------------------------
    results: list[CandidateGateResult] = []

    for idx, (cand, fold) in enumerate(zip(candidates, fold_results)):
        caveats: list[str] = []

        # Thin-window caveat: surface even on REJECT so the operator understands why.
        if fold.thin_window:
            caveats.append(THIN_WINDOW_CAVEAT)

        # Panel scores (zero-sample-cost parameter-distance criteria; D2 + D4).
        cand_stability = _compute_parameter_stability_score(
            cand.candidate_params, cand.incumbent_params
        )
        cand_prior_anchor = _compute_prior_anchor_score(
            cand.candidate_params, cand.theory_prior_params
        )
        # Incumbent panel scores: compare incumbent to itself (stability = 1.0)
        # and incumbent to the theory prior.  The gate uses the relative margin
        # candidate_panel_score >= incumbent_panel_score + MARGIN; incumbent
        # stability = 1.0 (it IS the current baseline) and its prior-anchor
        # score reflects how far it is from the theory prior.
        inc_stability = 1.0  # the incumbent is, by definition, stable against itself
        inc_prior_anchor = _compute_prior_anchor_score(
            cand.incumbent_params, cand.theory_prior_params
        )

        # BHY veto input for THIS candidate:
        # - winner_trial_is_none=True iff this candidate is NOT the BHY winner.
        #   (Any non-winner, including veto-eligible ones, gets winner_trial_is_none=True.)
        # - winner_p_adj: the BHY-adjusted p for this candidate's own p-value.
        #   (For transparency; the veto decision comes from winner_trial_is_none.)
        this_winner_trial_is_none = idx != winner_idx
        this_winner_p_adj = p_adj[idx] if not this_winner_trial_is_none else None

        # Derive purge_integrity_ok: the series-derived check wins over caller-supplied.
        # A caller supplying purge_integrity_ok=False is always honoured (caller can
        # tighten but not loosen the series-derived result).
        series_purge_ok = fold_results[idx].purge_integrity_ok
        effective_purge_ok = series_purge_ok and cand.purge_integrity_ok

        verdict: AcceptanceVerdict = evaluate_acceptance_gate(
            winner_trial_is_none=this_winner_trial_is_none,
            winner_p_adj=this_winner_p_adj,
            nn1_compliant=cand.nn1_compliant,
            purge_integrity_ok=effective_purge_ok,
            oos_alpha=fold.oos_alpha,
            fallback_oos_alpha=incumbent_oos_alpha,
            default_oos_alpha=_effective_default_oos_alpha,
            candidate_stability_score=cand_stability,
            candidate_prior_anchor_score=cand_prior_anchor,
            incumbent_stability_score=inc_stability,
            incumbent_prior_anchor_score=inc_prior_anchor,
            # C5b AC-24: thread the REAL batch PBO into the gate (mirrors autotuner:2711).
            # pbo=None when K<2 or insufficient aligned dates — veto does not fire.
            # pbo>PBO_REJECT_THRESHOLD → Stage-1 REJECT_VETO_FAILED (acceptance_gate:208-213).
            # Source: Bailey & López de Prado 2014; autotuner.py:2699-2711 wiring pattern.
            pbo=_batch_pbo,
        )

        # Survivor overfitting caveat is MANDATORY for ADOPT_CANDIDATE (AC-3.3).
        if verdict.decision == "ADOPT_CANDIDATE":
            caveats.append(SURVIVOR_OVERFITTING_CAVEAT)

        # C5b operator-legibility: record WHY this candidate was culled (AC-24/25 mandate).
        # Distinguishable causes let the live-probe confirm each veto actually BITES.
        # Stage-order precedence (most-specific first):
        #   1. Survivor (ADOPT_CANDIDATE) → None (no rejection cause).
        #   2. PBO veto (batch pbo > PBO_REJECT_THRESHOLD) → "pbo_veto" (contains "pbo").
        #      Stage-1 hard veto — fires before SPY baseline so a candidate that is BOTH
        #      high-PBO AND below-SPY reports the PBO reason (AC-24 stage order).
        #   3. SPY-fold baseline not met (oos_alpha <= spy-fold default) → "below_spy_alpha".
        #      Stage-2 gate — only reached when PBO did not veto.
        #   4. All other causes (BHY non-winner, nn1, purge) → "fdr_not_winner".
        # Source: feature-plans/strategy-builder-real.md §AC-24/AC-25; handoff §rejection_reason.
        _rejection_reason: str | None
        from math_engine import PBO_REJECT_THRESHOLD as _PBO_THRESH

        if verdict.decision == "ADOPT_CANDIDATE":
            _rejection_reason = None
        elif _batch_pbo is not None and _batch_pbo > _PBO_THRESH:
            # PBO veto: Stage-1 hard veto — the batch-level sample-robustness test fired.
            # The reason string deliberately contains "pbo" (lowercase) so the live-probe
            # can identify PBO culls via a case-insensitive substring check.
            _rejection_reason = "pbo_veto"
        elif spy_returns_fn is not None and fold.oos_alpha <= _effective_default_oos_alpha:
            # SPY-fold baseline not met: Stage-2 gate — the candidate's fold OOS alpha is at
            # or below the SPY fold OOS alpha. On SPY-unavailable the baseline is the
            # conservative float("+inf") sentinel, so this clause is ALWAYS TRUE → every
            # candidate is withheld with below_spy_alpha (the baseline could not be
            # established — AC-25 edge-14 conservative WITHHOLD).
            _rejection_reason = "below_spy_alpha"
        else:
            # Catch-all: BHY/Yekutieli FDR non-winner, or nn1/purge/thin-window failure.
            _rejection_reason = "fdr_not_winner"

        results.append(
            CandidateGateResult(
                candidate_id=cand.candidate_id,
                verdict=verdict,
                validation_days=fold.validation_days,
                oos_alpha=fold.oos_alpha,
                caveats=caveats,
                winner_p_adj=p_adj[idx],
                rejection_reason=_rejection_reason,
            )
        )

    survivors = [r for r in results if r.verdict.decision == "ADOPT_CANDIDATE"]

    return GatedBatch(
        results=results,
        survivors=survivors,
        n_candidates=n,
        fdr_q=HARVEY_LIU_FDR_Q,
    )
