"""Democratized OFFLINE acceptance gate (Phase 3b).

This module decides whether a freshly walk-forward-tuned candidate parameter
set should be ADOPTED over the live incumbent.  It is an OFFLINE, post-walk-
forward decision layer invoked from ``autotuner.py`` only — it MUST NEVER be
imported or called from ``alpha_bot_execution.py``'s 1-minute live exit path
(architecture constraint #1: no blocking I/O on the execution path).

Two-stage architecture (design: 01-acceptance-gate-design.md §Sub-question 1, 3):

  STAGE 1 — HARD VETOES (sequenced first, un-outvotable):
    1. BHY/Yekutieli significance haircut (``_haircut_select`` returned a
       winner — ``winner_trial_is_none == False``).  REUSES the existing
       ``autotuner._haircut_select`` veto; this gate only consumes its result.
    2. NN1 spec-freeze compliance (``nn1_compliant``).  REUSES the existing
       ``autotuner.validate_nn1_compliance`` gate result.
    3. Look-ahead / purge integrity (``purge_integrity_ok``) — the per-fold
       performance series feeding the significance veto was built on the
       purged validation fold.
    4. PBO sample-robustness veto (``pbo > PBO_REJECT_THRESHOLD``).  Fires when
       the Probability of Backtest Overfitting exceeds 0.5, indicating the IS-best
       config performs worse-than-random on OOS across date partitions.
       Source: Bailey & López de Prado 2014, DOI 10.21314/JCF.2014.005.
       ``pbo=None`` means not computed (fewer than 2 configs) — veto does not fire.

  STAGE 2 — WEIGHTED DISCRETIONARY SURVIVOR PANEL (scores veto-survivors only):
    A fixed-weight composite over the zero-sample-cost parameter-distance
    criteria the design endorses as the honest backbone at this data scale
    (design §Sub-question 2, D2 + D4):
      - stability vs incumbent (D2, parameter move-magnitude)
      - prior-anchor / theory-consistency (D4, distance from theory prior)
    Weights are FIXED named constants (never learned/tuned) that sum to 1.0,
    so the panel introduces ZERO additional search-space degrees of freedom
    (design §Reproducibility: fixed weights = zero added DoF).

ONE-DIRECTIONAL BRAKE (the load-bearing invariant):
  ``panel_score`` is ``None`` whenever ANY veto fails — the panel is never even
  computed for a veto-failed candidate, so a discretionary score can never
  resurrect a candidate the vetoes killed (the forbidden anti-pattern).  When
  vetoes pass, the panel can only WITHHOLD adoption (bias toward incumbent
  persistence); it can never promote a candidate that did not beat both OOS
  baselines (design §Sub-question 3, lexicographic vetoes-dominant cascade).

Orthogonality of BHY and PBO (anti-double-count invariant):
  BHY/n_effective = multiplicity axis — corrects for testing many parameter
    configurations (Harvey & Liu 2015); its veto requires a winner from
    ``_haircut_select``.
  PBO = sample-robustness axis — measures whether IS-selection generalises OOS
    across date-partitions (Bailey & López de Prado 2014); its veto fires when
    ``pbo > PBO_REJECT_THRESHOLD``.
  These axes are orthogonal: PBO must NOT alter n_effective or _haircut_select.

The Overfitting Conscience and Spec Critic producers are the panel's
discretionary advisor members (design §Sub-question 1); they are imported and
reused here — never re-implemented.  The Divergence Explainer is EXCLUDED by
binding constraint (project_cvar_divergence_validation_wall; §B.9): wiring it
in would re-import the twice-rejected CVaR-divergence idea.
"""

from __future__ import annotations

from typing import NamedTuple

# Discretionary panel advisor members — REUSED, never re-implemented
# (design §Sub-question 1; "adopt existing contracts" rule).  Imported as the
# canonical verdict producers; the Divergence Explainer is intentionally NOT
# imported (binding exclusion constraint).
from advisors.overfitting_conscience import (  # noqa: F401  (reused producer)
    compute_overfitting_conscience_observation,
)
from advisors.spec_critic import (  # noqa: F401  (reused producer)
    compute_spec_critic_observation,
)

# ---------------------------------------------------------------------------
# Decision enum (string literals — the AcceptanceVerdict.decision values).
# ---------------------------------------------------------------------------
# Source: PHASE3_DECISIONS.md / design §Sub-question 3 (the three terminal
# decisions of the lexicographic cascade) and the fixture decision_enum.
DECISION_ADOPT_CANDIDATE = "ADOPT_CANDIDATE"
DECISION_KEEP_INCUMBENT = "KEEP_INCUMBENT"
DECISION_REJECT_VETO_FAILED = "REJECT_VETO_FAILED"

# ---------------------------------------------------------------------------
# Fixed panel criteria weights (NAMED constants — no magic numbers, never tuned).
# ---------------------------------------------------------------------------
# The honest backbone of the discretionary panel at this data scale is the two
# zero-sample-cost parameter-distance criteria (design §Sub-question 2):
#   D2 — parameter stability vs incumbent (move-magnitude penalty)
#   D4 — prior-anchoring / theory-consistency (distance from theory prior)
# Both are pure functions of the candidate/incumbent parameter vectors; they
# consume NO validation-sample budget, so they cannot overfit and add zero DoF.
# Equal weighting is the principled prior: absent evidence that one distance
# criterion deserves more authority than the other, the symmetric split treats
# "stay close to last-known-good" and "stay close to the theory anchor" as
# equally-weighted soft preferences (design §Interpretation point 2).  These are
# governance constants, NOT learned parameters.
PANEL_WEIGHT_STABILITY = 0.5
PANEL_WEIGHT_PRIOR_ANCHOR = 0.5

# Burden-of-proof-on-deviation hurdle: the candidate's panel score must exceed
# the incumbent's by at least this margin to justify displacing the live params
# (design §Sub-question 3: "the panel can only ever make the gate STRICTER").
# 0.0 is the minimal principled hurdle — the candidate must at least match the
# incumbent's discretionary profile (a tie does not justify a move).  Source:
# design §Open Questions Q5 (MARGIN is a principled fixed constant, analogous to
# the FDR q choice); the conservative floor is zero, never negative.
PANEL_ADOPT_MARGIN_THRESHOLD = 0.0


class AcceptanceVerdict(NamedTuple):
    """Structured result of the acceptance gate decision.

    Fields
    ------
    vetoes_passed:
        True iff ALL three hard vetoes passed.  False if ANY veto failed.
    panel_score:
        The composite fixed-weight discretionary panel score in [0.0, 1.0]
        for the candidate when all vetoes passed; ``None`` when any veto
        failed (the one-directional-brake invariant — the panel is never
        computed for a veto-failed candidate).
    panel_breakdown:
        Per-criterion sub-score and weight detail for operator transparency.
        Empty dict on veto failure.  Never contains a 'divergence_explainer'
        key (binding exclusion constraint).
    decision:
        One of DECISION_ADOPT_CANDIDATE / DECISION_KEEP_INCUMBENT /
        DECISION_REJECT_VETO_FAILED.
    """

    vetoes_passed: bool
    panel_score: "float | None"
    panel_breakdown: dict
    decision: str


def _compute_panel_score(stability_score: float, prior_anchor_score: float) -> float:
    """Fixed-weight composite of the discretionary sub-scores.

    A weighted average of sub-scores in [0, 1] with weights summing to 1.0 is
    itself in [0, 1] (the panel_score-bounded invariant).
    """
    return (
        PANEL_WEIGHT_STABILITY * stability_score
        + PANEL_WEIGHT_PRIOR_ANCHOR * prior_anchor_score
    )


def evaluate_acceptance_gate(
    *,
    winner_trial_is_none: bool,
    winner_p_adj: "float | None",
    nn1_compliant: bool,
    purge_integrity_ok: bool,
    oos_alpha: float,
    fallback_oos_alpha: float,
    default_oos_alpha: float,
    candidate_stability_score: float,
    candidate_prior_anchor_score: float,
    incumbent_stability_score: float,
    incumbent_prior_anchor_score: float,
    pbo: "float | None" = None,
) -> AcceptanceVerdict:
    """Evaluate the democratized OFFLINE acceptance gate for one candidate.

    Stage 1 (hard vetoes) is sequenced before Stage 2 (discretionary panel).
    The decision is lexicographic and vetoes-dominant:

        if not all_vetoes_passed:                         REJECT_VETO_FAILED
        elif oos_alpha <= fallback or oos_alpha <= default: KEEP_INCUMBENT
        elif panel(cand) >= panel(inc) + MARGIN:          ADOPT_CANDIDATE
        else:                                             KEEP_INCUMBENT

    Parameters
    ----------
    winner_trial_is_none:
        True when the BHY/Yekutieli haircut (``_haircut_select``) found no
        trial clearing the FDR gate — the BHY veto FAILS (statistically
        indistinguishable from noise).
    winner_p_adj:
        The BHY-winning trial's adjusted p-value (or None when the veto failed).
        Carried for operator transparency; the veto decision is driven by
        ``winner_trial_is_none``.
    nn1_compliant:
        True when the NN1 spec-freeze gate passed (no BACKTEST_SELECTION facet,
        no tuned-spec leak).  False fails the NN1 hard structural veto.
    purge_integrity_ok:
        True when the per-fold series respected PURGE_DAYS/EMBARGO_DAYS.  False
        fails the look-ahead/purge integrity veto.
    oos_alpha, fallback_oos_alpha, default_oos_alpha:
        Out-of-sample objective for the candidate and the two baselines.
    candidate_stability_score, candidate_prior_anchor_score:
        Candidate discretionary sub-scores in [0, 1].
    incumbent_stability_score, incumbent_prior_anchor_score:
        Incumbent discretionary sub-scores in [0, 1] (for the margin comparison).
    pbo:
        Probability of Backtest Overfitting from ``math_engine.compute_pbo``
        (Bailey & López de Prado 2014).  ``None`` means not computed (fewer
        than 2 configs had ``cscv_date_returns`` — veto does not fire).
        When ``pbo > PBO_REJECT_THRESHOLD`` the PBO veto fires as a STAGE-1
        hard veto (orthogonal to BHY/n_effective — sample-robustness axis).
        The AI-Advisor call site (``advisors/backtest_gate_engine.py``) passes
        ``pbo=None`` — NO behavior change on the Advisor path.
    """
    from math_engine import PBO_REJECT_THRESHOLD

    # --- STAGE 1: HARD VETOES (un-outvotable, sequenced first) ---
    # PBO veto: pbo > PBO_REJECT_THRESHOLD (strict inequality — pbo=0.5 exactly
    # equals the threshold and must NOT veto; pbo=None means not computed → pass).
    _pbo_veto_passed = (pbo is None) or (pbo <= PBO_REJECT_THRESHOLD)
    vetoes_passed = (
        (not winner_trial_is_none)  # BHY haircut produced a deployable winner
        and nn1_compliant  # NN1 spec-freeze intact
        and purge_integrity_ok  # look-ahead/purge integrity intact
        and _pbo_veto_passed  # PBO sample-robustness veto (Bailey&LdP 2014)
    )

    # ONE-DIRECTIONAL BRAKE: the panel is NEVER computed on a veto-failed
    # candidate.  panel_score stays None and the decision is REJECT_VETO_FAILED
    # regardless of how stellar the discretionary metrics or OOS alpha look —
    # this makes the forbidden anti-pattern (a panel score outvoting a failed
    # veto) structurally impossible to express.
    if not vetoes_passed:
        return AcceptanceVerdict(
            vetoes_passed=False,
            panel_score=None,
            panel_breakdown={},
            decision=DECISION_REJECT_VETO_FAILED,
        )

    # --- STAGE 2: WEIGHTED DISCRETIONARY SURVIVOR PANEL (veto-survivors only) ---
    candidate_panel_score = _compute_panel_score(
        candidate_stability_score, candidate_prior_anchor_score
    )
    incumbent_panel_score = _compute_panel_score(
        incumbent_stability_score, incumbent_prior_anchor_score
    )

    panel_breakdown = {
        "stability": {
            "candidate_sub_score": candidate_stability_score,
            "incumbent_sub_score": incumbent_stability_score,
            "weight": PANEL_WEIGHT_STABILITY,
        },
        "prior_anchor": {
            "candidate_sub_score": candidate_prior_anchor_score,
            "incumbent_sub_score": incumbent_prior_anchor_score,
            "weight": PANEL_WEIGHT_PRIOR_ANCHOR,
        },
        "candidate_panel_score": candidate_panel_score,
        "incumbent_panel_score": incumbent_panel_score,
        "winner_p_adj": winner_p_adj,
    }

    # Lexicographic decision: the panel is a ONE-DIRECTIONAL BRAKE — it can only
    # WITHHOLD adoption, never promote a candidate that lost the OOS comparison.
    # OOS-superiority (strictly beating BOTH baselines) is a precondition for
    # adoption (mirrors the existing autotuner.py:2111 strict-positive AI rule).
    if oos_alpha <= fallback_oos_alpha or oos_alpha <= default_oos_alpha:
        decision = DECISION_KEEP_INCUMBENT
    elif candidate_panel_score >= incumbent_panel_score + PANEL_ADOPT_MARGIN_THRESHOLD:
        decision = DECISION_ADOPT_CANDIDATE
    else:
        # OOS-superior but the discretionary profile does not clear the
        # burden-of-proof hurdle — the brake withholds adoption.
        decision = DECISION_KEEP_INCUMBENT

    return AcceptanceVerdict(
        vetoes_passed=True,
        panel_score=candidate_panel_score,
        panel_breakdown=panel_breakdown,
        decision=decision,
    )
