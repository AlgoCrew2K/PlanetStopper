"""
Port-level best-fit symphony selector (AC-P2.7.*)

Pure functions — no I/O, no state mutation.

Selection metric: L1 sum-of-absolute-deviations on per-ticker dollar exposure.
Single-pick variant of multi-item knapsack family (Martello-Toth 1990), simplified
because the target is the constraint, not the optimum. Closest to the L1
nearest-neighbor formulation in transportation problems.

Amendment B1 (Critical): MC sanity gate applied to selected symphony before commit
(select_symphony_with_mc_gate). If selected symphony's MC gate would block an exit
at per-symphony altitude, the port-mode exit is SUPPRESSED — no fallback to second
candidate.
"""

from __future__ import annotations

import hashlib
import math

# Tie-detection: symphonies whose scores differ by less than this fraction of
# the smaller score are considered tied (AC-P2.7.4).
# 1% of the smaller dollar amount per spec.
_TIE_EPSILON_FRACTION = 0.01

# Default threshold: if best score exceeds this, abort selection (AC-P2.7.5).
# None means no threshold enforced (threshold must be passed explicitly to activate abort).
_DEFAULT_MIN_MATCH_QUALITY_THRESHOLD = None


def composition_hash(symphony_ids: list[str]) -> str:
    """
    Stable O(1)-detection hash for a set of symphony IDs (AC-P2.5.5 / AC-P2.7).

    Order-independent: sorted before hashing so {A, B} == {B, A}.
    Returns a hex-digest string.
    """
    canonical = ",".join(sorted(symphony_ids))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _compute_l1_score(
    candidate: dict,
    target_map: dict[str, float],
    all_tickers: set[str],
    over_shoot_penalty: float,
) -> float:
    """
    L1 score for a single candidate symphony against the target_reduction profile.

    score = sum over all tickers t of:
        max(0, target[t] - symphony.exposure[t])           # undershoot
      + max(0, symphony.exposure[t] - target[t]) * penalty  # overshoot

    all_tickers = union of target tickers and symphony exposure tickers, so that
    non-target-ticker holdings count as full overshoot.
    """
    exposure = candidate.get("exposure_usd", {})
    score = 0.0
    for ticker in all_tickers:
        target_amt = target_map.get(ticker, 0.0)
        actual_amt = exposure.get(ticker, 0.0)
        undershoot = max(0.0, target_amt - actual_amt)
        overshoot = max(0.0, actual_amt - target_amt) * over_shoot_penalty
        score += undershoot + overshoot
    return float(score)


def _tie_epsilon(score_a: float, score_b: float) -> float:
    """1% of the smaller (non-zero) L1 deviation score; falls back to absolute 0.01 if both zero.

    The inputs are L1 deviation scores from _compute_l1_score (a sum of
    per-ticker under/over-shoot deviations) — not a portfolio cash value.
    """
    smaller = min(abs(score_a), abs(score_b))
    if smaller == 0.0:
        return 0.01
    return smaller * _TIE_EPSILON_FRACTION


def _select_winner(
    scored: list[dict],
    over_shoot_penalty: float,
) -> tuple[dict | None, str | None]:
    """
    Pick the winner from a list of {symphony_id, score, candidate} dicts.

    Returns (winner_candidate, tie_breaker_used | None).
    Tie-breaker cascade (AC-P2.7.4):
      1. Largest symphony by total_value
      2. Oldest position (smallest position_open_date string — ISO 8601 sorts correctly)
      3. Lexicographic on symphony_id (deterministic last resort)
    """
    if not scored:
        return None, None

    # Sort by score ascending to find candidate group tied with the minimum
    scored_sorted = sorted(scored, key=lambda x: x["score"])
    best_score = scored_sorted[0]["score"]

    # Gather all candidates tied within epsilon of best score
    tied = []
    for s in scored_sorted:
        eps = _tie_epsilon(best_score, s["score"])
        if abs(s["score"] - best_score) <= eps:
            tied.append(s)
        else:
            break

    if len(tied) == 1:
        return tied[0]["candidate"], None

    # Tie-break 1: largest total_value
    max_val = max(t["candidate"].get("total_value", 0.0) for t in tied)
    by_value = [t for t in tied if t["candidate"].get("total_value", 0.0) == max_val]
    if len(by_value) == 1:
        return by_value[0]["candidate"], "largest_value"

    # Tie-break 2: oldest position_open_date (lexicographically smallest ISO date = earliest)
    min_date = min(t["candidate"].get("position_open_date", "") for t in by_value)
    by_date = [t for t in by_value if t["candidate"].get("position_open_date", "") == min_date]
    if len(by_date) == 1:
        return by_date[0]["candidate"], "oldest_position"

    # Tie-break 3: lexicographic symphony_id
    by_lex = sorted(by_date, key=lambda x: x["candidate"]["symphony_id"])
    return by_lex[0]["candidate"], "lexicographic"


def select_symphony(
    target_reduction: list[dict],
    candidates: list[dict],
    over_shoot_penalty: float = 1.0,
    min_match_quality_threshold: float | None = _DEFAULT_MIN_MATCH_QUALITY_THRESHOLD,
) -> dict:
    """
    Select the ONE symphony from candidates whose holdings best match target_reduction.

    Parameters
    ----------
    target_reduction:
        List of {"ticker": str, "amount_usd": float}. The port-signal target profile.
    candidates:
        List of symphony dicts, each with:
          - symphony_id: str
          - exposure_usd: {ticker: float}  # current dollar exposure per ticker
          - total_value: float             # used for tie-break 1
          - position_open_date: str        # ISO 8601; used for tie-break 2
    over_shoot_penalty:
        Multiplier on the overshoot term. 1.0 = symmetric L1.
        <1.0 biases toward over-shooting (exit more than needed).
        >1.0 biases toward under-shooting.
    min_match_quality_threshold:
        If best score > this value, abort selection (AC-P2.7.5). None = no abort check.

    Returns
    -------
    dict with keys:
        selected_symphony_id: str | None
        aborted: bool
        abort_reason: str | None
        tie_breaker_used: str | None
        all_scores: list[{symphony_id, score}]
        target_reduction: the input target (for gate_state_json telemetry, AC-P2.7.7)
    """
    # Build target map and union ticker set
    target_map: dict[str, float] = {
        item["ticker"]: float(item["amount_usd"]) for item in target_reduction
    }
    target_tickers = set(target_map.keys())

    scored = []
    for candidate in candidates:
        all_tickers = target_tickers | set(candidate.get("exposure_usd", {}).keys())
        score = _compute_l1_score(candidate, target_map, all_tickers, over_shoot_penalty)
        scored.append({"symphony_id": candidate["symphony_id"], "score": score, "candidate": candidate})

    all_scores = [{"symphony_id": s["symphony_id"], "score": s["score"]} for s in scored]

    # AC-P2.7.5: no-good-match abort
    if scored and min_match_quality_threshold is not None:
        best_score = min(s["score"] for s in scored)
        if best_score > min_match_quality_threshold:
            return {
                "selected_symphony_id": None,
                "aborted": True,
                "abort_reason": "best_score_exceeds_threshold",
                "best_score": float(best_score),
                "tie_breaker_used": None,
                "all_scores": all_scores,
                "target_reduction": target_reduction,
            }

    winner, tie_breaker = _select_winner(scored, over_shoot_penalty)

    return {
        "selected_symphony_id": winner["symphony_id"] if winner else None,
        "aborted": False,
        "abort_reason": None,
        "tie_breaker_used": tie_breaker,
        "all_scores": all_scores,
        "target_reduction": target_reduction,
    }


def select_symphony_euclidean(
    target_reduction: list[dict],
    candidates: list[dict],
    over_shoot_penalty: float = 1.0,
    min_match_quality_threshold: float | None = None,
) -> dict:
    """
    Euclidean adversarial alternative to L1 selection (AC-P2.7.3).

    Normalizes both target_reduction and candidate holdings to weight vectors
    (sum to 1); computes Euclidean distance. Lower = closer. Returns same shape
    as select_symphony so test-writer can validate selection robustness.
    """
    target_map: dict[str, float] = {
        item["ticker"]: float(item["amount_usd"]) for item in target_reduction
    }
    all_tickers = set(target_map.keys())
    for c in candidates:
        all_tickers |= set(c.get("exposure_usd", {}).keys())

    tickers_sorted = sorted(all_tickers)
    target_total = sum(target_map.values()) or 1.0
    target_vec = [target_map.get(t, 0.0) / target_total for t in tickers_sorted]

    scored = []
    for candidate in candidates:
        exposure = candidate.get("exposure_usd", {})
        exp_total = sum(exposure.values()) or 1.0
        exp_vec = [exposure.get(t, 0.0) / exp_total for t in tickers_sorted]
        dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(target_vec, exp_vec)))
        scored.append({"symphony_id": candidate["symphony_id"], "score": dist, "candidate": candidate})

    all_scores = [{"symphony_id": s["symphony_id"], "score": s["score"]} for s in scored]

    if scored and min_match_quality_threshold is not None:
        best_score = min(s["score"] for s in scored)
        if best_score > min_match_quality_threshold:
            return {
                "selected_symphony_id": None,
                "aborted": True,
                "abort_reason": "best_score_exceeds_threshold",
                "best_score": float(best_score),
                "tie_breaker_used": None,
                "all_scores": all_scores,
                "target_reduction": target_reduction,
            }

    winner, tie_breaker = _select_winner(scored, over_shoot_penalty)

    return {
        "selected_symphony_id": winner["symphony_id"] if winner else None,
        "aborted": False,
        "abort_reason": None,
        "tie_breaker_used": tie_breaker,
        "all_scores": all_scores,
        "target_reduction": target_reduction,
    }


def select_symphony_with_mc_gate(
    target_reduction: list[dict],
    candidates: list[dict],
    over_shoot_penalty: float = 1.0,
    min_match_quality_threshold: float | None = None,
) -> dict:
    """
    Amendment B1 (CRITICAL): L1 selection with per-constituent MC sanity gate.

    Runs standard L1 selection first. Then, before committing the exit, checks
    whether the SELECTED symphony's MC sanity gate would have blocked its exit
    at per-symphony altitude.

    If the selected symphony's mc_sanity_gate_would_block is True:
      - Suppress the exit entirely (do NOT fall back to second candidate)
      - Return suppressed=True with telemetry fields

    This preserves the MC safety floor for port-mode exits.
    Each candidate dict may carry mc_sanity_gate_would_block: bool (computed by
    the caller from the candidate symphony's prob_beating vs MC_SANITY_THRESHOLD).

    Parameters
    ----------
    candidates:
        Same as select_symphony, with optional extra field:
        mc_sanity_gate_would_block: bool  # True if prob_beating >= MC_SANITY_THRESHOLD
    """
    # Run standard L1 selection (without abort — we handle abort here)
    selection = select_symphony(
        target_reduction=target_reduction,
        candidates=candidates,
        over_shoot_penalty=over_shoot_penalty,
        min_match_quality_threshold=min_match_quality_threshold,
    )

    # If selection already aborted (no-good-match), propagate
    if selection["aborted"]:
        return {
            **selection,
            "suppressed": False,
            "suppression_reason": None,
            "suppressed_symphony_id": None,
        }

    selected_id = selection["selected_symphony_id"]
    if selected_id is None:
        return {
            **selection,
            "suppressed": False,
            "suppression_reason": None,
            "suppressed_symphony_id": None,
        }

    # Find the selected candidate and check MC gate
    selected_candidate = next(
        (c for c in candidates if c["symphony_id"] == selected_id), None
    )
    if selected_candidate is not None and selected_candidate.get("mc_sanity_gate_would_block", False):
        # Amendment B1: suppress the exit, do NOT fall back to next candidate
        return {
            "selected_symphony_id": None,
            "aborted": False,
            "abort_reason": None,
            "suppressed": True,
            "suppression_reason": "mc_sanity_gate_would_block",
            "suppressed_symphony_id": selected_id,
            "tie_breaker_used": selection["tie_breaker_used"],
            "all_scores": selection["all_scores"],
            "target_reduction": target_reduction,
        }

    return {
        **selection,
        "suppressed": False,
        "suppression_reason": None,
        "suppressed_symphony_id": None,
    }
