"""M4 Logic-Change engine — objective-directed logic-tweak proposals for the AI Advisor.

This module is OFFLINE only (not on the 1-minute live execution path).

Implements the two Logic-Change modes described in feature-plans/ai-advisor.md §M4
(AC-3.*):

  1. **Operator-initiated** (AC-3.1): ``propose_operator_logic_change()``.
     The operator supplies a ``LogicTweak`` describing the exact parameter change
     to try (node_path + param_key + old_value + new_value).  The engine applies
     the tweak, backtests the variant via M2 ``advisors.composer_backtest_client``,
     gates via M2 ``advisors.backtest_gate_engine``, and returns a
     ``LogicChangeRunResult``.

  2. **Advisor-suggested** (AC-3.1 + AC-3.2): ``suggest_logic_changes()``.
     Given an objective, the engine calls ``generate_reasoned_logic_candidates()``
     (R2-2 — an LLM-backed, objective-directed generator) to produce a bounded
     set of OBJECTIVE-DIRECTED parameter tweaks, then backtests all of them and
     feeds the FULL batch as ONE call to
     ``backtest_gate_engine.evaluate_candidate_batch`` so the BHY/FDR correction is
     applied across ALL N candidates jointly (AC-3.2).
     Never gates candidates individually — that defeats the multiple-testing correction.

Architecture constraints
------------------------
* This module MUST NOT be imported from ``alpha_bot_execution.py`` (AC-X2).
  It is an advise-only offline post-backtest decision layer.
* Only read + inline-backtest Composer endpoints are called (AC-X1):
  GET /score and stateless POST /api/v0.1/backtest.
  No write, mutate, or trade-placement calls of any kind.
* Every ADOPT_CANDIDATE survivor is persisted as an ``advisor_observation`` with
  ``is_advisory_only=1`` + ``observation_type="logic_change_proposal"`` (AC-X3).
* No Composer API key → engine returns a clear error, writes nothing (AC-X4).
* One candidate's backtest failure never aborts the batch (AC-X5).
* Zero survivors is a valid non-error outcome (AC-3.1).

Multiple-testing guardrail (AC-3.2 — MANDATORY)
-------------------------------------------------
N backtested logic candidates → acceptance applies a multiple-testing/FDR correction
across the FULL set (not per-candidate thresholds).  Explicit and tested: raising N
raises the bar each must clear.  This is wired by passing the complete bt_candidates
list as ONE batch to ``evaluate_candidate_batch``.  Under no circumstances are
candidates gated individually — that would silently disable the FDR denominator.

Reference: feature-plans/ai-advisor.md §Capability 3 (Logic-Change Proposals) +
           §AC-3.2 + §Gate-1 Resolutions.
López de Prado 2018 AFML Ch. 7 (walk-forward fold structure).
Harvey & Liu 2015 (BHY/Yekutieli FDR, DOI 10.3905/jpm.2015.42.1.013).
"""

from __future__ import annotations

import copy
import logging
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Honest caveat carried by every surfaced logic-change survivor (AC-3.3).
# Logic-change has the HIGHEST overfitting risk tier — selecting the best
# parameter tweak from N backtests is the canonical selection-bias trap.
# Re-exported from backtest_gate_engine so callers can reference it from here.
LOGIC_CHANGE_SURVIVOR_CAVEAT = SURVIVOR_OVERFITTING_CAVEAT

# Observation type tag written to advisor_observations (AC-X3 / fixture contract).
_OBSERVATION_TYPE = "logic_change_proposal"

# No-survivors message (AC-3.1 / AC-2.5 pattern — empty is a valid outcome).
NO_SURVIVORS_MESSAGE = "no logic change cleared the gate this run"

# Maximum number of advisor-suggested candidates per run.
# Bounding N is the primary overfitting-risk control for logic-change proposals:
# an unbounded search would make the FDR correction ineffective in practice
# (the pool of backtested candidates must be manageable).
# Source: feature-plans/ai-advisor.md §Gate-1 Resolutions #4 ("top ~3 survivors
# per capability per run"), extended to input bound of ~10x survivors.
MAX_SUGGESTED_CANDIDATES: int = 30

# Plain-text template for advise-only operator guidance (AC-X1 / AC-3.4).
ADVISE_ONLY_APPLY_TEMPLATE = (
    "To apply: open {symphony_name} in Composer and manually adjust "
    "{node_description} from {old_value} to {new_value}."
)

# ---------------------------------------------------------------------------
# LogicTweak — typed representation of one parameter change
# ---------------------------------------------------------------------------


@dataclass
class LogicTweak:
    """One concrete numeric parameter change to apply to a symphony logic tree.

    A logic change is a tweak to a single numeric node in the symphony's decision
    tree — e.g., a moving-average window, a RSI threshold, or a momentum lookback.
    The tweak is identified by ``node_path`` (a list of dict/list indices navigating
    the raw_value tree from root to the target node) and a ``param_key`` (the key
    within that node whose value is being changed).

    Fields
    ------
    node_path:
        Ordered list of navigation keys (string field names or integer list indices)
        that lead from the root of the raw_value tree to the node containing
        ``param_key``.  An empty list means the root node itself.
        Example: ``["children", 0, "children", 2]`` → root["children"][0]["children"][2].
    param_key:
        The key within the target node to change (e.g., ``"window"``, ``"threshold"``).
    old_value:
        The current value of ``param_key`` at the target node.
        Used for validation (the engine verifies the tree has this value before
        applying the tweak) and for the operator-facing apply guidance.
    new_value:
        The proposed replacement value.  Must be a numeric type (int or float).
    node_description:
        Human-readable description of the node and parameter being changed
        (for operator-facing apply guidance).
        E.g., ``"the 20-day SMA window in the momentum filter"``.
    """

    node_path: list  # list[str | int]
    param_key: str
    old_value: Any
    new_value: Any
    node_description: str = ""


# ---------------------------------------------------------------------------
# LogicChangeObjective — typed representation of the objective driving changes
# ---------------------------------------------------------------------------


@dataclass
class LogicChangeObjective:
    """Typed objective that drives a logic-change search (Gate-1 Resolution #2).

    Every logic change must be OBJECTIVE-DIRECTED — the advisor must be solving
    for a stated, measurable objective, not tweaking for vibes.

    Fields
    ------
    objective_type:
        One of ``"reduce_drawdown"``, ``"lift_risk_adjusted"``,
        ``"reduce_turnover"``, ``"improve_momentum_timing"``,
        ``"reduce_whipsaw"``, or any other named objective.
    measured_value:
        Display-only context value shown in the human-readable rationale
        surfaced alongside every survivor (AC-3.3).  Does not drive tweak
        generation, ranking, or gate decisions.  Current production callers
        (app.py's operator-evaluate route) pass 0.0 — no live statistic is
        wired in yet; the weekly_suggestions_scheduler.py call sites remain
        a known follow-up (AC-10, not yet covered).
    rationale:
        Human-readable explanation of why this objective was chosen and what
        measurement drove it.  Surfaced alongside every survivor (AC-3.3).
    """

    objective_type: str
    measured_value: float
    rationale: str = ""


# ---------------------------------------------------------------------------
# LogicChangeProposalResult — per-candidate result
# ---------------------------------------------------------------------------


@dataclass
class LogicChangeProposalResult:
    """Result for one evaluated logic-change candidate.

    Attributes
    ----------
    candidate_id:
        Opaque traceability ID: ``"{symphony_id}:{param_key}@{path}:{old}->{new}"``.
    symphony_id:
        The Composer symphony UUID.
    tweak:
        The ``LogicTweak`` that was applied (or attempted) to produce this variant.
        ``None`` when the tweak is structurally invalid.
    objective:
        The ``LogicChangeObjective`` driving this proposal (AC-3.3).
    objective_rationale:
        Human-readable explanation of how this change addresses the objective.
    gate_result:
        The ``CandidateGateResult`` from ``backtest_gate_engine.evaluate_candidate_batch``.
        ``None`` when the backtest failed before gating.
    baseline_stats:
        Stats dict from the backtest of the UNCHANGED tree.
        ``None`` when the baseline backtest failed.
    variant_stats:
        Stats dict from the backtest of the CHANGED tree.
        ``None`` when the variant backtest failed.
    caveats:
        Caveats surfaced to the operator.  SURVIVOR_OVERFITTING_CAVEAT is
        mandatory for every ADOPT_CANDIDATE survivor (AC-3.3).
    apply_guidance:
        Plain-text operator instruction (AC-X1 / AC-3.4): "To apply: open …
        manually."  Always present, never a button.
    backtest_error:
        ``None`` on success; descriptive string on failure (AC-X5).
    data_warnings:
        Ticker-level data-availability warnings from the Composer API.
    """

    candidate_id: str
    symphony_id: str
    tweak: LogicTweak | None
    objective: LogicChangeObjective
    objective_rationale: str

    gate_result: CandidateGateResult | None = None
    baseline_stats: dict | None = None
    variant_stats: dict | None = None
    caveats: list = field(default_factory=list)
    apply_guidance: str = ""
    backtest_error: str | None = None
    data_warnings: list = field(default_factory=list)


# Alias: LogicProposalResult is the public short name for the per-candidate result type.
# Both names are part of the public API (tests reference both).
LogicProposalResult = LogicChangeProposalResult


# ---------------------------------------------------------------------------
# LogicChangeRunResult — top-level result of a logic-change pipeline run
# ---------------------------------------------------------------------------


@dataclass
class LogicChangeRunResult:
    """Top-level result of a logic-change pipeline run (operator-initiated or advisor-suggested).

    Attributes
    ----------
    gate_batch:
        The ``GatedBatch`` from ``evaluate_candidate_batch``.  Always non-None
        even when zero candidates survive — it carries n_candidates, fdr_q, and
        the empty survivors list for operator audit trail.
    proposals:
        All evaluated ``LogicChangeProposalResult`` objects (survivors + rejected + failed).
    survivors:
        Subset of proposals where ``gate_result.verdict.decision == "ADOPT_CANDIDATE"``.
    rejected_candidates:
        Subset of proposals that were gated-out or backtest-failed.
    message:
        Human-readable run summary.  For zero survivors: ``NO_SURVIVORS_MESSAGE``.
    objective:
        The ``LogicChangeObjective`` that drove this run.
    no_api_key:
        ``True`` when the Composer API key is absent; proposals are empty (AC-X4).
    persistence_error:
        Non-None when the advisor_observation write failed (RC-5).  The result is
        still returned, but the operator/log must see that the audit-trail row never
        landed — a persistence failure must be surfaced, never swallowed to a warning.
    run_id:
        R2-2 (AC-7) — a UUID4 minted once per call (or a caller-supplied override),
        present on EVERY return path, including every early return. A correlation
        id for the call itself, traced into every persisted advisor_observations row.
    provenance:
        R2-2 (AC-5) — {"generation_model", "mode", "evidence_injected", "run_id"},
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
    objective: LogicChangeObjective | None = None
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
    try:
        from alpha_bot_execution import COMPOSER_KEY_ID, COMPOSER_SECRET  # noqa: PLC0415

        return bool(COMPOSER_KEY_ID and COMPOSER_SECRET)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Tree-manipulation helpers
# ---------------------------------------------------------------------------


def _navigate_to_node(raw_value: dict, node_path: list) -> Any:
    """Navigate from root through ``node_path`` and return the target node.

    Returns ``None`` if any step along the path is invalid.
    Supports both string key and integer list-index navigation.
    """
    node: Any = raw_value
    for step in node_path:
        if node is None:
            return None
        try:
            node = node[step]
        except (KeyError, IndexError, TypeError):
            return None
    return node


def apply_logic_tweak(raw_value: dict, tweak: LogicTweak) -> dict | None:
    """Deep-copy ``raw_value`` and apply ``tweak``.

    Verifies that the current value at the target node matches ``tweak.old_value``
    before applying the change.  Returns ``None`` (invalid variant) when:
    - The node_path cannot be navigated to in the tree.
    - The target node does not have ``tweak.param_key``.
    - The current value differs from ``tweak.old_value``.

    The input tree is never mutated.

    Args:
        raw_value: The full symphony decision tree.
        tweak: The ``LogicTweak`` to apply.

    Returns:
        A deep-copied, tweaked tree, or ``None`` if the tweak cannot be applied.
    """
    tree = copy.deepcopy(raw_value)
    target = _navigate_to_node(tree, tweak.node_path)

    if not isinstance(target, dict):
        return None
    if tweak.param_key not in target:
        return None
    if target[tweak.param_key] != tweak.old_value:
        return None

    target[tweak.param_key] = tweak.new_value
    return tree


def extract_numeric_params(raw_value: dict) -> list:
    """Collect all numeric parameter nodes in the tree.

    Returns a list of dicts, each with:
    - ``"node_path"``: list of navigation steps from root to the node.
    - ``"param_key"``: the key within the node.
    - ``"value"``: the current numeric value.

    Traverses the entire raw_value tree recursively, collecting every key whose
    value is a finite float or int (excluding boolean True/False values; those
    are flags, not parameters).  This feeds the advisor-suggested candidate
    generator with the full list of tweakable parameters.
    """
    results = []

    def _walk(node: Any, path: list) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                child_path = path + [key]
                if isinstance(val, bool):
                    # Booleans are flags, not continuous parameters — skip.
                    # (This guard MUST precede the numeric branch: bool is an int
                    # subclass, so True/False would otherwise be caught as numerics.)
                    pass
                elif isinstance(val, (int, float)):
                    # L1: a CURRENT numeric value of 0 or 1 does NOT make a param a
                    # flag — it is still a tweakable continuous parameter. Only genuine
                    # booleans (excluded above) are flags. Surface all finite numerics.
                    results.append(
                        {
                            "node_path": path,
                            "param_key": key,
                            "value": val,
                        }
                    )
                elif isinstance(val, (dict, list)):
                    _walk(val, child_path)
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                _walk(item, path + [idx])

    _walk(raw_value, [])
    return results


# ---------------------------------------------------------------------------
# Objective rationale generator
# ---------------------------------------------------------------------------


def _build_objective_rationale(tweak: LogicTweak, objective: LogicChangeObjective) -> str:
    """Build a human-readable rationale explaining how this tweak addresses the objective."""
    obj_type = objective.objective_type
    # AC-10: measured_value is display-only and every current production caller
    # passes 0.0 (see LogicChangeObjective's docstring) — no branch below
    # references it anymore, so it is not read here.

    if obj_type == "reduce_drawdown":
        return (
            f"Adjusting {tweak.param_key} from {tweak.old_value} to {tweak.new_value} "
            f"targets drawdown reduction by increasing signal reactivity."
        )
    elif obj_type == "lift_risk_adjusted":
        return (
            f"Adjusting {tweak.param_key} from {tweak.old_value} to {tweak.new_value} "
            f"aims to lift risk-adjusted return by widening entry thresholds."
        )
    elif obj_type == "reduce_turnover":
        return (
            f"Adjusting {tweak.param_key} from {tweak.old_value} to {tweak.new_value} "
            f"targets reduction of turnover by lengthening the lookback window."
        )
    elif obj_type == "improve_momentum_timing":
        return (
            f"Adjusting {tweak.param_key} from {tweak.old_value} to {tweak.new_value} "
            f"aims to improve momentum timing by shortening the lookback window."
        )
    elif obj_type == "reduce_whipsaw":
        return (
            f"Adjusting {tweak.param_key} from {tweak.old_value} to {tweak.new_value} "
            f"reduces whipsaw by extending the lookback window."
        )
    else:
        return (
            f"Adjusting {tweak.param_key} from {tweak.old_value} to {tweak.new_value} "
            f"per the stated objective ({obj_type})."
        )


# ---------------------------------------------------------------------------
# Candidate ID builder
# ---------------------------------------------------------------------------


def _make_candidate_id(symphony_id: str, tweak: LogicTweak) -> str:
    """Build an opaque traceability ID for a logic-change candidate."""
    path_str = ">".join(str(s) for s in tweak.node_path) if tweak.node_path else "root"
    return f"{symphony_id}:{tweak.param_key}@{path_str}:{tweak.old_value}->{tweak.new_value}"


# ---------------------------------------------------------------------------
# Persistence helper (AC-X3)
# ---------------------------------------------------------------------------


def _persist_observation(
    symphony_id: str,
    proposal: LogicChangeProposalResult,
    gate_result: CandidateGateResult,
    *,
    run_id: str = "",
    evidence_injected: dict | None = None,
) -> None:
    """Persist a logic-change proposal as an advisor_observation, REGARDLESS of verdict (RC-4).

    Writes the ACTUAL gate verdict (ADOPT_CANDIDATE / KEEP_INCUMBENT /
    REJECT_VETO_FAILED) with ``is_advisory_only=1`` and
    ``observation_type="logic_change_proposal"`` (AC-X3 structural requirements).

    RC-4: persistence is verdict-agnostic so the operator sees the engine ran and
    kept the incumbent — an ADOPT-only write left advisor_observations empty on the
    common KEEP path, making the advisor look dead.

    run_id / evidence_injected: R2-2 (AC-7) — additive traceability keys, always
    present, never a schema migration (raw_response is a free-form JSON blob
    column). Mirrors strategy_builder_engine.py's identical persistence pattern.
    """
    database.insert_advisor_observation(
        advisor_role="LOGIC_CHANGE",
        symphony_id=symphony_id,
        subject_type="logic_change_proposal",
        subject_id=proposal.candidate_id,
        verdict=gate_result.verdict.decision,
        is_advisory_only=1,
        observation_type=_OBSERVATION_TYPE,
        raw_response={
            "candidate_id": proposal.candidate_id,
            "param_key": proposal.tweak.param_key if proposal.tweak else None,
            "old_value": proposal.tweak.old_value if proposal.tweak else None,
            "new_value": proposal.tweak.new_value if proposal.tweak else None,
            "node_path": proposal.tweak.node_path if proposal.tweak else None,
            "objective_type": proposal.objective.objective_type,
            "objective_rationale": proposal.objective_rationale,
            "gate_decision": gate_result.verdict.decision,
            "validation_days": gate_result.validation_days,
            "oos_alpha": gate_result.oos_alpha,
            "caveats": gate_result.caveats,
            "run_id": run_id,
            "evidence_injected": evidence_injected if evidence_injected is not None else {},
        },
    )


# ---------------------------------------------------------------------------
# Core: evaluate a single logic-change variant
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
    tweak: LogicTweak,
    objective: LogicChangeObjective,
    symphony_name: str = "",
) -> tuple:
    """Backtest a single logic-change variant.

    Returns (BacktestCandidate | None, LogicChangeProposalResult, baseline_stats | None, baseline_returns_pct).

    - candidate is None when the variant backtest failed or the tweak is structurally
      invalid (AC-X5).
    - proposal has backtest_error set on failure.
    - baseline_stats is the stats dict from the baseline (or None on failure).
    - baseline_returns_pct is the baseline's daily log-returns converted to
      percent scale (or [] when the baseline was never backtested — the
      tweak-not-found branch, before any backtest call). AC-13: callers reuse
      this instead of re-backtesting the identical baseline tree a second time.
    """
    candidate_id = _make_candidate_id(symphony_id, tweak)
    rationale = _build_objective_rationale(tweak, objective)
    display_name = symphony_name or symphony_id

    apply_guidance = ADVISE_ONLY_APPLY_TEMPLATE.format(
        symphony_name=display_name,
        node_description=tweak.node_description or tweak.param_key,
        old_value=tweak.old_value,
        new_value=tweak.new_value,
    )

    # Apply tweak to a deep copy (AC-X1: never mutate the live tree).
    variant_tree = apply_logic_tweak(raw_value, tweak)
    if variant_tree is None:
        return (
            None,
            LogicChangeProposalResult(
                candidate_id=candidate_id,
                symphony_id=symphony_id,
                tweak=tweak,
                objective=objective,
                objective_rationale=rationale,
                apply_guidance=apply_guidance,
                backtest_error=(
                    f"could not backtest this variant: tweak {tweak.param_key!r} "
                    f"old_value={tweak.old_value!r} not found at path "
                    f"{tweak.node_path!r} in the symphony tree"
                ),
            ),
            None,
            [],
        )

    # R2-2 (AC-3, net-new guard): apply_logic_tweak only checks that the target
    # node/param_key/old_value exist and match (a NAVIGATION check) — it has no
    # opinion on whether the RESULTING tree is still structurally valid per the
    # Composer grammar. A reasoned edit can navigate to a real node (so
    # apply_logic_tweak succeeds) but corrupt a structural field. Drop that
    # variant here, before any backtest — the message is deliberately distinct
    # from the "not found" wording above (this is a NEW guard, not the old one).
    tree_errors = symphony_schema.validate_tree(variant_tree)
    if tree_errors:
        return (
            None,
            LogicChangeProposalResult(
                candidate_id=candidate_id,
                symphony_id=symphony_id,
                tweak=tweak,
                objective=objective,
                objective_rationale=rationale,
                apply_guidance=apply_guidance,
                backtest_error=(
                    f"could not backtest this variant: tweak {tweak.param_key!r} "
                    f"produced a structurally invalid tree (validate_tree: "
                    f"{'; '.join(tree_errors)})"
                ),
            ),
            None,
            [],
        )

    # Backtest baseline.
    baseline_result = run_backtest(raw_value, symphony_id=symphony_id)
    baseline_stats = baseline_result.stats
    # AC-13: computed once here, returned to the caller so
    # propose_operator_logic_change can reuse it instead of a second,
    # redundant baseline backtest.
    baseline_returns_pct = [r * 100.0 for r in baseline_result.daily_returns.values()]

    # Backtest variant (AC-X5: failure here is isolated to this candidate).
    variant_result = run_backtest(variant_tree, symphony_id=symphony_id)

    if variant_result.error:
        return (
            None,
            LogicChangeProposalResult(
                candidate_id=candidate_id,
                symphony_id=symphony_id,
                tweak=tweak,
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

    # Convert log-returns → percent for the fold-transform (same contract as M3).
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

    proposal_shell = LogicChangeProposalResult(
        candidate_id=candidate_id,
        symphony_id=symphony_id,
        tweak=tweak,
        objective=objective,
        objective_rationale=rationale,
        baseline_stats=baseline_stats,
        variant_stats=variant_result.stats,
        apply_guidance=apply_guidance,
        data_warnings=variant_result.data_warnings,
    )

    return (bt_candidate, proposal_shell, baseline_stats, baseline_returns_pct)


# ---------------------------------------------------------------------------
# LLM-reasoned candidate generation (R2-2) — replaces the fixed-multiplier
# generate_objective_directed_candidates on the reasoned path.
# ---------------------------------------------------------------------------

# Maximum tweakable-parameter entries listed in the generation prompt (AC-10:
# bounded regardless of tree size — a 2000+-numeric-param tree must not blow
# the prompt's output budget; the listing is capped, never proportional).
_MAX_PARAMS_LISTED_IN_PROMPT: int = 40

# Output budget for the reasoned generator's structured tool-use response — a
# bounded list of node_path/param_key/new_value/rationale edits, small.
_MAX_OUTPUT_TOKENS: int = 2048

# Explicit client-side timeout — never rely on the SDK/urllib3 default.
_REQUEST_TIMEOUT_SECONDS: float = 30.0

_EMIT_LOGIC_EDITS_TOOL = {
    "name": "emit_logic_edits",
    "description": (
        "Emit a bounded list of objective-directed numeric parameter edits to "
        "the operator's real symphony logic tree. Each edit MUST target an "
        "EXISTING numeric parameter via its exact node_path and param_key as "
        "listed in the candidate parameters below — never invent a new path."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "description": "List of proposed parameter edits.",
                "items": {
                    "type": "object",
                    "properties": {
                        "node_path": {
                            "type": "array",
                            "items": {"type": ["string", "integer"]},
                            "description": (
                                "Navigation path from the tree root, copied EXACTLY "
                                "from a listed candidate parameter."
                            ),
                        },
                        "param_key": {"type": "string"},
                        "new_value": {"type": "number"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["node_path", "param_key", "new_value"],
                },
            }
        },
        "required": ["edits"],
    },
}


def _build_client():
    """Construct the anthropic SDK client.

    Factory seam: tests patch ``logic_change_engine._build_client``. Mirrors
    ``ai_advisor._build_client`` / ``build_plan_generator._build_client``.

    Raises:
        RuntimeError: if the SDK is unavailable or no API key is configured.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — the reasoned logic-change generator "
            "is unavailable until an API key is configured."
        )
    try:
        import anthropic  # noqa: PLC0415 - CC-2 lazy import, off-execution-path
    except ImportError as exc:  # pragma: no cover - SDK is a declared dep
        raise RuntimeError(f"the anthropic SDK is not installed: {exc}") from exc
    return anthropic.Anthropic(api_key=api_key)


def _build_reasoned_generation_prompt(
    objective: LogicChangeObjective,
    candidate_params: list,
    *,
    reasoning_context: str | None,
    change_description: str | None,
) -> str:
    """Assemble the LLM prompt for a reasoned logic-change generation call.

    Bounded (AC-10): candidate_params is truncated to
    _MAX_PARAMS_LISTED_IN_PROMPT entries regardless of the real tree's size —
    the prompt must not scale with tree size. Never a raw json.dumps() of the
    tree (AC-1) — only a bounded, human-readable parameter listing.
    """
    sections = [f"OBJECTIVE: {objective.objective_type}"]
    if objective.rationale:
        sections.append(f"OBJECTIVE RATIONALE: {objective.rationale}")
    if change_description:
        sections.append(f"OPERATOR STEERING HINT: {change_description}")
    if reasoning_context:
        sections.append(reasoning_context)

    bounded_params = candidate_params[:_MAX_PARAMS_LISTED_IN_PROMPT]
    param_lines = [
        f"- node_path={p['node_path']!r}, param_key={p['param_key']!r}, "
        f"current_value={p['value']!r}"
        for p in bounded_params
    ]
    sections.append(
        "CANDIDATE TWEAKABLE PARAMETERS (reference these EXACT node_path/"
        "param_key values, never invent a new one):\n" + "\n".join(param_lines)
    )
    sections.append(
        "Propose a bounded list of objective-directed numeric edits to the "
        "candidate parameters above via the emit_logic_edits tool. Each edit "
        "must cite an existing node_path/param_key from the list."
    )
    return "\n\n".join(sections)


def generate_reasoned_logic_candidates(
    symphony_id: str,
    raw_value: dict,
    objective: LogicChangeObjective,
    *,
    reasoning_context: str | None = None,
    change_description: str | None = None,
    max_candidates: int = MAX_SUGGESTED_CANDIDATES,
) -> list:
    """Generate a bounded set of LLM-REASONED ``LogicTweak`` candidates (R2-2).

    Replaces the fixed-multiplier ``generate_objective_directed_candidates`` on
    the reasoned path: an LLM proposes objective-directed edits addressed to
    actual parameters of the operator's real tree, never a fixed-percentage stamp.

    SECURITY-CRITICAL: each proposed edit's ``node_path``/``param_key`` is
    resolved against the REAL ``raw_value`` tree (``_navigate_to_node``) — an
    edit that does not resolve to a real dict/key is DROPPED, never fabricated
    into a ``LogicTweak``. The resulting ``LogicTweak.old_value`` is always read
    from the real tree, NEVER trusted from any ``old_value`` the LLM's edit
    dict happens to include.

    D-1: never raises. ``_build_client()`` raising, the SDK call raising, or a
    malformed tool_use payload (missing/non-list ``"edits"``) all degrade to ``[]``.

    Args:
        symphony_id: the Composer symphony UUID (traceability only).
        raw_value: the real symphony decision tree.
        objective: the ``LogicChangeObjective`` driving generation.
        reasoning_context: optional operator-context text block (see
            ``ai_advisor.build_reasoning_context``) injected verbatim into the
            prompt when truthy. Falsy (``None``/``""``) -> zero trace of it in
            the prompt.
        change_description: optional operator free-text steering hint,
            injected verbatim into the prompt when truthy.
        max_candidates: upper bound on the number of returned candidates.

    Returns:
        A bounded list of ``LogicTweak`` objects (at most ``max_candidates``).
        Empty when there are no tweakable numeric parameters, the LLM proposes
        nothing usable, or any failure occurs.
    """
    try:
        candidate_params = extract_numeric_params(raw_value)
        if not candidate_params:
            return []

        prompt = _build_reasoned_generation_prompt(
            objective,
            candidate_params,
            reasoning_context=reasoning_context,
            change_description=change_description,
        )

        client = _build_client()
        response = client.messages.create(
            model=model_config.get_advisor_suggestion_model(),
            max_tokens=_MAX_OUTPUT_TOKENS,
            tools=[_EMIT_LOGIC_EDITS_TOOL],
            tool_choice={"type": "tool", "name": "emit_logic_edits"},
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

        raw_edits = tool_block.input.get("edits") if isinstance(tool_block.input, dict) else None
        if not isinstance(raw_edits, list):
            return []

        tweaks: list[LogicTweak] = []
        for edit in raw_edits:
            if not isinstance(edit, dict):
                continue
            node_path = edit.get("node_path")
            param_key = edit.get("param_key")
            if not isinstance(node_path, list) or not isinstance(param_key, str):
                continue
            if "new_value" not in edit:
                continue
            new_value = edit["new_value"]

            # SECURITY-CRITICAL: resolve against the REAL tree — never trust
            # any old_value the LLM's edit dict happens to include (never read
            # here at all, by design).
            target = _navigate_to_node(raw_value, node_path)
            if not isinstance(target, dict) or param_key not in target:
                continue
            real_old_value = target[param_key]

            tweaks.append(
                LogicTweak(
                    node_path=node_path,
                    param_key=param_key,
                    old_value=real_old_value,
                    new_value=new_value,
                    node_description=(
                        f"{param_key}={real_old_value} at path "
                        f"[{', '.join(str(s) for s in node_path)}]"
                    ),
                )
            )
            if len(tweaks) >= max_candidates:
                break

        return tweaks

    except Exception as exc:
        # D-1: degrade cleanly — never propagate an exception from the generator path.
        logger.debug(
            "generate_reasoned_logic_candidates: error (%s)", type(exc).__name__, exc_info=True
        )
        return []


# ---------------------------------------------------------------------------
# Baseline return extractor
# ---------------------------------------------------------------------------


def _backtest_returns_from_tree(raw_value: dict, symphony_id: str) -> list:
    """Run backtest on raw_value and return log-returns list.  Returns empty list on failure."""
    result = run_backtest(raw_value, symphony_id=symphony_id)
    if result.error:
        return []
    return list(result.daily_returns.values())


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def propose_operator_logic_change(
    symphony_id: str,
    score_tree: dict,
    tweak: LogicTweak | None = None,
    objective: LogicChangeObjective | None = None,
    *,
    change_description: str | None = None,
    incumbent_oos_alpha: float | None = None,
    default_oos_alpha: float = 0.0,
    reasoning_context: str | None = None,
    reasoning_manifest: dict | None = None,
    run_id: str | None = None,
) -> LogicChangeRunResult:
    """Evaluate one operator-specified logic change (AC-3.1 — operator-initiated mode).

    The operator supplies either a ``LogicTweak`` object (direct parameter specification)
    OR a ``change_description`` plain-text string — the latter is routed through the
    LLM-reasoned generator (``generate_reasoned_logic_candidates``, bounded to a single
    candidate) rather than a deterministic parse, so the reasoned edit is directionally
    informed by ``objective``/``reasoning_context`` (R2-2). Exactly one of ``tweak`` or
    ``change_description`` must be supplied.

    The engine applies the tweak, backtests the variant, gates via M2
    ``evaluate_candidate_batch`` (single-element batch, N=1 BHY correction), persists
    survivors as ``advisor_observation`` with ``is_advisory_only=1`` (AC-X3), and
    returns a ``LogicChangeRunResult``.

    No Composer API key → returns immediately with ``no_api_key=True``,
    writes nothing (AC-X4).

    Never raises on backtest or gate failure (AC-X5).

    The BHY/FDR correction with N=1 is still applied: a single operator-initiated
    candidate uses the same gate machinery as a batch — it is not given a free pass.
    AC-3.2 is satisfied structurally (all N candidates submitted together), even
    when N=1.

    Args:
        symphony_id:
            Composer symphony UUID.
        score_tree:
            The raw Composer score tree (``GET /api/v0.1/symphonies/{id}/score``).
        tweak:
            The ``LogicTweak`` to apply directly (node_path + param_key + old_value + new_value).
            Mutually exclusive with ``change_description``.
        objective:
            The ``LogicChangeObjective`` driving this change (surfaced alongside result
            per AC-3.3).  Required.
        change_description:
            Plain-text description of the proposed change (keyword-only), threaded into
            the reasoned generator as a steering hint. Mutually exclusive with ``tweak``.
        incumbent_oos_alpha:
            The live incumbent's OOS alpha for the gate's KEEP_INCUMBENT comparison.
        default_oos_alpha:
            The global-default params' OOS alpha.
        reasoning_context:
            R2-2 — an optional, ready-to-inject operator-context text block (see
            ``ai_advisor.build_reasoning_context``), threaded straight through to
            ``generate_reasoned_logic_candidates``. The caller (route) builds this;
            the engine never calls ``build_reasoning_context`` itself.
        reasoning_manifest:
            R2-2 — the honest per-source manifest paired with ``reasoning_context``.
            Stamped into ``LogicChangeRunResult.provenance["evidence_injected"]`` and
            persisted on the observation this run writes.
        run_id:
            R2-2 — an optional caller-supplied run id, used verbatim instead of minting
            a fresh UUID4. Omitted -> a UUID4 is minted.

    Returns:
        ``LogicChangeRunResult`` — always returned, never raises.
    """
    if objective is None:
        raise ValueError("propose_operator_logic_change: objective is required")

    # R2-2 (AC-5/AC-7): minted UNCONDITIONALLY, before any return below, so every
    # return path (early or normal) carries the SAME run_id/provenance — never
    # fabricated, never nulled. Mirrors strategy_builder_engine.propose_strategies.
    run_id = run_id or str(uuid.uuid4())
    provenance: dict = {
        "generation_model": model_config.get_advisor_suggestion_model(),
        "mode": "logic-change",
        "evidence_injected": (
            reasoning_manifest if reasoning_manifest is not None else ai_advisor._EMPTY_MANIFEST
        ),
        "run_id": run_id,
    }

    # Resolve the tweak: caller may supply a LogicTweak directly, or a
    # change_description steered through the reasoned generator (R2-2 — replaces
    # the old deterministic _parse_change_description_to_tweak).
    if tweak is None and change_description is not None:
        reasoned = generate_reasoned_logic_candidates(
            symphony_id,
            score_tree,
            objective,
            reasoning_context=reasoning_context,
            change_description=change_description,
            max_candidates=1,
        )
        tweak = reasoned[0] if reasoned else None
    elif tweak is None and change_description is None:
        # Neither supplied — produce a no-op empty run.
        return LogicChangeRunResult(
            gate_batch=_empty_gate_batch(),
            message=NO_SURVIVORS_MESSAGE,
            objective=objective,
            run_id=run_id,
            provenance=provenance,
        )

    symphony_name = (
        (score_tree.get("name") or symphony_id) if isinstance(score_tree, dict) else symphony_id
    )

    # AC-X4: check API key before any backtest call.
    if not _has_composer_key():
        logger.info(
            "propose_operator_logic_change: no Composer API key — returning no_api_key=True"
        )
        return LogicChangeRunResult(
            gate_batch=_empty_gate_batch(),
            no_api_key=True,
            message="advisor unavailable: API key not configured",
            objective=objective,
            run_id=run_id,
            provenance=provenance,
        )

    # R2-2 (AC-6): the reasoned generator proposed nothing this run (LLM outage,
    # malformed output, or no well-supported edit) — a clean empty result, never
    # fabricated. provenance/run_id stay populated (they are facts about the call
    # itself, not a claim that generation succeeded).
    if tweak is None:
        return LogicChangeRunResult(
            gate_batch=_empty_gate_batch(),
            message=NO_SURVIVORS_MESSAGE,
            objective=objective,
            run_id=run_id,
            provenance=provenance,
        )

    bt_candidate, proposal_shell, _baseline_stats, baseline_returns_pct = _evaluate_single_variant(
        raw_value=score_tree,
        symphony_id=symphony_id,
        tweak=tweak,
        objective=objective,
        symphony_name=symphony_name,
    )

    # Backtest failed or tree structurally invalid — zero candidates to gate.
    # Empty-branch site (AC-5 exempt, audit-verified at
    # backtest_gate_engine.py:627-633 — evaluate_candidate_batch returns before
    # spy_returns_fn is ever read when candidates=[]).
    if bt_candidate is None:
        gate_batch = evaluate_candidate_batch(
            [],
            incumbent_oos_alpha=incumbent_oos_alpha,
            default_oos_alpha=default_oos_alpha,
        )
        return LogicChangeRunResult(
            gate_batch=gate_batch,
            proposals=[proposal_shell],
            survivors=[],
            rejected_candidates=[proposal_shell],
            message=NO_SURVIVORS_MESSAGE,
            objective=objective,
            run_id=run_id,
            provenance=provenance,
        )

    # Derive incumbent OOS alpha from baseline if not supplied.
    # H6/RC-1: fold-matched baseline (_fold_transform_single) so the gate compares
    # the candidate's validation-fold alpha against the incumbent's SAME fold, not
    # against a full-history sum (which biases the gate to systematic KEEP_INCUMBENT).
    # H5: an explicit incumbent_oos_alpha (incl. 0.0) is honoured; only None falls back.
    # AC-13: reuses the baseline_returns_pct already computed by
    # _evaluate_single_variant instead of a second, redundant baseline backtest.
    fold_baseline_oos_alpha = _fold_transform_single(baseline_returns_pct).oos_alpha
    effective_incumbent_oos_alpha = (
        incumbent_oos_alpha if incumbent_oos_alpha is not None else fold_baseline_oos_alpha
    )

    # Gate as a single-element batch (AC-3.2: ALL N candidates in one batch call).
    # AC-5: real SPY-OOS baseline sourced once here (real gate call — not the
    # empty-branch site above).
    gate_batch = evaluate_candidate_batch(
        [bt_candidate],
        incumbent_oos_alpha=effective_incumbent_oos_alpha,
        default_oos_alpha=default_oos_alpha,
        spy_returns_fn=_spy_returns_fn_for(symphony_id),
    )
    gate_result = gate_batch.results[0]

    proposal_shell.gate_result = gate_result
    # Propagate caveats from gate_result (AC-3.3 / SURVIVOR_OVERFITTING_CAVEAT).
    proposal_shell.caveats = list(gate_result.caveats)

    proposals = [proposal_shell]
    survivors = []
    rejected = []

    if gate_result.verdict.decision == "ADOPT_CANDIDATE":
        survivors.append(proposal_shell)
    else:
        rejected.append(proposal_shell)

    # RC-4: persist regardless of verdict. RC-5: surface a persistence failure.
    persistence_error = None
    try:
        _persist_observation(
            symphony_id,
            proposal_shell,
            gate_result,
            run_id=run_id,
            evidence_injected=provenance["evidence_injected"],
        )
    except Exception as exc:
        persistence_error = f"{type(exc).__name__}: {exc}"
        logger.error(
            "propose_operator_logic_change: failed to persist observation for %s (%s)",
            proposal_shell.candidate_id,
            persistence_error,
            exc_info=True,
        )

    message = (
        f"{len(survivors)} logic change(s) survived the gate for {symphony_name}"
        if survivors
        else NO_SURVIVORS_MESSAGE
    )

    return LogicChangeRunResult(
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


def suggest_logic_changes(
    symphony_id: str,
    score_tree: dict,
    objective: LogicChangeObjective,
    *,
    incumbent_oos_alpha: float | None = None,
    default_oos_alpha: float = 0.0,
    baseline_stats: dict | None = None,
    reasoning_context: str | None = None,
    reasoning_manifest: dict | None = None,
    run_id: str | None = None,
) -> LogicChangeRunResult:
    """Evaluate advisor-suggested objective-directed logic-change candidates (AC-3.1 + AC-3.2).

    Generates candidates via ``generate_reasoned_logic_candidates()`` — an LLM-backed,
    objective-directed generator (R2-2 — replaces the old fixed-multiplier
    ``generate_objective_directed_candidates``) — then backtests ALL of them and feeds
    the FULL batch as ONE call to ``evaluate_candidate_batch`` so the BHY/FDR
    correction is applied across ALL N candidates jointly (AC-3.2).

    This is the critical difference from per-candidate gating: raising N raises
    the adjusted p-value threshold each candidate must clear.  An implementation
    that gates candidates individually silently disables the multiple-testing
    correction — that is a test FAIL (AC-3.2 is tested explicitly).

    An absent Composer API key → returns an empty ``LogicChangeRunResult`` with
    ``no_api_key=True`` and writes nothing (AC-X4).

    Args:
        symphony_id:
            Composer symphony UUID.
        score_tree:
            The raw Composer score tree.
        objective:
            The ``LogicChangeObjective`` driving candidate generation.
        incumbent_oos_alpha:
            Incumbent's OOS alpha for the gate's KEEP_INCUMBENT comparison.
        default_oos_alpha:
            Global-default params' OOS alpha.
        baseline_stats:
            Optional pre-computed baseline stats (avoids a duplicate baseline call
            when the caller already has them).
        reasoning_context:
            R2-2 — an optional, ready-to-inject operator-context text block (see
            ``ai_advisor.build_reasoning_context``), threaded straight through to
            ``generate_reasoned_logic_candidates``. Omitted (e.g. the weekly
            scheduler's call site) means ``None`` — the candidates are still
            reasoned, just without injected live operator context.
        reasoning_manifest:
            R2-2 — the honest per-source manifest paired with ``reasoning_context``.
            Stamped into ``LogicChangeRunResult.provenance["evidence_injected"]`` and
            persisted on every observation this run writes.
        run_id:
            R2-2 — an optional caller-supplied run id, used verbatim instead of minting
            a fresh UUID4. Omitted -> a UUID4 is minted.

    Returns:
        ``LogicChangeRunResult`` — always returned, never raises.
        Zero survivors is a valid outcome.
    """
    symphony_name = (
        (score_tree.get("name") or symphony_id) if isinstance(score_tree, dict) else symphony_id
    )

    # R2-2 (AC-5/AC-7): minted UNCONDITIONALLY, before any return below, so every
    # return path carries the SAME run_id/provenance — never fabricated, never
    # nulled. Mirrors strategy_builder_engine.propose_strategies.
    run_id = run_id or str(uuid.uuid4())
    provenance: dict = {
        "generation_model": model_config.get_advisor_suggestion_model(),
        "mode": "logic-change",
        "evidence_injected": (
            reasoning_manifest if reasoning_manifest is not None else ai_advisor._EMPTY_MANIFEST
        ),
        "run_id": run_id,
    }

    # Detect absent API key early (AC-X4).
    if not _has_composer_key():
        logger.info("suggest_logic_changes: no Composer API key — returning no_api_key=True")
        return LogicChangeRunResult(
            gate_batch=_empty_gate_batch(),
            no_api_key=True,
            message="advisor unavailable: API key not configured",
            objective=objective,
            run_id=run_id,
            provenance=provenance,
        )

    # Objective-directed candidate generation (bounded by MAX_SUGGESTED_CANDIDATES).
    candidate_tweaks = generate_reasoned_logic_candidates(
        symphony_id,
        score_tree,
        objective,
        reasoning_context=reasoning_context,
        max_candidates=MAX_SUGGESTED_CANDIDATES,
    )

    if not candidate_tweaks:
        return LogicChangeRunResult(
            gate_batch=_empty_gate_batch(),
            message=NO_SURVIVORS_MESSAGE,
            objective=objective,
            run_id=run_id,
            provenance=provenance,
        )

    # Backtest each candidate variant independently (AC-X5: isolate failures).
    bt_candidates = []
    proposal_shells = []

    for tweak in candidate_tweaks:
        bt_cand, proposal_shell, _, _ = _evaluate_single_variant(
            raw_value=score_tree,
            symphony_id=symphony_id,
            tweak=tweak,
            objective=objective,
            symphony_name=symphony_name,
        )
        proposal_shells.append(proposal_shell)
        if bt_cand is not None:
            bt_candidates.append(bt_cand)

    # AC-3.2 CRITICAL: gate ALL successfully-backtested candidates as ONE batch.
    # This is the multiple-testing correction.  Never gate individually.
    if bt_candidates:
        # Derive incumbent OOS alpha from baseline (once for the batch). Computed
        # HERE (not unconditionally above) — R2-2 (AC-3): when every candidate was
        # dropped before backtest (e.g. by the validate_tree guard), this value is
        # never read, so computing it would be a wasted run_backtest call.
        # H6/RC-1: fold-matched baseline (see propose_operator_logic_change). H5: explicit-0.0 safe.
        # (This per-symphony weekly baseline call is separate from the per-candidate
        # baseline calls inside _evaluate_single_variant above — AC-13 is scoped to
        # the operator single-eval route only, per audit D-7; not touched here.)
        baseline_returns = _backtest_returns_from_tree(score_tree, symphony_id)
        baseline_returns_pct = [r * 100.0 for r in baseline_returns]
        fold_baseline_oos_alpha = _fold_transform_single(baseline_returns_pct).oos_alpha
        effective_incumbent_oos_alpha = (
            incumbent_oos_alpha if incumbent_oos_alpha is not None else fold_baseline_oos_alpha
        )

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
            # Propagate gate caveats (AC-3.3 / SURVIVOR_OVERFITTING_CAVEAT).
            shell.caveats = list(gate_result.caveats)
            if gate_result.verdict.decision == "ADOPT_CANDIDATE":
                survivors.append(shell)
            else:
                rejected.append(shell)
            try:
                _persist_observation(
                    symphony_id,
                    shell,
                    gate_result,
                    run_id=run_id,
                    evidence_injected=provenance["evidence_injected"],
                )
            except Exception as exc:
                if persistence_error is None:
                    persistence_error = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "suggest_logic_changes: failed to persist observation for %s (%s: %s)",
                    shell.candidate_id,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
        else:
            # Backtest failed for this candidate — goes into rejected.
            rejected.append(shell)

    message = (
        f"{len(survivors)} logic change(s) survived the gate for {symphony_name}"
        if survivors
        else NO_SURVIVORS_MESSAGE
    )

    return LogicChangeRunResult(
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
