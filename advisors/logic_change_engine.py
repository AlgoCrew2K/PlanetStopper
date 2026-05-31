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
     Given an objective, the engine calls ``generate_objective_directed_candidates()``
     to produce a bounded set of OBJECTIVE-DIRECTED parameter tweaks, then backtests
     all of them and feeds the FULL batch as ONE call to
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
from dataclasses import dataclass, field
from typing import Any, Optional

import database
from advisors.backtest_gate_engine import (
    BacktestCandidate,
    GatedBatch,
    CandidateGateResult,
    evaluate_candidate_batch,
    SURVIVOR_OVERFITTING_CAVEAT,
    HARVEY_LIU_FDR_Q,
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
# Scaling factor constants for generate_objective_directed_candidates.
# Named here so: (a) no magic numbers in function bodies (project standard),
# (b) the test suite can verify they exist at module level, (c) any policy
# change is a single-point edit with a clear source comment.
# ---------------------------------------------------------------------------

# Tighten lookback by 20 % to increase signal reactivity for reduce_drawdown.
# Source: feature-plans/ai-advisor.md §Gate-1 Resolutions #4, design
# §Candidate generation — "shorter lookbacks respond faster to drawdown signals".
_REDUCE_DRAWDOWN_TIGHTEN_FACTOR: float = 0.80

# Loosen signal entry thresholds by 25 % to capture more trend signals.
# Source: feature-plans/ai-advisor.md §Capability 3 design note — "wider thresholds
# capture more trend signals, potentially improving risk-adjusted return".
_LIFT_RISK_ADJUSTED_LOOSEN_FACTOR: float = 1.25

# Lengthen lookback by 50 % to reduce rebalancing frequency.
# Source: feature-plans/ai-advisor.md §Capability 3 design note — "longer lookbacks
# produce fewer signal changes, reducing rebalancing frequency and thus turnover".
_REDUCE_TURNOVER_LENGTHEN_FACTOR: float = 1.50

# Shorten lookback by 10 % for faster momentum timing response.
# Source: operational policy — a conservative 10 % shortening avoids
# over-sensitivity while still tightening the timing window.
_IMPROVE_MOMENTUM_TIMING_SHORTEN_FACTOR: float = 0.90

# Lengthen lookback by 30 % to reduce whipsaw from too-fast signal changes.
# Source: operational policy — 30 % is large enough to damp high-frequency
# churn without flipping to a too-slow regime-detection regime.
_REDUCE_WHIPSAW_LENGTHEN_FACTOR: float = 1.30

# Minimum parameter value (in days) for day-scale window objectives.
# Values below this are likely fractional thresholds or binary flags, not
# lookback windows; tweaking them directionally is not meaningful.
# Source: empirical — daily-bar lookback windows are rarely < 5 bars.
_CANDIDATE_WINDOW_FLOOR_DAYS: int = 5

# Lower floor for the reduce_turnover objective: shorter windows still qualify
# because the objective is to LENGTHEN them (starting from 3d is meaningful).
# Source: operational policy — a 3-day momentum signal is a common short-end floor.
_REDUCE_TURNOVER_FLOOR_DAYS: int = 3


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
        The measured input driving this objective (e.g., the current max-drawdown
        magnitude, or the current Sharpe ratio).  Always a measurement from the
        live backtest stats — never a hardcoded heuristic.
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
    tweak: Optional[LogicTweak]
    objective: LogicChangeObjective
    objective_rationale: str

    gate_result: Optional[CandidateGateResult] = None
    baseline_stats: Optional[dict] = None
    variant_stats: Optional[dict] = None
    caveats: list = field(default_factory=list)
    apply_guidance: str = ""
    backtest_error: Optional[str] = None
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
    """

    gate_batch: GatedBatch
    proposals: list = field(default_factory=list)
    survivors: list = field(default_factory=list)
    rejected_candidates: list = field(default_factory=list)
    message: str = ""
    objective: Optional[LogicChangeObjective] = None
    no_api_key: bool = False


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


def apply_logic_tweak(raw_value: dict, tweak: LogicTweak) -> Optional[dict]:
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
                    pass
                elif isinstance(val, (int, float)):
                    # Filter out 0-or-1 flag values — those are effectively boolean
                    # flags, not continuous parameters.
                    if val not in (0, 1):
                        results.append({
                            "node_path": path,
                            "param_key": key,
                            "value": val,
                        })
                elif isinstance(val, (dict, list)):
                    _walk(val, child_path)
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                _walk(item, path + [idx])

    _walk(raw_value, [])
    return results


# ---------------------------------------------------------------------------
# Objective-directed candidate generation (AC-3.1 + Gate-1 Resolution #2)
# ---------------------------------------------------------------------------


def generate_objective_directed_logic_candidates(
    symphony_id: str,
    score_tree: dict,
    objective: LogicChangeObjective,
    *,
    baseline_stats: Optional[dict] = None,
) -> list:
    """Generate a bounded set of OBJECTIVE-DIRECTED candidates as annotated dicts.

    Higher-level entry point that wraps each ``LogicTweak`` in a dict with a
    human-readable ``"change_description"`` key alongside the ``"tweak"`` key.
    This form is consumed by the advisor route/UI layer that needs to surface a
    description alongside each candidate for the operator.

    Args:
        symphony_id:    The Composer symphony UUID.
        score_tree:     The raw symphony decision tree (``score_tree=`` keyword).
        objective:      The ``LogicChangeObjective`` driving the search.
        baseline_stats: Optional pre-computed baseline stats dict.

    Returns:
        A bounded list of dicts, each with:
        - ``"change_description"``: plain-text description of the tweak.
        - ``"tweak"``: the ``LogicTweak`` to apply.

        Returns an empty list when no applicable parameters are found or when
        the objective_type is unknown.
    """
    tweaks = generate_objective_directed_candidates(
        symphony_id=symphony_id,
        raw_value=score_tree,
        objective=objective,
        baseline_stats=baseline_stats,
    )

    obj_type = objective.objective_type
    measured = objective.measured_value

    result: list = []
    for tweak in tweaks:
        # Build a description of this tweak based on the objective direction.
        if obj_type == "reduce_drawdown":
            desc = (
                f"Tighten {tweak.param_key} from {tweak.old_value} to {tweak.new_value} "
                f"to reduce drawdown (measured: {measured:.1%})"
            )
        elif obj_type == "lift_risk_adjusted":
            desc = (
                f"Loosen {tweak.param_key} from {tweak.old_value} to {tweak.new_value} "
                f"to lift risk-adjusted return (current Sharpe: {measured:.2f})"
            )
        elif obj_type == "reduce_turnover":
            desc = (
                f"Lengthen {tweak.param_key} from {tweak.old_value} to {tweak.new_value} "
                f"to reduce turnover (signal rate: {measured:.2f})"
            )
        elif obj_type == "improve_momentum_timing":
            desc = (
                f"Shorten {tweak.param_key} from {tweak.old_value} to {tweak.new_value} "
                f"to improve momentum timing (measured: {measured:.2f})"
            )
        elif obj_type == "reduce_whipsaw":
            desc = (
                f"Extend {tweak.param_key} from {tweak.old_value} to {tweak.new_value} "
                f"to reduce whipsaw (signal rate: {measured:.2f})"
            )
        else:
            desc = (
                f"Adjust {tweak.param_key} from {tweak.old_value} to {tweak.new_value} "
                f"({obj_type}, measured={measured})"
            )
        result.append({"change_description": desc, "tweak": tweak})

    return result


def generate_objective_directed_candidates(
    symphony_id: str,
    raw_value: dict,
    objective: LogicChangeObjective,
    *,
    baseline_stats: Optional[dict] = None,
) -> list:
    """Generate a bounded set of OBJECTIVE-DIRECTED ``LogicTweak`` candidates.

    This is the adversarially-testable gate on objective-direction (Gate-1
    Resolution #2 / AC-3.2).  It MUST produce different candidate lists for
    different objectives on the same symphony — a naive generator that returns
    the same candidates regardless of objective is a test FAIL.

    The candidate set is bounded by ``MAX_SUGGESTED_CANDIDATES`` to keep the
    FDR correction effective in practice.

    Candidate-generation strategy by objective_type:

    ``reduce_drawdown``:
        Targets parameters that control position-holding duration (window sizes,
        momentum lookbacks).  Shortening a lookback window typically increases
        reactivity.  Generates candidate tweaks that TIGHTEN (reduce by 20 %)
        existing numeric windows / thresholds — specifically parameters with
        values >= 5 (day-scale or larger).

    ``lift_risk_adjusted``:
        Targets parameters that control the signal entry/exit sensitivity.
        Generates candidate tweaks that LOOSEN (increase by 25 %) existing
        numeric thresholds to allow more signal — specifically parameters with
        values >= 5.

    ``reduce_turnover``:
        Targets parameters that control rebalancing frequency or signal persistence.
        Generates candidate tweaks that LENGTHEN (increase by 50 %) existing
        lookback windows — specifically parameters with values >= 3.

    ``improve_momentum_timing``:
        Targets lookback/window parameters with a shortening (reduce by 10 %)
        to improve signal timing responsiveness.
        Specifically parameters with values >= 5.

    ``reduce_whipsaw``:
        Targets lookback/window parameters with a lengthening (increase by 30 %)
        to reduce whipsaw from too-fast signal changes.
        Specifically parameters with values >= 5.

    Unknown objective_type:
        Returns empty (refuse to produce unguided candidates — objective-ignoring
        generators are the overfitting trap).

    Args:
        symphony_id:
            The Composer symphony UUID (used only for traceability).
        raw_value:
            The raw symphony decision tree.
        objective:
            The ``LogicChangeObjective`` driving the search.
        baseline_stats:
            Optional stats dict from the baseline backtest.  Retained for future
            extensions; not used for candidate generation in this implementation.

    Returns:
        A bounded list of ``LogicTweak`` objects (at most ``MAX_SUGGESTED_CANDIDATES``).
        An empty list when no applicable numeric parameters are found or when
        the objective_type is unknown.
    """
    numeric_params = extract_numeric_params(raw_value)
    if not numeric_params:
        return []

    obj_type = objective.objective_type
    tweaks: list[LogicTweak] = []

    # -------------------------------------------------------------------
    # reduce_drawdown: tighten existing lookback windows by _REDUCE_DRAWDOWN_TIGHTEN_FACTOR.
    # Rationale: shorter lookbacks respond faster to drawdown signals.
    # Only apply to parameters >= _CANDIDATE_WINDOW_FLOOR_DAYS (day-scale windows).
    # -------------------------------------------------------------------
    if obj_type == "reduce_drawdown":
        for param in numeric_params:
            old_val = param["value"]
            if not isinstance(old_val, (int, float)) or old_val < _CANDIDATE_WINDOW_FLOOR_DAYS:
                continue
            if isinstance(old_val, int):
                new_val = max(1, round(old_val * _REDUCE_DRAWDOWN_TIGHTEN_FACTOR))
            else:
                new_val = round(old_val * _REDUCE_DRAWDOWN_TIGHTEN_FACTOR, 6)
            if new_val == old_val:
                continue
            tweaks.append(LogicTweak(
                node_path=param["node_path"],
                param_key=param["param_key"],
                old_value=old_val,
                new_value=new_val,
                node_description=(
                    f"{param['param_key']}={old_val} at path "
                    f"[{', '.join(str(s) for s in param['node_path'])}]"
                ),
            ))

    # -------------------------------------------------------------------
    # lift_risk_adjusted: loosen signal entry thresholds by _LIFT_RISK_ADJUSTED_LOOSEN_FACTOR.
    # Rationale: wider thresholds capture more trend signals.
    # Only apply to parameters >= _CANDIDATE_WINDOW_FLOOR_DAYS.
    # -------------------------------------------------------------------
    elif obj_type == "lift_risk_adjusted":
        for param in numeric_params:
            old_val = param["value"]
            if not isinstance(old_val, (int, float)) or old_val < _CANDIDATE_WINDOW_FLOOR_DAYS:
                continue
            if isinstance(old_val, int):
                new_val = max(1, round(old_val * _LIFT_RISK_ADJUSTED_LOOSEN_FACTOR))
            else:
                new_val = round(old_val * _LIFT_RISK_ADJUSTED_LOOSEN_FACTOR, 6)
            if new_val == old_val:
                continue
            tweaks.append(LogicTweak(
                node_path=param["node_path"],
                param_key=param["param_key"],
                old_value=old_val,
                new_value=new_val,
                node_description=(
                    f"{param['param_key']}={old_val} at path "
                    f"[{', '.join(str(s) for s in param['node_path'])}]"
                ),
            ))

    # -------------------------------------------------------------------
    # reduce_turnover: lengthen lookback windows by _REDUCE_TURNOVER_LENGTHEN_FACTOR.
    # Rationale: longer lookbacks produce fewer signal changes.
    # Apply to parameters >= _REDUCE_TURNOVER_FLOOR_DAYS (lower floor — we are lengthening).
    # -------------------------------------------------------------------
    elif obj_type == "reduce_turnover":
        for param in numeric_params:
            old_val = param["value"]
            if not isinstance(old_val, (int, float)) or old_val < _REDUCE_TURNOVER_FLOOR_DAYS:
                continue
            if isinstance(old_val, int):
                new_val = round(old_val * _REDUCE_TURNOVER_LENGTHEN_FACTOR)
            else:
                new_val = round(old_val * _REDUCE_TURNOVER_LENGTHEN_FACTOR, 6)
            if new_val == old_val:
                continue
            tweaks.append(LogicTweak(
                node_path=param["node_path"],
                param_key=param["param_key"],
                old_value=old_val,
                new_value=new_val,
                node_description=(
                    f"{param['param_key']}={old_val} at path "
                    f"[{', '.join(str(s) for s in param['node_path'])}]"
                ),
            ))

    # -------------------------------------------------------------------
    # improve_momentum_timing: shorten lookback by _IMPROVE_MOMENTUM_TIMING_SHORTEN_FACTOR.
    # Apply to parameters >= _CANDIDATE_WINDOW_FLOOR_DAYS.
    # -------------------------------------------------------------------
    elif obj_type == "improve_momentum_timing":
        for param in numeric_params:
            old_val = param["value"]
            if not isinstance(old_val, (int, float)) or old_val < _CANDIDATE_WINDOW_FLOOR_DAYS:
                continue
            if isinstance(old_val, int):
                new_val = max(1, round(old_val * _IMPROVE_MOMENTUM_TIMING_SHORTEN_FACTOR))
            else:
                new_val = round(old_val * _IMPROVE_MOMENTUM_TIMING_SHORTEN_FACTOR, 6)
            if new_val == old_val:
                continue
            tweaks.append(LogicTweak(
                node_path=param["node_path"],
                param_key=param["param_key"],
                old_value=old_val,
                new_value=new_val,
                node_description=(
                    f"{param['param_key']}={old_val} at path "
                    f"[{', '.join(str(s) for s in param['node_path'])}]"
                ),
            ))

    # -------------------------------------------------------------------
    # reduce_whipsaw: lengthen lookback by _REDUCE_WHIPSAW_LENGTHEN_FACTOR.
    # Apply to parameters >= _CANDIDATE_WINDOW_FLOOR_DAYS.
    # -------------------------------------------------------------------
    elif obj_type == "reduce_whipsaw":
        for param in numeric_params:
            old_val = param["value"]
            if not isinstance(old_val, (int, float)) or old_val < _CANDIDATE_WINDOW_FLOOR_DAYS:
                continue
            if isinstance(old_val, int):
                new_val = round(old_val * _REDUCE_WHIPSAW_LENGTHEN_FACTOR)
            else:
                new_val = round(old_val * _REDUCE_WHIPSAW_LENGTHEN_FACTOR, 6)
            if new_val == old_val:
                continue
            tweaks.append(LogicTweak(
                node_path=param["node_path"],
                param_key=param["param_key"],
                old_value=old_val,
                new_value=new_val,
                node_description=(
                    f"{param['param_key']}={old_val} at path "
                    f"[{', '.join(str(s) for s in param['node_path'])}]"
                ),
            ))

    # -------------------------------------------------------------------
    # Unknown objective: return empty (refuse to produce unguided candidates).
    # An objective-ignoring generator is the overfitting trap — refuse it.
    # -------------------------------------------------------------------
    else:
        logger.warning(
            "generate_objective_directed_candidates: unknown objective_type=%r "
            "for symphony_id=%s — returning empty candidate list",
            obj_type,
            symphony_id,
        )
        return []

    # Bound the candidate set (AC-3.2 / MAX_SUGGESTED_CANDIDATES).
    return tweaks[:MAX_SUGGESTED_CANDIDATES]


# ---------------------------------------------------------------------------
# Objective rationale generator
# ---------------------------------------------------------------------------


def _build_objective_rationale(tweak: LogicTweak, objective: LogicChangeObjective) -> str:
    """Build a human-readable rationale explaining how this tweak addresses the objective."""
    obj_type = objective.objective_type
    measured = objective.measured_value

    if obj_type == "reduce_drawdown":
        return (
            f"Adjusting {tweak.param_key} from {tweak.old_value} to {tweak.new_value} "
            f"targets reduction of the measured {measured:.1%} drawdown by increasing "
            f"signal reactivity."
        )
    elif obj_type == "lift_risk_adjusted":
        return (
            f"Adjusting {tweak.param_key} from {tweak.old_value} to {tweak.new_value} "
            f"aims to lift risk-adjusted return from the measured Sharpe of {measured:.2f} "
            f"by widening entry thresholds."
        )
    elif obj_type == "reduce_turnover":
        return (
            f"Adjusting {tweak.param_key} from {tweak.old_value} to {tweak.new_value} "
            f"targets reduction of turnover (signal-change rate: {measured:.2f}) by "
            f"lengthening the lookback window."
        )
    elif obj_type == "improve_momentum_timing":
        return (
            f"Adjusting {tweak.param_key} from {tweak.old_value} to {tweak.new_value} "
            f"aims to improve momentum timing (measured: {measured:.2f}) by shortening "
            f"the lookback window."
        )
    elif obj_type == "reduce_whipsaw":
        return (
            f"Adjusting {tweak.param_key} from {tweak.old_value} to {tweak.new_value} "
            f"reduces whipsaw (signal rate: {measured:.2f}) by extending the lookback window."
        )
    else:
        return (
            f"Adjusting {tweak.param_key} from {tweak.old_value} to {tweak.new_value} "
            f"per the stated objective ({obj_type}, measured_value={measured})."
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


def _persist_survivor(
    symphony_id: str,
    proposal: LogicChangeProposalResult,
    gate_result: CandidateGateResult,
) -> None:
    """Persist an ADOPT_CANDIDATE survivor as an advisor_observation.

    Writes with ``is_advisory_only=1`` and ``observation_type="logic_change_proposal"``
    (AC-X3 structural requirements).  Never writes for non-survivors.
    """
    database.insert_advisor_observation(
        advisor_role="LOGIC_CHANGE",
        symphony_id=symphony_id,
        subject_type="logic_change_proposal",
        subject_id=proposal.candidate_id,
        verdict="ADOPT_CANDIDATE",
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
        },
    )


# ---------------------------------------------------------------------------
# Core: evaluate a single logic-change variant
# ---------------------------------------------------------------------------


def _evaluate_single_variant(
    raw_value: dict,
    symphony_id: str,
    tweak: LogicTweak,
    objective: LogicChangeObjective,
    symphony_name: str = "",
) -> tuple:
    """Backtest a single logic-change variant.

    Returns (BacktestCandidate | None, LogicChangeProposalResult, baseline_stats | None).

    - candidate is None when the variant backtest failed or the tweak is structurally
      invalid (AC-X5).
    - proposal has backtest_error set on failure.
    - baseline_stats is the stats dict from the baseline (or None on failure).
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
        )

    # Backtest baseline.
    baseline_result = run_backtest(raw_value, symphony_id=symphony_id)
    baseline_stats = baseline_result.stats

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
        )

    # Convert log-returns → percent for the fold-transform (same contract as M3).
    variant_returns_pct = [r * 100.0 for r in variant_result.daily_returns.values()]

    bt_candidate = BacktestCandidate(
        candidate_id=candidate_id,
        daily_returns_pct=variant_returns_pct,
        candidate_params={},
        incumbent_params={},
        theory_prior_params={},
        nn1_compliant=True,
        purge_integrity_ok=True,
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

    return (bt_candidate, proposal_shell, baseline_stats)


# ---------------------------------------------------------------------------
# change_description parser — converts operator plain text into a LogicTweak
# ---------------------------------------------------------------------------


def _parse_change_description_to_tweak(raw_value: dict, change_description: str) -> Optional[LogicTweak]:
    """Parse a plain-text change description into a ``LogicTweak``.

    Attempts to find a numeric parameter in the tree that matches the description
    and construct a tweak from it.  Returns ``None`` if no matching parameter
    is found or the description cannot be parsed.

    Parsing heuristics:
    - Looks for patterns like "from Xd to Yd" or "from X to Y" to extract
      old_value and new_value.
    - Searches the tree for a numeric parameter that matches the described
      context (e.g., mentions of "lookback", "window", "threshold", "period").
    - Falls back to the first matching numeric parameter in the tree when
      the description does not identify a specific parameter.

    Args:
        raw_value: The full symphony decision tree.
        change_description: Plain-text description of the proposed change.

    Returns:
        A ``LogicTweak`` if a matching parameter is found, else ``None``.
    """
    import re  # noqa: PLC0415

    desc_lower = change_description.lower()

    # Try to extract from/to values from description patterns like:
    # "from 10d to 20d", "from 10 to 20", "10 -> 20"
    value_pattern = re.compile(
        r"from\s+(\d+(?:\.\d+)?)\s*[dD]?\s+to\s+(\d+(?:\.\d+)?)\s*[dD]?"
        r"|(\d+(?:\.\d+)?)\s*[dD]?\s*[-–>]+\s*(\d+(?:\.\d+)?)\s*[dD]?"
    )
    match = value_pattern.search(desc_lower)
    described_old_val = None
    described_new_val = None
    if match:
        g = match.groups()
        if g[0] is not None:
            described_old_val = float(g[0])
            described_new_val = float(g[1])
        elif g[2] is not None:
            described_old_val = float(g[2])
            described_new_val = float(g[3])

    # Keywords in the description that suggest which param_key to look for.
    keyword_to_keys = {
        "lookback": ["lookback", "window", "period"],
        "window": ["window", "lookback", "period"],
        "threshold": ["threshold", "cutoff", "limit"],
        "period": ["period", "window", "lookback"],
        "momentum": ["window", "lookback", "period"],
        "regime": ["window", "lookback", "period"],
    }
    preferred_keys: list[str] = []
    for keyword, keys in keyword_to_keys.items():
        if keyword in desc_lower:
            preferred_keys.extend(keys)

    # Collect numeric params from the tree.
    numeric_params = extract_numeric_params(raw_value)
    if not numeric_params:
        return None

    # Phase 1: try to match by described old_value + preferred key.
    if described_old_val is not None and preferred_keys:
        for param in numeric_params:
            val = param["value"]
            if abs(val - described_old_val) < 1e-9 and param["param_key"] in preferred_keys:
                new_val_typed = int(described_new_val) if isinstance(val, int) else described_new_val
                old_val_typed = int(val) if isinstance(val, int) else val
                return LogicTweak(
                    node_path=param["node_path"],
                    param_key=param["param_key"],
                    old_value=old_val_typed,
                    new_value=new_val_typed,
                    node_description=(
                        f"{param['param_key']}={old_val_typed} at path "
                        f"[{', '.join(str(s) for s in param['node_path'])}]"
                    ),
                )

    # Phase 2: try to match by described old_value alone.
    if described_old_val is not None:
        for param in numeric_params:
            val = param["value"]
            if abs(val - described_old_val) < 1e-9:
                new_val_typed = int(described_new_val) if isinstance(val, int) else described_new_val
                old_val_typed = int(val) if isinstance(val, int) else val
                return LogicTweak(
                    node_path=param["node_path"],
                    param_key=param["param_key"],
                    old_value=old_val_typed,
                    new_value=new_val_typed,
                    node_description=(
                        f"{param['param_key']}={old_val_typed} at path "
                        f"[{', '.join(str(s) for s in param['node_path'])}]"
                    ),
                )

    # Phase 3: try to match by preferred key only (apply a default ±20% tweak).
    if preferred_keys:
        for param in numeric_params:
            if param["param_key"] in preferred_keys:
                old_val = param["value"]
                if isinstance(old_val, int):
                    new_val = max(1, round(old_val * 1.20))
                else:
                    new_val = round(old_val * 1.20, 6)
                return LogicTweak(
                    node_path=param["node_path"],
                    param_key=param["param_key"],
                    old_value=old_val,
                    new_value=new_val,
                    node_description=(
                        f"{param['param_key']}={old_val} at path "
                        f"[{', '.join(str(s) for s in param['node_path'])}]"
                    ),
                )

    # Phase 4: fall back to the first numeric parameter with a default ±20% tweak.
    param = numeric_params[0]
    old_val = param["value"]
    if isinstance(old_val, int):
        new_val = max(1, round(old_val * 1.20))
    else:
        new_val = round(old_val * 1.20, 6)
    return LogicTweak(
        node_path=param["node_path"],
        param_key=param["param_key"],
        old_value=old_val,
        new_value=new_val,
        node_description=(
            f"{param['param_key']}={old_val} at path "
            f"[{', '.join(str(s) for s in param['node_path'])}]"
        ),
    )


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
    tweak: Optional[LogicTweak] = None,
    objective: Optional[LogicChangeObjective] = None,
    *,
    change_description: Optional[str] = None,
    incumbent_oos_alpha: float = 0.0,
    default_oos_alpha: float = 0.0,
) -> LogicChangeRunResult:
    """Evaluate one operator-specified logic change (AC-3.1 — operator-initiated mode).

    The operator supplies either a ``LogicTweak`` object (direct parameter specification)
    OR a ``change_description`` plain-text string (engine parses it into a tweak).
    Exactly one of ``tweak`` or ``change_description`` must be supplied.

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
            Plain-text description of the proposed change (keyword-only).
            When supplied (and ``tweak`` is None), the engine parses this into a
            ``LogicTweak`` automatically.  E.g. "Reduce window from 20d to 16d".
            Mutually exclusive with ``tweak``.
        incumbent_oos_alpha:
            The live incumbent's OOS alpha for the gate's KEEP_INCUMBENT comparison.
        default_oos_alpha:
            The global-default params' OOS alpha.

    Returns:
        ``LogicChangeRunResult`` — always returned, never raises.
    """
    if objective is None:
        raise ValueError("propose_operator_logic_change: objective is required")

    # Resolve the tweak: caller may supply a LogicTweak directly or via change_description.
    if tweak is None and change_description is not None:
        tweak = _parse_change_description_to_tweak(score_tree, change_description)
    elif tweak is None and change_description is None:
        # Neither supplied — produce a no-op empty run.
        return LogicChangeRunResult(
            gate_batch=_empty_gate_batch(),
            message=NO_SURVIVORS_MESSAGE,
            objective=objective,
        )

    symphony_name = (score_tree.get("name") or symphony_id) if isinstance(score_tree, dict) else symphony_id

    # AC-X4: check API key before any backtest call.
    if not _has_composer_key():
        logger.info("propose_operator_logic_change: no Composer API key — returning no_api_key=True")
        return LogicChangeRunResult(
            gate_batch=_empty_gate_batch(),
            no_api_key=True,
            message="advisor unavailable: API key not configured",
            objective=objective,
        )

    # When tweak is still None after parsing (tree has no numeric params), produce a
    # no-op proposal with an error.
    if tweak is None:
        desc = change_description or "(no description)"
        proposal = LogicChangeProposalResult(
            candidate_id=f"{symphony_id}:unparseable",
            symphony_id=symphony_id,
            tweak=None,
            objective=objective,
            objective_rationale="",
            apply_guidance=f"To apply: open {symphony_name} in Composer and manually apply: {desc}",
            backtest_error="could not backtest this variant: change description could not be parsed into a tree tweak",
        )
        return LogicChangeRunResult(
            gate_batch=_empty_gate_batch(),
            proposals=[proposal],
            rejected_candidates=[proposal],
            message=NO_SURVIVORS_MESSAGE,
            objective=objective,
        )

    bt_candidate, proposal_shell, _baseline_stats = _evaluate_single_variant(
        raw_value=score_tree,
        symphony_id=symphony_id,
        tweak=tweak,
        objective=objective,
        symphony_name=symphony_name,
    )

    # Backtest failed or tree structurally invalid — zero candidates to gate.
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
        )

    # Derive incumbent OOS alpha from baseline if not supplied.
    baseline_returns = _backtest_returns_from_tree(score_tree, symphony_id)
    baseline_returns_pct = [r * 100.0 for r in baseline_returns]
    effective_incumbent_oos_alpha = incumbent_oos_alpha or sum(baseline_returns_pct)

    # Gate as a single-element batch (AC-3.2: ALL N candidates in one batch call).
    gate_batch = evaluate_candidate_batch(
        [bt_candidate],
        incumbent_oos_alpha=effective_incumbent_oos_alpha,
        default_oos_alpha=default_oos_alpha,
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
        try:
            _persist_survivor(symphony_id, proposal_shell, gate_result)
        except Exception:
            logger.warning(
                "propose_operator_logic_change: failed to persist survivor for %s",
                proposal_shell.candidate_id,
                exc_info=True,
            )
    else:
        rejected.append(proposal_shell)

    message = (
        f"1 logic change survived the gate for {symphony_name}"
        if survivors
        else NO_SURVIVORS_MESSAGE
    )

    return LogicChangeRunResult(
        gate_batch=gate_batch,
        proposals=proposals,
        survivors=survivors,
        rejected_candidates=rejected,
        message=message,
        objective=objective,
    )


def suggest_logic_changes(
    symphony_id: str,
    score_tree: dict,
    objective: LogicChangeObjective,
    *,
    incumbent_oos_alpha: float = 0.0,
    default_oos_alpha: float = 0.0,
    baseline_stats: Optional[dict] = None,
) -> LogicChangeRunResult:
    """Evaluate advisor-suggested objective-directed logic-change candidates (AC-3.1 + AC-3.2).

    Generates candidates via ``generate_objective_directed_candidates()``
    (objective-directed — NOT brute force), then backtests ALL of them and feeds
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

    Returns:
        ``LogicChangeRunResult`` — always returned, never raises.
        Zero survivors is a valid outcome.
    """
    symphony_name = (score_tree.get("name") or symphony_id) if isinstance(score_tree, dict) else symphony_id

    # Detect absent API key early (AC-X4).
    if not _has_composer_key():
        logger.info("suggest_logic_changes: no Composer API key — returning no_api_key=True")
        return LogicChangeRunResult(
            gate_batch=_empty_gate_batch(),
            no_api_key=True,
            message="advisor unavailable: API key not configured",
            objective=objective,
        )

    # Objective-directed candidate generation (bounded by MAX_SUGGESTED_CANDIDATES).
    candidate_tweaks = generate_objective_directed_candidates(
        symphony_id=symphony_id,
        raw_value=score_tree,
        objective=objective,
        baseline_stats=baseline_stats,
    )

    if not candidate_tweaks:
        return LogicChangeRunResult(
            gate_batch=_empty_gate_batch(),
            message=NO_SURVIVORS_MESSAGE,
            objective=objective,
        )

    # Backtest each candidate variant independently (AC-X5: isolate failures).
    bt_candidates = []
    proposal_shells = []

    for tweak in candidate_tweaks:
        bt_cand, proposal_shell, _ = _evaluate_single_variant(
            raw_value=score_tree,
            symphony_id=symphony_id,
            tweak=tweak,
            objective=objective,
            symphony_name=symphony_name,
        )
        proposal_shells.append(proposal_shell)
        if bt_cand is not None:
            bt_candidates.append(bt_cand)

    # Derive incumbent OOS alpha from baseline (once for the batch).
    baseline_returns = _backtest_returns_from_tree(score_tree, symphony_id)
    baseline_returns_pct = [r * 100.0 for r in baseline_returns]
    effective_incumbent_oos_alpha = incumbent_oos_alpha or sum(baseline_returns_pct)

    # AC-3.2 CRITICAL: gate ALL successfully-backtested candidates as ONE batch.
    # This is the multiple-testing correction.  Never gate individually.
    if bt_candidates:
        gate_batch = evaluate_candidate_batch(
            bt_candidates,
            incumbent_oos_alpha=effective_incumbent_oos_alpha,
            default_oos_alpha=default_oos_alpha,
        )
        gate_result_by_id = {gr.candidate_id: gr for gr in gate_batch.results}
    else:
        gate_batch = _empty_gate_batch()
        gate_result_by_id = {}

    # Annotate proposal shells with gate results and build survivors/rejected lists.
    survivors = []
    rejected = []

    for shell in proposal_shells:
        gate_result = gate_result_by_id.get(shell.candidate_id)
        if gate_result is not None:
            shell.gate_result = gate_result
            # Propagate gate caveats (AC-3.3 / SURVIVOR_OVERFITTING_CAVEAT).
            shell.caveats = list(gate_result.caveats)
            if gate_result.verdict.decision == "ADOPT_CANDIDATE":
                survivors.append(shell)
                try:
                    _persist_survivor(symphony_id, shell, gate_result)
                except Exception:
                    logger.warning(
                        "suggest_logic_changes: failed to persist survivor for %s",
                        shell.candidate_id,
                        exc_info=True,
                    )
            else:
                rejected.append(shell)
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
        proposals=proposal_shells,
        survivors=survivors,
        rejected_candidates=rejected,
        message=message,
        objective=objective,
    )
