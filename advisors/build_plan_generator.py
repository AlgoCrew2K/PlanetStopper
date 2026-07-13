"""Opus Build-Plan Generator — Component 2 + 2b of the real Strategy Builder.

Calls the Anthropic SDK (structured tool-use) to generate objective-shaped
build-plan dicts in the canonical DSL, validates ticker membership, deduplicates
structurally-identical plans, and admits community (Atlas) candidates ranked by
objective-matched OOS metrics.

Public surface
--------------
Objective : enum
    Four-value objective enum (diversify / cut_drawdown / lift_risk_adjusted /
    volatility_mitigation). Extends the three-value enum in strategy_builder_engine.

N_PLANS_PER_OBJECTIVE : int = 12
    Named tunable constant for the requested plan count per SDK call.

PROVENANCE_BUILT_NEW : str = "built-new"
PROVENANCE_ATLAS_SUGGESTED : str = "atlas-suggested"
    Explicit provenance tags carried on every plan / candidate.

GeneratorResult : dataclass
    Container returned by generate_build_plans. Fields: .plans (list[dict]),
    .reason (str | None).

_build_client() -> anthropic.Anthropic
    SDK factory seam — patched by tests.

plan_tickers(plan) -> set[str]
    Deterministic walk over the DSL returning every ticker referenced in a plan.
    Excludes the '%' placeholder. Never raises.

generate_build_plans(objective, membership_set, *, n_plans) -> GeneratorResult
    AC-7/8/9/10/11. Calls _build_client() -> tool-use -> parse build-plans.
    Prunes off-universe tickers; rejects degenerate plans; deduplicates.
    D-1 never-raises — on any failure returns empty .plans + .reason.

admit_community_candidates(community_result, objective, *, max_candidates) -> list
    AC-12. Ranks community candidates by the objective OOS stat; missing-stat
    docs kept-last; bounded; each tagged provenance='atlas-suggested'. Never raises.

load_atlas_candidates(objective, *, max_candidates) -> list
    Wraps load_community_strategies(force_refresh=False) then admit_community_candidates.

pool_candidates(built_new, atlas_suggested) -> list
    Pool the two provenance sources, preserving each item's tag.

Design constraints
------------------
- D-1 contract: reason strings carry ONLY type(exc).__name__ — no key/path/message leak.
- Advisory-only: no LIVE_EXECUTION, no Composer write call, no _SETTINGS_WRITE_ALLOWLIST.
- SDK lazy-import: anthropic is imported only inside _build_client (CC-2).
- Off-execution-path: not imported from alpha_bot_execution.py.
- bill-protection: Atlas loaded with force_refresh=False (weekly cache).
"""

from __future__ import annotations

import copy
import enum
import hashlib
import json
import logging
import math
import os
from dataclasses import dataclass, field

import model_config

# Re-export so the AC-12 bound tests can resolve it from this module.
from advisors.strategy_builder_engine import (  # noqa: F401
    MAX_COMMUNITY_CANDIDATES_PER_RUN,
    CandidateInfo,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named constants (no magic numbers)
# ---------------------------------------------------------------------------

# Number of build-plans requested from the SDK per objective call (AC-10 named constant).
N_PLANS_PER_OBJECTIVE: int = 12

# Explicit provenance tags (AC-13).
PROVENANCE_BUILT_NEW = "built-new"
PROVENANCE_ATLAS_SUGGESTED = "atlas-suggested"

# The '%' placeholder used inside binary-compound tickers[] is excluded from
# plan_tickers() — it is a syntactic marker, not a real ticker (AC-9 invariant).
_PCT_PLACEHOLDER = "%"

# NODE kinds that are allocation containers (can hold sub-sleeves or sub-allocations).
# Used by the diversify sleeve-count and the general _iter_all_nodes helper.
_CONTAINER_KINDS: frozenset[str] = frozenset({"group", "weight", "filter", "if", "if_compound"})

# Momentum/quality sort-by indicators satisfying the lift_risk_adjusted FILTER signature.
_MOMENTUM_QUALITY_SORTS: frozenset[str] = frozenset({"cumulative-return", "moving-average-return"})

# Low/min-vol sort-by indicators satisfying the volatility_mitigation FILTER signature.
_LOW_VOL_SORTS: frozenset[str] = frozenset(
    {"max-drawdown", "standard-deviation-return", "standard-deviation-price"}
)

# Maximum output tokens for the Opus build-plan SDK call.  4096 (the old value) truncated
# 12 full-grammar plans; this ceiling fits them comfortably.  max_tokens is a BILLING
# CEILING — you pay for actual output, not the ceiling — so a generous value is free.
# Calibrated 2026-06-20 (streaming, max_tokens=32000, stop_reason=tool_use confirmed):
#   cut_drawdown  4906 output tokens  (worst-case — regime-gate / if-node trees)
#   diversify     5015 output tokens
# ceil(5015 * 1.25) = 6269; floored at the RED test minimum of 16000.
# 16384 = 16000 floor + small buffer, safely above both the calibrated worst-case
# (5015 tokens) and the RED >= 16000 assertion.
MAX_OUTPUT_TOKENS: int = 16384

# Bounded retry limit for SDK calls that return stop_reason="max_tokens".  After this
# many consecutive truncations the call degrades per D-1 (no infinite retry loop).
MAX_GENERATION_ATTEMPTS: int = 3


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class Objective(enum.Enum):
    """Four-value objective enum steering structure and admission ranking (AC-8)."""

    diversify = "diversify"
    cut_drawdown = "cut_drawdown"
    lift_risk_adjusted = "lift_risk_adjusted"
    volatility_mitigation = "volatility_mitigation"


@dataclass
class GeneratorResult:
    """Container returned by generate_build_plans. Never None.

    Fields
    ------
    plans : list[dict]
        Admitted build-plan dicts (may be empty on failure or full degenerate-prune).
    reason : str | None
        On failure: type(exc).__name__ only (D-1 contract, no key/path/message leak).
        None on success.
    """

    plans: list[dict] = field(default_factory=list)
    reason: str | None = None


# ---------------------------------------------------------------------------
# SDK client factory seam
# ---------------------------------------------------------------------------


def _build_client():
    """Construct the Anthropic SDK client.

    Factory seam: tests patch ``advisors.build_plan_generator._build_client``.
    Mirrors ai_advisor._build_client (ai_advisor.py:1590).

    Raises
    ------
    RuntimeError
        If ANTHROPIC_API_KEY is absent or the anthropic SDK is not installed.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — the build-plan generator is "
            "unavailable until an API key is configured."
        )
    try:
        import anthropic  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - SDK is a declared dep
        raise RuntimeError(f"the anthropic SDK is not installed: {exc}") from exc
    return anthropic.Anthropic(api_key=api_key)


# ---------------------------------------------------------------------------
# plan_tickers — deterministic walk over the DSL
# ---------------------------------------------------------------------------


def _collect_condition_tickers(cond: dict, out: set) -> None:
    """Collect every real ticker referenced in a CONDITION block (recursive).

    Reads the CANONICAL-FLAT binary leaf encoding (binary-encoding-fix):
    lhs_ticker (flat field, not nested cond["lhs"]["ticker"]).
    rhs: {fixed: num} carries no ticker; {fn, ticker, window} (ticker-comparison)
    carries rhs["ticker"] directly (flat, no nesting).
    """
    if not isinstance(cond, dict):
        return
    ctype = cond.get("type")
    if ctype == "binary":
        # Flat lhs ticker (canonical-flat encoding).
        t = cond.get("lhs_ticker")
        if t and t != _PCT_PLACEHOLDER:
            out.add(t)
        # Ticker-comparison rhs: {fn, ticker, window}; fixed rhs has no ticker.
        rhs = cond.get("rhs", {})
        if isinstance(rhs, dict):
            t = rhs.get("ticker")
            if t and t != _PCT_PLACEHOLDER:
                out.add(t)
    elif ctype == "binary_compound":
        for t in cond.get("tickers", []):
            if t and t != _PCT_PLACEHOLDER:
                out.add(t)
    elif ctype == "compound":
        for sub in cond.get("conditions", []):
            _collect_condition_tickers(sub, out)


def plan_tickers(plan: dict) -> set[str]:
    """Return every ticker referenced anywhere in a build-plan (DSL walk).

    Walks the full DSL NODE tree from plan['root'] and collects:
    - asset: node['ticker']
    - weight/equal/inverse_vol/market_cap: recurse children
    - weight/specified: recurse entry['node'] for each entry in children
    - group: recurse children
    - filter: recurse children (asset leaves)
    - if: condition lhs_ticker + rhs ticker (if present); recurse then/else
    - if_compound: recurse condition block (binary/binary_compound/compound);
      recurse then/else

    Excludes the '%' placeholder (binary-compound syntactic marker).
    Never raises — returns empty set on any malformed input.
    """
    out: set[str] = set()
    try:
        root = plan.get("root")
        if not isinstance(root, dict):
            return out
        # Iterative DFS: stack entries are NODE dicts.
        stack: list[dict] = [root]
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            kind = node.get("kind")

            if kind == "asset":
                t = node.get("ticker")
                if t and t != _PCT_PLACEHOLDER:
                    out.add(t)

            elif kind == "weight":
                scheme = node.get("scheme")
                if scheme == "specified":
                    for entry in node.get("children", []):
                        if isinstance(entry, dict) and "node" in entry:
                            stack.append(entry["node"])
                else:
                    # equal / inverse_vol / market_cap
                    for child in node.get("children", []):
                        stack.append(child)

            elif kind in ("group", "filter"):
                for child in node.get("children", []):
                    stack.append(child)

            elif kind == "if":
                # Condition: lhs_ticker + rhs ticker (if present, not fixed).
                cond = node.get("condition", {})
                if isinstance(cond, dict):
                    lhs_t = cond.get("lhs_ticker")
                    if lhs_t and lhs_t != _PCT_PLACEHOLDER:
                        out.add(lhs_t)
                    rhs = cond.get("rhs", {})
                    if isinstance(rhs, dict):
                        rhs_t = rhs.get("ticker")
                        if rhs_t and rhs_t != _PCT_PLACEHOLDER:
                            out.add(rhs_t)
                for child in node.get("then", []) or []:
                    stack.append(child)
                for child in node.get("else", []) or []:
                    stack.append(child)

            elif kind == "if_compound":
                cond = node.get("condition", {})
                _collect_condition_tickers(cond, out)
                for child in node.get("then", []) or []:
                    stack.append(child)
                for child in node.get("else", []) or []:
                    stack.append(child)
    except Exception:  # pragma: no cover - defensive; never raises contract
        logger.debug("plan_tickers: unexpected error", exc_info=True)
    return out


# ---------------------------------------------------------------------------
# AC-9: membership-validation prune / reject
# ---------------------------------------------------------------------------


def _prune_node(node: dict, membership: frozenset) -> dict | None:
    """Recursively prune off-universe asset children from a NODE.

    Returns the pruned node dict, or None if pruning would leave the node
    empty/degenerate (caller should reject the containing plan).

    Condition tickers (lhs_ticker / rhs ticker in 'if' nodes; tickers[] in
    binary_compound) are SIGNAL references that cannot be safely repaired by
    pruning — if any such ticker is off-universe the function returns None
    to signal a degenerate prune (caller rejects the plan).
    """
    if not isinstance(node, dict):
        return None
    kind = node.get("kind")

    if kind == "asset":
        t = node.get("ticker", "")
        return node if t in membership else None

    elif kind == "weight":
        scheme = node.get("scheme")
        if scheme == "specified":
            pruned_children = []
            for entry in node.get("children", []):
                if not isinstance(entry, dict) or "node" not in entry:
                    continue
                pruned_child = _prune_node(entry["node"], membership)
                if pruned_child is not None:
                    pruned_children.append({"node": pruned_child, "pct": entry.get("pct")})
            if not pruned_children:
                return None
            return {**node, "children": pruned_children}
        else:
            # equal / inverse_vol / market_cap
            pruned_children = []
            for child in node.get("children", []):
                pruned = _prune_node(child, membership)
                if pruned is not None:
                    pruned_children.append(pruned)
            if not pruned_children:
                return None
            return {**node, "children": pruned_children}

    elif kind == "group" or kind == "filter":
        pruned_children = []
        for child in node.get("children", []):
            pruned = _prune_node(child, membership)
            if pruned is not None:
                pruned_children.append(pruned)
        if not pruned_children:
            return None
        return {**node, "children": pruned_children}

    elif kind == "if":
        # Condition signal tickers — reject the plan if any are off-universe.
        cond = node.get("condition", {})
        if isinstance(cond, dict):
            lhs_t = cond.get("lhs_ticker")
            if lhs_t and lhs_t not in membership:
                return None  # signal ticker off-universe -> reject plan
            rhs = cond.get("rhs", {})
            if isinstance(rhs, dict) and "ticker" in rhs:
                rhs_t = rhs["ticker"]
                if rhs_t and rhs_t not in membership:
                    return None  # rhs ticker off-universe -> reject plan

        pruned_then = []
        for child in node.get("then", []) or []:
            pruned = _prune_node(child, membership)
            if pruned is not None:
                pruned_then.append(pruned)
        pruned_else = []
        for child in node.get("else", []) or []:
            pruned = _prune_node(child, membership)
            if pruned is not None:
                pruned_else.append(pruned)

        # Both branches must remain non-empty for the if-node to be valid.
        if not pruned_then or not pruned_else:
            return None
        return {**node, "then": pruned_then, "else": pruned_else}

    elif kind == "if_compound":
        # For compound conditions, collect all tickers referenced and reject if any
        # are off-universe (condition logic cannot be safely repaired by pruning).
        cond = node.get("condition", {})
        cond_tickers: set[str] = set()
        _collect_condition_tickers(cond, cond_tickers)
        if cond_tickers - membership:
            return None  # off-universe condition ticker -> reject plan

        pruned_then = []
        for child in node.get("then", []) or []:
            pruned = _prune_node(child, membership)
            if pruned is not None:
                pruned_then.append(pruned)
        pruned_else = []
        for child in node.get("else", []) or []:
            pruned = _prune_node(child, membership)
            if pruned is not None:
                pruned_else.append(pruned)

        if not pruned_then or not pruned_else:
            return None
        return {**node, "then": pruned_then, "else": pruned_else}

    # Unknown kind — reject the plan (e.g. "weighted", "node" from Opus drift).
    # Passing through would silently admit a 0-ticker plan that plan_tickers()
    # cannot walk and that the C3 compiler will reject anyway.
    return None


def _validate_and_prune(plan: dict, membership: frozenset) -> dict | None:
    """Return a membership-validated (pruned) deep-copy of plan, or None if degenerate.

    Operates on the 'root' NODE only. Non-root fields (plan_id, name, objective,
    rebalance, provenance) are preserved unchanged.

    The returned plan is a deep-copy — it does not alias any node objects from the
    SDK input. Component 3 mutates admitted trees during compilation; aliasing the
    SDK response would corrupt the caller's input (the symphony_schema constructors
    deep-copy for exactly this reason).
    """
    root = plan.get("root")
    if not isinstance(root, dict):
        return None
    pruned_root = _prune_node(root, membership)
    if pruned_root is None:
        return None
    # Deep-copy the full plan so admitted nodes never alias SDK input objects.
    validated = copy.deepcopy({**plan, "root": pruned_root})
    # Reject zero-ticker plans: a plan with no extractable tickers cannot become
    # a valid Composer tree (the C3 compiler would also reject it). This catches
    # any drift-vocabulary plan that survived _prune_node's unknown-kind guard
    # (e.g. a nested unknown kind inside a known outer kind).
    if not plan_tickers(validated):
        return None
    return validated


# ---------------------------------------------------------------------------
# Structural deduplication
# ---------------------------------------------------------------------------


def _root_fingerprint(plan: dict) -> str:
    """Compute a structural fingerprint over the plan's 'root' node only.

    Deterministic: sorts keys, strips plan_id/name/provenance (volatile fields
    that differ between clones but do not affect structure). Two plans with
    identical root structures produce identical fingerprints regardless of their
    plan_id or name.
    """
    root = plan.get("root", {})
    canonical = json.dumps(root, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# AC-8 objective-signature predicate — single source of truth
# ---------------------------------------------------------------------------


def _iter_all_nodes(root: dict):
    """Iterative DFS yielding every NODE dict in a plan's root tree.

    Handles all DSL node kinds: asset, weight (equal/specified/inverse_vol/market_cap),
    group, filter, if, if_compound. Never raises — skips non-dict entries.
    """
    stack: list[dict] = [root]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        yield node
        kind = node.get("kind")
        if kind == "weight" and node.get("scheme") == "specified":
            for entry in node.get("children", []):
                if isinstance(entry, dict) and "node" in entry:
                    stack.append(entry["node"])
        else:
            for child in node.get("children", []) or []:
                stack.append(child)
        for branch in ("then", "else"):
            for child in node.get(branch, []) or []:
                stack.append(child)


def _diversify_sleeve_count(root: dict) -> int:
    """Count allocation-container sleeves that are direct children of the root.

    A sleeve is a direct child of the root node whose kind is a container
    (group / weight / filter / if / if_compound). Asset leaves do NOT count —
    a lone weight/filter over N asset leaves is 1 sleeve, not N.

    Special cases:
    - if/if_compound root: direct children are then[] + else[] branches.
    - specified-weight root: direct {node, pct} entries whose node is a container.
    - All other container roots: direct children[] that are containers.
    """
    if not isinstance(root, dict):
        return 0
    kind = root.get("kind")

    if kind in ("if", "if_compound"):
        branches = list(root.get("then", []) or []) + list(root.get("else", []) or [])
        return sum(1 for c in branches if isinstance(c, dict) and c.get("kind") in _CONTAINER_KINDS)

    if kind == "weight" and root.get("scheme") == "specified":
        return sum(
            1
            for entry in root.get("children", [])
            if isinstance(entry, dict)
            and isinstance(entry.get("node"), dict)
            and entry["node"].get("kind") in _CONTAINER_KINDS
        )

    # group / weight(equal/inverse_vol/market_cap) / filter: count container children.
    return sum(
        1
        for c in root.get("children", []) or []
        if isinstance(c, dict) and c.get("kind") in _CONTAINER_KINDS
    )


def plan_matches_objective(plan: dict, objective) -> bool:
    """Return True if plan's root structure satisfies the objective's AC-8 signature.

    This is the SINGLE SOURCE OF TRUTH for the admission filter and the test
    assertions — the generator calls this after prune+dedup; tests import and
    delegate to it so the filter and the assertions cannot drift.

    Per-objective signature (PM-approved, AC-8 decision B):
      diversify          — root has >=2 allocation-container direct children (sleeves).
                           Asset leaves do not count; a lone filter/weight over N assets
                           = 1 sleeve. (Refinement: only container-typed children count.)
      cut_drawdown       — anywhere in the tree: a regime gate (if/if_compound) OR an
                           inverse_vol weight (kind=weight, scheme=inverse_vol).
      lift_risk_adjusted — anywhere: a momentum/quality FILTER (filter whose sort_by_fn
                           is in _MOMENTUM_QUALITY_SORTS). A bare basket does NOT match.
      volatility_mitigation — anywhere: an inverse_vol weight OR a low/min-vol filter
                              (filter whose sort_by_fn is in _LOW_VOL_SORTS).

    Never raises (D-1). Returns False for any malformed input.
    """
    try:
        obj_name = objective.value if isinstance(objective, Objective) else str(objective)
        root = plan.get("root")
        if not isinstance(root, dict):
            return False

        if obj_name == "diversify":
            return _diversify_sleeve_count(root) >= 2

        elif obj_name == "cut_drawdown":
            for node in _iter_all_nodes(root):
                if node.get("kind") in ("if", "if_compound"):
                    return True
                if node.get("kind") == "weight" and node.get("scheme") == "inverse_vol":
                    return True
            return False

        elif obj_name == "lift_risk_adjusted":
            for node in _iter_all_nodes(root):
                if (
                    node.get("kind") == "filter"
                    and node.get("sort_by_fn") in _MOMENTUM_QUALITY_SORTS
                ):
                    return True
            return False

        elif obj_name == "volatility_mitigation":
            for node in _iter_all_nodes(root):
                if node.get("kind") == "weight" and node.get("scheme") == "inverse_vol":
                    return True
                if node.get("kind") == "filter" and node.get("sort_by_fn") in _LOW_VOL_SORTS:
                    return True
            return False

        # Unknown objective — fail closed.
        return False

    except Exception:  # pragma: no cover - defensive; never-raises contract
        logger.debug("plan_matches_objective: unexpected error", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Anthropic tool definition for structured build-plan emission
# ---------------------------------------------------------------------------

# The tool the SDK is asked to call. The generator reads the tool_use block's
# .input['plans'] — a list of build-plan dicts conforming to the DSL.
#
# The input_schema is TIGHTENED (defense-in-depth against Opus vocabulary drift):
# - NODE.kind is enum-constrained to the valid set (excludes "weighted"/"node").
# - NODE.scheme for weight nodes is enum-constrained to {equal, specified, inverse_vol}.
# - specified children carry {node, pct} — the 'pct' field is named explicitly.
# This cannot fully prevent deep nesting drift, but forces the correct top-level tokens.
_EMIT_BUILD_PLANS_TOOL = {
    "name": "emit_build_plans",
    "description": (
        "Emit a list of objective-shaped build-plan dicts conforming to the "
        "Planet Stopper build-plan DSL. Each plan is a JSON-serializable dict "
        "with keys: plan_id, objective, name, rebalance, root (NODE). "
        "NODE is a tagged union on 'kind': asset | weight | group | filter | if | if_compound. "
        "weight nodes carry a 'scheme' field: equal | specified | inverse_vol. "
        "specified children are {node: NODE, pct: number} entries."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "plans": {
                "type": "array",
                "description": "List of build-plan dicts in the Planet Stopper DSL.",
                "items": {
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string"},
                        "objective": {"type": "string"},
                        "name": {"type": "string"},
                        "rebalance": {
                            "type": "string",
                            "enum": ["daily", "weekly", "monthly", "quarterly", "yearly", "none"],
                        },
                        "root": {
                            "type": "object",
                            "description": (
                                "A DSL NODE. kind must be one of: asset, weight, group, filter, "
                                "if, if_compound. For weight nodes, scheme must be one of: "
                                "equal, specified, inverse_vol."
                            ),
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": [
                                        "asset",
                                        "weight",
                                        "group",
                                        "filter",
                                        "if",
                                        "if_compound",
                                    ],
                                },
                                "scheme": {
                                    "type": "string",
                                    "enum": ["equal", "specified", "inverse_vol"],
                                    "description": "Required when kind='weight'.",
                                },
                                "ticker": {
                                    "type": "string",
                                    "description": "Required when kind='asset'.",
                                },
                                "children": {
                                    "type": "array",
                                    "description": (
                                        "Sub-nodes. For scheme='specified', each entry is "
                                        "{node: NODE, pct: number}. For all other container "
                                        "kinds, each entry is a NODE dict."
                                    ),
                                },
                                "condition": {
                                    "type": "object",
                                    "description": (
                                        "Required for kind='if' and kind='if_compound'. "
                                        "MUST be a dict — NEVER a string label. "
                                        "For kind='if' (flat): fields lhs_fn, lhs_ticker, "
                                        "window, comparator, rhs. "
                                        "For kind='if_compound' (typed union): use a 'type' "
                                        "discriminator: binary / binary_compound / compound. "
                                        "binary_compound: fn, tickers[], comparator, rhs, "
                                        "window, operator (any/all). "
                                        "compound: operator (all/any), conditions[]."
                                    ),
                                    "properties": {
                                        "lhs_fn": {"type": "string"},
                                        "lhs_ticker": {"type": "string"},
                                        "window": {"type": "integer"},
                                        "comparator": {
                                            "type": "string",
                                            "enum": ["gt", "lt", "gte", "lte"],
                                        },
                                        "rhs": {"type": "object"},
                                        "type": {
                                            "type": "string",
                                            "enum": [
                                                "binary",
                                                "binary_compound",
                                                "compound",
                                            ],
                                            "description": (
                                                "Condition type for if_compound: binary, "
                                                "binary_compound, or compound."
                                            ),
                                        },
                                        "operator": {
                                            "type": "string",
                                            "enum": ["any", "all"],
                                            "description": (
                                                "For binary_compound: any/all over tickers. "
                                                "For compound: any (OR) / all (AND)."
                                            ),
                                        },
                                        "conditions": {
                                            "type": "array",
                                            "description": ("Sub-conditions for type='compound'."),
                                        },
                                        "tickers": {
                                            "type": "array",
                                            "description": (
                                                "Tickers for type='binary_compound' broadcast."
                                            ),
                                            "items": {"type": "string"},
                                        },
                                        "fn": {
                                            "type": "string",
                                            "description": (
                                                "Indicator function for binary_compound."
                                            ),
                                        },
                                    },
                                },
                                "then": {
                                    "type": "array",
                                    "description": "if/if_compound true-branch list.",
                                },
                                "else": {
                                    "type": "array",
                                    "description": "if/if_compound false-branch list.",
                                },
                            },
                            "required": ["kind"],
                        },
                    },
                    "required": ["plan_id", "objective", "name", "rebalance", "root"],
                },
            }
        },
        "required": ["plans"],
    },
}


# ---------------------------------------------------------------------------
# Prompt-builder seam — extracted so tests can inspect the sent instructions
# without mocking the full SDK round-trip.
# ---------------------------------------------------------------------------

# Conforming DSL example embedded in the diversify prompt (derived from the
# byte-exact shapes accepted by the C3 compiler). This plan has plan_tickers>0
# and matches the 'diversify' objective (two container sleeves under a group).
_EXAMPLE_PLAN: dict = {
    "plan_id": "example-1",
    "objective": "diversify",
    "name": "Example Diversified Portfolio",
    "rebalance": "daily",
    "root": {
        "kind": "group",
        "name": "portfolio",
        "children": [
            {
                "kind": "weight",
                "scheme": "equal",
                "children": [
                    {"kind": "asset", "ticker": "SPY"},
                    {"kind": "asset", "ticker": "QQQ"},
                ],
            },
            {
                "kind": "weight",
                "scheme": "inverse_vol",
                "children": [
                    {"kind": "asset", "ticker": "TLT"},
                    {"kind": "asset", "ticker": "GLD"},
                ],
            },
        ],
    },
}

# Conforming if-node DSL example embedded in the cut_drawdown prompt. The
# condition is a DICT (not a string label) — verified to compile clean through
# the C3 compiler (plan_tree_compiler.compile_plan -> tree not None,
# validate_tree == []). Derived from the byte-exact compiler-accepted shape
# in tests/advisors/test_plan_tree_compiler.py.
_EXAMPLE_IF_PLAN: dict = {
    "plan_id": "example-cd-1",
    "objective": "cut_drawdown",
    "name": "Example Regime-Gate Portfolio",
    "rebalance": "daily",
    "root": {
        "kind": "if",
        "condition": {
            "lhs_fn": "relative-strength-index",
            "lhs_ticker": "SPY",
            "window": 10,
            "comparator": "gt",
            "rhs": {"fixed": 80},
        },
        "then": [
            {
                "kind": "weight",
                "scheme": "equal",
                "children": [
                    {"kind": "asset", "ticker": "UVXY"},
                    {"kind": "asset", "ticker": "TLT"},
                ],
            }
        ],
        "else": [
            {
                "kind": "weight",
                "scheme": "inverse_vol",
                "children": [
                    {"kind": "asset", "ticker": "SPY"},
                    {"kind": "asset", "ticker": "IEF"},
                    {"kind": "asset", "ticker": "GLD"},
                ],
            }
        ],
    },
}

# Conforming if_compound DSL example — a compound all-of-N regime gate verified
# to compile clean through the C3 compiler (tree not None, validate_tree == []).
# The condition uses the typed union shape: {type, operator, conditions[]}, with
# a MIXED sub-condition list: one flat type:"binary" leaf (lhs_fn/lhs_ticker/window
# + rhs:{fixed}) + one type:"binary_compound" broadcast leaf. This teaches Opus
# both flat-binary and binary_compound shapes inside a compound.
_EXAMPLE_IF_COMPOUND_PLAN: dict = {
    "plan_id": "example-ifc-1",
    "objective": "cut_drawdown",
    "name": "Example Compound-Gate Portfolio",
    "rebalance": "daily",
    "root": {
        "kind": "if_compound",
        "condition": {
            "type": "compound",
            "operator": "all",
            "conditions": [
                {
                    "type": "binary",
                    "lhs_fn": "relative-strength-index",
                    "lhs_ticker": "SPY",
                    "window": 14,
                    "comparator": "gt",
                    "rhs": {"fixed": 70},
                },
                {
                    "type": "binary_compound",
                    "fn": "max-drawdown",
                    "tickers": ["QQQ"],
                    "comparator": "lt",
                    "rhs": {"const": 20},
                    "window": 30,
                    "operator": "any",
                },
            ],
        },
        "then": [
            {
                "kind": "weight",
                "scheme": "equal",
                "children": [
                    {"kind": "asset", "ticker": "UVXY"},
                    {"kind": "asset", "ticker": "TLT"},
                ],
            }
        ],
        "else": [
            {
                "kind": "weight",
                "scheme": "inverse_vol",
                "children": [
                    {"kind": "asset", "ticker": "SPY"},
                    {"kind": "asset", "ticker": "IEF"},
                ],
            }
        ],
    },
}

# Per-objective structural signatures described in the prompt. Each entry is a
# brief natural-language description of the required structure plus a hint at the
# DSL construct that satisfies the AC-8 admission predicate.
_OBJECTIVE_SIGNATURES: dict[str, str] = {
    "diversify": (
        "The plan's root must have AT LEAST 2 distinct allocation-container sleeves "
        "(kind='group', kind='weight', or kind='filter' children). Use a group node "
        "with two or more weight/filter children — for example, one equal-weight equity "
        "sleeve and one inverse_vol bond sleeve. A lone weight node over N assets is "
        "only 1 sleeve and does NOT satisfy the diversify signature."
    ),
    "cut_drawdown": (
        "The plan must contain a regime gate (kind='if' or kind='if_compound') OR an "
        "inverse-volatility weight (kind='weight', scheme='inverse_vol'). A typical "
        "cut_drawdown plan uses a regime-triggered if node to switch between a risk-on "
        "allocation and a defensive allocation, or an inverse_vol sleeve that "
        "dynamically underweights high-volatility assets."
    ),
    "lift_risk_adjusted": (
        "The plan must contain a momentum/quality FILTER node: kind='filter' with "
        "sort_by_fn='cumulative-return' or sort_by_fn='moving-average-return'. The "
        "filter selects the top-N performers from a broader asset pool, concentrating "
        "into momentum leaders. A bare equal-weight basket does NOT satisfy this "
        "signature — the filter construct is required."
    ),
    "volatility_mitigation": (
        "The plan must contain an inverse-volatility weight (kind='weight', "
        "scheme='inverse_vol') OR a low/min-vol filter (kind='filter' with "
        "sort_by_fn='standard-deviation-return' or sort_by_fn='max-drawdown'). Both "
        "constructs dynamically reduce exposure to high-volatility assets."
    ),
}


def _build_generation_prompt(
    objective,
    n_plans: int = N_PLANS_PER_OBJECTIVE,
    membership=None,
) -> str:
    """Build the SDK prompt for the given objective.

    Embeds the FULL build-plan DSL grammar (kind/scheme vocabulary, the {node,pct}
    specified-children shape), a CONCRETE conforming example plan (valid DSL,
    plan_tickers>0, matches the diversify objective), and the per-objective
    structural signature so Opus emits the right vocabulary.

    Parameters
    ----------
    objective : Objective
        The structural objective steering the SDK prompt.
    n_plans : int
        Number of distinct plans requested.
    membership : frozenset | set | None
        The valid ticker universe (included in prompt for reference).

    Returns
    -------
    str
        The full prompt string sent to the SDK.
    """
    obj_name = objective.value if isinstance(objective, Objective) else str(objective)
    signature = _OBJECTIVE_SIGNATURES.get(obj_name, "")
    example_json = json.dumps(_EXAMPLE_PLAN, indent=2)
    # Embed the if-node example for the gate-capable objective (cut_drawdown).
    # For all other objectives, include both examples as supplementary DSL references
    # so every prompt teaches lhs_fn/lhs_ticker/comparator/rhs AND the compound union.
    if_example_json = json.dumps(_EXAMPLE_IF_PLAN, indent=2)
    if_compound_example_json = json.dumps(_EXAMPLE_IF_COMPOUND_PLAN, indent=2)
    universe_hint = ""
    if membership:
        sample = sorted(membership)[:20]
        universe_hint = (
            f"\n\nAVAILABLE TICKERS (use ONLY these — {len(membership)} total, "
            f"sample): {', '.join(sample)}" + (" ..." if len(membership) > 20 else "")
        )

    return (
        f"You are a quantitative strategy designer for the Planet Stopper risk engine.\n\n"
        f"TASK: Generate exactly {n_plans} DISTINCT build-plans for objective='{obj_name}'.\n\n"
        f"CRITICAL — USE THE EXACT DSL GRAMMAR BELOW. Do NOT invent new field names.\n\n"
        f"## Build-Plan DSL Grammar\n\n"
        f"Every plan is a JSON dict with keys: plan_id, objective, name, rebalance, root.\n"
        f"'root' is a NODE. Every NODE is a dict with a 'kind' field.\n\n"
        f"### Valid NODE kinds (ONLY these — never use 'weighted' or any other value):\n"
        f"- kind='asset'        — leaf holding one ticker.  Required field: ticker (string).\n"
        f"- kind='weight'       — allocation container.     Required field: scheme.\n"
        f"- kind='group'        — named grouping container. Required field: name (string).\n"
        f"- kind='filter'       — top-N selector.  "
        f"Required fields: select_fn, select_n, sort_by_fn, window, children.\n"
        f"- kind='if'           — regime gate.  "
        f"Required fields: condition (DICT — see below), then (list), else (list).\n"
        f"- kind='if_compound'  — compound regime gate.  "
        f"Required fields: condition (DICT — see below), then (list), else (list).\n\n"
        f"### Weight scheme values (the 'scheme' field on kind='weight' nodes — ONLY these):\n"
        f"- scheme='equal'       — equal-weight all children.\n"
        f"- scheme='specified'   — each child entry is "
        f"{{\"node\": NODE, \"pct\": number}}  (note: 'pct', NOT 'weight')\n"
        f"- scheme='inverse_vol' — inverse-volatility weight across children.\n\n"
        f"### Specified-weight children shape (IMPORTANT — NOT a bare weight float):\n"
        f"  WRONG shape (do not use): a child entry carrying a 'weight' float directly\n"
        f'  CORRECT shape: each child entry is {{"node": {{...NODE...}}, "pct": 60}}\n'
        f"  The fields are 'node' (the sub-NODE dict) and 'pct' (numeric percentage).\n\n"
        f"### if condition shape (CRITICAL — condition is a DICT, NOT a string):\n"
        f"  WRONG: condition = 'spy_above_200d_sma'  ← NEVER use a string label\n"
        f"  CORRECT (flat binary condition for kind='if'):\n"
        f"  condition = {{\n"
        f'    "lhs_fn": "<indicator>",   // e.g. "relative-strength-index"\n'
        f'    "lhs_ticker": "<TICKER>",  // e.g. "SPY"\n'
        f'    "window": <int>,           // lookback window in days\n'
        f'    "comparator": "<op>",      // one of: gt, lt, gte, lte\n'
        f'    "rhs": {{"fixed": <num>}}  // fixed-value rhs: {{"fixed": 80}}\n'
        f"              // OR ticker-comparison rhs: "
        f'{{"fn": "<indicator>", "ticker": "<T>", "window": <int>}}\n'
        f"  }}\n"
        f"  Fields: lhs_fn, lhs_ticker, window, comparator, rhs are ALL required.\n\n"
        f"### if_compound condition shape (compound typed union — for multi-condition gates):\n"
        f"  kind='if_compound' uses a TYPED condition union selected by a 'type' field.\n"
        f"  Three types:\n"
        f"  1. type='binary' — same as the flat if condition (lhs_fn/lhs_ticker/window/"
        f"comparator/rhs).\n"
        f"  2. type='binary_compound' — broadcast one indicator over MULTIPLE tickers:\n"
        f"     {{\n"
        f'       "type": "binary_compound",\n'
        f'       "fn": "<indicator>",       // e.g. "relative-strength-index"\n'
        f'       "tickers": ["SPY", "QQQ"], // list of tickers (broadcast)\n'
        f'       "comparator": "gt",        // one of: gt, lt, gte, lte\n'
        f'       "rhs": {{"const": 70}},   // use key "const" (not "fixed") here\n'
        f'       "window": 14,\n'
        f'       "operator": "any"          // "any" = at least one ticker; "all" = all\n'
        f"     }}\n"
        f"  3. type='compound' — combine multiple conditions with AND/OR:\n"
        f"     {{\n"
        f'       "type": "compound",\n'
        f'       "operator": "all",         // "all" = AND, "any" = OR\n'
        f'       "conditions": [            // list of binary / binary_compound entries\n'
        f'         {{"type": "binary_compound", "fn": "...", "tickers": [...], ...}},\n'
        f"         ...\n"
        f"       ]\n"
        f"     }}\n\n"
        f"## CONCRETE CONFORMING EXAMPLE — diversify (copy this structure):\n\n"
        f"```json\n{example_json}\n```\n\n"
        f"## CONCRETE CONFORMING EXAMPLE — cut_drawdown with flat regime gate (if node):\n\n"
        f"```json\n{if_example_json}\n```\n\n"
        f"## CONCRETE CONFORMING EXAMPLE — cut_drawdown with compound gate (if_compound):\n\n"
        f"```json\n{if_compound_example_json}\n```\n\n"
        f"## Structural Requirement for objective='{obj_name}':\n\n"
        f"{signature}\n"
        f"{universe_hint}\n\n"
        f"Emit exactly {n_plans} distinct plans using the emit_build_plans tool. "
        f"Every plan must satisfy the '{obj_name}' structural requirement above. "
        f"Use diverse asset combinations across plans."
    )


# ---------------------------------------------------------------------------
# generate_build_plans — AC-7/8/9/10/11
# ---------------------------------------------------------------------------


def generate_build_plans(
    objective,
    membership_set,
    *,
    n_plans: int = N_PLANS_PER_OBJECTIVE,
) -> GeneratorResult:
    """Generate N objective-shaped build-plans via the Anthropic SDK (tool-use).

    Parameters
    ----------
    objective : Objective
        The structural objective steering the SDK prompt.
    membership_set : frozenset | set
        The valid ticker universe (AC-9 membership validation).
    n_plans : int
        Maximum number of structurally-distinct admitted plans to return.

    Returns
    -------
    GeneratorResult
        .plans — list of validated, tagged, deduped build-plan dicts.
        .reason — type(exc).__name__ on failure; None on success (D-1 contract).

    Never raises (AC-11). On any failure: empty .plans + .reason.
    """
    try:
        membership = frozenset(membership_set) if membership_set else frozenset()
        obj_name = objective.value if isinstance(objective, Objective) else str(objective)

        # Build the SDK client (patched in tests via the _build_client seam).
        client = _build_client()

        prompt = _build_generation_prompt(objective, n_plans, membership)

        # Bounded retry on truncation (stop_reason="max_tokens").  The old bare literal
        # max_tokens=4096 was too small for 12 full-grammar plans; MAX_OUTPUT_TOKENS fixes
        # that, but we also retry in case the model still exceeds the ceiling.  Any other
        # stop_reason (e.g. "tool_use") is NOT a truncation and does not retry.
        response = None
        for _attempt in range(MAX_GENERATION_ATTEMPTS):
            response = client.messages.create(
                model=model_config.get_advisor_suggestion_model(),
                max_tokens=MAX_OUTPUT_TOKENS,
                tools=[_EMIT_BUILD_PLANS_TOOL],
                tool_choice={"type": "tool", "name": "emit_build_plans"},
                messages=[{"role": "user", "content": prompt}],
            )
            if getattr(response, "stop_reason", None) != "max_tokens":
                # Non-truncated response — proceed to parse below.
                break
            logger.warning(
                "generate_build_plans: stop_reason=max_tokens on attempt %d/%d",
                _attempt + 1,
                MAX_GENERATION_ATTEMPTS,
            )
        else:
            # All MAX_GENERATION_ATTEMPTS returned stop_reason="max_tokens".
            return GeneratorResult(
                plans=[], reason="max_tokens: response truncated after all attempts"
            )

        # Find the tool_use block in the response.
        tool_block = None
        for block in response.content or []:
            if getattr(block, "type", None) == "tool_use":
                tool_block = block
                break

        if tool_block is None:
            return GeneratorResult(plans=[], reason="NoToolUseBlock")

        raw_plans = tool_block.input.get("plans") if isinstance(tool_block.input, dict) else None
        if not isinstance(raw_plans, list):
            return GeneratorResult(plans=[], reason="InvalidToolUsePayload")

        # Validate, prune, tag, dedup, signature-filter, and accumulate up to n_plans.
        # Order is fixed (AC-8 enforcement test pins it): prune -> dedup -> signature filter.
        seen_fingerprints: set[str] = set()
        admitted: list[dict] = []

        for raw in raw_plans:
            if not isinstance(raw, dict):
                continue
            # AC-9: prune off-universe tickers; reject degenerate plans.
            pruned = _validate_and_prune(raw, membership)
            if pruned is None:
                continue
            # AC-13: tag provenance='built-new'.
            pruned["provenance"] = PROVENANCE_BUILT_NEW
            # AC-10: dedup structurally-identical plans (fingerprint on root only).
            fp = _root_fingerprint(pruned)
            if fp in seen_fingerprints:
                continue
            seen_fingerprints.add(fp)
            # AC-8 (B): signature filter — drop plans that don't satisfy the objective.
            # Runs AFTER prune+dedup so a plan whose structure degrades below the
            # signature threshold after pruning is correctly rejected here.
            if not plan_matches_objective(pruned, objective):
                continue
            admitted.append(pruned)
            if len(admitted) >= n_plans:
                break

        # AC-8 (B) + AC-11/AC-23: if the filter leaves zero plans, surface an honest
        # reason — never silently return an empty list with reason=None (that would
        # look like a parse failure rather than a signature-floor result).
        if not admitted:
            return GeneratorResult(
                plans=[],
                reason=f"no plans matched the {obj_name} signature after prune and dedup",
            )

        return GeneratorResult(plans=admitted, reason=None)

    except Exception as exc:
        # D-1: reason contains ONLY the exception class name — no key/path/message.
        logger.debug("generate_build_plans: error", exc_info=True)
        return GeneratorResult(plans=[], reason=type(exc).__name__)


# ---------------------------------------------------------------------------
# admit_community_candidates — AC-12 objective-matched admission
# ---------------------------------------------------------------------------


def _extract_tickers_from_tree(tree: dict) -> set[str]:
    """Walk a community candidate's tree dict and return referenced ticker strings.

    Community candidate trees may be DSL-shaped or legacy Composer raw_value trees.
    This is a best-effort walk that collects string values from 'ticker' keys at
    any depth (iterative, never raises). Used for Jaccard-based diversify ranking.
    """
    out: set[str] = set()
    try:
        stack: list = [tree]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                t = node.get("ticker")
                if isinstance(t, str) and t and t != _PCT_PLACEHOLDER:
                    out.add(t)
                for v in node.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(node, list):
                stack.extend(node)
    except Exception:  # pragma: no cover - defensive
        pass
    return out


def _jaccard_overlap(set_a: set, set_b: set) -> float:
    """Return the Jaccard similarity (intersection / union) of two ticker sets.

    Returns 0.0 when both sets are empty (no overlap), or 1.0 when both
    are identical. Used as the diversify greedy-admission overlap metric.
    """
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def admit_community_candidates(
    community_result,
    objective,
    *,
    max_candidates: int = MAX_COMMUNITY_CANDIDATES_PER_RUN,
) -> list:
    """Rank and admit community strategies by the objective OOS metric.

    Ranking convention per objective (PM-approved). Field names are the REAL
    captplanet.strategies oos_metrics keys (DE-ATLAS-STAT-FIELD-001 — the
    lowercase keys below existed on 0 live docs; generalizes the same
    field-path bug fixed in community_strats._parse_sharpe). cut_drawdown and
    volatility_mitigation read a KEY-UNION (DE-ATLAS-STAT-FIELD-002 — real
    docs are raw-data-inconsistent, carrying either the %-suffixed or bare
    key form; community_strats passes oos_metrics through verbatim with no
    key normalization):
      cut_drawdown          — 'Max Drawdown %' / 'Max Drawdown' (%-string),
                              descending (nearer zero = shallower, better)
      volatility_mitigation — 'Volatility (ann.) %' / 'Volatility (ann.)' (%-string),
                              ascending (lowest first)
      lift_risk_adjusted    — 'Sharpe' (plain-decimal string, NOT %-formatted,
                              single key form), descending (highest first)
      diversify            — greedy low-ticker-overlap (Jaccard), deterministic+complete

    Missing / unparseable stat docs are KEPT-LAST (admitted after all docs that have
    the stat). The bound applies over the entire ranked list.

    Each admitted candidate is a CandidateInfo tagged provenance='atlas-suggested'
    in .params (AC-13).

    Never raises (D-1). Malformed input degrades to empty list.
    """
    try:
        if not isinstance(community_result, dict):
            return []
        if not community_result.get("available", False):
            return []
        raw = community_result.get("candidates")
        if not raw or not isinstance(raw, list):
            return []

        obj_name = objective.value if isinstance(objective, Objective) else str(objective)

        def _stat(doc: dict, keys: list[str], *, percent: bool = False) -> float | None:
            """Read the first parseable value from doc['oos_metrics'] across a
            candidate key-union, trying each key in `keys` in order.

            DE-ATLAS-STAT-FIELD-002: real captplanet.strategies docs are
            raw-data-inconsistent — some carry a %-suffixed key form (e.g.
            'Max Drawdown %'), others the bare form (e.g. 'Max Drawdown');
            community_strats passes oos_metrics through verbatim (no key
            normalization), so both forms genuinely coexist in the source
            collection. No known doc carries more than one of a given pair, so
            precedence among a genuine collision is unspecified but
            deterministic (list order) — first key that yields a parseable
            value wins; a present-but-unparseable value falls through to the
            next candidate key rather than short-circuiting to None.

            Mirrors community_strats._parse_sharpe's defensive contract
            (DE-ATLAS-STAT-FIELD-001): missing key, non-numeric value, and
            'nan'/'inf' (which pass Python's bare float() but are not valid
            metric values) all resolve to None, never raise. When percent=True,
            a trailing '%' is stripped before parsing each candidate key —
            'Max Drawdown'/'Volatility (ann.)' (both key forms) are
            %-string-valued on real docs; 'Sharpe' is plain-decimal and must
            NOT be stripped.
            """
            oos = doc.get("oos_metrics")
            if not isinstance(oos, dict):
                return None
            for key in keys:
                val = oos.get(key)
                if val is None:
                    continue
                raw = str(val).strip()
                if percent and raw.endswith("%"):
                    raw = raw[:-1]
                try:
                    parsed = float(raw)
                except (TypeError, ValueError):
                    continue
                if math.isnan(parsed) or math.isinf(parsed):
                    continue
                return parsed
            return None

        if obj_name == "cut_drawdown":
            # 'Max Drawdown' stored as a %-string negative number; nearer zero =
            # shallower = better. Sort descending: -1.50% > -8.70% > -22.30%.
            # Key-union: real docs use either 'Max Drawdown %' (dominant form)
            # or the bare 'Max Drawdown' (DE-ATLAS-STAT-FIELD-002).
            # Missing/unparseable docs -> float("-inf") -> last.
            keyed = [(_stat(d, ["Max Drawdown %", "Max Drawdown"], percent=True), d) for d in raw]
            keyed.sort(
                key=lambda x: x[0] if x[0] is not None else float("-inf"),
                reverse=True,
            )
            ranked_docs = [d for _, d in keyed]

        elif obj_name == "volatility_mitigation":
            # 'Volatility (ann.)' is a %-string. Lowest first -> ascending.
            # Key-union: real docs use either 'Volatility (ann.) %' (dominant
            # form) or the bare 'Volatility (ann.)' (DE-ATLAS-STAT-FIELD-002).
            # Missing/unparseable -> float("+inf") -> last.
            keyed = [
                (_stat(d, ["Volatility (ann.) %", "Volatility (ann.)"], percent=True), d)
                for d in raw
            ]
            keyed.sort(key=lambda x: x[0] if x[0] is not None else float("+inf"))
            ranked_docs = [d for _, d in keyed]

        elif obj_name == "lift_risk_adjusted":
            # 'Sharpe' is a plain-decimal string (NOT %-formatted), a single
            # key form across all live docs — no key-union needed. Highest
            # first -> descending. Missing/unparseable -> float("-inf") -> last.
            keyed = [(_stat(d, ["Sharpe"]), d) for d in raw]
            keyed.sort(
                key=lambda x: x[0] if x[0] is not None else float("-inf"),
                reverse=True,
            )
            ranked_docs = [d for _, d in keyed]

        else:
            # diversify — greedy low-ticker-overlap selection (Jaccard).
            # Tiebreaks resolved by sid sort (deterministic). Admits complete set
            # bounded by max_candidates (greedy terminates when cap reached).
            sorted_pool = sorted(raw, key=lambda d: d.get("sid", ""))
            ranked_docs = []
            admitted_tickers: set[str] = set()
            remaining = list(sorted_pool)
            while remaining:
                # Pick the candidate with the lowest Jaccard overlap vs admitted tickers.
                best_idx = 0
                best_overlap = float("+inf")
                for idx, doc in enumerate(remaining):
                    doc_tickers = _extract_tickers_from_tree(doc.get("tree", {}))
                    overlap = _jaccard_overlap(doc_tickers, admitted_tickers)
                    if overlap < best_overlap:
                        best_overlap = overlap
                        best_idx = idx
                chosen = remaining.pop(best_idx)
                ranked_docs.append(chosen)
                chosen_tickers = _extract_tickers_from_tree(chosen.get("tree", {}))
                admitted_tickers |= chosen_tickers
            # ranked_docs now contains all docs in greedy-diversify order.

        # Apply cap and tag each admitted candidate as provenance='atlas-suggested'.
        admitted: list[CandidateInfo] = []
        for doc in ranked_docs[:max_candidates]:
            try:
                sid = doc.get("sid") or doc.get("candidate_id", "")
                admitted.append(
                    CandidateInfo(
                        candidate_id=sid,
                        tree=doc.get("tree", {}),
                        template_id="community",
                        params={
                            "sid": sid,
                            "name": doc.get("name", ""),
                            "composition_hash": doc.get("composition_hash", ""),
                            "provenance": PROVENANCE_ATLAS_SUGGESTED,
                        },
                        metrics={},
                        backtest_error=None,
                    )
                )
            except Exception:
                logger.debug("admit_community_candidates: skipping malformed doc", exc_info=True)
                continue

        return admitted

    except Exception:
        logger.debug("admit_community_candidates: unexpected error", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# load_atlas_candidates — wraps community_strats with bill-protection
# ---------------------------------------------------------------------------


def load_atlas_candidates(
    objective,
    *,
    max_candidates: int = MAX_COMMUNITY_CANDIDATES_PER_RUN,
) -> list:
    """Load and admit community strategies from the Atlas weekly cache.

    Passes force_refresh=False unconditionally (operator bill-protection directive —
    Atlas is a third-party provider; weekly cache minimizes reads).

    Never raises (D-1). On any failure: returns [].
    """
    try:
        from advisors import community_strats  # noqa: PLC0415 - CC-2 lazy import

        community_result = community_strats.load_community_strategies(force_refresh=False)
        return admit_community_candidates(
            community_result, objective, max_candidates=max_candidates
        )
    except Exception:
        logger.debug("load_atlas_candidates: unexpected error", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# pool_candidates — AC-13 provenance-preserving pool
# ---------------------------------------------------------------------------


def pool_candidates(built_new: list, atlas_suggested: list) -> list:
    """Pool built-new and atlas-suggested candidates into one provenance-preserving list.

    Each item's existing provenance tag is preserved unchanged. This is the C2/2b
    slice of AC-13 — the 'both through SAME FDR gate' end-to-end assertion is
    deferred to C3/C5 (compiler + route, forward-AC).

    Never raises.
    """
    return list(built_new) + list(atlas_suggested)
