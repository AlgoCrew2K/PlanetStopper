"""M3 Asset-Swap engine — objective-directed swap proposals for the AI Advisor.

This module is OFFLINE only (not on the 1-minute live execution path).

Implements the two Asset-Swap modes described in feature-plans/ai-advisor.md §M3:

  1. **Operator-initiated** (AC-2.1): ``propose_operator_swap()``.
     The operator specifies incumbent_asset + candidate_asset + symphony.
     The engine applies the swap, backtests the variant via M2
     ``advisors.composer_backtest_client``, gates the result via M2
     ``advisors.backtest_gate_engine``, and returns a ``SwapRunResult``.

  2. **Advisor-suggested** (AC-2.2): ``suggest_swaps()``.
     Given an objective and a pool of available_assets, the engine calls
     ``generate_objective_directed_candidates()`` to shortlist objective-driven
     candidates, then backtests/gates each one and returns survivors.

Architecture constraints
------------------------
* This module MUST NOT be imported from ``alpha_bot_execution.py`` (AC-X2).
  It is an advise-only offline post-backtest decision layer.
* Only read + inline-backtest Composer endpoints are called (AC-X1):
  GET /score and stateless POST /api/v0.1/backtest.
  No write, mutate, or trade-placement calls of any kind.
* Every ADOPT_CANDIDATE survivor is persisted as an ``advisor_observation`` with
  ``is_advisory_only=1`` + ``observation_type="asset_swap_proposal"`` (AC-X3).
* No Composer API key → engine returns a clear error, writes nothing (AC-X4).
* One candidate's backtest failure never aborts the batch (AC-X5).
* Zero survivors is a valid non-error outcome (AC-2.5).

Reference: feature-plans/ai-advisor.md §M3;
           feature-plans/ai-advisor.md §Gate-1 Resolutions #2 (objective-direction).
"""

from __future__ import annotations

import copy
import logging
import math
from dataclasses import dataclass, field
from typing import Any

import database
from advisors import symphony_schema
from advisors.backtest_gate_engine import (
    SURVIVOR_OVERFITTING_CAVEAT,
    BacktestCandidate,
    CandidateGateResult,
    GatedBatch,
    _fold_transform_single,
    evaluate_candidate_batch,
)
from advisors.composer_backtest_client import run_backtest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Honest caveat attached to every surfaced swap survivor (AC-2.3 + AC-3.3).
SWAP_SURVIVOR_CAVEAT = SURVIVOR_OVERFITTING_CAVEAT

# Plain-text template for advise-only apply guidance (AC-X1 / AC-2.3).
ADVISE_ONLY_APPLY_TEMPLATE = (
    "To apply: open {symphony_name} in Composer and swap {from_ticker} → {to_ticker} manually."
)

# Observation type tag written to advisor_observations (AC-X3 / fixture contract).
_OBSERVATION_TYPE = "asset_swap_proposal"

# No-survivors message (AC-2.5 — must appear in SwapRunResult.message).
NO_SURVIVORS_MESSAGE = "no swap cleared the gate this run"

# Additive weight applied to the normalised mean lens score when blending into
# the objective-directed ranking (Cycle-3 AC-2).  0 < w ≤ 1.
# Kept at 0.25 so the lens signal nudges ranking without overriding the primary
# correlation/variance measurement from the objective (lens is supporting evidence,
# not the main signal).  Named constant per no-magic-numbers rule.
LENS_BLEND_WEIGHT: float = 0.25

# Neutral lens score assigned to tickers absent from lens_scores during blending.
# 0.5 represents "no evidence — neither boost nor penalty" on the [0,1] lens scale.
# Named to avoid the magic-number 0.5 appearing inline (reviewer advisory AC-2).
_LENS_NEUTRAL_SCORE: float = 0.5

# Scale constant for squashing technicals.payload["momentum"] (an UNBOUNDED raw
# 20-day return -- see lens_technicals.py's Jegadeesh & Titman momentum window,
# typically ~+/-0.05 to +/-0.15 in practice, but not formally bounded) onto the
# [0, 1] favorability scale _apply_lens_blend expects (neutral =
# _LENS_NEUTRAL_SCORE = 0.5). 0.10 is chosen so a TYPICAL momentum (~0.05) maps
# to a clearly non-neutral but non-saturated score (tanh(0.5)/2+0.5 ~= 0.73),
# while an EXTREME momentum (>=0.15) approaches -- but by tanh's construction
# never reaches exactly -- the [0, 1] bounds. Live-E2E follow-up
# (DE-LENS-SCORE-SHAPE-001): the prior "ticker_scores" parser fabricated a key
# no real producer emits; this constant/formula only needs to satisfy the
# pinned invariant (bounded, neutral-at-zero, monotonic, adversarially
# separated) -- the exact k is an implementation choice, not a pinned value.
_MOMENTUM_SQUASH_SCALE: float = 0.10


def _squash_momentum_to_unit_interval(momentum: float) -> float:
    """Map an unbounded raw 20-day momentum return onto (0.0, 1.0).

    ``_apply_lens_blend`` treats lens scores as an already-normalised
    favorability on [0, 1] with ``_LENS_NEUTRAL_SCORE`` (0.5) as neutral, but
    technicals' real per-ticker signal (``payload["momentum"]``) is an
    UNBOUNDED raw return, not pre-normalised. A bounded, strictly-monotonic
    tanh transform satisfies all four required properties:
      - momentum == 0.0  -> exactly 0.5 (neutral -- matches the "no evidence"
        default so a flat ticker never silently nudges the blend).
      - momentum > 0.0   -> score > 0.5 (bullish tilt), strictly monotonic.
      - momentum < 0.0   -> score < 0.5 (bearish tilt), strictly monotonic.
      - any finite input -> strictly within (0.0, 1.0), never exactly 0 or 1.

    Args:
        momentum: raw 20-day return (e.g. 0.05 == +5%).

    Returns:
        A float in the open interval (0.0, 1.0).
    """
    return 0.5 + 0.5 * math.tanh(momentum / _MOMENTUM_SQUASH_SCALE)


# ---------------------------------------------------------------------------
# Cycle-3 AC-1: Lens-score extraction helper
# ---------------------------------------------------------------------------


def extract_lens_scores(context: dict) -> dict:
    """Extract per-ticker lens scores from an assembled advisor context dict.

    Live-E2E follow-up (DE-LENS-SCORE-SHAPE-001): the prior implementation
    read a fabricated ``"ticker_scores"`` key that NO real lens producer ever
    emits (0 real occurrences outside the stale fixture / this function) --
    caught by a live droplet-DB E2E run where a fresh MARKET_LENS_CACHE
    bundle produced ``{}`` and silently no-opped the D-workstream lens blend.

    Only ``technicals`` genuinely carries a per-ticker signal on the real
    5-lens payload contracts:
      - ``technicals.payload["momentum"]``: ``{ticker: float}`` -- an
        UNBOUNDED raw 20-day return (``ai_advisor.py:542-552``,
        ``advisors/lens_technicals.py:265-272``). Squashed onto ``[0, 1]``
        via ``_squash_momentum_to_unit_interval`` before being returned.
      - ``technicals.payload["ma_posture"]`` (per-ticker
        above_sma50/above_sma200 flags) exists but is NOT used here --
        momentum alone is sufficient signal; folding ma_posture in is a
        natural follow-up, not required.

    ``sentiment`` / ``derivatives`` / ``macro`` are MARKET-WIDE scalars
    (``tone_score``, VIX level, FRED series keyed by ``series_id``) with no
    per-ticker structure -- they contribute NOTHING even when
    ``available=True``; fabricating a per-ticker score from a market-wide
    number would violate honest-availability. ``fundamentals`` IS per-ticker-
    KEYED (``payload["tickers"]``) but its values are raw financials
    (``key_facts``), not a clean scalar -- excluded from v1 by design (a
    fundamentals-derived score is a distinct, more involved design problem
    than this parser's scope).

    Only an ``available=True`` technicals block contributes; ``available=
    False`` is honored regardless of what the payload nominally contains
    (AC-6 honest-availability is checked BEFORE payload content, never
    bypassed by a rich-looking payload).

    Args:
        context:
            The dict returned by ``ai_advisor.assemble_advisor_context``, or
            any dict with lens-block values keyed by lens name. Missing lens
            keys, None payload, and malformed blocks are handled gracefully
            — they contribute nothing and never raise.

    Returns:
        ``{ticker: {"technicals": score_in_0_1}, ...}`` — dict of dicts.
        Returns ``{}`` when technicals is absent/unavailable/has no momentum
        data. Never raises; malformed input degrades to ``{}``.
    """
    if not isinstance(context, dict):
        return {}

    block = context.get("technicals")
    if not isinstance(block, dict):
        return {}
    if not block.get("available", False):
        # Honest-availability: unavailable lens -> nothing, regardless of
        # whatever the payload nominally contains.
        return {}

    payload = block.get("payload")
    if not isinstance(payload, dict):
        return {}

    momentum = payload.get("momentum")
    if not isinstance(momentum, dict):
        return {}

    lens_name = block.get("lens", "technicals")
    result: dict[str, dict[str, float]] = {}
    for ticker, raw_value in momentum.items():
        if not isinstance(ticker, str) or not isinstance(raw_value, (int, float)):
            continue
        result[ticker] = {lens_name: _squash_momentum_to_unit_interval(float(raw_value))}

    return result


# ---------------------------------------------------------------------------
# SwapObjective — typed representation of the objective driving a swap
# ---------------------------------------------------------------------------


@dataclass
class SwapObjective:
    """Typed objective that drives an asset-swap search (Gate-1 Resolution #2).

    Every swap must be OBJECTIVE-DIRECTED — the advisor must be solving for a
    stated, measurable objective, not swapping for vibes or brute-force.

    Fields
    ------
    objective_type:
        One of ``"reduce_correlation"``, ``"reduce_drawdown"``,
        ``"lift_risk_adjusted"``.
    target_pair:
        Two-tuple of symphony IDs or ticker names identifying the pair whose
        correlation is being addressed.  ``None`` for non-correlation objectives.
    measured_value:
        Display-only context value shown in the human-readable rationale
        surfaced alongside the swap result.  Does not drive candidate
        generation, ranking, or gate decisions.  Current production callers
        (app.py's operator-evaluate route) pass 0.0 — no live statistic is
        wired in yet; the weekly_suggestions_scheduler.py call sites remain
        a known follow-up (AC-10, not yet covered).
    """

    objective_type: str
    target_pair: tuple[str, str] | None
    measured_value: float


# ---------------------------------------------------------------------------
# SwapProposalResult — per-candidate result
# ---------------------------------------------------------------------------


@dataclass
class SwapProposalResult:
    """Result for one evaluated swap candidate.

    Attributes
    ----------
    candidate_id:
        Opaque traceability ID: ``"{symphony_id}:{incumbent_asset}->{candidate_asset}"``.
    symphony_id:
        The Composer symphony UUID.
    incumbent_asset:
        The holding being replaced.
    candidate_asset:
        The candidate replacement ticker.
    objective:
        The ``SwapObjective`` that drove this swap (AC-2.3: surfaced alongside result).
    objective_rationale:
        Human-readable explanation of how this swap addresses the objective (AC-2.3:
        de-correlation rationale must be surfaced). E.g. "IALT has low correlation
        with the incumbent pair and addresses the 0.85 correlation objective."
    gate_result:
        The ``CandidateGateResult`` from ``backtest_gate_engine.evaluate_candidate_batch``
        (carries verdict, validation_days, oos_alpha, caveats, winner_p_adj).
        ``None`` when the backtest failed before gating.
    baseline_stats:
        Stats dict from the backtest of the UNCHANGED tree (baseline).
        ``None`` when the baseline backtest failed.
    variant_stats:
        Stats dict from the backtest of the SWAPPED tree (variant).
        ``None`` when the variant backtest failed.
    apply_guidance:
        Plain-text operator instruction (AC-X1): "To apply: open … manually."
        Always present, never a button.
    backtest_error:
        ``None`` on success; descriptive string on backtest failure (AC-X5).
    data_warnings:
        List of ticker-level data-availability warnings from the Composer API.
    """

    candidate_id: str
    symphony_id: str
    incumbent_asset: str
    candidate_asset: str
    objective: SwapObjective
    objective_rationale: str

    gate_result: CandidateGateResult | None = None
    baseline_stats: dict | None = None
    variant_stats: dict | None = None

    # Caveats surfaced to the operator — always non-empty for ADOPT_CANDIDATE survivors
    # (SURVIVOR_OVERFITTING_CAVEAT is mandatory per AC-3.3).  Populated from
    # gate_result.caveats after gating; may also be set directly by callers/tests.
    caveats: list = field(default_factory=list)
    apply_guidance: str = ""
    backtest_error: str | None = None
    data_warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# SwapRunResult — top-level result of a swap pipeline run
# ---------------------------------------------------------------------------


@dataclass
class SwapRunResult:
    """Top-level result of a swap pipeline run (operator-initiated or advisor-suggested).

    Attributes
    ----------
    gate_batch:
        The ``GatedBatch`` from ``evaluate_candidate_batch``.  Always non-None
        even when zero candidates survive — it carries n_candidates, fdr_q, and
        the empty survivors list for operator audit trail (AC-2.5).
    proposals:
        All evaluated ``SwapProposalResult`` objects (survivors + rejected + failed).
    survivors:
        Subset of proposals where ``gate_result.verdict.decision == "ADOPT_CANDIDATE"``.
    rejected_candidates:
        Subset of proposals that were gated-out or backtest-failed.
    message:
        Human-readable run summary.  For zero survivors: ``NO_SURVIVORS_MESSAGE``
        (AC-2.5: explicit 'no swap cleared the gate this run' — not a silent empty list).
    objective:
        The ``SwapObjective`` that drove this run.
    no_api_key:
        ``True`` when the Composer API key is absent; proposals are empty (AC-X4).
    persistence_error:
        Non-None when the advisor_observation write failed (RC-5).  The survivor is
        still returned, but the operator/log must see that its audit-trail row never
        landed — a persistence failure must be surfaced, never swallowed to a warning.
    """

    gate_batch: GatedBatch
    proposals: list = field(default_factory=list)
    survivors: list = field(default_factory=list)
    rejected_candidates: list = field(default_factory=list)
    message: str = ""
    objective: SwapObjective | None = None
    no_api_key: bool = False
    persistence_error: str | None = None


# ---------------------------------------------------------------------------
# Empty GatedBatch sentinel (used for no-API-key / empty-candidate paths)
# ---------------------------------------------------------------------------

from advisors.backtest_gate_engine import HARVEY_LIU_FDR_Q as _FDR_Q  # noqa: E402


def _empty_gate_batch() -> GatedBatch:
    """Return an empty GatedBatch (zero candidates, zero survivors)."""
    from advisors.backtest_gate_engine import GatedBatch  # noqa: PLC0415

    return GatedBatch(results=[], survivors=[], n_candidates=0, fdr_q=_FDR_Q)


# ---------------------------------------------------------------------------
# API-key guard
# ---------------------------------------------------------------------------


def _has_composer_key() -> bool:
    """Return True iff Composer API credentials are configured."""
    from alpha_bot_execution import COMPOSER_KEY_ID, COMPOSER_SECRET  # noqa: PLC0415

    return bool(COMPOSER_KEY_ID and COMPOSER_SECRET)


# ---------------------------------------------------------------------------
# Tree-manipulation helpers
# ---------------------------------------------------------------------------


def apply_ticker_swap(raw_value: dict, from_ticker: str, to_ticker: str) -> dict:
    """Deep-copy ``raw_value`` and replace all ticker=``from_ticker`` with ``to_ticker``.

    Traverses the entire tree recursively, substituting at every node where
    ``node["ticker"] == from_ticker``.  The input is never mutated.
    Returns a deep copy with zero substitutions if from_ticker is absent (no-op).
    """
    tree = copy.deepcopy(raw_value)

    def _walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("ticker") == from_ticker:
            node["ticker"] = to_ticker
        for child in node.get("children", []) or []:
            _walk(child)

    _walk(tree)
    return tree


def extract_tickers(raw_value: dict) -> set:
    """Collect all ticker values in the tree.  Returns empty set on malformed input."""
    if not isinstance(raw_value, dict):
        return set()
    tickers: set = set()
    stack = [raw_value]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("ticker"):
            tickers.add(node["ticker"])
        for child in node.get("children", []) or []:
            stack.append(child)
    return tickers


# ---------------------------------------------------------------------------
# Cycle-3 AC-2: Lens-score blend helper
# ---------------------------------------------------------------------------


def _apply_lens_blend(
    candidates: list,
    lens_scores: dict | None,
) -> list:
    """Re-rank an already-sorted candidate list by blending in lens evidence.

    AC-D1: blends lens evidence with the CONTINUOUS primary ``"score"`` field
    already present on every candidate dict (see
    ``generate_objective_directed_candidates``) — NEVER the discrete
    ``enumerate()`` position. A position-based blend is mathematically inert:
    for any two adjacent positions the integer gap (>= 1) always exceeds the
    maximum possible lens contribution (``LENS_BLEND_WEIGHT`` <= 1), so lens
    evidence could never change the order (see the closed-form inertness proof
    in ``tests/ai_advisor/test_lens_blend_efficacy.py``'s module docstring).

    Blend formula — cumulative absolute score distance from the top candidate:
        cum_gap[0] = 0
        cum_gap[i] = cum_gap[i-1] + |score[i] - score[i-1]|   (i >= 1, walked
                                                                 in the caller's
                                                                 own pre-sorted
                                                                 order)
        blended_key[i] = cum_gap[i] - LENS_BLEND_WEIGHT * (mean_lens[i] - _LENS_NEUTRAL_SCORE)

    ``cum_gap`` walks the candidate list IN THE ORDER GIVEN (index 0 is already
    the best candidate per the caller's own primary sort — ascending or
    descending, ``_apply_lens_blend`` does not need to know which) and
    accumulates the ABSOLUTE raw-score distance between neighbours. This is
    deliberately NOT a per-batch min-max normalization: min-max would always
    rescale a 2-candidate gap to fill the full [0, 1] span regardless of its
    true magnitude (a 0.0001 gap and a 0.90 gap would look identical after
    min-max), which defeats AC-D2's "small gap can move, large gap cannot
    invert" invariant. Using the RAW absolute gap directly preserves that
    magnitude information — a near-tied pair (small ``cum_gap`` increment)
    sits within ``LENS_BLEND_WEIGHT``'s max swing and can be reordered; a
    commanding primary lead (large increment) cannot be overcome.

    Args:
        candidates:
            List of ``{"ticker": ..., "score": ...}`` dicts, pre-sorted by the
            primary objective metric (index 0 = best). Returned unchanged when
            ``lens_scores`` is None or empty. A missing/non-numeric ``"score"``
            degrades that candidate to its 0-based position (legacy/defensive
            fallback — matches the pre-Cycle-3 position-based ordering for
            candidate dicts built without a "score" key, e.g. the unknown-
            objective fallback in ``generate_objective_directed_candidates``).
        lens_scores:
            ``{ticker: {lens_name: score, ...}}`` as returned by
            ``extract_lens_scores``.  None or empty → no reranking.

    Returns:
        Re-ordered candidate list.  All input candidates are preserved (lens
        scoring does NOT eliminate candidates — that is the gate's job).
    """
    if not lens_scores:
        # lens_scores=None or {} → no blend; return as-is (backward-compat).
        return candidates

    if not candidates:
        return candidates

    # Compute the mean lens score for each candidate ticker.
    # Tickers absent from lens_scores get _LENS_NEUTRAL_SCORE so they don't
    # benefit from or lose to missing-data artefacts.
    def _mean_lens(ticker: str) -> float:
        scores = lens_scores.get(ticker)
        if not scores or not isinstance(scores, dict):
            return _LENS_NEUTRAL_SCORE  # neutral: no evidence available
        vals = [v for v in scores.values() if isinstance(v, (int, float))]
        return sum(vals) / len(vals) if vals else _LENS_NEUTRAL_SCORE

    def _primary_score(position: int, cand: Any) -> float:
        val = cand.get("score") if isinstance(cand, dict) else None
        return float(val) if isinstance(val, (int, float)) else float(position)

    scores = [_primary_score(i, cand) for i, cand in enumerate(candidates)]

    # cum_gap[i]: cumulative ABSOLUTE score distance from the top candidate,
    # walked in the given (already primary-sorted) order. See formula above.
    cum_gap = [0.0] * len(candidates)
    for i in range(1, len(candidates)):
        cum_gap[i] = cum_gap[i - 1] + abs(scores[i] - scores[i - 1])

    blended = []
    for i, cand in enumerate(candidates):
        ticker = cand.get("ticker", "") if isinstance(cand, dict) else str(cand)
        mean_lens = _mean_lens(ticker)
        # Deviation from neutral: a strongly lens-favored candidate (mean_lens
        # near 1.0) subtracts from its blended key (moves toward the front);
        # a strongly disfavored one (near 0.0) adds (moves toward the back).
        lens_term = LENS_BLEND_WEIGHT * (mean_lens - _LENS_NEUTRAL_SCORE)
        blended_key = cum_gap[i] - lens_term
        # Stable tie-break on original index for exact blended-key ties.
        blended.append((blended_key, i, cand))

    blended.sort(key=lambda t: (t[0], t[1]))
    return [cand for _, _, cand in blended]


# ---------------------------------------------------------------------------
# Objective-directed candidate generation (AC-2.2 / Gate-1 Resolution #2)
# ---------------------------------------------------------------------------


def generate_objective_directed_candidates(
    symphony_id: str,
    objective: SwapObjective,
    correlation_data: dict,
    available_assets: list,
    lens_scores: dict | None = None,
) -> list:
    """Generate a shortlist of swap candidates that address the stated objective.

    This is the adversarially-testable gate on objective-direction (Gate-1
    Resolution #2).  It MUST produce different candidate lists for different
    objectives on the same symphony — an objective-ignoring generator that
    returns the same candidates regardless of objective is a test FAIL.

    Candidate-generation strategy by objective_type:

    ``reduce_correlation``:
        Shortlist assets from ``available_assets`` whose return series shows
        low correlation with the symphony identified in ``objective.target_pair``.
        For each available_asset, compute its correlation against the first
        element of target_pair using ``correlation_data``.  Prefer assets with
        low absolute correlation (< 0.40) — i.e., assets that would genuinely
        de-correlate the portfolio (not brute-force or popularity-based).

    ``reduce_drawdown``:
        Shortlist assets that exhibit lower volatility / defensive characteristics.
        In absence of asset-level volatility metadata, prefer assets that show
        smoother return series (lower absolute sum of squared returns as a proxy).

    ``lift_risk_adjusted``:
        Shortlist assets that show higher mean return relative to their variance
        in the correlation_data series.

    Args:
        symphony_id:
            The Composer symphony being modified.
        objective:
            The ``SwapObjective`` driving the search.
        correlation_data:
            Dict of entity_id → list[float] return series, used to compute
            pairwise correlations and volatility estimates for candidate ranking.
        available_assets:
            The candidate pool.  No allowlist; open universe per Gate-1 Res. #2.
        lens_scores:
            Optional per-ticker lens evidence dict as returned by
            ``extract_lens_scores``.  When ``None`` (default) or empty, behaviour
            is byte-identical to the pre-Cycle-3 implementation.  When provided,
            the mean lens score is blended into the post-primary-sort ranking via
            ``_apply_lens_blend`` (additive, weighted by ``LENS_BLEND_WEIGHT``).
            Lens scoring influences RANKING ONLY — it never bypasses the gate.

    Returns:
        An ordered list of candidate dicts, each with a ``"ticker"`` key
        (plus optional metadata).  Top-ranked first.  Never plain strings.
        The ``"ticker"`` key is the primary consumer contract.
    """
    if not available_assets:
        return []

    obj_type = objective.objective_type

    # ---------------------------------------------------------------------------
    # reduce_correlation: rank by low absolute correlation vs target pair member
    # ---------------------------------------------------------------------------
    if obj_type == "reduce_correlation":
        target = objective.target_pair[0] if objective.target_pair else symphony_id
        target_series = correlation_data.get(target, [])

        if not target_series:
            # No reference series; return all available assets (can't rank by correlation).
            return [{"ticker": t, "score": 0.5} for t in available_assets]

        def _pearson_corr(xs: list, ys: list) -> float:
            """Pearson correlation between two equal-length series.  Returns 0.0 on degenerate input."""  # noqa: E501  # un-wrappable long line
            n = min(len(xs), len(ys))
            if n < 2:
                return 0.0
            xs, ys = xs[:n], ys[:n]
            mean_x = sum(xs) / n
            mean_y = sum(ys) / n
            cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(xs, ys)) / n
            var_x = sum((xi - mean_x) ** 2 for xi in xs) / n
            var_y = sum((yi - mean_y) ** 2 for yi in ys) / n
            denom = (var_x * var_y) ** 0.5
            return cov / denom if denom > 1e-12 else 0.0

        # Score each candidate by its absolute correlation with the target series:
        # lower absolute correlation = better de-correlation candidate.
        scored = []
        for asset in available_assets:
            asset_series = correlation_data.get(asset, [])
            if asset_series:
                corr = abs(_pearson_corr(target_series, asset_series))
            else:
                # No data for this asset; treat as neutral correlation (0.5 = unknown).
                corr = 0.5
            scored.append((asset, corr))

        # Sort ascending by absolute correlation: lowest first = most de-correlated.
        scored.sort(key=lambda t: t[1])
        # lower absolute correlation = better
        return _apply_lens_blend(
            [{"ticker": asset, "score": corr} for asset, corr in scored],
            lens_scores=lens_scores,
        )

    # ---------------------------------------------------------------------------
    # reduce_drawdown: rank by smoothness of return series (low variance as proxy)
    # ---------------------------------------------------------------------------
    elif obj_type == "reduce_drawdown":
        scored = []
        for asset in available_assets:
            series = correlation_data.get(asset, [])
            if series:
                # Proxy for drawdown risk: higher variance → higher drawdown risk.
                n = len(series)
                mean = sum(series) / n
                variance = sum((r - mean) ** 2 for r in series) / n
            else:
                variance = float("inf")  # No data → worst rank
            scored.append((asset, variance))

        # Sort ascending by variance: lowest variance = most defensive.
        scored.sort(key=lambda t: t[1])
        # lower variance = better
        return _apply_lens_blend(
            [{"ticker": asset, "score": v} for asset, v in scored],
            lens_scores=lens_scores,
        )

    # ---------------------------------------------------------------------------
    # lift_risk_adjusted: rank by mean return / std (Sharpe-like from return series)
    # ---------------------------------------------------------------------------
    elif obj_type == "lift_risk_adjusted":
        scored = []
        for asset in available_assets:
            series = correlation_data.get(asset, [])
            if series and len(series) > 1:
                n = len(series)
                mean = sum(series) / n
                variance = sum((r - mean) ** 2 for r in series) / n
                std = variance**0.5
                pseudo_sharpe = mean / std if std > 1e-12 else 0.0
            else:
                pseudo_sharpe = 0.0
            scored.append((asset, pseudo_sharpe))

        # Sort descending by pseudo-Sharpe: highest risk-adjusted return first.
        scored.sort(key=lambda t: t[1], reverse=True)
        # higher Sharpe = better
        return _apply_lens_blend(
            [{"ticker": asset, "score": s} for asset, s in scored],
            lens_scores=lens_scores,
        )

    # ---------------------------------------------------------------------------
    # Unknown objective: return full available_assets as-is (defensive default).
    # ---------------------------------------------------------------------------
    return _apply_lens_blend(
        [{"ticker": t} for t in available_assets],
        lens_scores=lens_scores,
    )


# ---------------------------------------------------------------------------
# Objective rationale generator
# ---------------------------------------------------------------------------


def _build_objective_rationale(
    candidate_asset: str,
    incumbent_asset: str,
    objective: SwapObjective,
    lens_scores: dict | None = None,
) -> str:
    """Build a human-readable rationale string explaining why this candidate addresses the objective.  # noqa: E501  # un-wrappable long line

    When ``lens_scores`` is provided and contains evidence for ``candidate_asset``,
    a lens evidence summary is appended to the rationale (Cycle-3 AC-5).
    Each candidate stands on its own merits — no ranked single-winner verdict.
    """
    obj_type = objective.objective_type
    # AC-10: measured_value is display-only and every current production caller
    # passes 0.0 (see SwapObjective's docstring) — no branch below references it
    # anymore, so it is not read here.
    pair = objective.target_pair

    if obj_type == "reduce_correlation":
        pair_str = f"{pair[0]} and {pair[1]}" if pair else "the correlated pair"
        base = (
            f"Replacing {incumbent_asset} with {candidate_asset} addresses the "
            f"correlation between {pair_str}. "
            f"{candidate_asset} is expected to exhibit lower correlation with the pair "
            f"based on objective-directed candidate scoring."
        )
    elif obj_type == "reduce_drawdown":
        base = (
            f"Replacing {incumbent_asset} with {candidate_asset} targets drawdown "
            f"reduction. {candidate_asset} was selected as a lower-volatility "
            f"candidate from objective-directed ranking."
        )
    elif obj_type == "lift_risk_adjusted":
        base = (
            f"Replacing {incumbent_asset} with {candidate_asset} aims to lift "
            f"risk-adjusted return. {candidate_asset} was scored as a higher "
            f"risk-adjusted candidate."
        )
    else:
        base = (
            f"Replacing {incumbent_asset} with {candidate_asset} per the stated "
            f"objective ({obj_type})."
        )

    # Cycle-3 AC-5: append lens evidence summary when available.
    lens_summary = _build_lens_evidence_summary(candidate_asset, lens_scores)
    if lens_summary:
        return f"{base} {lens_summary}"
    return base


def _build_lens_evidence_summary(ticker: str, lens_scores: dict | None) -> str:
    """Return a concise lens evidence sentence for inclusion in the rationale.

    Returns an empty string when no lens evidence is available for the ticker —
    callers must handle this gracefully (honest-availability, AC-6).
    """
    if not lens_scores or not isinstance(lens_scores, dict):
        return ""
    ticker_scores = lens_scores.get(ticker)
    if not ticker_scores or not isinstance(ticker_scores, dict):
        return ""
    valid = {k: v for k, v in ticker_scores.items() if isinstance(v, (int, float))}
    if not valid:
        return ""
    # Format as "Lens evidence (sentiment: 0.80, macro: 0.60)."
    parts = ", ".join(f"{lens}: {score:.2f}" for lens, score in sorted(valid.items()))
    return f"Lens evidence ({parts})."


def _build_candidate_lens_evidence(ticker: str, lens_scores: dict | None) -> dict:
    """Build the lens_evidence dict for a single candidate ticker (AC-4 persistence).

    Returns ``{ticker: {signal: mean_score, source_lens: [lens_names], confidence: mean_score}}``
    when lens evidence is available, or ``{}`` when lens_scores is absent/empty.
    Only available lenses (present in lens_scores) contribute — honest-availability.
    """
    if not lens_scores or not isinstance(lens_scores, dict):
        return {}
    ticker_scores = lens_scores.get(ticker)
    if not ticker_scores or not isinstance(ticker_scores, dict):
        return {}
    valid = {k: v for k, v in ticker_scores.items() if isinstance(v, (int, float))}
    if not valid:
        return {}
    mean_score = sum(valid.values()) / len(valid)
    return {
        ticker: {
            "signal": mean_score,
            "source_lens": sorted(valid.keys()),
            "confidence": mean_score,
        }
    }


def _collect_lens_sources(lens_scores: dict | None) -> list:
    """Return sources list from lens_scores metadata (AC-4 persistence).

    Currently lens_scores carries only numeric scores (no embedded citation dicts).
    Returns empty list — callers that have access to the full lens context blocks
    should pass sources directly.  This function exists as the structural hook
    for future enrichment without a signature change.
    """
    # lens_scores dict contains {ticker: {lens_name: score}} — no citation objects.
    # Source citations live in the context's lens block "sources" lists, which are
    # not threaded through generate_objective_directed_candidates (they live at the
    # assemble_advisor_context level).  An empty list is the correct honest value here.
    return []


# ---------------------------------------------------------------------------
# Persistence helper (AC-X3)
# ---------------------------------------------------------------------------


def _persist_observation(
    symphony_id: str,
    proposal: SwapProposalResult,
    gate_result: CandidateGateResult,
    lens_evidence: dict | None = None,
    sources: list | None = None,
) -> None:
    """Persist a swap proposal as an advisor_observation, REGARDLESS of verdict (RC-4).

    Writes the ACTUAL gate verdict (ADOPT_CANDIDATE / KEEP_INCUMBENT /
    REJECT_VETO_FAILED) with ``is_advisory_only=1`` and
    ``observation_type="asset_swap_proposal"`` (AC-X3 structural requirements).

    RC-4: persistence is verdict-agnostic so the operator sees the engine ran and
    kept the incumbent — an ADOPT-only write left advisor_observations empty on the
    common KEEP path, making the advisor look dead.

    Cycle-3 AC-4: ``lens_evidence`` and ``sources`` are written into ``raw_response``
    when provided, enabling downstream audit of which lens signals contributed to
    surfacing this candidate.  Both default to empty/None (additions-only).

    AC-4 citation validation: each entry in ``sources`` is validated through
    ``ai_advisor.build_citation`` before writing.  Malformed citation dicts
    (missing required fields, invalid URL scheme) are dropped so the persisted
    audit row only contains well-formed ``{title, url, published, lens}`` objects.
    Uses a deferred import to avoid module-level circular-import risk.
    """
    # Validate source citations through the shared build_citation gate (AC-4 / CC-4).
    # Deferred import: ai_advisor does not import asset_swap_engine, so this is safe
    # at call-time even though a top-level import would be circular.
    _validated_sources: list = []
    if sources:
        try:
            from ai_advisor import build_citation as _bc  # noqa: PLC0415

            _validated_sources = [r for s in sources if (r := _bc(s)) is not None]
        except Exception:
            # Fallback: write valid-looking dicts unvalidated rather than losing them.
            _validated_sources = [s for s in sources if isinstance(s, dict)]

    database.insert_advisor_observation(
        advisor_role="ASSET_SWAP",
        symphony_id=symphony_id,
        subject_type="asset_swap_proposal",
        subject_id=proposal.candidate_id,
        verdict=gate_result.verdict.decision,
        is_advisory_only=1,
        observation_type=_OBSERVATION_TYPE,
        raw_response={
            "candidate_id": proposal.candidate_id,
            "incumbent_asset": proposal.incumbent_asset,
            "candidate_asset": proposal.candidate_asset,
            "objective_type": proposal.objective.objective_type,
            "objective_rationale": proposal.objective_rationale,
            "gate_decision": gate_result.verdict.decision,
            "validation_days": gate_result.validation_days,
            "oos_alpha": gate_result.oos_alpha,
            "caveats": gate_result.caveats,
            # Cycle-3 AC-4: lens evidence + validated citations for auditability.
            # Sources are filtered through build_citation — only valid structs persist.
            "lens_evidence": lens_evidence if lens_evidence is not None else {},
            "sources": _validated_sources,
        },
    )


# ---------------------------------------------------------------------------
# Core: evaluate a single swap variant
# ---------------------------------------------------------------------------


def _spy_returns_fn_for(symphony_id: str):
    """Source a real SPY OOS-fold baseline once (AC-5/AC-25), mirroring
    strategy_builder_engine.py:807-826. Returns a zero-arg callable suitable
    for evaluate_candidate_batch's spy_returns_fn= seam. A 100%-SPY tree is
    the minimal valid Composer tree for a pure SPY backtest — same
    run_backtest client used for candidates, no new endpoint. On SPY-fetch
    error or an empty daily_returns series the callable returns {} so the
    gate's conservative +inf-sentinel WITHHOLD fires (edge-14) instead of a
    silent fall-back to the old beats-a-flat-0.0%-return baseline."""
    spy_tree = symphony_schema.make_root(
        "SPY Benchmark",
        "daily",
        [symphony_schema.make_weight_equal([symphony_schema.make_asset("SPY")])],
    )
    spy_result = run_backtest(spy_tree, symphony_id=symphony_id)
    if spy_result.error or not spy_result.daily_returns:
        spy_returns_dict: dict[str, float] = {}
    else:
        # Pct-scale matches dated_returns on candidates (log × 100 → pct).
        spy_returns_dict = {d: r * 100.0 for d, r in spy_result.daily_returns.items()}
    return lambda: spy_returns_dict


def _evaluate_single_variant(
    raw_value: dict,
    symphony_id: str,
    incumbent_asset: str,
    candidate_asset: str,
    objective: SwapObjective,
    symphony_name: str = "",
    lens_scores: dict | None = None,
) -> tuple:
    """Backtest a single swap variant.  Returns (BacktestCandidate | None, SwapProposalResult, baseline_stats, baseline_returns_pct).  # noqa: E501  # un-wrappable long line

    Returns (candidate, proposal_shell, baseline_stats, baseline_returns_pct) where:
    - candidate is None when the variant backtest failed (AC-X5)
    - proposal_shell is a SwapProposalResult with backtest_error set on failure
    - baseline_stats is the stats dict from the baseline backtest (or None on failure)
    - baseline_returns_pct is the baseline's daily log-returns converted to
      percent scale (or [] when the baseline was never backtested — the
      incumbent-not-in-tree branch, before any backtest call). AC-13: callers
      reuse this instead of re-backtesting the identical baseline tree a
      second time.

    Cycle-3 AC-5: when ``lens_scores`` is provided, the rationale incorporates
    lens evidence for ``candidate_asset``.
    """
    candidate_id = f"{symphony_id}:{incumbent_asset}->{candidate_asset}"
    rationale = _build_objective_rationale(
        candidate_asset, incumbent_asset, objective, lens_scores=lens_scores
    )
    symphony_name = symphony_name or symphony_id

    apply_guidance = ADVISE_ONLY_APPLY_TEMPLATE.format(
        symphony_name=symphony_name,
        from_ticker=incumbent_asset,
        to_ticker=candidate_asset,
    )

    # Verify incumbent_asset is in the tree before spending a backtest (AC-2.4).
    present = extract_tickers(raw_value)
    if incumbent_asset not in present:
        return (
            None,
            SwapProposalResult(
                candidate_id=candidate_id,
                symphony_id=symphony_id,
                incumbent_asset=incumbent_asset,
                candidate_asset=candidate_asset,
                objective=objective,
                objective_rationale=rationale,
                apply_guidance=apply_guidance,
                backtest_error=(
                    f"could not backtest this variant: '{incumbent_asset}' is not in "
                    f"the symphony tree (present: {sorted(present)[:10]})"
                ),
            ),
            None,
            [],
        )

    # Backtest baseline.
    baseline_result = run_backtest(raw_value, symphony_id=symphony_id)
    baseline_stats = baseline_result.stats
    # AC-13: computed once here, returned to the caller so propose_operator_swap
    # can reuse it instead of a second, redundant baseline backtest.
    baseline_returns_pct = [r * 100.0 for r in baseline_result.daily_returns.values()]

    # Apply swap and backtest variant (AC-X5: failure here is isolated).
    variant_tree = apply_ticker_swap(raw_value, incumbent_asset, candidate_asset)
    variant_result = run_backtest(variant_tree, symphony_id=symphony_id)

    if variant_result.error:
        return (
            None,
            SwapProposalResult(
                candidate_id=candidate_id,
                symphony_id=symphony_id,
                incumbent_asset=incumbent_asset,
                candidate_asset=candidate_asset,
                objective=objective,
                objective_rationale=rationale,
                baseline_stats=baseline_stats,
                apply_guidance=apply_guidance,
                backtest_error=f"backtest failed: {variant_result.error}",
                data_warnings=variant_result.data_warnings,
            ),
            baseline_stats,
            baseline_returns_pct,
        )

    # Convert log-returns → percent for the fold-transform.
    variant_returns_pct = [r * 100.0 for r in variant_result.daily_returns.values()]
    # AC-4: date-keyed pct-scale returns enable the batch PBO veto (mirrors
    # strategy_builder_engine.py:843).
    dated_returns_pct = {d: r * 100.0 for d, r in variant_result.daily_returns.items()}

    bt_candidate = BacktestCandidate(
        candidate_id=candidate_id,
        daily_returns_pct=variant_returns_pct,
        candidate_params={},
        incumbent_params={},
        theory_prior_params={},
        nn1_compliant=True,
        purge_integrity_ok=True,
        dated_returns=dated_returns_pct,
    )

    proposal_shell = SwapProposalResult(
        candidate_id=candidate_id,
        symphony_id=symphony_id,
        incumbent_asset=incumbent_asset,
        candidate_asset=candidate_asset,
        objective=objective,
        objective_rationale=rationale,
        baseline_stats=baseline_stats,
        variant_stats=variant_result.stats,
        apply_guidance=apply_guidance,
        data_warnings=variant_result.data_warnings,
    )

    return (bt_candidate, proposal_shell, baseline_stats, baseline_returns_pct)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def propose_operator_swap(
    symphony_id: str,
    score_tree: dict,
    incumbent_asset: str,
    candidate_asset: str,
    objective: SwapObjective,
    *,
    incumbent_oos_alpha: float | None = None,
    default_oos_alpha: float = 0.0,
    lens_scores: dict | None = None,
    lens_sources: list | None = None,
) -> SwapRunResult:
    """Evaluate one operator-specified asset swap (AC-2.1).

    Backtests the variant, gates via M2 ``evaluate_candidate_batch``, persists
    survivors as ``advisor_observation`` with ``is_advisory_only=1`` (AC-X3),
    and returns a ``SwapRunResult``.  Never raises on backtest or gate failure (AC-X5).

    Args:
        symphony_id:
            Composer symphony UUID.
        score_tree:
            The raw Composer score tree (``GET /api/v0.1/symphonies/{id}/score``).
        incumbent_asset:
            The ticker to replace.
        candidate_asset:
            The replacement ticker (no allowlist — open universe per Gate-1 Res. #2).
        objective:
            The ``SwapObjective`` driving this swap (surfaces alongside result per AC-2.3).
        incumbent_oos_alpha:
            The live incumbent's OOS alpha (sum of validation-fold returns, percent).
            Used as the gate's KEEP_INCUMBENT comparison baseline.
        default_oos_alpha:
            The global-default params' OOS alpha.
        lens_scores:
            Optional per-ticker lens evidence dict as returned by ``extract_lens_scores``.
            When provided, the rationale mentions lens signals (AC-5) and
            ``lens_evidence``/``sources`` are written to the persisted observation (AC-4).
            Ranking of candidates is unaffected by this param in operator mode
            (the operator already chose the candidate). Default None.

    Returns:
        ``SwapRunResult`` — always returned, never raises.
    """
    symphony_name = (
        (score_tree.get("name") or symphony_id) if isinstance(score_tree, dict) else symphony_id
    )

    bt_candidate, proposal_shell, baseline_stats, baseline_returns_pct = _evaluate_single_variant(
        raw_value=score_tree,
        symphony_id=symphony_id,
        incumbent_asset=incumbent_asset,
        candidate_asset=candidate_asset,
        objective=objective,
        symphony_name=symphony_name,
        lens_scores=lens_scores,
    )

    # Backtest failed — zero candidates to gate. Empty-branch site (AC-5 exempt,
    # audit-verified at backtest_gate_engine.py:627-633 — evaluate_candidate_batch
    # returns before spy_returns_fn is ever read when candidates=[]).
    if bt_candidate is None:
        gate_batch = evaluate_candidate_batch(
            [],
            incumbent_oos_alpha=incumbent_oos_alpha,
            default_oos_alpha=default_oos_alpha,
        )
        return SwapRunResult(
            gate_batch=gate_batch,
            proposals=[proposal_shell],
            survivors=[],
            rejected_candidates=[proposal_shell],
            message=NO_SURVIVORS_MESSAGE,
            objective=objective,
        )

    # Compute baseline OOS alpha from the baseline return series.
    # H6/RC-1: the baseline must be the SAME validation-fold alpha the candidate
    # is scored on (_fold_transform_single), not the full-history sum — otherwise a
    # ~25-day candidate fold is compared against a ~125-day full-history baseline and
    # the gate is biased to systematic KEEP_INCUMBENT.
    # H5: a caller-supplied incumbent_oos_alpha is used only when explicitly provided
    # (is not None); an explicit 0.0 (real break-even) must NOT trigger the fallback.
    # AC-13: reuses the baseline_returns_pct already computed by _evaluate_single_variant
    # instead of a second, redundant baseline backtest.
    fold_baseline_oos_alpha = _fold_transform_single(baseline_returns_pct).oos_alpha
    effective_incumbent_oos_alpha = (
        incumbent_oos_alpha if incumbent_oos_alpha is not None else fold_baseline_oos_alpha
    )

    # Gate the single candidate. AC-5: real SPY-OOS baseline sourced once here
    # (real gate call — not the empty-branch site above).
    gate_batch = evaluate_candidate_batch(
        [bt_candidate],
        incumbent_oos_alpha=effective_incumbent_oos_alpha,
        default_oos_alpha=default_oos_alpha,
        spy_returns_fn=_spy_returns_fn_for(symphony_id),
    )
    gate_result = gate_batch.results[0]

    proposal_shell.gate_result = gate_result
    # Propagate caveats from gate_result onto the proposal so callers and the
    # route can read proposal.caveats directly (AC-3.3 / SURVIVOR_OVERFITTING_CAVEAT).
    proposal_shell.caveats = list(gate_result.caveats)

    proposals = [proposal_shell]
    survivors = []
    rejected = []

    if gate_result.verdict.decision == "ADOPT_CANDIDATE":
        survivors.append(proposal_shell)
    else:
        rejected.append(proposal_shell)

    # RC-4: persist the observation REGARDLESS of verdict so the operator sees the
    # engine ran (incl. KEEP_INCUMBENT / REJECT_VETO_FAILED), not just on ADOPT.
    # RC-5: a persistence failure must be SURFACED (persistence_error), not swallowed
    # to a warning — otherwise an adopted survivor is shown whose audit row never landed.
    # Cycle-3 AC-4: build lens_evidence dict for the candidate asset from lens_scores.
    # Use caller-supplied lens_sources when provided (AC-4 citation provenance);
    # fall back to auto-collected sources from lens_scores metadata otherwise.
    candidate_lens_evidence = _build_candidate_lens_evidence(candidate_asset, lens_scores)
    candidate_sources = (
        lens_sources if lens_sources is not None else _collect_lens_sources(lens_scores)
    )
    persistence_error = None
    try:
        _persist_observation(
            symphony_id,
            proposal_shell,
            gate_result,
            lens_evidence=candidate_lens_evidence,
            sources=candidate_sources,
        )
    except Exception as exc:
        persistence_error = f"{type(exc).__name__}: {exc}"
        logger.error(
            "propose_operator_swap: failed to persist observation for %s (%s)",
            proposal_shell.candidate_id,
            persistence_error,
            exc_info=True,
        )

    message = f"1 swap survived the gate for {symphony_name}" if survivors else NO_SURVIVORS_MESSAGE

    return SwapRunResult(
        gate_batch=gate_batch,
        persistence_error=persistence_error,
        proposals=proposals,
        survivors=survivors,
        rejected_candidates=rejected,
        message=message,
        objective=objective,
    )


def _backtest_returns_from_tree(raw_value: dict, symphony_id: str) -> list:
    """Run backtest on raw_value and return log-returns list.  Returns empty list on failure."""
    result = run_backtest(raw_value, symphony_id=symphony_id)
    if result.error:
        return []
    return list(result.daily_returns.values())


def suggest_swaps(
    symphony_id: str,
    score_tree: dict,
    objective: SwapObjective,
    correlation_data: dict,
    available_assets: list,
    *,
    incumbent_oos_alpha: float | None = None,
    default_oos_alpha: float = 0.0,
    lens_scores: dict | None = None,
    lens_sources: list | None = None,
) -> SwapRunResult:
    """Evaluate advisor-suggested objective-directed swap candidates (AC-2.2).

    Generates candidates via ``generate_objective_directed_candidates()``
    (objective-directed shortlisting — not brute-force), then backtests/gates
    the full batch together (n_effective = N for honest BHY FDR per AC-3.2),
    and returns only survivors.

    An absent Composer API key → returns an empty ``SwapRunResult`` with
    ``no_api_key=True`` and writes nothing (AC-X4).

    Args:
        symphony_id:
            Composer symphony UUID.
        score_tree:
            The raw Composer score tree.
        objective:
            The ``SwapObjective`` driving candidate generation.
        correlation_data:
            Dict of entity_id → list[float] return series for objective-directed
            candidate ranking.
        available_assets:
            The candidate pool.  Open universe; ranked by objective-direction.
        incumbent_oos_alpha:
            Incumbent's OOS alpha for the gate's KEEP_INCUMBENT comparison.
        default_oos_alpha:
            Global-default params' OOS alpha.
        lens_scores:
            Optional per-ticker lens evidence as returned by ``extract_lens_scores``.
            When provided, blended into candidate ranking (AC-2) and written to
            persistence (AC-4).  None → byte-identical pre-Cycle-3 behaviour.

    Returns:
        ``SwapRunResult`` — always returned, never raises.
        Zero survivors is a valid outcome (AC-2.5).
    """
    symphony_name = (
        (score_tree.get("name") or symphony_id) if isinstance(score_tree, dict) else symphony_id
    )

    # Detect absent API key early (AC-X4).
    if not _has_composer_key():
        logger.info("suggest_swaps: no Composer API key — returning no_api_key=True")
        return SwapRunResult(
            gate_batch=_empty_gate_batch(),
            no_api_key=True,
            message="advisor unavailable: API key not configured",
            objective=objective,
        )

    # Identify the current holdings in the score tree (the incumbents to try replacing).
    present_tickers = extract_tickers(score_tree)
    if not present_tickers:
        return SwapRunResult(
            gate_batch=_empty_gate_batch(),
            message=NO_SURVIVORS_MESSAGE,
            objective=objective,
        )

    # Objective-directed candidate generation (Cycle-3: lens_scores blends into ranking).
    candidates_ranked = generate_objective_directed_candidates(
        symphony_id=symphony_id,
        objective=objective,
        correlation_data=correlation_data,
        available_assets=available_assets,
        lens_scores=lens_scores,
    )

    if not candidates_ranked:
        return SwapRunResult(
            gate_batch=_empty_gate_batch(),
            message=NO_SURVIVORS_MESSAGE,
            objective=objective,
        )

    # For advisor-suggested mode: swap the most correlated (or highest-drawdown)
    # holding in the tree, as identified by the objective.
    # Find the incumbent: pick the most-correlated holding from the tree's tickers.
    incumbent_asset = _select_incumbent_asset(
        present_tickers=present_tickers,
        objective=objective,
        correlation_data=correlation_data,
    )

    # Backtest each candidate variant independently (AC-X5: isolate failures).
    bt_candidates = []
    proposal_shells = []

    for candidate_entry in candidates_ranked:
        # candidates_ranked contains dicts with "ticker" key (per generate_objective_directed_candidates).  # noqa: E501  # inline comment cannot be wrapped without splitting the annotation
        candidate_asset = (
            candidate_entry["ticker"] if isinstance(candidate_entry, dict) else candidate_entry
        )
        if candidate_asset in present_tickers:
            # Skip tickers already in the tree (swapping A→A is a no-op).
            continue

        bt_cand, proposal_shell, _, _ = _evaluate_single_variant(
            raw_value=score_tree,
            symphony_id=symphony_id,
            incumbent_asset=incumbent_asset,
            candidate_asset=candidate_asset,
            objective=objective,
            symphony_name=symphony_name,
            lens_scores=lens_scores,
        )
        proposal_shells.append(proposal_shell)
        if bt_cand is not None:
            bt_candidates.append(bt_cand)

    # Gate all successfully-backtested candidates together (honest n_effective = N).
    # H6/RC-1: fold-matched baseline (see propose_operator_swap). H5: explicit-0.0 safe.
    # (This per-symphony weekly baseline call is separate from the per-candidate
    # baseline calls inside _evaluate_single_variant above — AC-13 is scoped to
    # the operator single-eval route only, per audit D-7; not touched here.)
    baseline_returns = _backtest_returns_from_tree(score_tree, symphony_id)
    baseline_returns_pct = [r * 100.0 for r in baseline_returns]
    fold_baseline_oos_alpha = _fold_transform_single(baseline_returns_pct).oos_alpha
    effective_incumbent_oos_alpha = (
        incumbent_oos_alpha if incumbent_oos_alpha is not None else fold_baseline_oos_alpha
    )

    if bt_candidates:
        # AC-5: real SPY-OOS baseline sourced once for the whole weekly batch.
        gate_batch = evaluate_candidate_batch(
            bt_candidates,
            incumbent_oos_alpha=effective_incumbent_oos_alpha,
            default_oos_alpha=default_oos_alpha,
            spy_returns_fn=_spy_returns_fn_for(symphony_id),
        )
        gate_result_by_id = {gr.candidate_id: gr for gr in gate_batch.results}
    else:
        gate_batch = _empty_gate_batch()
        gate_result_by_id = {}

    # Annotate proposal shells with gate results and build survivors/rejected lists.
    # RC-4: persist every gated proposal regardless of verdict. RC-5: surface the
    # first persistence failure (persistence_error), never swallow it to a warning.
    survivors = []
    rejected = []
    persistence_error = None
    for shell in proposal_shells:
        gate_result = gate_result_by_id.get(shell.candidate_id)
        if gate_result is not None:
            shell.gate_result = gate_result
            # Propagate gate caveats onto the proposal (AC-3.3 / SURVIVOR_OVERFITTING_CAVEAT).
            shell.caveats = list(gate_result.caveats)
            if gate_result.verdict.decision == "ADOPT_CANDIDATE":
                survivors.append(shell)
            else:
                rejected.append(shell)
            try:
                # Cycle-3 AC-4: build per-candidate lens_evidence for persistence.
                cand_ticker = shell.candidate_asset if hasattr(shell, "candidate_asset") else ""
                cand_lens_ev = _build_candidate_lens_evidence(cand_ticker, lens_scores)
                # AC-4: use caller-supplied citations when provided; fall back to
                # auto-collected metadata from lens_scores otherwise.
                cand_sources = (
                    lens_sources if lens_sources is not None else _collect_lens_sources(lens_scores)
                )
                _persist_observation(
                    symphony_id,
                    shell,
                    gate_result,
                    lens_evidence=cand_lens_ev,
                    sources=cand_sources,
                )
            except Exception as exc:
                if persistence_error is None:
                    persistence_error = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "suggest_swaps: failed to persist observation for %s (%s: %s)",
                    shell.candidate_id,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
        else:
            # Backtest failed for this candidate — goes into rejected.
            rejected.append(shell)

    message = (
        f"{len(survivors)} swap(s) survived the gate for {symphony_name}"
        if survivors
        else NO_SURVIVORS_MESSAGE
    )

    return SwapRunResult(
        gate_batch=gate_batch,
        persistence_error=persistence_error,
        proposals=proposal_shells,
        survivors=survivors,
        rejected_candidates=rejected,
        message=message,
        objective=objective,
    )


def _select_incumbent_asset(
    present_tickers: set,
    objective: SwapObjective,
    correlation_data: dict,
) -> str:
    """Select the incumbent asset to replace based on the objective.

    For ``reduce_correlation``: select the holding with the highest absolute
    correlation to the target pair member in correlation_data.

    For other objectives: select the first holding alphabetically (deterministic fallback).
    """
    if not present_tickers:
        return ""

    obj_type = objective.objective_type

    if obj_type == "reduce_correlation" and objective.target_pair:
        target = objective.target_pair[0]
        target_series = correlation_data.get(target, [])
        if target_series:
            best_ticker = max(
                present_tickers,
                key=lambda t: abs(
                    _pearson_corr_series(
                        correlation_data.get(t, []),
                        target_series,
                    )
                ),
            )
            return best_ticker

    # Deterministic fallback: alphabetical first.
    return sorted(present_tickers)[0]


def _pearson_corr_series(xs: list, ys: list) -> float:
    """Pearson correlation between two series.  Returns 0.0 on degenerate input."""
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    xs, ys = xs[:n], ys[:n]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(xs, ys)) / n
    var_x = sum((xi - mean_x) ** 2 for xi in xs) / n
    var_y = sum((yi - mean_y) ** 2 for yi in ys) / n
    denom = (var_x * var_y) ** 0.5
    return cov / denom if denom > 1e-12 else 0.0
