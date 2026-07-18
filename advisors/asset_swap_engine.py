"""M3 Asset-Swap engine — objective-directed swap proposals for the AI Advisor.

This module is OFFLINE only (not on the 1-minute live execution path).

Implements the two Asset-Swap modes described in feature-plans/ai-advisor.md §M3:

  1. **Operator-initiated** (AC-2.1): ``propose_operator_swap()``.
     Two modes, branched on whether BOTH ``incumbent_asset``/``candidate_asset``
     are supplied:
       - EXPLICIT-PAIR (both supplied): the operator's exact pair is applied,
         backtested via M2 ``advisors.composer_backtest_client``, gated via M2
         ``advisors.backtest_gate_engine`` — byte-preserved pre-R2-3 behavior.
       - REASONED (R2-3): the LLM-reasoned generator proposes the pair(s) over
         the operator's real holdings + the real tradeable universe.

  2. **Advisor-suggested** (AC-2.2): ``suggest_swaps()``.
     Given an objective, the engine calls ``generate_reasoned_swap_candidates()``
     (R2-3 — an LLM-backed, objective-directed generator, replacing the deleted
     fixed-statistical-sort deterministic generator) to
     produce a bounded set of OBJECTIVE-DIRECTED (incumbent, candidate) pairs,
     then backtests all of them and feeds the FULL batch as ONE call to
     ``backtest_gate_engine.evaluate_candidate_batch`` so the BHY/FDR correction
     is applied across ALL N candidates jointly (AC-3.2 / R2-3 AC-4).
     Never gates candidates individually — that defeats the multiple-testing
     correction.

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
* R2-3: the anthropic SDK import stays lazy inside ``_build_client`` (CC-2,
  off-execution-path); ``generate_reasoned_swap_candidates`` never raises
  (D-1) — LLM outage/malformed output degrades to ``[]``, never a silent
  fallback to the deleted deterministic sort.

Reference: feature-plans/ai-advisor.md §M3;
           feature-plans/ai-advisor.md §Gate-1 Resolutions #2 (objective-direction);
           feature-plans/advisor-r2-3-asset-swaps.md (reasoning port).
"""

from __future__ import annotations

import copy
import logging
import math
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

import ai_advisor
import database
import model_config
from advisors import symphony_schema
from advisors.backtest_gate_engine import (
    HARVEY_LIU_FDR_Q,
    SURVIVOR_OVERFITTING_CAVEAT,
    BacktestCandidate,
    CandidateGateResult,
    GatedBatch,
    _fold_transform_single,
    evaluate_candidate_batch,
)
from advisors.composer_backtest_client import run_backtest
from advisors.universe_provider import get_tradeable_set

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
# R2-3: _apply_lens_blend itself is preserved byte-unchanged (AC-12) — it no
# longer has a production call site (candidate SELECTION is now the LLM's, per
# Q4), but stays as a tested, standalone helper with its own dedicated coverage
# (tests/ai_advisor/test_lens_blend_efficacy.py).
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
# R2-3: SwapCandidate — one LLM-reasoned (incumbent, candidate) pair
# ---------------------------------------------------------------------------


@dataclass
class SwapCandidate:
    """One LLM-reasoned incumbent->candidate swap pair proposed by
    ``generate_reasoned_swap_candidates``.

    Fields
    ------
    incumbent_asset:
        The held ticker to replace. Always verified present in the real
        symphony tree (``extract_tickers``) before this object is constructed
        — never a raw, untrusted LLM claim.
    candidate_asset:
        The proposed replacement ticker. Always verified a member of the
        real tradeable universe (``get_tradeable_set()`` or a caller-supplied
        override, intersected with ``available_assets`` when given) before
        this object is constructed.
    rationale:
        The LLM's own free-text rationale for this pair, carried through for
        traceability. Never used as a trust signal for incumbent/candidate
        validity — those are independently verified.
    """

    incumbent_asset: str
    candidate_asset: str
    rationale: str = ""


# ---------------------------------------------------------------------------
# R2-3: reasoned-generation constants (mirrors logic_change_engine's R2-2 shape)
# ---------------------------------------------------------------------------

# Maximum number of advisor-suggested candidates per run.
# Bounding N is the primary overfitting-risk control for asset-swap proposals:
# an unbounded search would make the FDR correction ineffective in practice
# (the pool of backtested candidates must be manageable).
MAX_SUGGESTED_CANDIDATES: int = 30

# Bounds the candidate-universe listing rendered into the generation prompt —
# regardless of how large the real tradeable set is (~12.7k symbols). The
# LLM proposes freely; the full set is never injected verbatim (Q3).
_MAX_ASSETS_LISTED_IN_PROMPT: int = 40

# Output budget for the reasoned generator's structured tool-use response — a
# bounded list of incumbent/candidate/rationale pairs, small.
_MAX_OUTPUT_TOKENS: int = 2048

# Explicit client-side timeout — never rely on the SDK/urllib3 default.
_REQUEST_TIMEOUT_SECONDS: float = 30.0


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
    run_id:
        R2-3 (AC-7) — a UUID4 minted once per call (or a caller-supplied override),
        present on EVERY return path, including every early return. A correlation
        id for the call itself, traced into every persisted advisor_observations row.
    provenance:
        R2-3 (AC-5) — {"generation_model", "mode", "evidence_injected", "run_id"},
        a REAL 4-key dict on every return path, never None, never fabricated.
        generation_model/mode/run_id are cheap, non-fabricated facts about the call
        itself, never nulled on an error path — only evidence_injected's own
        per-source values carry the honesty signal.
    """

    gate_batch: GatedBatch
    proposals: list = field(default_factory=list)
    survivors: list = field(default_factory=list)
    rejected_candidates: list = field(default_factory=list)
    message: str = ""
    objective: SwapObjective | None = None
    no_api_key: bool = False
    persistence_error: str | None = None
    run_id: str = ""
    provenance: dict | None = None


# ---------------------------------------------------------------------------
# Empty GatedBatch sentinel (used for no-API-key / empty-candidate paths)
# ---------------------------------------------------------------------------


def _empty_gate_batch() -> GatedBatch:
    """Return an empty GatedBatch (zero candidates, zero survivors)."""
    return GatedBatch(results=[], survivors=[], n_candidates=0, fdr_q=HARVEY_LIU_FDR_Q)


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
    already present on every candidate dict — NEVER the discrete
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

    R2-3: this function has no production call site (candidate selection is
    now the LLM's, per Q4) but is preserved byte-unchanged (AC-12) — it keeps
    its own dedicated test coverage.

    Args:
        candidates:
            List of ``{"ticker": ..., "score": ...}`` dicts, pre-sorted by the
            primary objective metric (index 0 = best). Returned unchanged when
            ``lens_scores`` is None or empty. A missing/non-numeric ``"score"``
            degrades that candidate to its 0-based position (legacy/defensive
            fallback).
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
    # not threaded through generate_reasoned_swap_candidates (they live at the
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
    *,
    run_id: str = "",
    evidence_injected: dict | None = None,
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

    run_id / evidence_injected: R2-3 (AC-7) — additive traceability keys, always
    present, never a schema migration (raw_response is a free-form JSON blob
    column). Mirrors logic_change_engine.py's identical R2-2 persistence pattern.
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
            # R2-3 AC-7: run correlation + honest per-source manifest.
            "run_id": run_id,
            "evidence_injected": evidence_injected if evidence_injected is not None else {},
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
    - candidate is None when the variant backtest failed, the tree is
      structurally invalid, or the incumbent is absent (AC-X5 / R2-3 AC-3)
    - proposal_shell is a SwapProposalResult with backtest_error set on failure
    - baseline_stats is the stats dict from the baseline backtest (or None on failure)
    - baseline_returns_pct is the baseline's daily simple returns (AC-5 /
      DE-MATH-R2-001: composer_backtest_client._extract_returns emits simple,
      not log, returns) converted to percent scale (or [] when the baseline
      was never backtested — the incumbent-not-in-tree / structurally-invalid
      branches, before any backtest call). AC-13: callers reuse this instead
      of re-backtesting the identical baseline tree a second time.

    Cycle-3 AC-5: when ``lens_scores`` is provided, the rationale incorporates
    lens evidence for ``candidate_asset``.

    R2-3 AC-3: after ``apply_ticker_swap`` and BEFORE any backtest call
    (including the baseline), the swapped tree is structurally re-validated
    via ``symphony_schema.validate_tree`` — mirrors logic_change_engine's
    identical guard placement. Cheap insurance ahead of any spend; Composer
    /backtest remains the real tradeability arbiter.
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

    # Apply the swap and structurally re-validate BEFORE any backtest call
    # (R2-3 AC-3). Distinct, honest wording from the "not in tree" branch above.
    variant_tree = apply_ticker_swap(raw_value, incumbent_asset, candidate_asset)
    tree_errors = symphony_schema.validate_tree(variant_tree)
    if tree_errors:
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
                    "could not backtest this variant: the swapped tree failed "
                    f"structural validation ({'; '.join(tree_errors)})"
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

    # Backtest variant (AC-X5: failure here is isolated).
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

    # Convert simple returns → percent for the fold-transform (AC-5: the
    # producer emits simple returns, not log — composer_backtest_client._extract_returns).
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
# R2-3: LLM-reasoned candidate generation — replaces the deleted fixed-
# statistical-sort deterministic candidate generator (Q4).
# ---------------------------------------------------------------------------

_EMIT_SWAP_CANDIDATES_TOOL = {
    "name": "emit_swap_candidates",
    "description": (
        "Emit a bounded list of objective-directed asset-swap candidate pairs "
        "over the operator's real holdings. Each pair's incumbent_asset MUST "
        "be copied EXACTLY from a ticker genuinely held in the operator's "
        "strategy — never invent one. Each candidate_asset should be a real, "
        "liquid, tradeable US-equity ticker addressing the stated objective; "
        "it will be independently validated against the real tradeable "
        "universe before use — never claim tradeability yourself, it carries "
        "no weight."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "description": "List of proposed incumbent->candidate swap pairs.",
                "items": {
                    "type": "object",
                    "properties": {
                        "incumbent_asset": {"type": "string"},
                        "candidate_asset": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["incumbent_asset", "candidate_asset"],
                },
            }
        },
        "required": ["candidates"],
    },
}


def _build_client():
    """Construct the anthropic SDK client.

    Factory seam: tests patch ``asset_swap_engine._build_client``. Mirrors
    ``logic_change_engine._build_client`` / ``build_plan_generator._build_client``.

    Raises:
        RuntimeError: if the SDK is unavailable or no API key is configured.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — the reasoned asset-swap generator "
            "is unavailable until an API key is configured."
        )
    try:
        import anthropic  # noqa: PLC0415 - CC-2 lazy import, off-execution-path
    except ImportError as exc:  # pragma: no cover - SDK is a declared dep
        raise RuntimeError(f"the anthropic SDK is not installed: {exc}") from exc
    return anthropic.Anthropic(api_key=api_key)


def _build_reasoned_swap_generation_prompt(
    objective: SwapObjective,
    universe: frozenset,
    *,
    reasoning_context: str | None,
    correlation_data: dict | None,
) -> str:
    """Assemble the LLM prompt for a reasoned swap-candidate generation call.

    Bounded (AC-1/Q3): the candidate-universe listing is truncated to
    _MAX_ASSETS_LISTED_IN_PROMPT entries regardless of the real universe's
    size — the prompt must not scale with a ~12.7k-symbol tradeable set.
    Never a raw json.dumps() of the tree (AC-1) — the operator's real
    holdings reach the LLM only via reasoning_context (when supplied),
    never independently rendered here.
    """
    sections = [f"OBJECTIVE: {objective.objective_type}"]
    if reasoning_context:
        sections.append(reasoning_context)
    if correlation_data:
        keys = sorted(correlation_data.keys())
        sections.append(
            "CORRELATION EVIDENCE (entities with return-series data available, "
            "informing but not dictating candidate selection): " + ", ".join(keys)
        )
    bounded_universe = sorted(universe)[:_MAX_ASSETS_LISTED_IN_PROMPT]
    sections.append(
        "SAMPLE OF THE TRADEABLE CANDIDATE UNIVERSE (a bounded sample — "
        "candidate_asset will be validated against the full real tradeable "
        "set, not limited to this sample): " + ", ".join(bounded_universe)
    )
    sections.append(
        "Propose a bounded list of objective-directed incumbent->candidate "
        "swap pairs via the emit_swap_candidates tool. incumbent_asset must "
        "be a ticker genuinely held in the operator's strategy."
    )
    return "\n\n".join(sections)


def generate_reasoned_swap_candidates(
    symphony_id: str,
    raw_value: dict,
    objective: SwapObjective,
    *,
    reasoning_context: str | None = None,
    correlation_data: dict | None = None,
    available_assets: list | None = None,
    tradeable_universe: frozenset | None = None,
    max_candidates: int = MAX_SUGGESTED_CANDIDATES,
) -> list:
    """Generate a bounded set of LLM-REASONED ``SwapCandidate`` pairs (R2-3).

    Replaces the fixed-statistical-sort deterministic generator (deleted, Q4):
    an LLM proposes objective-directed incumbent->candidate pairs
    over the operator's real holdings, never a fixed correlation/variance sort.

    SECURITY-CRITICAL: each proposed pair's ``incumbent_asset`` is resolved
    against the REAL ``raw_value`` tree (``extract_tickers``) — a pair whose
    incumbent does not resolve to a real holding is DROPPED, never fabricated
    into a ``SwapCandidate``. Each ``candidate_asset`` is independently
    validated against the real tradeable universe (``get_tradeable_set()``, or
    a caller-supplied ``tradeable_universe`` override, intersected with
    ``available_assets`` when supplied) — the LLM's own claim of tradeability
    in free-text rationale is NEVER trusted (Q3).

    D-1: never raises. ``_build_client()`` raising, the SDK call raising, or a
    malformed tool_use payload (missing/non-list ``"candidates"``) all degrade
    to ``[]``.

    Args:
        symphony_id: the Composer symphony UUID (traceability only).
        raw_value: the real symphony decision tree.
        objective: the ``SwapObjective`` driving generation.
        reasoning_context: optional operator-context text block (see
            ``ai_advisor.build_reasoning_context``) injected verbatim into the
            prompt when truthy. Falsy (``None``/``""``) -> zero trace of it in
            the prompt.
        correlation_data: optional dict of entity_id -> return series (Q4:
            retained as prompt EVIDENCE — its entity keys are surfaced to the
            LLM — never used for a programmatic ranking anymore).
        available_assets: optional caller-supplied candidate pool. When
            supplied, narrows (never widens) the tradeable-universe membership
            check — the effective set is the INTERSECTION.
        tradeable_universe: optional caller-supplied override of the real
            tradeable set. When supplied, ``get_tradeable_set()`` is NEVER
            called (a genuine bypass, not an additional filter layer).
        max_candidates: upper bound on the number of returned candidates.

    Returns:
        A bounded list of ``SwapCandidate`` objects (at most ``max_candidates``).
        Empty when there are no real holdings, the LLM proposes nothing
        usable, or any failure occurs.
    """
    try:
        held = extract_tickers(raw_value)
        if not held:
            return []

        # Q3: resolve the effective candidate-membership universe. A caller-
        # supplied override bypasses the live fetch entirely (never called).
        if tradeable_universe is not None:
            universe = frozenset(tradeable_universe)
        else:
            universe = frozenset(get_tradeable_set())
        if available_assets:
            universe = universe & frozenset(available_assets)

        prompt = _build_reasoned_swap_generation_prompt(
            objective,
            universe,
            reasoning_context=reasoning_context,
            correlation_data=correlation_data,
        )

        client = _build_client()
        response = client.messages.create(
            model=model_config.get_advisor_suggestion_model(),
            max_tokens=_MAX_OUTPUT_TOKENS,
            tools=[_EMIT_SWAP_CANDIDATES_TOOL],
            tool_choice={"type": "tool", "name": "emit_swap_candidates"},
            messages=[{"role": "user", "content": prompt}],
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )

        tool_block = None
        for block in getattr(response, "content", None) or []:
            if getattr(block, "type", None) == "tool_use":
                tool_block = block
                break
        if tool_block is None:
            return []

        raw_candidates = (
            tool_block.input.get("candidates") if isinstance(tool_block.input, dict) else None
        )
        if not isinstance(raw_candidates, list):
            return []

        results: list[SwapCandidate] = []
        for entry in raw_candidates:
            if not isinstance(entry, dict):
                continue
            incumbent = entry.get("incumbent_asset")
            candidate = entry.get("candidate_asset")
            if not isinstance(incumbent, str) or not isinstance(candidate, str):
                continue

            # SECURITY-CRITICAL: incumbent must resolve to a REAL holding —
            # never trust the LLM's own claim.
            if incumbent not in held:
                continue
            # SECURITY-CRITICAL: candidate must clear the real tradeable
            # universe (+ available_assets intersection) — never trust any
            # LLM free-text claim of tradeability.
            if candidate not in universe:
                continue
            # Swap-into-self is a no-op that wastes a backtest — drop.
            if candidate == incumbent:
                continue

            raw_rationale = entry.get("rationale", "")
            results.append(
                SwapCandidate(
                    incumbent_asset=incumbent,
                    candidate_asset=candidate,
                    rationale=raw_rationale if isinstance(raw_rationale, str) else "",
                )
            )
            if len(results) >= max_candidates:
                break

        return results

    except Exception as exc:
        # D-1: degrade cleanly — never propagate an exception from the generator path.
        logger.debug(
            "generate_reasoned_swap_candidates: error (%s)", type(exc).__name__, exc_info=True
        )
        return []


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def _evaluate_explicit_pair(
    symphony_id: str,
    score_tree: dict,
    incumbent_asset: str,
    candidate_asset: str,
    objective: SwapObjective,
    *,
    symphony_name: str,
    incumbent_oos_alpha: float | None,
    default_oos_alpha: float,
    lens_scores: dict | None,
    lens_sources: list | None,
    run_id: str,
    provenance: dict,
) -> SwapRunResult:
    """Evaluate ONE (incumbent, candidate) pair as a single-element gated batch.

    The byte-preserved core shared by ``propose_operator_swap``'s explicit-pair
    mode (AC-12) and its reasoned mode (once the reasoned generator has
    resolved a single pair) — "evaluates it exactly like the explicit-pair
    path once resolved".
    """
    bt_candidate, proposal_shell, _baseline_stats, baseline_returns_pct = _evaluate_single_variant(
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
            run_id=run_id,
            provenance=provenance,
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
            run_id=run_id,
            evidence_injected=provenance["evidence_injected"],
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
        run_id=run_id,
        provenance=provenance,
    )


def propose_operator_swap(
    symphony_id: str,
    score_tree: dict,
    objective: SwapObjective,
    *,
    incumbent_asset: str | None = None,
    candidate_asset: str | None = None,
    incumbent_oos_alpha: float | None = None,
    default_oos_alpha: float = 0.0,
    lens_scores: dict | None = None,
    lens_sources: list | None = None,
    reasoning_context: str | None = None,
    reasoning_manifest: dict | None = None,
    run_id: str | None = None,
) -> SwapRunResult:
    """Evaluate an operator-initiated asset swap (AC-2.1). Two modes (R2-3):

    - EXPLICIT-PAIR (both ``incumbent_asset``/``candidate_asset`` truthy):
      the operator's exact pair is backtested + gated — BYTE-PRESERVED
      pre-R2-3 behavior (AC-12). ``generate_reasoned_swap_candidates`` and
      ``get_tradeable_set`` are NEVER called on this path.
    - REASONED (either/both omitted): the LLM-reasoned generator
      (``generate_reasoned_swap_candidates``, bounded to a single candidate)
      proposes the pair over the operator's real holdings + the real
      tradeable universe, then evaluates it exactly like the explicit-pair
      path once resolved.

    Persists survivors as ``advisor_observation`` with ``is_advisory_only=1``
    (AC-X3). Never raises on backtest, gate, or generation failure (AC-X5 / D-1).

    Args:
        symphony_id:
            Composer symphony UUID.
        score_tree:
            The raw Composer score tree (``GET /api/v0.1/symphonies/{id}/score``).
        objective:
            The ``SwapObjective`` driving this swap (surfaces alongside result per AC-2.3).
        incumbent_asset:
            The ticker to replace. Both this and ``candidate_asset`` must be
            truthy to select EXPLICIT-PAIR mode.
        candidate_asset:
            The replacement ticker (no allowlist — open universe per Gate-1 Res. #2).
        lens_scores:
            Optional per-ticker lens evidence dict as returned by ``extract_lens_scores``.
            When provided, the rationale mentions lens signals (AC-5) and
            ``lens_evidence``/``sources`` are written to the persisted observation (AC-4).
        reasoning_context:
            R2-3 — an optional, ready-to-inject operator-context text block (see
            ``ai_advisor.build_reasoning_context``), threaded straight through to
            ``generate_reasoned_swap_candidates`` on the REASONED path. The
            caller (route) builds this; the engine never calls
            ``build_reasoning_context`` itself.
        reasoning_manifest:
            R2-3 — the honest per-source manifest paired with ``reasoning_context``.
            Stamped into ``SwapRunResult.provenance["evidence_injected"]`` and
            persisted on the observation this run writes.
        run_id:
            R2-3 — an optional caller-supplied run id, used verbatim instead of minting
            a fresh UUID4. Omitted -> a UUID4 is minted.

    Returns:
        ``SwapRunResult`` — always returned, never raises.
    """
    # R2-3 (AC-5/AC-7): minted UNCONDITIONALLY, before any return below, so every
    # return path (early or normal) carries the SAME run_id/provenance — never
    # fabricated, never nulled. Mirrors logic_change_engine.propose_operator_logic_change.
    run_id = run_id or str(uuid.uuid4())
    provenance: dict = {
        "generation_model": model_config.get_advisor_suggestion_model(),
        "mode": "asset-swap",
        "evidence_injected": (
            reasoning_manifest if reasoning_manifest is not None else ai_advisor._EMPTY_MANIFEST
        ),
        "run_id": run_id,
    }

    symphony_name = (
        (score_tree.get("name") or symphony_id) if isinstance(score_tree, dict) else symphony_id
    )

    if incumbent_asset and candidate_asset:
        # EXPLICIT-PAIR mode — byte-preserved pre-R2-3 behavior (AC-12). No
        # Composer-key gate here either (pre-R2-3 behavior never had one on
        # this path — byte-preservation includes that omission).
        return _evaluate_explicit_pair(
            symphony_id,
            score_tree,
            incumbent_asset,
            candidate_asset,
            objective,
            symphony_name=symphony_name,
            incumbent_oos_alpha=incumbent_oos_alpha,
            default_oos_alpha=default_oos_alpha,
            lens_scores=lens_scores,
            lens_sources=lens_sources,
            run_id=run_id,
            provenance=provenance,
        )

    # REASONED mode. AC-X4: the Composer-key check must run BEFORE the
    # reasoned generator is ever called — a valid ANTHROPIC_API_KEY with no
    # Composer credentials must never bill a live Anthropic call for a run
    # guaranteed to be discarded.
    if not _has_composer_key():
        logger.info("propose_operator_swap: no Composer API key — returning no_api_key=True")
        return SwapRunResult(
            gate_batch=_empty_gate_batch(),
            no_api_key=True,
            message="advisor unavailable: API key not configured",
            objective=objective,
            run_id=run_id,
            provenance=provenance,
        )

    candidates = generate_reasoned_swap_candidates(
        symphony_id,
        score_tree,
        objective,
        reasoning_context=reasoning_context,
        max_candidates=1,
    )

    if not candidates:
        return SwapRunResult(
            gate_batch=_empty_gate_batch(),
            message=NO_SURVIVORS_MESSAGE,
            objective=objective,
            run_id=run_id,
            provenance=provenance,
        )

    chosen = candidates[0]
    return _evaluate_explicit_pair(
        symphony_id,
        score_tree,
        chosen.incumbent_asset,
        chosen.candidate_asset,
        objective,
        symphony_name=symphony_name,
        incumbent_oos_alpha=incumbent_oos_alpha,
        default_oos_alpha=default_oos_alpha,
        lens_scores=lens_scores,
        lens_sources=lens_sources,
        run_id=run_id,
        provenance=provenance,
    )


def _backtest_returns_from_tree(raw_value: dict, symphony_id: str) -> list:
    """Run backtest on raw_value and return simple-returns list.  Returns empty list on failure."""
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
    reasoning_context: str | None = None,
    reasoning_manifest: dict | None = None,
    run_id: str | None = None,
) -> SwapRunResult:
    """Evaluate advisor-suggested objective-directed swap candidates (AC-2.2).

    Generates candidates via ``generate_reasoned_swap_candidates()`` (R2-3 —
    LLM-reasoned, replaces the deleted fixed-statistical-sort deterministic
    generator), then backtests/gates the
    full batch together (n_effective = N for honest BHY FDR per AC-3.2), and
    returns only survivors.

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
            Dict of entity_id → list[float] return series. R2-3 (Q4): kept as
            prompt EVIDENCE surfaced to the LLM — no longer drives a
            programmatic ranking.
        available_assets:
            The candidate pool. R2-3: an ADDITIONAL constraint intersected
            with the real tradeable universe inside the reasoned generator —
            never widens beyond it.
        incumbent_oos_alpha:
            Incumbent's OOS alpha for the gate's KEEP_INCUMBENT comparison.
        default_oos_alpha:
            Global-default params' OOS alpha.
        lens_scores:
            Optional per-ticker lens evidence as returned by ``extract_lens_scores``.
            Threaded into the rationale (AC-5) and persistence (AC-4). Does not
            affect the LLM-reasoned selection/ranking (that is the LLM's, R2-3).
        reasoning_context:
            R2-3 — an optional, ready-to-inject operator-context text block (see
            ``ai_advisor.build_reasoning_context``), threaded straight through to
            ``generate_reasoned_swap_candidates``. Omitted (e.g. the weekly
            scheduler's call site) means ``None`` — the candidates are still
            reasoned, just without injected live operator context.
        reasoning_manifest:
            R2-3 — the honest per-source manifest paired with ``reasoning_context``.
            Stamped into ``SwapRunResult.provenance["evidence_injected"]`` and
            persisted on every observation this run writes.
        run_id:
            R2-3 — an optional caller-supplied run id, used verbatim instead of minting
            a fresh UUID4. Omitted -> a UUID4 is minted.

    Returns:
        ``SwapRunResult`` — always returned, never raises.
        Zero survivors is a valid outcome (AC-2.5).
    """
    symphony_name = (
        (score_tree.get("name") or symphony_id) if isinstance(score_tree, dict) else symphony_id
    )

    # R2-3 (AC-5/AC-7): minted UNCONDITIONALLY, before any return below, so every
    # return path carries the SAME run_id/provenance — never fabricated, never
    # nulled. Mirrors logic_change_engine.suggest_logic_changes.
    run_id = run_id or str(uuid.uuid4())
    provenance: dict = {
        "generation_model": model_config.get_advisor_suggestion_model(),
        "mode": "asset-swap",
        "evidence_injected": (
            reasoning_manifest if reasoning_manifest is not None else ai_advisor._EMPTY_MANIFEST
        ),
        "run_id": run_id,
    }

    # Detect absent API key early (AC-X4) — before the reasoned generator (and
    # therefore any billed LLM call) is ever reached.
    if not _has_composer_key():
        logger.info("suggest_swaps: no Composer API key — returning no_api_key=True")
        return SwapRunResult(
            gate_batch=_empty_gate_batch(),
            no_api_key=True,
            message="advisor unavailable: API key not configured",
            objective=objective,
            run_id=run_id,
            provenance=provenance,
        )

    # Objective-directed, LLM-reasoned candidate generation (R2-3).
    candidates = generate_reasoned_swap_candidates(
        symphony_id,
        score_tree,
        objective,
        reasoning_context=reasoning_context,
        correlation_data=correlation_data,
        available_assets=available_assets,
        max_candidates=MAX_SUGGESTED_CANDIDATES,
    )

    if not candidates:
        return SwapRunResult(
            gate_batch=_empty_gate_batch(),
            message=NO_SURVIVORS_MESSAGE,
            objective=objective,
            run_id=run_id,
            provenance=provenance,
        )

    # Backtest each candidate variant independently (AC-X5: isolate failures).
    # R2-3: each SwapCandidate already carries its own incumbent_asset — loop
    # over the returned pairs directly (no more single-incumbent-then-iterate
    # shape).
    bt_candidates = []
    proposal_shells = []

    for cand in candidates:
        bt_cand, proposal_shell, _, _ = _evaluate_single_variant(
            raw_value=score_tree,
            symphony_id=symphony_id,
            incumbent_asset=cand.incumbent_asset,
            candidate_asset=cand.candidate_asset,
            objective=objective,
            symphony_name=symphony_name,
            lens_scores=lens_scores,
        )
        proposal_shells.append(proposal_shell)
        if bt_cand is not None:
            bt_candidates.append(bt_cand)

    # Gate all successfully-backtested candidates together (honest n_effective = N).
    # H6/RC-1: fold-matched baseline (see _evaluate_explicit_pair). H5: explicit-0.0 safe.
    if bt_candidates:
        baseline_returns = _backtest_returns_from_tree(score_tree, symphony_id)
        baseline_returns_pct = [r * 100.0 for r in baseline_returns]
        fold_baseline_oos_alpha = _fold_transform_single(baseline_returns_pct).oos_alpha
        effective_incumbent_oos_alpha = (
            incumbent_oos_alpha if incumbent_oos_alpha is not None else fold_baseline_oos_alpha
        )

        # AC-5: real SPY-OOS baseline sourced once for the whole batch.
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
                    run_id=run_id,
                    evidence_injected=provenance["evidence_injected"],
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
        run_id=run_id,
        provenance=provenance,
    )
