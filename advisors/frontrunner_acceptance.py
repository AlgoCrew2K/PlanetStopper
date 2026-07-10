"""Calmar acceptance gate for the Frontrunner Builder (feature-plans/
frontrunner-builder.md AC-7).

Acceptance is TWO independent paths, either of which admits a candidate:

  1. IMPROVE — candidate's Calmar ratio (CAGR / |max_drawdown|) is strictly
     higher than the incumbent's, AND the candidate's own absolute drawdown
     does not breach an absolute floor (a candidate cannot buy an improved
     ratio via extreme leverage that also posts a catastrophic drawdown).
  2. PRESERVE + SIMPLIFY — candidate's Calmar is within tolerance of the
     incumbent's (neither meaningfully better nor worse) AND the candidate
     tree is MATERIALLY smaller (a genuine any/all collapse), same drawdown
     floor guard applies.

Sharpe and volatility are always REPORTED on the result for the Advisor-tab
card, but NEVER gate acceptance either way (AC-7) — a terrible Sharpe cannot
sink an otherwise-accepted candidate, and a great Sharpe cannot rescue an
otherwise-rejected one.

This module NEVER trusts an incoming pre-computed Calmar figure — it derives
Calmar itself from the caller-supplied CAGR + max_drawdown metrics dict
fields, mirroring the project-wide "never trust incoming oos_metrics" posture
(build_plan_generator / strategy_builder_engine). Fails closed (rejects) on
any missing/None metric or malformed input — never raises, never fabricates
an accept on incomplete data.

Public surface
--------------
AcceptanceResult : dataclass
    Returned by ``evaluate_calmar_acceptance``. Fields: ``accepted`` (bool),
    ``tags`` (set[str] — subset of {"performance", "simplification"}, empty
    on reject), ``node_count_delta`` (int — candidate minus incumbent node
    count), ``candidate_sharpe`` / ``candidate_volatility`` (float | None —
    reported, never gating), ``incumbent_calmar`` / ``candidate_calmar``
    (float | None).

compute_calmar(cagr, max_drawdown) -> float | None
    Calmar = cagr / abs(max_drawdown). Returns None (never raises, never
    inf) when max_drawdown is exactly 0.

evaluate_calmar_acceptance(incumbent_metrics, candidate_metrics, *,
                            incumbent_node_count, candidate_node_count) -> AcceptanceResult
    The AC-7 acceptance gate. Never raises (D-1) — malformed/missing metrics
    degrade to a rejected result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named constants — no magic numbers.
# ---------------------------------------------------------------------------

# A candidate's Calmar is considered "preserved" (neither meaningfully better
# nor worse than the incumbent) when the two ratios differ by less than this
# relative tolerance. Chosen to be tight enough that a genuine performance
# improvement is never miscategorized as "merely preserved", while still
# tolerating floating-point noise from independent re-backtests of
# structurally-identical trees.
CALMAR_PRESERVE_TOLERANCE: float = 0.02  # 2% relative tolerance

# A candidate tree is "materially simpler" when its node count is at most
# this fraction of the incumbent's — i.e. at least a 50% reduction. This is
# deliberately a large threshold: the feature plan's own grounding note
# describes collapsing "hundreds of flat RSI-gt rungs" via any/all, which is
# an order-of-magnitude reduction, not a marginal trim.
MATERIAL_SIMPLIFICATION_MAX_RATIO: float = 0.50

# Absolute drawdown floor (as a positive fraction) — a candidate's OWN max
# drawdown must not exceed this, regardless of how much its Calmar ratio
# improved. Prevents accepting a candidate that "improves" Calmar only by
# combining an extreme CAGR with an extreme (and separately unacceptable)
# drawdown. 0.40 (40%) is a generous ceiling — well above any of the
# operator's real incumbent symphonies' historical drawdowns, so it only
# fires on a genuinely pathological candidate.
MAX_ABSOLUTE_DRAWDOWN_FLOOR: float = 0.40


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass
class AcceptanceResult:
    """Result of ``evaluate_calmar_acceptance``. Never None.

    Fields
    ------
    accepted : bool
        True if either acceptance path admitted the candidate.
    tags : set[str]
        Subset of {"performance", "simplification"} — which path(s) admitted
        the candidate. Always empty when ``accepted`` is False.
    node_count_delta : int
        candidate_node_count - incumbent_node_count (negative = simpler).
    candidate_sharpe : float | None
        Reported for the Advisor-tab card; never gates acceptance.
    candidate_volatility : float | None
        Reported for the Advisor-tab card; never gates acceptance.
    incumbent_calmar : float | None
        The incumbent's derived Calmar ratio, or None if undefined (zero
        drawdown) or unavailable (missing metric).
    candidate_calmar : float | None
        The candidate's derived Calmar ratio, or None if undefined/unavailable.
    """

    accepted: bool
    tags: set[str] = field(default_factory=set)
    node_count_delta: int = 0
    candidate_sharpe: float | None = None
    candidate_volatility: float | None = None
    incumbent_calmar: float | None = None
    candidate_calmar: float | None = None


def _rejected(
    *,
    node_count_delta: int = 0,
    candidate_sharpe: float | None = None,
    candidate_volatility: float | None = None,
    incumbent_calmar: float | None = None,
    candidate_calmar: float | None = None,
) -> AcceptanceResult:
    return AcceptanceResult(
        accepted=False,
        tags=set(),
        node_count_delta=node_count_delta,
        candidate_sharpe=candidate_sharpe,
        candidate_volatility=candidate_volatility,
        incumbent_calmar=incumbent_calmar,
        candidate_calmar=candidate_calmar,
    )


# ---------------------------------------------------------------------------
# compute_calmar
# ---------------------------------------------------------------------------


def compute_calmar(cagr: float, max_drawdown: float) -> float | None:
    """Return CAGR / |max_drawdown|, or None if max_drawdown is exactly 0.

    Never raises. ``max_drawdown`` follows the quantstats convention (<= 0;
    a negative fraction, e.g. -0.08 = 8% drawdown) — the absolute value is
    used as the denominator so a valid negative max_drawdown always yields a
    signed Calmar matching the sign of cagr.
    """
    try:
        if max_drawdown == 0:
            return None
        return cagr / abs(max_drawdown)
    except (TypeError, ZeroDivisionError):
        return None


# ---------------------------------------------------------------------------
# evaluate_calmar_acceptance — AC-7
# ---------------------------------------------------------------------------


def _safe_float(value) -> float | None:
    """Coerce value to float, returning None for None/missing/non-numeric input."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate_calmar_acceptance(
    incumbent_metrics: dict,
    candidate_metrics: dict,
    *,
    incumbent_node_count: int,
    candidate_node_count: int,
) -> AcceptanceResult:
    """Evaluate whether a candidate frontrunner overlay is accepted (AC-7).

    Parameters
    ----------
    incumbent_metrics, candidate_metrics : dict
        Metrics dicts shaped like ``analytics.compute_quantstats_metrics``'s
        output: ``annualized_return`` (CAGR), ``max_drawdown`` (<= 0),
        optionally ``sharpe`` / ``volatility``.
    incumbent_node_count, candidate_node_count : int
        Total node counts of the incumbent and candidate symphony trees —
        the AC-7 "materially simplifying" signal.

    Returns
    -------
    AcceptanceResult
        Never raises (D-1). Malformed/missing metrics fail CLOSED (rejected),
        never fabricate an accept on incomplete data.
    """
    try:
        node_count_delta = int(candidate_node_count) - int(incumbent_node_count)

        incumbent_cagr = _safe_float(incumbent_metrics.get("annualized_return"))
        incumbent_mdd = _safe_float(incumbent_metrics.get("max_drawdown"))
        candidate_cagr = _safe_float(candidate_metrics.get("annualized_return"))
        candidate_mdd = _safe_float(candidate_metrics.get("max_drawdown"))
        candidate_sharpe = _safe_float(candidate_metrics.get("sharpe"))
        candidate_volatility = _safe_float(candidate_metrics.get("volatility"))

        # Fail-closed: any missing/None metric on either side means we cannot
        # honestly evaluate acceptance — reject rather than guess.
        if None in (incumbent_cagr, incumbent_mdd, candidate_cagr, candidate_mdd):
            return _rejected(
                node_count_delta=node_count_delta,
                candidate_sharpe=candidate_sharpe,
                candidate_volatility=candidate_volatility,
            )

        incumbent_calmar = compute_calmar(incumbent_cagr, incumbent_mdd)
        candidate_calmar = compute_calmar(candidate_cagr, candidate_mdd)

        if incumbent_calmar is None or candidate_calmar is None:
            return _rejected(
                node_count_delta=node_count_delta,
                candidate_sharpe=candidate_sharpe,
                candidate_volatility=candidate_volatility,
                incumbent_calmar=incumbent_calmar,
                candidate_calmar=candidate_calmar,
            )

        # Drawdown-floor guard: applies to BOTH acceptance paths — a
        # candidate cannot buy acceptance (via either an improved ratio or a
        # "preserved + simpler" claim) while breaching the absolute floor.
        if abs(candidate_mdd) > MAX_ABSOLUTE_DRAWDOWN_FLOOR:
            return _rejected(
                node_count_delta=node_count_delta,
                candidate_sharpe=candidate_sharpe,
                candidate_volatility=candidate_volatility,
                incumbent_calmar=incumbent_calmar,
                candidate_calmar=candidate_calmar,
            )

        tags: set[str] = set()

        # Path 1 — IMPROVE: candidate's Calmar strictly better.
        if candidate_calmar > incumbent_calmar:
            tags.add("performance")

        # Path 2 — SIMPLIFY: the candidate tree is materially smaller AND its
        # Calmar is not WORSE than the incumbent's (either genuinely improved,
        # covered by "performance" above too, or preserved within tolerance —
        # both are acceptable grounds for "this simplification didn't cost
        # anything"). A candidate that is simpler but has a strictly WORSE
        # Calmar outside tolerance does not qualify — AC-7's "preserve within
        # tolerance while materially simplifying" phrasing sets a floor, not a
        # requirement that Calmar be UNCHANGED when it also happens to be
        # better.
        calmar_not_worse = (
            candidate_calmar >= incumbent_calmar
            or abs(candidate_calmar - incumbent_calmar)
            <= CALMAR_PRESERVE_TOLERANCE * abs(incumbent_calmar)
            if incumbent_calmar != 0
            else candidate_calmar >= incumbent_calmar
        )
        is_materially_simpler = (
            incumbent_node_count > 0
            and candidate_node_count <= incumbent_node_count * MATERIAL_SIMPLIFICATION_MAX_RATIO
        )
        if calmar_not_worse and is_materially_simpler:
            tags.add("simplification")

        accepted = bool(tags)

        return AcceptanceResult(
            accepted=accepted,
            tags=tags if accepted else set(),
            node_count_delta=node_count_delta,
            candidate_sharpe=candidate_sharpe,
            candidate_volatility=candidate_volatility,
            incumbent_calmar=incumbent_calmar,
            candidate_calmar=candidate_calmar,
        )

    except Exception as exc:  # pragma: no cover - defensive; never raises contract
        logger.debug("evaluate_calmar_acceptance: unexpected error", exc_info=True)
        return _rejected()
