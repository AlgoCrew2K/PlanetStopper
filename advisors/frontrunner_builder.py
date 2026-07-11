"""Frontrunner Builder — orchestrates candidate frontrunner-overlay generation,
splicing, and (via the shared ``composer_draft_client``) the approval-gated
Composer upload path (feature-plans/frontrunner-builder.md).

Pipeline (per symphony): detect (``frontrunner_detector``) -> gather Atlas
patterns (``atlas_cache`` / ``community_strats``, 7-day cache) -> generate a
candidate overlay via **Fable** (``claude-fable-5``) -> compile
(``plan_tree_compiler``) -> splice into the incumbent symphony -> independently
re-backtest incumbent AND candidate (``composer_backtest_client``) -> gate
(``backtest_gate_engine.evaluate_candidate_batch`` — mandatory, never bypassed)
-> Calmar acceptance -> queue for operator approval.

NO-AUTO-TRADE BOUNDARY (structural)
------------------------------------
This module NEVER calls ``composer_draft_client.save_symphony`` from the
unattended build/run path — only the operator-driven approval route may do
that (see ``advisors/frontrunner_proposals.py`` / the ``/approve`` route, once
wired). It does not implement, import, or reference ``invest_in_symphony`` or
any ``/deploy/`` endpoint. This is enforced by
``tests/security/test_frontrunner_no_trade_boundary.py``'s adversarial source
scans.

Public surface
--------------
GenerationResult : dataclass
    Returned by ``generate_candidate_overlay``. Fields: ``candidate`` (dict |
    None — the accepted DSL overlay node), ``error`` (str | None), and
    ``compiled_tree`` (dict | None — populated when the candidate compiles
    clean via ``plan_tree_compiler``).

generate_candidate_overlay(signal_context: dict, *, n_attempts=...) -> GenerationResult
    Calls Fable (tool-use) to compose a candidate frontrunner overlay DSL node,
    enforces AC-4's post-generation hard constraints (>=1 VIX-family ticker;
    mergeable flat RSI-gt rungs collapsed to any/all; scale-in tiers
    preserved), and compiles it. Never raises (D-1) — degrades to
    ``.candidate=None, .error=<reason>``.

splice_candidate_into_symphony(incumbent_symphony, incumbent_cascade, candidate) -> dict | None
    Replace the detected incumbent cascade subtree with the compiled candidate
    overlay inside a full copy of the incumbent symphony, re-validating via
    ``symphony_schema.validate_tree``. Returns None on any structural failure
    (never raises).

run_frontrunner_build(symphony_ids: list[str] | None = None) -> None
    D-1 never-raises entry point for the scheduler/route: detect -> generate
    -> splice -> INDEPENDENTLY re-backtest both incumbent and candidate ->
    gate (backtest_gate_engine.evaluate_candidate_batch, mandatory, never
    bypassed) -> Calmar-accept (frontrunner_acceptance) -> queue for
    approval, for each live symphony. A candidate that fails EITHER the gate
    or Calmar acceptance is rejected and never reaches the approval queue
    (AC-6/7). NEVER calls composer_draft_client.save_symphony.

Design constraints
------------------
- D-1 contract: reason strings carry ONLY type(exc).__name__ — no key/path/message leak.
- SDK lazy-import + factory seam mirrors build_plan_generator._build_client
  exactly, so tests patch this module's own ``_build_client``.
- Advisory-only until operator approval: no LIVE_EXECUTION, no
  _SETTINGS_WRITE_ALLOWLIST, no Composer write call from this module's own
  generation/build path.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from dataclasses import dataclass

from advisors import plan_tree_compiler, symphony_schema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named constants — no magic numbers.
# ---------------------------------------------------------------------------

# Model used for candidate generation — operator directive (feature plan
# Decisions table: "Generation via Fable").
FABLE_MODEL: str = "claude-fable-5"

# Maximum output tokens for the candidate-overlay SDK call. A single overlay
# (even a multi-tier scale-in cascade) is a small DSL fragment compared to
# build_plan_generator's full-symphony plans, so this is deliberately smaller
# than MAX_OUTPUT_TOKENS there.
MAX_OUTPUT_TOKENS: int = 8192

# Bounded retry limit for SDK calls that return stop_reason="max_tokens" or a
# rejected/degenerate candidate (AC-11: "reject + bounded retry").
MAX_GENERATION_ATTEMPTS: int = 3

# VIX-family tickers recognized as hedge/fire-basket instruments — same
# vocabulary as frontrunner_detector.VIX_FAMILY_TICKERS (imported, not
# duplicated, so the two modules can never drift on this list).
from advisors.frontrunner_detector import VIX_FAMILY_TICKERS  # noqa: E402

_PCT_PLACEHOLDER = "%"

# AC-6: DoF-ledger spec_bundle_id sentinel for frontrunner-builder search-breadth
# rows. researcher_dof_ledger.spec_bundle_id is a SOFT FK ("soft FK to
# spec_bundles.bundle_hash", migrations/018_researcher_dof_ledger.sql:33 — no
# SQL FOREIGN KEY constraint, enforcement is app-level only per
# database.insert_dof_ledger_row's own docstring). A distinct sentinel string
# (never a real theory spec_bundle_id) keeps these rows OUT of any future
# bundle-scoped count_dof_backtest_selections(spec_bundle_id=...) read, and out
# of the autotuner's own global (spec_bundle_id=None) N_effective haircut —
# the frontrunner builder's search breadth is a distinct search process from
# the per-symphony Optuna walk-forward and must never inflate its FDR bar.
_DOF_LEDGER_SPEC_BUNDLE_SENTINEL: str = "frontrunner_builder"

# AC-6/7: acceptance_gate's Stage-2 discretionary panel (backtest_gate_engine's
# _compute_parameter_stability_score / _compute_prior_anchor_score) was designed
# to compare an Optuna-tuned candidate's PARAMETER VECTOR against the incumbent's
# — a frontrunner tree-splice candidate has no such vector. Passing empty dicts
# is structurally disadvantageous, not neutral: backtest_gate_engine.py's own
# inc_stability is hardcoded to 1.0 ("the incumbent is, by definition, stable
# against itself") while an empty candidate_params/incumbent_params pair falls
# back to the 0.5 neutral-prior short-circuit — giving incumbent_panel_score
# (0.75) a floor the candidate (0.5) can never clear (verified via a direct
# evaluate_candidate_batch probe: vetoes_passed=True, panel_score stuck at 0.5,
# decision=KEEP_INCUMBENT regardless of how large the OOS-alpha gap is).
# Passing an IDENTICAL non-empty dict for candidate_params/incumbent_params/
# theory_prior_params makes every parameter-distance sub-score resolve to a
# perfect match (1.0/1.0 on both sides) — a genuine, honest TIE — so the panel
# becomes a neutral pass-through for tree-splice candidates instead of an
# unwinnable brake, while the REAL vetoes (BHY/FDR significance, PBO,
# OOS-alpha-beats-both-baselines) remain fully load-bearing and unaffected.
_TREE_SPLICE_PANEL_PARAMS_SENTINEL: dict = {"tree_splice_candidate": 1.0}


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass
class GenerationResult:
    """Result of ``generate_candidate_overlay``. Never None.

    Fields
    ------
    candidate : dict | None
        The accepted build-plan-DSL overlay node (kind='if'/'if_compound'),
        or None if rejected/failed.
    error : str | None
        Reason string on rejection/failure (D-1: type(exc).__name__ on an
        internal error). None on success.
    compiled_tree : dict | None
        The compiled Composer tree for ``candidate`` (via
        ``plan_tree_compiler.compile_plan``), when compilation succeeded.
        None if not yet compiled or compilation failed.
    """

    candidate: dict | None = None
    error: str | None = None
    compiled_tree: dict | None = None


# ---------------------------------------------------------------------------
# SDK client factory seam — mirrors build_plan_generator._build_client exactly.
# ---------------------------------------------------------------------------


def _build_client():
    """Construct the Anthropic SDK client for Fable candidate generation.

    Factory seam: tests patch ``advisors.frontrunner_builder._build_client``.
    Mirrors build_plan_generator._build_client (advisors/build_plan_generator.py:153).

    Raises
    ------
    RuntimeError
        If ANTHROPIC_API_KEY is absent or the anthropic SDK is not installed.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — the frontrunner builder is "
            "unavailable until an API key is configured."
        )
    try:
        import anthropic  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - SDK is a declared dep
        raise RuntimeError(f"the anthropic SDK is not installed: {exc}") from exc
    return anthropic.Anthropic(api_key=api_key)


# ---------------------------------------------------------------------------
# DSL walk helpers — overlay-node ticker extraction (mirrors
# build_plan_generator.plan_tickers' walk, scoped to a bare overlay node
# rather than a full {plan_id, ..., root} envelope).
# ---------------------------------------------------------------------------


def _walk_overlay_tickers(node, out: set[str] | None = None) -> set[str]:
    """Collect every ticker referenced anywhere in a DSL overlay node.

    Handles asset/weight/group/filter/if/if_compound kinds (same DSL grammar
    as build_plan_generator.plan_tickers). Never raises — malformed input
    degrades to an empty/partial set.
    """
    if out is None:
        out = set()
    try:
        if not isinstance(node, dict):
            return out
        kind = node.get("kind")

        if kind == "asset":
            t = node.get("ticker")
            if t and t != _PCT_PLACEHOLDER:
                out.add(t)

        elif kind == "weight":
            scheme = node.get("scheme")
            if scheme == "specified":
                for entry in node.get("children", []) or []:
                    if isinstance(entry, dict) and "node" in entry:
                        _walk_overlay_tickers(entry["node"], out)
            else:
                for child in node.get("children", []) or []:
                    _walk_overlay_tickers(child, out)

        elif kind in ("group", "filter"):
            for child in node.get("children", []) or []:
                _walk_overlay_tickers(child, out)

        elif kind == "if":
            cond = node.get("condition") or {}
            if isinstance(cond, dict):
                lhs_t = cond.get("lhs_ticker")
                if lhs_t and lhs_t != _PCT_PLACEHOLDER:
                    out.add(lhs_t)
                rhs = cond.get("rhs") or {}
                if isinstance(rhs, dict):
                    rhs_t = rhs.get("ticker")
                    if rhs_t and rhs_t != _PCT_PLACEHOLDER:
                        out.add(rhs_t)
            for child in node.get("then", []) or []:
                _walk_overlay_tickers(child, out)
            for child in node.get("else", []) or []:
                _walk_overlay_tickers(child, out)

        elif kind == "if_compound":
            cond = node.get("condition") or {}
            _walk_condition_tickers(cond, out)
            for child in node.get("then", []) or []:
                _walk_overlay_tickers(child, out)
            for child in node.get("else", []) or []:
                _walk_overlay_tickers(child, out)
    except Exception:  # pragma: no cover - defensive; never raises
        logger.debug("_walk_overlay_tickers: unexpected error", exc_info=True)
    return out


def _walk_condition_tickers(cond: dict, out: set[str]) -> None:
    if not isinstance(cond, dict):
        return
    ctype = cond.get("type")
    if ctype == "binary":
        t = cond.get("lhs_ticker")
        if t and t != _PCT_PLACEHOLDER:
            out.add(t)
        rhs = cond.get("rhs") or {}
        if isinstance(rhs, dict):
            t = rhs.get("ticker")
            if t and t != _PCT_PLACEHOLDER:
                out.add(t)
    elif ctype == "binary_compound":
        for t in cond.get("tickers", []) or []:
            if t and t != _PCT_PLACEHOLDER:
                out.add(t)
    elif ctype == "compound":
        for sub in cond.get("conditions", []) or []:
            _walk_condition_tickers(sub, out)


def _fire_branch_tickers(node: dict) -> set[str]:
    """Return the tickers referenced in an overlay if/if_compound node's
    fire (then) branch specifically — the branch the AC-4a VIX-presence check
    is scoped to (not the else/continuation side, which legitimately carries
    core placeholder content in test fixtures / real trees)."""
    out: set[str] = set()
    then_children = node.get("then") or []
    for child in then_children:
        _walk_overlay_tickers(child, out)
    return out


# ---------------------------------------------------------------------------
# AC-4(a): post-generation VIX-presence enforcement
# ---------------------------------------------------------------------------


def _has_vix_ticker_in_fire_branch(node: dict) -> bool:
    """Return True if the overlay's fire (then) branch contains >=1 VIX-family
    ticker anywhere (AC-4a). Recurses into a nested tier's own then-branch too
    (a multi-tier candidate's OUTER then may itself be a nested if firing
    the VIX ticker only at a deeper tier)."""
    if not isinstance(node, dict):
        return False
    fire_tickers = _fire_branch_tickers(node)
    if fire_tickers & VIX_FAMILY_TICKERS:
        return True
    # Recurse: a tiered candidate's then-branch may itself be another if/
    # if_compound node whose OWN then-branch is where the VIX ticker lives.
    for child in node.get("then") or []:
        if isinstance(child, dict) and child.get("kind") in ("if", "if_compound"):
            if _has_vix_ticker_in_fire_branch(child):
                return True
    return False


# ---------------------------------------------------------------------------
# AC-4(c): collapse mergeable flat RSI-gt rungs into a binary-compound
# ---------------------------------------------------------------------------


def _condition_signature(cond: dict) -> tuple | None:
    """Return a hashable signature (fn, comparator, rhs_fixed) for a flat
    'binary' condition dict, or None if not a mergeable flat condition.

    Two flat if-nodes are mergeable when they share this signature but watch
    DIFFERENT tickers and fire the SAME then/else content — collapsing them
    into one binary-compound broadcast (AC-4c) is equivalent and removes
    hundreds of structurally-identical rungs.
    """
    if not isinstance(cond, dict):
        return None
    if "lhs_fn" not in cond or "lhs_ticker" not in cond:
        return None
    rhs = cond.get("rhs") or {}
    if "fixed" not in rhs:
        return None
    return (cond.get("lhs_fn"), cond.get("comparator"), rhs.get("fixed"), cond.get("window"))


def _same_fire_content(a: list, b: list) -> bool:
    """Return True if two then/else child lists are structurally identical
    (ignoring volatile per-node ids, which this DSL doesn't carry anyway)."""
    try:
        return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return a == b


def _collect_mergeable_chain(node: dict) -> tuple[list[dict], list, list] | None:
    """Walk a chain of NESTED flat 'if' nodes (each one's else being the next)
    that share an identical (fn, comparator, rhs_fixed, window) signature and
    an identical fire/continuation payload, differing ONLY in lhs_ticker.

    Returns (list_of_conditions, then_children, terminal_else_children) if a
    mergeable chain of length >= 2 is found starting at `node`, else None.
    Never raises.
    """
    if not isinstance(node, dict) or node.get("kind") != "if":
        return None
    root_cond = node.get("condition") or {}
    sig = _condition_signature(root_cond)
    if sig is None:
        return None

    conditions = [root_cond]
    then_children = node.get("then") or []
    current_else = node.get("else") or []

    while True:
        if len(current_else) != 1:
            break
        next_node = current_else[0]
        if not isinstance(next_node, dict) or next_node.get("kind") != "if":
            break
        next_cond = next_node.get("condition") or {}
        if _condition_signature(next_cond) != sig:
            break
        next_then = next_node.get("then") or []
        if not _same_fire_content(next_then, then_children):
            break
        conditions.append(next_cond)
        current_else = next_node.get("else") or []

    if len(conditions) < 2:
        return None
    return conditions, then_children, current_else


def _collapse_mergeable_rungs(node: dict) -> dict:
    """Recursively collapse mergeable flat RSI-gt rung CHAINS into a single
    if_compound node with a binary_compound (any) condition (AC-4c).

    A chain qualifies when >=2 consecutive nested 'if' nodes share the exact
    same (fn, comparator, rhs_fixed, window) signature and fire IDENTICAL
    then-content, differing only in the watched ticker — this is precisely
    the "hundreds of flat RSI-gt rungs, collapsible to any/all" shape from the
    feature plan's grounding note. A genuine SCALE-IN tier (different
    threshold or different fire content at each level) never matches this
    signature and is therefore never touched — preserving AC-4d.
    """
    if not isinstance(node, dict):
        return node

    if node.get("kind") == "if":
        chain = _collect_mergeable_chain(node)
        if chain is not None:
            conditions, then_children, terminal_else = chain
            tickers = [c.get("lhs_ticker") for c in conditions]
            fn = conditions[0].get("lhs_fn")
            comparator = conditions[0].get("comparator")
            window = conditions[0].get("window")
            rhs_fixed = conditions[0].get("rhs", {}).get("fixed")
            return {
                "kind": "if_compound",
                "condition": {
                    "type": "binary_compound",
                    "fn": fn,
                    "tickers": tickers,
                    "comparator": comparator,
                    "rhs": {"const": rhs_fixed},
                    "window": window,
                    "operator": "any",
                },
                "then": [_collapse_mergeable_rungs(c) for c in then_children],
                "else": [_collapse_mergeable_rungs(c) for c in terminal_else],
            }
        # Not a mergeable chain root — recurse into then/else independently
        # (a scale-in tier's nested if is preserved as-is; AC-4d).
        return {
            **{k: v for k, v in node.items() if k not in ("then", "else")},
            "then": [_collapse_mergeable_rungs(c) for c in node.get("then") or []],
            "else": [_collapse_mergeable_rungs(c) for c in node.get("else") or []],
        }

    if node.get("kind") == "if_compound":
        return {
            **{k: v for k, v in node.items() if k not in ("then", "else")},
            "then": [_collapse_mergeable_rungs(c) for c in node.get("then") or []],
            "else": [_collapse_mergeable_rungs(c) for c in node.get("else") or []],
        }

    if node.get("kind") in ("group", "filter"):
        return {
            **{k: v for k, v in node.items() if k != "children"},
            "children": [_collapse_mergeable_rungs(c) for c in node.get("children") or []],
        }

    if node.get("kind") == "weight":
        if node.get("scheme") == "specified":
            return {
                **{k: v for k, v in node.items() if k != "children"},
                "children": [
                    {**entry, "node": _collapse_mergeable_rungs(entry.get("node"))}
                    for entry in node.get("children") or []
                    if isinstance(entry, dict)
                ],
            }
        return {
            **{k: v for k, v in node.items() if k != "children"},
            "children": [_collapse_mergeable_rungs(c) for c in node.get("children") or []],
        }

    return copy.deepcopy(node)


# ---------------------------------------------------------------------------
# Anthropic tool definition for structured overlay emission
# ---------------------------------------------------------------------------

_EMIT_OVERLAY_TOOL = {
    "name": "emit_frontrunner_overlay",
    "description": (
        "Emit a single candidate frontrunner overlay: a leading RSI-overbought "
        "-> VIX/hedge-basket cascade node conforming to the Planet Stopper "
        "build-plan DSL. The node's kind is 'if' (flat condition) or "
        "'if_compound' (compound condition)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "overlay": {
                "type": "object",
                "description": "A single build-plan-DSL 'if'/'if_compound' NODE.",
            }
        },
        "required": ["overlay"],
    },
}


def _build_generation_prompt(signal_context: dict) -> str:
    """Build the SDK prompt for candidate overlay generation.

    Embeds the frontrunner-specific DSL grammar reminder + the AC-4 hard
    constraints + the caller-supplied signal_context (watched tickers /
    Atlas-derived patterns) so Fable has concrete grounding.
    """
    watched = signal_context.get("watched_tickers") or []
    atlas_patterns = signal_context.get("atlas_patterns") or []
    watched_hint = ", ".join(str(t) for t in watched[:20]) or "(none supplied)"
    atlas_hint = json.dumps(atlas_patterns[:5]) if atlas_patterns else "(none supplied)"

    return (
        "You are designing a FRONTRUNNER overlay for a Composer trading symphony: "
        "a leading cascade of RSI-overbought if-nodes that, when triggered, fire a "
        "small VIX/hedge basket ahead of the symphony's core strategy.\n\n"
        "HARD REQUIREMENTS (all mandatory):\n"
        "1. The fire (then) branch MUST contain at least one VIX-family ticker: "
        "VIXY, VIXM, UVXY, UVIX, VXX, SVXY, or SVIX.\n"
        "2. Vary the VIX instrument — do not default to VIXY every time.\n"
        "3. If you find yourself repeating an identical rung structure over many "
        "watched tickers at the same threshold, that is fine to emit as separate "
        "nested if-nodes — a downstream step collapses mergeable rungs.\n"
        "4. If you use a scale-in structure (a lower RSI threshold firing a light "
        "hedge blend, a higher nested threshold firing a heavier hedge), preserve "
        "it as TIERED nested if-nodes — never flatten multiple thresholds into a "
        "single OR condition.\n\n"
        f"Watched core signal tickers to consider: {watched_hint}\n"
        f"Atlas-derived frontrunner patterns for reference: {atlas_hint}\n\n"
        "Emit exactly ONE candidate overlay node using the emit_frontrunner_overlay "
        "tool. The node's 'kind' is 'if' (a flat RSI condition: lhs_fn, lhs_ticker, "
        "window, comparator, rhs:{fixed: number}) or 'if_compound' (condition: "
        "{type: binary/binary_compound/compound, ...}). 'then' fires the hedge "
        "basket; 'else' continues toward the core (use a placeholder asset there)."
    )


# ---------------------------------------------------------------------------
# generate_candidate_overlay — AC-4/AC-5
# ---------------------------------------------------------------------------


def generate_candidate_overlay(
    signal_context: dict,
    *,
    n_attempts: int = MAX_GENERATION_ATTEMPTS,
) -> GenerationResult:
    """Generate one candidate frontrunner overlay via Fable (tool-use).

    Parameters
    ----------
    signal_context : dict
        Caller-supplied context: at minimum ``watched_tickers`` (list of core
        signal tickers observed in the incumbent cascade), optionally
        ``atlas_patterns`` (patterns extracted from the Atlas frontrunner
        corpus, feature plan AC-3).
    n_attempts : int
        Bounded retry count — a rejected/degenerate candidate (no VIX ticker,
        truncated response) is retried up to this many times before giving up
        (AC-11: "reject + bounded retry").

    Returns
    -------
    GenerationResult
        ``.candidate`` populated + ``.error=None`` on success (with AC-4c
        collapsing already applied and ``.compiled_tree`` set when
        compilation succeeds). ``.candidate=None`` + ``.error=<reason>`` on
        rejection or failure. Never raises (D-1).
    """
    try:
        client = _build_client()
        prompt = _build_generation_prompt(signal_context)

        last_reason = "no attempts made"
        for attempt in range(n_attempts):
            response = client.messages.create(
                model=FABLE_MODEL,
                max_tokens=MAX_OUTPUT_TOKENS,
                tools=[_EMIT_OVERLAY_TOOL],
                tool_choice={"type": "tool", "name": "emit_frontrunner_overlay"},
                messages=[{"role": "user", "content": prompt}],
            )

            if getattr(response, "stop_reason", None) == "max_tokens":
                last_reason = "max_tokens: response truncated"
                logger.warning(
                    "generate_candidate_overlay: truncated on attempt %d/%d",
                    attempt + 1,
                    n_attempts,
                )
                continue

            tool_block = None
            for block in response.content or []:
                if getattr(block, "type", None) == "tool_use":
                    tool_block = block
                    break
            if tool_block is None:
                last_reason = "NoToolUseBlock"
                continue

            overlay = (
                tool_block.input.get("overlay") if isinstance(tool_block.input, dict) else None
            )
            if not isinstance(overlay, dict):
                last_reason = "InvalidToolUsePayload"
                continue

            # AC-4a: post-generation VIX-presence enforcement — never trust
            # the model's raw output.
            if not _has_vix_ticker_in_fire_branch(overlay):
                last_reason = "candidate fire branch contains no VIX-family ticker"
                logger.warning(
                    "generate_candidate_overlay: rejected (no VIX ticker) on "
                    "attempt %d/%d",
                    attempt + 1,
                    n_attempts,
                )
                continue

            # AC-4c: collapse mergeable flat RSI-gt rungs. AC-4d (scale-in
            # tiers) is preserved automatically — _collapse_mergeable_rungs
            # only touches chains sharing an identical signature+fire-content,
            # which a genuine tiered escalation never does.
            collapsed = _collapse_mergeable_rungs(copy.deepcopy(overlay))

            # AC-5: compile via the real plan_tree_compiler (the only public
            # entry point).
            plan_envelope = {
                "plan_id": "frontrunner-candidate",
                "objective": "cut_drawdown",
                "name": "Frontrunner Candidate",
                "rebalance": "daily",
                "root": collapsed,
            }
            compile_result = plan_tree_compiler.compile_plan(plan_envelope)

            return GenerationResult(
                candidate=collapsed,
                error=None,
                compiled_tree=compile_result.tree,
            )

        return GenerationResult(candidate=None, error=last_reason)

    except Exception as exc:
        # D-1: reason contains ONLY the exception class name.
        logger.debug("generate_candidate_overlay: error", exc_info=True)
        return GenerationResult(candidate=None, error=type(exc).__name__)


# ---------------------------------------------------------------------------
# splice_candidate_into_symphony — AC-5
# ---------------------------------------------------------------------------


def _find_node_by_id(tree: dict, target_id: str):
    """Iterative DFS returning the Composer node dict with id==target_id, or None."""
    if not isinstance(tree, dict):
        return None
    stack = [tree]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("id") == target_id:
            return node
        for child in node.get("children") or []:
            stack.append(child)
    return None


def _replace_node_by_id(tree: dict, target_id: str, replacement: dict) -> dict | None:
    """Return a deep-copied tree with the node whose id==target_id replaced by
    ``replacement``. Returns None if target_id is not found anywhere."""
    if not isinstance(tree, dict):
        return None

    def _walk(node):
        if not isinstance(node, dict):
            return node, False
        if node.get("id") == target_id:
            return copy.deepcopy(replacement), True
        found = False
        children = node.get("children")
        if isinstance(children, list):
            new_children = []
            for child in children:
                new_child, child_found = _walk(child)
                new_children.append(new_child)
                found = found or child_found
            new_node = {**node, "children": new_children}
            return new_node, found
        return copy.deepcopy(node), False

    new_tree, found = _walk(tree)
    return new_tree if found else None


def splice_candidate_into_symphony(
    incumbent_symphony: dict,
    incumbent_cascade,
    candidate: dict,
) -> dict | None:
    """Replace the detected incumbent cascade with the compiled candidate.

    Parameters
    ----------
    incumbent_symphony : dict
        The full Composer raw_value tree containing the incumbent cascade.
    incumbent_cascade : frontrunner_detector.Cascade
        The detection result identifying which subtree (by node id, taken
        from ``incumbent_cascade.overlay_tree``) to replace.
    candidate : dict
        The candidate overlay — either a raw build-plan-DSL node (compiled
        here via plan_tree_compiler) or an already-compiled Composer tree
        node (detected by the presence of a 'step' key rather than 'kind').

    Returns
    -------
    dict | None
        The full spliced symphony (validated via symphony_schema.validate_tree
        internally, and returned ONLY if that validation is clean), or None
        on any failure (node not found, compile failure, validate_tree
        errors). Never raises (D-1).
    """
    try:
        target_id = incumbent_cascade.overlay_tree.get("id") if isinstance(
            incumbent_cascade.overlay_tree, dict
        ) else None
        if not target_id:
            logger.warning("splice_candidate_into_symphony: incumbent cascade has no id")
            return None

        # Compile the candidate if it's still a raw DSL node (kind=...);
        # accept an already-compiled Composer node (step=...) as-is.
        if isinstance(candidate, dict) and "kind" in candidate:
            plan_envelope = {
                "plan_id": "frontrunner-splice-candidate",
                "objective": "cut_drawdown",
                "name": "Frontrunner Splice Candidate",
                "rebalance": "daily",
                "root": candidate,
            }
            compile_result = plan_tree_compiler.compile_plan(plan_envelope)
            if compile_result.tree is None:
                logger.warning(
                    "splice_candidate_into_symphony: candidate failed to compile (%s)",
                    compile_result.reason,
                )
                return None
            # compile_plan wraps the compiled node in a root; unwrap the
            # single top-level child to get the raw compiled if/if_compound
            # node suitable for splicing in place of the incumbent cascade.
            compiled_root = compile_result.tree
            compiled_children = compiled_root.get("children") or []
            if len(compiled_children) != 1:
                logger.warning(
                    "splice_candidate_into_symphony: unexpected compiled root shape"
                )
                return None
            compiled_node = compiled_children[0]
        elif isinstance(candidate, dict) and "step" in candidate:
            compiled_node = candidate
        else:
            logger.warning("splice_candidate_into_symphony: candidate is not a valid node")
            return None

        spliced = _replace_node_by_id(incumbent_symphony, target_id, compiled_node)
        if spliced is None:
            logger.warning(
                "splice_candidate_into_symphony: incumbent cascade node id %r not found",
                target_id,
            )
            return None

        errors = symphony_schema.validate_tree(spliced)
        if errors:
            logger.warning(
                "splice_candidate_into_symphony: spliced tree failed validate_tree: %s",
                errors,
            )
            return None

        return spliced

    except Exception as exc:
        logger.debug("splice_candidate_into_symphony: unexpected error", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# run_frontrunner_build — the scheduler/route entrypoint (AC-1)
# ---------------------------------------------------------------------------


def run_frontrunner_build(symphony_ids: list[str] | None = None) -> None:
    """Run the frontrunner build over all (or the given) live symphonies.

    D-1 never-raises entry point for the weekly scheduler hook and the
    on-demand route. This function NEVER calls
    ``composer_draft_client.save_symphony`` — accepted candidates are queued
    for operator approval only; the actual Composer create happens exclusively
    on the operator-driven approval route.

    Parameters
    ----------
    symphony_ids : list[str] | None
        When None (the default), resolves the roster from live bot_state at
        run time (AC-1). When supplied, restricts the build to those symphony
        ids (used by the on-demand route / tests).

    Never raises (D-1). A per-symphony failure is logged and skipped; it does
    not abort the batch.
    """
    try:
        roster = symphony_ids if symphony_ids is not None else _resolve_live_symphony_roster()
    except Exception as exc:
        logger.error("run_frontrunner_build: roster resolution failed (%s)", type(exc).__name__)
        return

    for symphony_id in roster:
        try:
            _run_build_for_symphony(symphony_id)
        except Exception as exc:
            logger.warning(
                "run_frontrunner_build: symphony_id=%s failed (%s)",
                symphony_id,
                type(exc).__name__,
            )
            continue


def _resolve_live_symphony_roster() -> list[str]:
    """Resolve the live symphony roster from bot_state at run time (AC-1).

    Never raises — degrades to an empty roster on any failure.
    """
    try:
        import database  # noqa: PLC0415 - CC-2 lazy; state-DB read only

        bot_state = database.load_state()
        if not isinstance(bot_state, dict):
            return []
        return [k for k, v in bot_state.items() if isinstance(v, dict)]
    except Exception:
        logger.debug("_resolve_live_symphony_roster: unexpected error", exc_info=True)
        return []


def _count_tree_nodes(node) -> int:
    """Total node count of a Composer raw_value tree (for the Calmar
    acceptance gate's node_count_delta / material-simplification signal)."""
    if not isinstance(node, dict):
        return 0
    return 1 + sum(_count_tree_nodes(c) for c in node.get("children") or [])


def _run_build_for_symphony(symphony_id: str) -> None:
    """Detect -> generate -> splice -> INDEPENDENTLY re-backtest BOTH incumbent
    and candidate -> gate (evaluate_candidate_batch, mandatory) -> Calmar
    accept -> queue for approval, for ONE symphony.

    AC-6 (non-negotiable): a candidate reaches the approval queue ONLY if it
    (a) independently re-backtests cleanly, (b) is the BHY/FDR-gate survivor
    of a batch containing BOTH the incumbent and the candidate (never trusts
    the incumbent's own pre-computed oos_metrics — both sides are backtested
    fresh here), AND (c) passes the Calmar acceptance gate
    (frontrunner_acceptance.evaluate_calmar_acceptance). An un-gated or
    un-accepted candidate is REJECTED and never queued — this function does
    not call composer_draft_client.save_symphony; only
    approve_frontrunner_proposal (the operator-driven approval route) does
    that.

    Never raises (D-1); any step's failure degrades to a logged skip for this
    symphony/candidate only — it never aborts the batch.
    """
    from advisors import frontrunner_detector  # noqa: PLC0415 - CC-2 lazy

    try:
        import symphony_logic  # noqa: PLC0415 - CC-2 lazy

        tree = symphony_logic.fetch_symphony_score(symphony_id)
    except Exception as exc:
        logger.info(
            "_run_build_for_symphony: symphony_id=%s fetch failed (%s) — skipping",
            symphony_id,
            type(exc).__name__,
        )
        return

    if not isinstance(tree, dict):
        logger.info(
            "_run_build_for_symphony: symphony_id=%s returned no tree — skipping", symphony_id
        )
        return

    detection = frontrunner_detector.detect_frontrunner_cascades(tree)
    if not detection.cascades:
        logger.info(
            "_run_build_for_symphony: symphony_id=%s no incumbent frontrunner (%s) — skipping",
            symphony_id,
            detection.skip_reason,
        )
        return

    for cascade in detection.cascades:
        watched_tickers = sorted(
            _walk_overlay_tickers(cascade.overlay_tree) if "kind" in cascade.overlay_tree else []
        )
        signal_context = {"watched_tickers": watched_tickers}
        result = generate_candidate_overlay(signal_context)
        if result.candidate is None:
            logger.info(
                "_run_build_for_symphony: symphony_id=%s candidate generation failed (%s)",
                symphony_id,
                result.error,
            )
            continue

        spliced = splice_candidate_into_symphony(tree, cascade, result.candidate)
        if spliced is None:
            logger.info(
                "_run_build_for_symphony: symphony_id=%s splice failed — skipping candidate",
                symphony_id,
            )
            continue

        accepted, metrics = _gate_and_accept_candidate(
            symphony_id=symphony_id,
            incumbent_tree=tree,
            candidate_tree=spliced,
        )
        if not accepted:
            logger.info(
                "_run_build_for_symphony: symphony_id=%s candidate rejected by "
                "gate/acceptance (%s) — not queued",
                symphony_id,
                metrics.get("reject_reason"),
            )
            # AC-11 "fails gates or fails to improve Calmar → rejected item w/
            # reason+deltas": record an audit observation whenever real
            # comparison data exists (a gate or Calmar reject — signaled by
            # "candidate_cagr" being present). A pre-gate backtest failure has
            # no valid deltas to report (metrics carries only reject_reason),
            # so it stays a log-only skip — nothing to persist a reason
            # against beyond the log line, mirroring the no-incumbent /
            # generation-exhausted skips just above.
            if "candidate_cagr" in metrics:
                try:
                    import database  # noqa: PLC0415 - CC-2 lazy

                    database.insert_advisor_observation(
                        advisor_role="FRONTRUNNER_BUILDER",
                        subject_type="frontrunner_candidate",
                        subject_id=symphony_id,
                        verdict=metrics.get("reject_reason", "rejected"),
                        raw_response=metrics,
                        symphony_id=symphony_id,
                    )
                except Exception:
                    logger.debug(
                        "_run_build_for_symphony: failed to record rejected-candidate "
                        "observation — proceeding (never blocks the build loop)",
                        exc_info=True,
                    )
            continue

        try:
            import database  # noqa: PLC0415 - CC-2 lazy

            database.insert_frontrunner_proposal(
                symphony_id=symphony_id,
                proposal_source="frontrunner_builder",
                candidate_tree=spliced,
                metrics_json=metrics,
            )
            logger.info(
                "_run_build_for_symphony: symphony_id=%s candidate queued for approval",
                symphony_id,
            )
        except Exception as exc:
            logger.warning(
                "_run_build_for_symphony: symphony_id=%s failed to queue proposal (%s)",
                symphony_id,
                type(exc).__name__,
            )


def _gate_and_accept_candidate(
    *,
    symphony_id: str,
    incumbent_tree: dict,
    candidate_tree: dict,
) -> tuple[bool, dict]:
    """AC-6/AC-7: independently re-backtest BOTH incumbent and candidate,
    run them through evaluate_candidate_batch (mandatory, never bypassed),
    and — only for the candidate if it's the gate's ADOPT_CANDIDATE survivor
    — apply the Calmar acceptance gate.

    Returns (accepted, metrics). On acceptance, metrics is a dict suitable
    for frontrunner_proposals.metrics_json (incumbent-vs-candidate Calmar/
    CAGR/MDD/node-count deltas, AC-8). On a gate or Calmar rejection, metrics
    carries 'reject_reason' PLUS the same incumbent-vs-candidate CAGR/MDD/
    node-count deltas (AC-11: "rejected item w/ reason+deltas") — the caller
    uses their presence (e.g. "candidate_cagr" in metrics) to decide whether
    a rejected-candidate audit record is persistable. A pre-gate failure
    (either backtest call erroring) has no valid comparison data yet, so its
    metrics dict carries ONLY 'reject_reason' — same for the catch-all
    unexpected-error path. Never raises (D-1).
    """
    try:
        from advisors.backtest_gate_engine import BacktestCandidate, evaluate_candidate_batch
        from advisors.composer_backtest_client import run_backtest
        from advisors.frontrunner_acceptance import evaluate_calmar_acceptance
        from analytics import compute_quantstats_metrics

        # Independent re-backtest of BOTH sides — never trust the incumbent's
        # own pre-computed oos_metrics (AC-3/AC-6).
        incumbent_bt = run_backtest(incumbent_tree, symphony_id=symphony_id)
        if incumbent_bt.error or not incumbent_bt.daily_returns:
            return False, {"reject_reason": f"incumbent backtest failed: {incumbent_bt.error}"}

        candidate_bt = run_backtest(candidate_tree, symphony_id=symphony_id)
        if candidate_bt.error or not candidate_bt.daily_returns:
            return False, {"reject_reason": f"candidate backtest failed: {candidate_bt.error}"}

        incumbent_returns_pct = [r * 100.0 for r in incumbent_bt.daily_returns.values()]
        candidate_returns_pct = [r * 100.0 for r in candidate_bt.daily_returns.values()]
        candidate_dated_pct = {d: r * 100.0 for d, r in candidate_bt.daily_returns.items()}

        incumbent_metrics = compute_quantstats_metrics(incumbent_returns_pct)
        candidate_metrics = compute_quantstats_metrics(candidate_returns_pct)

        # The candidate alone goes through the mandatory gate — AC-6's
        # "independently re-backtested ... run through
        # backtest_gate_engine.evaluate_candidate_batch (mandatory attach
        # point)". The incumbent's own FRESH backtest (never its stored
        # oos_metrics) supplies incumbent_oos_alpha as the scalar KEEP_INCUMBENT
        # baseline — the same shape as the established
        # strategy_builder_engine.propose_strategies call (bt_candidates holds
        # only the proposed candidate(s); the incumbent is never a batch
        # member). Putting the incumbent INTO the batch would make it compete
        # with the candidate for the single BHY-winner slot instead of serving
        # as the baseline — a distinct bug, not this gate's intended usage.
        incumbent_oos_alpha = sum(incumbent_returns_pct)
        bt_candidates = [
            BacktestCandidate(
                candidate_id="candidate",
                daily_returns_pct=candidate_returns_pct,
                # Identical non-empty params on both sides — see
                # _TREE_SPLICE_PANEL_PARAMS_SENTINEL's module-level comment: a
                # tree-splice candidate has no real parameter vector, so this
                # makes the discretionary panel a neutral tie rather than a
                # structurally-unwinnable brake.
                candidate_params=_TREE_SPLICE_PANEL_PARAMS_SENTINEL,
                incumbent_params=_TREE_SPLICE_PANEL_PARAMS_SENTINEL,
                theory_prior_params=_TREE_SPLICE_PANEL_PARAMS_SENTINEL,
                dated_returns=candidate_dated_pct,
            ),
        ]
        # Node counts (AC-8's "node-count deltas") are needed on EVERY return
        # path — including a gate/Calmar reject — so AC-11's rejected-item
        # deltas can be persisted. Computed once, up front, and reused below.
        incumbent_node_count = _count_tree_nodes(incumbent_tree)
        candidate_node_count = _count_tree_nodes(candidate_tree)

        gated_batch = evaluate_candidate_batch(
            bt_candidates,
            incumbent_oos_alpha=incumbent_oos_alpha,
            default_oos_alpha=incumbent_oos_alpha,
        )

        # AC-6: record this candidate's search breadth to the DoF ledger — a
        # backtest-selection search happened (the candidate was independently
        # backtested and put through the gate) regardless of the gate's
        # verdict. Never lets a ledger-write failure affect the accept/reject
        # decision (D-1) — logged and swallowed on its own.
        try:
            import database  # noqa: PLC0415 - CC-2 lazy

            database.insert_dof_ledger_row(
                facet_name="frontrunner_candidate_search",
                facet_category="specification",
                decision_type="SEARCHED",
                evidence_source="BACKTEST_SELECTION",
                n_configs_searched=1,
                spec_bundle_id=_DOF_LEDGER_SPEC_BUNDLE_SENTINEL,
                justification=f"frontrunner_builder candidate search for symphony_id={symphony_id}",
            )
        except Exception:
            logger.debug(
                "_gate_and_accept_candidate: DoF ledger write failed — proceeding "
                "(never blocks the accept/reject decision)",
                exc_info=True,
            )

        candidate_gate_result = next(
            (r for r in gated_batch.results if r.candidate_id == "candidate"), None
        )
        if candidate_gate_result is None or candidate_gate_result.verdict.decision != "ADOPT_CANDIDATE":
            reason = (
                candidate_gate_result.rejection_reason
                if candidate_gate_result is not None
                else "gate_result_missing"
            )
            # AC-11 "fails gates ... → rejected item w/ reason+deltas": the
            # gate reject still carries the incumbent-vs-candidate deltas
            # (both backtests + quantstats already ran) so the caller can
            # persist an audit record, not just a log line.
            return False, {
                "reject_reason": f"gate rejected candidate: {reason}",
                "incumbent_cagr": incumbent_metrics.get("annualized_return"),
                "incumbent_max_drawdown": incumbent_metrics.get("max_drawdown"),
                "candidate_cagr": candidate_metrics.get("annualized_return"),
                "candidate_max_drawdown": candidate_metrics.get("max_drawdown"),
                "node_count_delta": candidate_node_count - incumbent_node_count,
            }

        # AC-7: Calmar acceptance — gate survival is necessary but not
        # sufficient; the candidate must ALSO improve or preserve+simplify.
        acceptance = evaluate_calmar_acceptance(
            incumbent_metrics,
            candidate_metrics,
            incumbent_node_count=incumbent_node_count,
            candidate_node_count=candidate_node_count,
        )
        if not acceptance.accepted:
            # AC-11 "fails to improve Calmar → rejected item w/ reason+deltas".
            return False, {
                "reject_reason": "calmar_acceptance_rejected",
                "incumbent_cagr": incumbent_metrics.get("annualized_return"),
                "incumbent_max_drawdown": incumbent_metrics.get("max_drawdown"),
                "incumbent_calmar": acceptance.incumbent_calmar,
                "candidate_cagr": candidate_metrics.get("annualized_return"),
                "candidate_max_drawdown": candidate_metrics.get("max_drawdown"),
                "candidate_calmar": acceptance.candidate_calmar,
                "node_count_delta": acceptance.node_count_delta,
            }

        metrics = {
            "incumbent_cagr": incumbent_metrics.get("annualized_return"),
            "incumbent_max_drawdown": incumbent_metrics.get("max_drawdown"),
            "incumbent_calmar": acceptance.incumbent_calmar,
            "candidate_cagr": candidate_metrics.get("annualized_return"),
            "candidate_max_drawdown": candidate_metrics.get("max_drawdown"),
            "candidate_calmar": acceptance.candidate_calmar,
            "candidate_sharpe": acceptance.candidate_sharpe,
            "candidate_volatility": acceptance.candidate_volatility,
            "node_count_delta": acceptance.node_count_delta,
            "tags": sorted(acceptance.tags),
        }
        return True, metrics

    except Exception as exc:
        logger.debug("_gate_and_accept_candidate: unexpected error", exc_info=True)
        return False, {"reject_reason": type(exc).__name__}


# ---------------------------------------------------------------------------
# approve_frontrunner_proposal — the ONLY path that may call
# composer_draft_client.save_symphony (AC-9). Never invoked from
# run_frontrunner_build / _run_build_for_symphony / the scheduler hook.
# ---------------------------------------------------------------------------


@dataclass
class ApprovalResult:
    """Result of ``approve_frontrunner_proposal``. Never None.

    Fields
    ------
    success : bool
        True only when the Composer create succeeded AND the created symphony
        verified as zero-allocation (undeployed).
    symphony_id : str | None
        The newly-created Composer symphony id, populated on success.
    error : str | None
        Reason string on failure (D-1: type(exc).__name__ on an internal
        error). None on success.
    """

    success: bool
    symphony_id: str | None = None
    error: str | None = None


def approve_frontrunner_proposal(proposal_id: int) -> ApprovalResult:
    """Operator-approved: create the candidate symphony in Composer as a NEW
    UNDEPLOYED symphony, verify zero-allocation, and mark the proposal
    'uploaded' (AC-9).

    This is the ONLY function in this module (and, by construction of
    ``composer_draft_client``, the only function in the whole frontrunner
    surface) that may call ``composer_draft_client.save_symphony``. It is
    invoked exclusively from the operator-driven ``/approve`` route — never
    from the unattended weekly build path.

    Idempotent: re-approving an already-uploaded proposal is a no-op (routes
    through ``save_symphony``'s own ``already_uploaded_symphony_id`` seam, so
    no duplicate Composer symphony is ever created).

    Parameters
    ----------
    proposal_id : int
        The ``frontrunner_proposals.id`` row to approve.

    Returns
    -------
    ApprovalResult
        ``.success=True`` + ``.symphony_id`` set only when the create
        succeeded AND ``verify_undeployed`` confirms zero allocation. On ANY
        failure (proposal not found, Composer 4xx/5xx, verify_undeployed
        returning False), ``.success=False`` and the proposal is left
        un-marked 'uploaded' — never marks uploaded on a failure or an
        unverified create (AC-11: "do NOT mark uploaded, do NOT retry
        blindly").

    Never raises (D-1).
    """
    from advisors import composer_draft_client

    try:
        import database  # noqa: PLC0415 - CC-2 lazy

        proposal = database.get_frontrunner_proposal(proposal_id)
        if proposal is None:
            return ApprovalResult(success=False, error="proposal not found")

        if proposal["approval_status"] == "uploaded" and proposal.get("created_symphony_id"):
            # Idempotent no-op: already uploaded.
            return ApprovalResult(success=True, symphony_id=proposal["created_symphony_id"])

        candidate_tree = proposal["candidate_tree"]
        draft_result = composer_draft_client.save_symphony(
            name=f"Frontrunner Candidate — {proposal['symphony_id']}",
            description="Frontrunner Builder candidate (operator-approved)",
            color="#4287f5",
            hashtag="#frontrunner",
            raw_value=candidate_tree,
            already_uploaded_symphony_id=proposal.get("created_symphony_id"),
        )

        if not draft_result.success or not draft_result.symphony_id:
            database.update_frontrunner_proposal_status(
                proposal_id,
                approval_status="approved",
                error_message=draft_result.error or "unknown create failure",
            )
            return ApprovalResult(success=False, error=draft_result.error)

        # AC-9 belt-and-suspenders: verify the created symphony holds ZERO
        # allocation before marking the proposal 'uploaded'.
        is_undeployed = composer_draft_client.verify_undeployed(draft_result.symphony_id)
        if not is_undeployed:
            database.update_frontrunner_proposal_status(
                proposal_id,
                approval_status="approved",
                created_symphony_id=draft_result.symphony_id,
                error_message="post-create verify_undeployed check failed — NOT marked uploaded",
            )
            logger.error(
                "approve_frontrunner_proposal: proposal_id=%s symphony_id=%s FAILED "
                "verify_undeployed — refusing to mark uploaded",
                proposal_id,
                draft_result.symphony_id,
            )
            return ApprovalResult(
                success=False,
                symphony_id=draft_result.symphony_id,
                error="verify_undeployed_failed",
            )

        database.update_frontrunner_proposal_status(
            proposal_id,
            approval_status="uploaded",
            created_symphony_id=draft_result.symphony_id,
        )
        database.insert_advisor_observation(
            advisor_role="FRONTRUNNER_BUILDER",
            subject_type="frontrunner_proposal",
            subject_id=str(proposal_id),
            verdict="uploaded",
            raw_response={"created_symphony_id": draft_result.symphony_id},
            symphony_id=proposal["symphony_id"],
        )
        return ApprovalResult(success=True, symphony_id=draft_result.symphony_id)

    except Exception as exc:
        logger.debug("approve_frontrunner_proposal: unexpected error", exc_info=True)
        return ApprovalResult(success=False, error=type(exc).__name__)
