"""Calmar acceptance gate for the Frontrunner Builder (feature-plans/
frontrunner-builder.md AC-7).

Acceptance is TWO independent paths, either of which admits a candidate:

  1. IMPROVE — candidate's Calmar ratio (CAGR / |max_drawdown|) is strictly
     higher than the incumbent's, AND the candidate's own absolute drawdown
     does not breach an absolute floor (a candidate cannot buy an improved
     ratio via extreme leverage that also posts a catastrophic drawdown).
  2. PRESERVE + SIMPLIFY — candidate's Calmar is within tolerance of the
     incumbent's (neither meaningfully better nor worse) AND the generated
     overlay's SIGNAL LOGIC (condition + real fire/then branch, EXCLUDING
     the placeholder-else) is MATERIALLY smaller than the replaced cascade's
     SIGNAL LOGIC (condition + real fire branch, EXCLUDING the stub-padded
     continuation) — never the whole-symphony node counts, which stay
     ~98-100% of each other for any single-cascade splice, and never the
     whole compacted/compiled subtree including its stub/placeholder branch
     (DE-FR-SIMPLIFY-001 Revise 3 RULING 1 — counting the stub padding as
     "replaced logic" was a CRITICAL defect that inverted this path from
     unreachable to admitting oversized overlays). AND the whole-symphony
     tree did not GROW (``node_count_delta <= 0`` — RULING 2, a third,
     independent gate: the delta-scoped ratio alone cannot see a candidate
     whose overall tree grew even though its signal logic shrank). Same
     drawdown floor guard applies.

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
                            incumbent_node_count, candidate_node_count,
                            overlay_node_count=None,
                            replaced_cascade_node_count=None,
                            fire_is_else_branch=False) -> AcceptanceResult
    The AC-7 acceptance gate. Never raises (D-1) — malformed/missing metrics
    degrade to a rejected result. The SIMPLIFY path compares
    ``overlay_node_count`` (the generated overlay's SIGNAL-LOGIC-ONLY count,
    caller-derived) against ``replaced_cascade_node_count`` (the replaced
    cascade's SIGNAL-LOGIC-ONLY count, caller-derived — Revise 4: read
    verbatim off the detector's own ``cascade.signal_logic_node_count``,
    never re-derived here) — NOT the whole-symphony
    ``incumbent_node_count``/``candidate_node_count`` (stay ~98-100% of each
    other for any single-cascade splice) and NOT the whole compacted/
    compiled subtree including its stub/placeholder branch. Those two
    whole-tree params are retained for the ``node_count_delta`` display
    metric AND (RULING 2) as an independent third SIMPLIFY gate —
    ``node_count_delta <= 0`` — alongside the delta-scoped ratio and the
    Calmar-not-worse check. R4-5 (Revise 4): each whole-tree param is
    coerced via ``_safe_node_count_int`` (bool/non-numeric declines,
    genuinely numeric strings still accepted); a coercion failure on either
    side makes ``node_count_delta`` itself ``None`` rather than raising.
    Omitting the delta-scoped keyword-only params (a legacy call site) makes
    SIMPLIFY structurally unreachable rather than falling back to the old,
    broken whole-tree comparison. ``fire_is_else_branch=True`` (Revise 4's
    final pin) unconditionally declines SIMPLIFY regardless of the other
    gates, with a WARNING log — see the function's own docstring.
"""

from __future__ import annotations

import logging
import math
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

# A candidate's generated overlay (signal-logic-only — condition + real
# fire/then branch, excluding the placeholder-else) is "materially simpler"
# than the replaced cascade's own signal logic (condition + real fire
# branch, excluding the stub-padded continuation) when the overlay's count
# is at most this fraction of the cascade's — i.e. at least a 50%
# reduction. This is deliberately a large threshold: the feature plan's own
# grounding note describes collapsing "hundreds of flat RSI-gt rungs" via
# any/all, which is an order-of-magnitude reduction, not a marginal trim.
# The calibration basis was always overlay-scale (a handful to a few dozen
# nodes), never whole-symphony scale — DE-FR-SIMPLIFY-001 Revise 3's
# delta-scoping/signal-logic-only fix corrected WHICH operands this ratio is
# compared against, not the ratio's own value.
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
    node_count_delta: int | None = 0
    candidate_sharpe: float | None = None
    candidate_volatility: float | None = None
    incumbent_calmar: float | None = None
    candidate_calmar: float | None = None


def _rejected(
    *,
    node_count_delta: int | None = 0,
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


def _safe_node_count_float(value) -> float | None:
    """Stricter float coercion for the SIMPLIFY-clause node-count operands
    ONLY (never used for CAGR/MDD/sharpe/volatility parsing — see
    ``_safe_float`` for that; deliberately NOT touching that general-purpose
    helper keeps this hardening scoped to exactly the operands it targets).

    F9 (DE-FR-SIMPLIFY-001 Revise 3): a node count is never legitimately a
    bool (Python's ``bool`` is an ``int`` subtype — ``float(True) == 1.0``
    would silently coerce a caller bug into a plausible-looking tiny count),
    a numeric string (``float("20") == 20.0`` — same silent-coercion risk),
    or a non-finite value (``inf`` trivially satisfies any ratio comparison
    for a finite counterpart; ``nan`` comparisons are always False, an
    ambiguous fall-through rather than an honest decline). A huge integer
    (e.g. ``10**400``) raises ``OverflowError`` from ``float()`` — caught
    here so the caller's own reporting fields (Sharpe/volatility/both Calmar
    values/node_count_delta, all independently computable) are never lost to
    an unhandled exception escaping to ``evaluate_calmar_acceptance``'s outer
    catch-all, which nulls everything.

    Declines (returns None, never raises) on any of the above.
    """
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    if not result.is_integer():
        # B2 (DE-FR-SIMPLIFY-001 Revise 4): a node count is never
        # legitimately a non-whole float either (3.7 nodes is meaningless).
        # A genuinely whole-number float (e.g. 20.0, which a caller-side
        # arithmetic operation might plausibly produce) is still accepted
        # above this check; only a fractional value declines here.
        return None
    return result


def _safe_node_count_int(value) -> int | None:
    """Stricter int coercion for RULING 2's own operands
    (``incumbent_node_count``/``candidate_node_count``, feeding
    ``node_count_delta``) — the SAME hardening class as
    ``_safe_node_count_float`` (F9/B2), R4-5 (DE-FR-SIMPLIFY-001 Revise 4).

    ``bool`` is rejected explicitly (``int(True) == 1`` silently coerces —
    the same class of caller-bug risk every other operand in this module
    guards against). ``None``/a non-numeric string/other malformed input
    degrades via the natural ``int(value)`` ``TypeError``/``ValueError``,
    caught here rather than allowed to escape to
    ``evaluate_calmar_acceptance``'s outer catch-all (which would null every
    OTHER reporting field, not just ``node_count_delta``). A genuinely
    numeric string (e.g. ``"50"``) is still accepted -- ``int()`` already
    handles that correctly and no test requires rejecting it, unlike
    ``_safe_node_count_float``'s ratio operands, which reject ALL strings.

    Declines (returns None, never raises) on any of the above.
    """
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_delta_scoped_material_simplification(
    overlay_node_count, replaced_cascade_node_count
) -> bool:
    """Fail-closed SIMPLIFY-clause check (DE-FR-SIMPLIFY-001, AC-1/AC-5).

    Compares the candidate's OWN OVERLAY (the small generated subtree) against
    the REPLACED CASCADE (the incumbent subtree it swaps out) — never the
    whole-symphony node counts, which stay ~98-100% of each other for any
    single-cascade splice and can never signal a genuine simplification.

    Declines (returns False, never raises) when: either operand is missing/
    None, either operand is non-numeric (bool/string/other — F9, via
    ``_safe_node_count_float``), either operand is non-finite (inf/nan —
    F9), either operand is zero or negative (a real compiled overlay always
    has >=1 node and a real replaced cascade always has >=1 node, so 0 can
    only mean "count unavailable" upstream — treated identically to
    None/absent, never as a legitimately tiny value that would trivially
    satisfy the ratio), or the overlay is literally bigger than the cascade
    it replaces (F12: this last check is a "ratio >= 1" tripwire — the ratio
    comparison below would already reject this case on its own; it is kept
    as an explicit, self-documenting guard, not a co-equal condition
    alongside the `<=0` checks above it). A caller omitting both operands
    (the legacy invocation shape) always declines here — SIMPLIFY becomes
    structurally unreachable for an un-migrated call site rather than
    silently keeping the old whole-tree comparison's behavior.
    """
    overlay = _safe_node_count_float(overlay_node_count)
    cascade = _safe_node_count_float(replaced_cascade_node_count)
    if overlay is None or cascade is None:
        return False
    if overlay <= 0 or cascade <= 0:
        return False
    if overlay > cascade:
        return False
    return overlay <= cascade * MATERIAL_SIMPLIFICATION_MAX_RATIO


def evaluate_calmar_acceptance(
    incumbent_metrics: dict,
    candidate_metrics: dict,
    *,
    incumbent_node_count: int,
    candidate_node_count: int,
    overlay_node_count: int | None = None,
    replaced_cascade_node_count: int | None = None,
    fire_is_else_branch: bool = False,
) -> AcceptanceResult:
    """Evaluate whether a candidate frontrunner overlay is accepted (AC-7).

    Parameters
    ----------
    incumbent_metrics, candidate_metrics : dict
        Metrics dicts shaped like ``analytics.compute_quantstats_metrics``'s
        output: ``annualized_return`` (CAGR), ``max_drawdown`` (<= 0),
        optionally ``sharpe`` / ``volatility``.
    incumbent_node_count, candidate_node_count : int
        Total node counts of the incumbent and candidate SYMPHONY trees —
        used ONLY for the ``node_count_delta`` display metric AND (RULING 2)
        the whole-symphony-did-not-grow SIMPLIFY gate. R4-5 (Revise 4):
        coerced via ``_safe_node_count_int``, which declines (rather than
        raising) on a bool/non-numeric-string/other malformed value —
        ``node_count_delta`` itself becomes ``None`` (never a fabricated
        number) when either side fails to coerce.
    overlay_node_count, replaced_cascade_node_count : int | None
        Delta-scoped node counts (DE-FR-SIMPLIFY-001) driving the SIMPLIFY
        acceptance path — the candidate's own small generated overlay vs. the
        incumbent cascade subtree it replaces. Keyword-only and additive
        (default None); omitting them makes SIMPLIFY structurally
        unreachable (fail-closed), never a silent fallback to the whole-tree
        comparison.
    fire_is_else_branch : bool
        Revise 4's final pin: when True, the cascade's fire (signal) content
        sits on the ``is-else-condition?==True`` side (inverted polarity) —
        SIMPLIFY is declined UNCONDITIONALLY regardless of what the ratio/
        node_count_delta gates alone would say, since ``_graft_incumbent_
        core``'s core-preservation logic assumes normal polarity and would
        silently drop the real core on this cascade shape. Default False.

    Returns
    -------
    AcceptanceResult
        Never raises (D-1). Malformed/missing metrics fail CLOSED (rejected),
        never fabricate an accept on incomplete data.
    """
    try:
        incumbent_count_int = _safe_node_count_int(incumbent_node_count)
        candidate_count_int = _safe_node_count_int(candidate_node_count)
        node_count_delta = (
            candidate_count_int - incumbent_count_int
            if incumbent_count_int is not None and candidate_count_int is not None
            else None
        )

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

        # Path 2 — SIMPLIFY: the candidate's OVERLAY is materially smaller than
        # the REPLACED CASCADE it swaps out (DE-FR-SIMPLIFY-001 — delta-scoped
        # signal-logic-only counts, never the whole-symphony node counts,
        # which stay ~98-100% of each other for any single-cascade splice)
        # AND its Calmar is not WORSE than the incumbent's (either genuinely
        # improved, covered by "performance" above too, or preserved within
        # tolerance — both are acceptable grounds for "this simplification
        # didn't cost anything") AND (RULING 2, Revise 3) the WHOLE-SYMPHONY
        # tree did not GROW. A candidate that is simpler but has a strictly
        # WORSE Calmar outside tolerance does not qualify — AC-7's "preserve
        # within tolerance while materially simplifying" phrasing sets a
        # floor, not a requirement that Calmar be UNCHANGED when it also
        # happens to be better. RULING 2 restores an independent invariant
        # the delta-scoped ratio alone cannot see: _graft_incumbent_core
        # re-inserting the incumbent's full core into the candidate's else
        # branch can make the overall spliced tree BIGGER even though the
        # signal logic genuinely shrank — "simplification" tagged on a
        # bigger tree is an absurdity the ratio alone cannot catch, so this
        # is a THIRD, independent gate, never traded off against the ratio.
        calmar_not_worse = (
            candidate_calmar >= incumbent_calmar
            or abs(candidate_calmar - incumbent_calmar)
            <= CALMAR_PRESERVE_TOLERANCE * abs(incumbent_calmar)
            if incumbent_calmar != 0
            else candidate_calmar >= incumbent_calmar
        )
        is_materially_simpler = _is_delta_scoped_material_simplification(
            overlay_node_count, replaced_cascade_node_count
        )
        # R4-5 (Revise 4): an undeterminable delta (either whole-tree node
        # count failed to coerce) never satisfies the "did not grow" gate —
        # declining, not silently treating None as passing.
        whole_tree_did_not_grow = node_count_delta is not None and node_count_delta <= 0

        if fire_is_else_branch:
            # Final pin (Revise 4): inverted-polarity cascade — SIMPLIFY is
            # structurally untrustworthy here regardless of what the ratio/
            # delta gates alone would say (see the docstring above), so this
            # is evaluated FIRST and short-circuits the rest of the SIMPLIFY
            # check entirely. Logged so a genuinely-materially-simpler-
            # looking candidate withheld for this reason is never silent.
            logger.warning(
                "evaluate_calmar_acceptance: SIMPLIFY declined unconditionally "
                "-- cascade fire content sits on the is-else-condition?==True "
                "side (inverted polarity); node_count_delta/ratio gates are "
                "not evaluated for this candidate"
            )
        elif calmar_not_worse and is_materially_simpler and whole_tree_did_not_grow:
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

    except Exception:  # pragma: no cover - defensive; never raises contract
        logger.debug("evaluate_calmar_acceptance: unexpected error", exc_info=True)
        return _rejected()
