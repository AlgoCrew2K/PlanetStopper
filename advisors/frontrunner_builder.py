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

splice_candidate_into_symphony(
    incumbent_symphony, incumbent_cascade, candidate, *, compiled_tree=None
) -> dict | None
    Replace the detected incumbent cascade subtree with the compiled candidate
    overlay inside a full copy of the incumbent symphony, re-validating via
    ``symphony_schema.validate_tree``. ``compiled_tree``, when supplied, is
    reused as the already-compiled candidate instead of compiling ``candidate``
    fresh. Returns None on any structural failure (never raises).

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

# AC-12: Fable API budget cap — bounds the number of Fable calls (one per
# detected cascade) a single _run_build_for_symphony run can make. VERIFIED
# against all 11 real validation trees (tests/fixtures/advisors/frontrunner/
# real_tree_*.json) via the real frontrunner_detector: observed cascade
# counts were {26, 12, 8, 4, 4, 3, 2, 1, 1, 1, 0}, max=26 (real_tree_01, a
# legitimate, unambiguous detection — not an edge case). 40 (~1.5x the
# observed max) gives real headroom above that outlier for legitimate future
# sub-strategy growth while still bounding a pathological/mis-parsed
# detection (e.g. hundreds of spurious cascades) from triggering unbounded
# Fable spend. Team-lead-ratified 2026-07-11, replacing an initial guess of
# 10 that would have silently truncated candidate generation on 2 of the
# operator's 11 real live symphonies — the exact failure mode this cap must
# not itself cause. Cascades beyond the cap are skipped with a logged
# reason, never silently dropped.
MAX_CASCADES_PER_SYMPHONY_RUN: int = 40

# AC-12: self-imposed runaway-creation safety valve for the operator-approved
# Composer upload path. Composer documents NO per-account symphony-count cap
# or create-time quota (Tier-1 OpenAPI + Tier-2 MCP + help-center
# triangulation — composer-api-researcher); fetch_symphony_stats is
# DEPLOYED-scoped and cannot see the undeployed symphonies this feature
# creates, so it cannot serve as the guard's denominator either
# (team-lead-ruled 2026-07-11). This bounds how many candidate symphonies the
# operator-approved upload path can accumulate in the operator's Composer
# account before requiring manual cleanup — a generous lifetime ceiling given
# the overfitting gates reject most candidates (mirrors the AI Advisor's own
# "empty suggestions is the common case" behavior), and a fine runaway-bug
# blast-radius cap either way. NOT a Composer limit — ours alone.
MAX_FRONTRUNNER_UPLOADS_PENDING_REVIEW: int = 25

# frontrunner-proposal-identity feature (2026-08-20): bounds on the Composer
# symphony name/description strings build_proposal_symphony_name /
# build_proposal_symphony_description construct for a proposal's approve-path
# upload. The trailing "#{proposal_id})" token always survives truncation
# intact — only display_name is ever shortened. Self-imposed bounds ONLY
# (mirrors MAX_FRONTRUNNER_UPLOADS_PENDING_REVIEW's own "ours alone" framing
# above) — NOT verified against Composer's real field limits; verifying that
# needs a live Composer create, which stays under the existing operator-gated
# task-zero live-create test (F11).
MAX_PROPOSAL_NAME_CHARS: int = 100
MAX_PROPOSAL_DESCRIPTION_CHARS: int = 1000

# VIX-family tickers recognized as hedge/fire-basket instruments — same
# vocabulary as frontrunner_detector.VIX_FAMILY_TICKERS (imported, not
# duplicated, so the two modules can never drift on this list). AC-8:
# _collect_tickers is imported alongside it — the step-aware asset-ticker
# collector _collect_step_keyed_signal_tickers (below) builds on it.
# STUBBED_CORE_CONTINUATION_TICKER (DE-FR-SIMPLIFY-001 Revise 3 RULING 1):
# frontrunner_detector's internal reporting-stub ticker for the collapsed
# core-continuation branch (frontrunner_detector.py's _build_cascade_overlay)
# — a synthetic placeholder for SIZE only, explicitly documented there as
# "never a placeholder for scale that could be mistaken for a real ticker".
# Imported (not duplicated as a second local literal) so a detector-side
# rename can never silently break either this module's watched_tickers
# exclusion or the SIMPLIFY-path signal-logic-only node counter's stub-branch
# identification, both of which depend on matching this exact string.
# _count_clause_aware_signal_logic (DE-FR-SIMPLIFY-001 Revise 4, R4-2/B3):
# the dedicated clause-aware node counter — imported verbatim (never
# reimplemented) so the overlay-side SIMPLIFY operand below and the
# detector's own cascade-side signal_logic_node_count share ONE counting
# implementation.
from advisors.frontrunner_detector import (  # noqa: E402
    STUBBED_CORE_CONTINUATION_TICKER,
    VIX_FAMILY_TICKERS,
    _collect_tickers,
    _count_clause_aware_signal_logic,
)

_PCT_PLACEHOLDER = "%"

# F13 (PR #128 /code-review): the distinct plan_id markers this module
# stamps onto its own plan_tree_compiler.compile_plan calls — one per
# consumer (generate_candidate_overlay's real generation compile,
# splice_candidate_into_symphony's own compile), previously inline literals.
# Named here so tests asserting against the literal strings keep working
# unchanged while production code has a single source of truth per marker.
# _PLAN_ID_OVERLAY_NODE_COUNT (the third original marker) is DELETED, not
# kept-and-unused — DE-FR-SIMPLIFY-001 Revise 4's R4-6 removed the fresh-
# compile tier of _count_overlay_node_count that was its sole consumer;
# compile_plan is now called exactly once per candidate across the whole
# build/gate/splice chain, and no code path ever stamps this marker again.
_PLAN_ID_GENERATION = "frontrunner-candidate"
_PLAN_ID_SPLICE_CANDIDATE = "frontrunner-splice-candidate"

# AC-6: DoF-ledger isolation for frontrunner-builder search-breadth rows.
#
# THE ACTUAL ISOLATION MECHANISM is evidence_source="OVERLAY_BACKTEST_SELECTION"
# (database._VALID_DOF_EVIDENCE_SOURCES), not spec_bundle_id. Every real
# consumer that aggregates across researcher_dof_ledger —
# database.count_dof_backtest_selections and database.get_researcher_dof_ledger_for_run
# (the production N_effective feed at autotuner.py:2487) — filters on the
# literal string evidence_source='BACKTEST_SELECTION'. A distinct evidence_source
# value is therefore excluded from EVERY such consumer by construction: zero
# schema/query change, no producer/subsystem column needed. Verified via a
# real-DB integration suite (tests/advisors/test_frontrunner_dof_isolation.py)
# that inserts a real autotuner-shaped BACKTEST_SELECTION row alongside a
# frontrunner OVERLAY_BACKTEST_SELECTION row and asserts every consumer's
# output is byte-identical to before the frontrunner row existed.
#
# CORRECTION HISTORY: an earlier design (f51cffe) attempted isolation via a
# distinct spec_bundle_id sentinel alone — frtest's RCA (10af53c) proved this
# does NOT isolate: get_researcher_dof_ledger_for_run excludes ONLY rows
# matching the CURRENT run's own winning_spec_bundle_id, so any OTHER
# spec_bundle_id value (including a sentinel) still sweeps into every
# symphony's real N_effective. Team-lead-ratified fix (cff1264c, 2026-07-11):
# switch the ISOLATION mechanism to evidence_source; KEEP the spec_bundle_id
# sentinel below as belt-and-suspenders audit legibility ONLY (a scoped
# count_dof_backtest_selections(spec_bundle_id="frontrunner_builder") read
# correctly excludes these rows too, and it's never indistinguishable from a
# pre-bundle-era NULL row) — it is not, and was never, the load-bearing
# guarantee.
#
# researcher_dof_ledger.spec_bundle_id is a SOFT FK ("soft FK to
# spec_bundles.bundle_hash", migrations/018_researcher_dof_ledger.sql:33 — no
# SQL FOREIGN KEY constraint, enforcement is app-level only per
# database.insert_dof_ledger_row's own docstring).
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


def _collect_step_keyed_signal_tickers(node) -> set[str]:
    """AC-8: step-aware ticker collector for a Composer step-keyed overlay
    tree (``frontrunner_detector.Cascade.overlay_tree``) — replaces the
    permanently-False ``"kind" in cascade.overlay_tree`` guard, which tested
    for the DSL-shape key vocabulary (``kind``/``then``/``else``) that a real
    Composer tree never carries (it is ``step``-keyed: ``step``, ``children``,
    ``is-else-condition?``).

    Collects two signal sources, matching what ``_walk_overlay_tickers``
    captures for the DSL shape: (a) real asset tickers via
    ``frontrunner_detector._collect_tickers`` (fire/else basket holdings,
    excluding the detector's own internal size-stub ticker), and (b) each
    flat if-child condition's own watched ticker(s) — ``lhs-val``, and
    ``rhs-val`` when the rhs is itself a ticker comparison
    (``rhs-fixed-value?`` is False) — which is the "core signal ticker" (the
    RSI(ticker) trigger) a real Composer tree stores entirely inside the
    condition, never as an asset node. (b) is what makes two symphonies
    watching different core tickers (same fire-basket asset) actually
    distinguishable.

    Iterative (explicit stack), never raises (D-1) — malformed input
    degrades to a partial/empty set.
    """
    out: set[str] = {t for t in _collect_tickers(node) if t != STUBBED_CORE_CONTINUATION_TICKER}
    try:
        if not isinstance(node, dict):
            return out
        stack: list = [node]
        while stack:
            current = stack.pop()
            if not isinstance(current, dict):
                continue
            if current.get("step") == "if-child" and isinstance(current.get("lhs-fn"), str):
                lhs_val = current.get("lhs-val")
                if isinstance(lhs_val, str) and lhs_val:
                    out.add(lhs_val)
                if current.get("rhs-fixed-value?") is False:
                    rhs_val = current.get("rhs-val")
                    if isinstance(rhs_val, str) and rhs_val:
                        out.add(rhs_val)
            for child in current.get("children") or []:
                stack.append(child)
    except Exception:  # pragma: no cover - defensive; never raises
        logger.debug("_collect_step_keyed_signal_tickers: unexpected error", exc_info=True)
    return out


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
        if (
            isinstance(child, dict)
            and child.get("kind") in ("if", "if_compound")
            and _has_vix_ticker_in_fire_branch(child)
        ):
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
        "'if_compound' (compound condition). The condition ALWAYS lives under "
        "a nested 'condition' key on the node — never as bare fields directly "
        "on the node itself."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "overlay": {
                "type": "object",
                "description": (
                    "A single build-plan-DSL 'if'/'if_compound' NODE. kind must "
                    "be 'if' or 'if_compound'."
                ),
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["if", "if_compound"],
                    },
                    "condition": {
                        "type": "object",
                        "description": (
                            "REQUIRED, nested under this key — never bare fields "
                            "on the node. For kind='if': lhs_fn, lhs_ticker, "
                            "window, comparator, rhs:{fixed: number}. For "
                            "kind='if_compound': a typed union via 'type': "
                            "binary / binary_compound / compound."
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
                                "enum": ["binary", "binary_compound", "compound"],
                            },
                        },
                    },
                    "then": {
                        "type": "array",
                        "description": (
                            "Fire branch — the hedge basket. Each entry is a "
                            "NODE: kind='weight' (scheme: equal/specified/"
                            "inverse_vol; specified children are "
                            "{node: NODE, pct: number}, never flat "
                            "{ticker, weight}), kind='asset' (ticker), or a "
                            "nested kind='if'/'if_compound' for a scale-in tier."
                        ),
                    },
                    "else": {
                        "type": "array",
                        "description": (
                            "Continuation toward the core strategy — use a "
                            "single placeholder kind='asset' node here."
                        ),
                    },
                },
                "required": ["kind", "condition", "then", "else"],
            }
        },
        "required": ["overlay"],
    },
}


# Conforming worked example embedded in the generation prompt — a 2-tier
# scale-in overlay verified to compile clean through the real
# plan_tree_compiler (tree not None, validate_tree == []). Mirrors
# build_plan_generator._EXAMPLE_IF_PLAN's role: this module previously had
# zero worked examples, and the prompt's free-text description alone had
# drifted from the compiler's actual contract (RC#1/#2).
_EXAMPLE_OVERLAY: dict = {
    "kind": "if",
    "condition": {
        "lhs_fn": "relative-strength-index",
        "lhs_ticker": "SPY",
        "window": 10,
        "comparator": "gt",
        "rhs": {"fixed": 79},
    },
    "then": [
        {
            "kind": "if",
            "condition": {
                "lhs_fn": "relative-strength-index",
                "lhs_ticker": "SPY",
                "window": 10,
                "comparator": "gt",
                "rhs": {"fixed": 83},
            },
            "then": [
                {
                    "kind": "weight",
                    "scheme": "specified",
                    "children": [
                        {"node": {"kind": "asset", "ticker": "UVXY"}, "pct": 60},
                        {"node": {"kind": "asset", "ticker": "VIXM"}, "pct": 25},
                        {"node": {"kind": "asset", "ticker": "BIL"}, "pct": 15},
                    ],
                }
            ],
            "else": [
                {
                    "kind": "weight",
                    "scheme": "specified",
                    "children": [
                        {"node": {"kind": "asset", "ticker": "UVXY"}, "pct": 25},
                        {"node": {"kind": "asset", "ticker": "VIXM"}, "pct": 25},
                        {"node": {"kind": "asset", "ticker": "BIL"}, "pct": 50},
                    ],
                }
            ],
        }
    ],
    "else": [{"kind": "asset", "ticker": "CORE_STRATEGY_PLACEHOLDER"}],
}


# AC-1b: a second worked example — genuinely compound (kind='if_compound',
# condition type='compound') combining TWO distinct signal functions (RSI
# plus a non-RSI KNOWN_INDICATOR_FNS entry), so anchoring pulls Fable toward
# multi-signal sophistication instead of only the flat 2-tier RSI-only shape
# above. Verified to compile clean through the real plan_tree_compiler (tree
# not None, validate_tree == []) before being hardcoded here — same
# provenance discipline as _EXAMPLE_OVERLAY.
_EXAMPLE_COMPOUND_OVERLAY: dict = {
    "kind": "if_compound",
    "condition": {
        "type": "compound",
        "operator": "all",
        "conditions": [
            {
                "type": "binary",
                "lhs_fn": "relative-strength-index",
                "lhs_ticker": "QQQ",
                "window": 10,
                "comparator": "gt",
                "rhs": {"fixed": 80},
            },
            {
                "type": "binary",
                "lhs_fn": "cumulative-return",
                "lhs_ticker": "QQQ",
                "window": 5,
                "comparator": "lt",
                "rhs": {"fixed": 0},
            },
        ],
    },
    "then": [
        {
            "kind": "weight",
            "scheme": "specified",
            "children": [
                {"node": {"kind": "asset", "ticker": "UVXY"}, "pct": 60},
                {"node": {"kind": "asset", "ticker": "VIXM"}, "pct": 40},
            ],
        }
    ],
    "else": [{"kind": "asset", "ticker": "CORE_STRATEGY_PLACEHOLDER"}],
}


def _build_stable_instructional_prefix() -> str:
    """The genuinely invariant leading span of the overlay-generation prompt
    (DE-ADVISOR-CACHE-001, site #7): the HARD REQUIREMENTS block, the
    node-shape explanation, the tool-usage instructions, and both worked
    JSON examples. Contains ZERO signal_context-derived content by
    construction -- this is the portion the generate_candidate_overlay call
    site wraps in a cache_control breakpoint. Byte-identical to the
    pre-reorder producer's leading + trailing spans concatenated (see
    tests/fixtures/frontrunner_builder/generation_prompt_stable_content_baseline.json).
    """
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
        "it as TIERED nested if-nodes, nested inside the OUTER node's 'then' "
        "branch — never flatten multiple thresholds into a single OR condition, "
        "and never nest another tier inside 'else'.\n"
        "5. The trigger does not need to be a single RSI rung — you may combine "
        "two signals into one compound condition when a second signal genuinely "
        "sharpens the trigger, for example pairing an RSI-overbought reading with "
        "a confirming secondary indicator such as cumulative-return, "
        "max-drawdown, or moving-average-price. Consider a compound condition "
        "(kind='if_compound', condition type='compound' joining two binary "
        "sub-conditions with any/all, or type='binary_compound' broadcasting one "
        "indicator over several watched tickers) whenever it captures the "
        "pattern better than a flat single-signal if — this is optional, a flat "
        "if is equally valid when that is what the pattern calls for.\n\n"
        "Emit exactly ONE candidate overlay node using the emit_frontrunner_overlay "
        "tool. The node's 'kind' is 'if' or 'if_compound' — EITHER WAY the "
        "condition fields live under a nested 'condition' key: lhs_fn, "
        "lhs_ticker, window, comparator, rhs:{fixed: number} for 'if', or a "
        "{type: binary/binary_compound/compound, ...} block for 'if_compound' — "
        "NEVER as bare fields directly on the node. Weight nodes use "
        "kind='weight' with a 'scheme' field (equal/specified/inverse_vol); "
        "scheme='specified' children are {node: NODE, pct: number} pairs, "
        "never flat {ticker, weight} pairs. 'then' fires the hedge basket; "
        "'else' continues toward the core (use a single placeholder asset node "
        "there — only the OUTERMOST node's 'else' is ever the placeholder; a "
        "nested tier's own 'else' is real hedge content, not a placeholder). "
        f"Conforming single-signal example (compiles clean): {json.dumps(_EXAMPLE_OVERLAY)}\n\n"
        f"Conforming compound/multi-signal example (compiles clean): "
        f"{json.dumps(_EXAMPLE_COMPOUND_OVERLAY)}"
    )


def _build_signal_context_hints_section(signal_context: dict) -> str:
    """The volatile per-symphony trailing section (DE-ADVISOR-CACHE-001, site
    #7): watched tickers / Atlas-derived patterns / live edge signals.
    Relocated here (previously interpolated mid-prompt) so
    ``_build_stable_instructional_prefix`` can form a genuinely invariant
    cache_control prefix -- this section is always UNCACHED at the
    generate_candidate_overlay call site.
    """
    watched = signal_context.get("watched_tickers") or []
    atlas_patterns = signal_context.get("atlas_patterns") or []
    edge_signals = signal_context.get("edge_signals") or {}
    watched_hint = ", ".join(str(t) for t in watched[:20]) or "(none supplied)"
    atlas_hint = json.dumps(atlas_patterns[:5]) if atlas_patterns else "(none supplied)"
    edge_signals_hint = json.dumps(edge_signals) if edge_signals else "(none supplied)"

    return (
        "\n\n## LIVE SIGNAL CONTEXT\n\n"
        f"Watched core signal tickers to consider: {watched_hint}\n"
        f"Atlas-derived frontrunner patterns for reference: {atlas_hint}\n"
        f"Positive-edge frontrunner signals observed LIVE for this symphony "
        f"(each key is TICKER:WINDOW:THRESHOLD; prefer watching one of these "
        f"exact ticker/window/threshold combinations when it fits the pattern "
        f"— these are proven, currently-'keep'-classified real edge stats, "
        f"not hypothetical): {edge_signals_hint}"
    )


def _build_generation_prompt(signal_context: dict) -> str:
    """Build the SDK prompt for candidate overlay generation.

    DE-ADVISOR-CACHE-001 (site #7) reorder: the invariant instructional span
    (HARD REQUIREMENTS + node-shape + tool-usage + both worked examples) now
    leads, with the volatile watched-tickers / Atlas-patterns / live-edge-
    signals hints relocated to a trailing "## LIVE SIGNAL CONTEXT" section
    (mirroring build_plan_generator's "## OPERATOR CONTEXT" pattern) --
    producing a genuinely signal_context-independent leading span that
    generate_candidate_overlay wraps in a cache_control breakpoint. See
    _build_stable_instructional_prefix / _build_signal_context_hints_section.
    """
    return _build_stable_instructional_prefix() + _build_signal_context_hints_section(
        signal_context
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
        # _build_generation_prompt remains the seam other callers/tests observe
        # (e.g. test_frontrunner_builder_signal_wiring.py spies on it directly) --
        # call it here rather than bypassing it, then split its return value at
        # the cache_control boundary via a byte slice against the independently
        # -computed stable prefix (DE-ADVISOR-CACHE-001, site #7). The stable
        # prefix is signal_context-independent by construction, so it is byte-
        # identical across every call regardless of watched tickers/Atlas
        # patterns/live edge signals -- reused across this function's own
        # n_attempts retry loop below AND across separate builds for different
        # symphonies. The hints section is genuinely volatile and stays UNCACHED.
        full_prompt = _build_generation_prompt(signal_context)
        stable_prompt = _build_stable_instructional_prefix()
        hints_section = full_prompt[len(stable_prompt) :]
        content_blocks = [
            {"type": "text", "text": stable_prompt, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": hints_section},
        ]

        last_reason = "no attempts made"
        for attempt in range(n_attempts):
            response = client.messages.create(
                model=FABLE_MODEL,
                max_tokens=MAX_OUTPUT_TOKENS,
                tools=[_EMIT_OVERLAY_TOOL],
                tool_choice={"type": "tool", "name": "emit_frontrunner_overlay"},
                messages=[{"role": "user", "content": content_blocks}],
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
                    "generate_candidate_overlay: rejected (no VIX ticker) on attempt %d/%d",
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
                "plan_id": _PLAN_ID_GENERATION,
                "objective": "cut_drawdown",
                "name": "Frontrunner Candidate",
                "rebalance": "daily",
                "root": collapsed,
            }
            compile_result = plan_tree_compiler.compile_plan(plan_envelope)
            if compile_result.tree is None:
                # RC#1/#2: a failed compile must never be reported as a
                # silent success with a None .compiled_tree — treat it as a
                # rejected candidate (same idiom as the VIX-rejection /
                # truncation continues above) and retry.
                last_reason = f"candidate failed to compile: {compile_result.reason}"
                logger.warning(
                    "generate_candidate_overlay: compile failed on attempt %d/%d (%s)",
                    attempt + 1,
                    n_attempts,
                    compile_result.reason,
                )
                continue

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


def _find_terminal_else_child(node: dict) -> dict | None:
    """Return a compiled if/if_compound node's TERMINAL else if-child — the
    real continuation/placeholder slot.

    This DSL's established scale-in convention (``_build_generation_prompt``,
    ``_dsl_scale_in_tiers_overlay`` in test_frontrunner_builder.py, and both
    real captured fixtures in tests/fixtures/advisors/frontrunner/) nests
    every successive tier as the SOLE child of the PRECEDING tier's true
    (fire) branch — i.e. tiers nest ONLY inside 'then', never inside 'else'.
    A nested tier's own else is legitimate lower-intensity hedge content
    (e.g. a lighter VIX blend fired when only the outer, not the inner,
    threshold is met) — NOT a placeholder. Only the OUTERMOST node's else is
    ever the placeholder Fable was told to emit — there is exactly one
    terminal slot per candidate, and it is always the top-level one. (Verified
    empirically: descending into a nested tier's own else here, instead of
    stopping at the top level, grafts over real hedge content and leaves the
    true placeholder untouched — the opposite of RC#3's fix.)

    ``make_if`` and ``make_if_compound`` both emit ``{"step": "if", ...}``,
    so this is agnostic to flat vs compound conditions. Returns None if
    ``node`` isn't shaped as an if-node with both branches present — never
    raises.
    """
    if not isinstance(node, dict) or node.get("step") != "if":
        return None
    children = node.get("children") or []
    return next(
        (c for c in children if isinstance(c, dict) and c.get("is-else-condition?") is True),
        None,
    )


def _graft_incumbent_core(original_node: dict, compiled_node: dict) -> dict:
    """Return a deep copy of ``compiled_node`` with its TERMINAL else
    (continuation) branch replaced by ``original_node``'s real else branch.

    ``_replace_node_by_id`` swaps the WHOLE incumbent if-node (condition +
    real fire branch + real continuation/core branch, potentially thousands
    of nodes) for the candidate's compiled node, whose own terminal else
    branch is only the placeholder Fable was told to emit there. Grafting
    the incumbent's real else-branch children back in before the replace
    preserves the incumbent's core strategy content (RC#3): the result is
    IF (rsi cascade) THEN (frontrunner hedge) ELSE (incumbent's real core) —
    never a structurally-present-but-semantically-broken graft that only
    happens to satisfy a ticker-presence check.

    ``original_node`` is the untouched incumbent if-node (the cascade's own
    top-level, which — like the compiled candidate — has exactly one real
    continuation, at its own top-level else; frontrunner_detector never
    recurses tiers into 'else'). Never raises — a node shaped unexpectedly
    (on either side) degrades to returning ``compiled_node`` unchanged
    (deep-copied); this is the rare-exception path, not the common case, for
    every real if/if_compound node this module's own compiler produces.
    """
    grafted = copy.deepcopy(compiled_node)
    try:
        original_else = next(
            (
                c
                for c in original_node.get("children") or []
                if isinstance(c, dict) and c.get("is-else-condition?") is True
            ),
            None,
        )
        terminal_else = _find_terminal_else_child(grafted)
        if original_else is not None and terminal_else is not None:
            terminal_else["children"] = copy.deepcopy(original_else.get("children") or [])
        else:
            logger.warning(
                "_graft_incumbent_core: could not locate a graftable else slot "
                "on %s side — incumbent core NOT preserved",
                "original_node" if original_else is None else "compiled_node",
            )
    except Exception:
        logger.debug("_graft_incumbent_core: unexpected error", exc_info=True)
    return grafted


def splice_candidate_into_symphony(
    incumbent_symphony: dict,
    incumbent_cascade,
    candidate: dict,
    *,
    compiled_tree: dict | None = None,
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
        Used only as the fresh-compile fallback INPUT when ``compiled_tree``
        is unavailable.
    compiled_tree : dict | None
        F7 (DE-FR-SIMPLIFY-001 Revise 3): the ALREADY-compiled Composer tree
        for ``candidate`` (``GenerationResult.compiled_tree``), when
        available — the PREFERRED input. Same invariant as
        ``_count_overlay_node_count``'s own ``compiled_tree`` reuse (folded
        in at f2611ee1): ``generate_candidate_overlay`` already compiled
        ``candidate`` once, so reusing that result here avoids a THIRD
        redundant compile-plus-validate pass on the successful-generation
        happy path. Falls back to compiling ``candidate`` fresh ONLY when
        ``compiled_tree`` is None.

    Returns
    -------
    dict | None
        The full spliced symphony (validated via symphony_schema.validate_tree
        internally, and returned ONLY if that validation is clean), or None
        on any failure (node not found, compile failure, validate_tree
        errors). Never raises (D-1).
    """
    try:
        target_id = (
            incumbent_cascade.overlay_tree.get("id")
            if isinstance(incumbent_cascade.overlay_tree, dict)
            else None
        )
        if not target_id:
            logger.warning("splice_candidate_into_symphony: incumbent cascade has no id")
            return None

        # RC#3: resolve the REAL incumbent node (not the detector's own
        # reporting copy) BEFORE it's replaced below, so its real
        # continuation/core content can be grafted into the candidate.
        original_node = _find_node_by_id(incumbent_symphony, target_id)

        # F7: reuse the already-compiled tree when the caller supplied one —
        # no compile_plan call at all. Falls back to compiling the raw DSL
        # candidate (kind=...) fresh, or accepting an already-compiled
        # Composer node (step=...) as-is, only when compiled_tree is absent.
        if isinstance(compiled_tree, dict):
            compiled_node = _unwrap_single_compiled_child(compiled_tree)
            if compiled_node is None:
                logger.warning(
                    "splice_candidate_into_symphony: compiled_tree present but failed "
                    "to unwrap to a single node"
                )
                return None
        elif isinstance(candidate, dict) and "kind" in candidate:
            plan_envelope = {
                "plan_id": _PLAN_ID_SPLICE_CANDIDATE,
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
            compiled_node = _unwrap_single_compiled_child(compile_result.tree)
            if compiled_node is None:
                logger.warning("splice_candidate_into_symphony: unexpected compiled root shape")
                return None
        elif isinstance(candidate, dict) and "step" in candidate:
            compiled_node = candidate
        else:
            logger.warning("splice_candidate_into_symphony: candidate is not a valid node")
            return None

        # RC#3: graft the incumbent's real continuation/core branch into the
        # candidate's placeholder terminal-else slot before the whole-node
        # replace below — without this, the replace silently discards it.
        if original_node is not None:
            compiled_node = _graft_incumbent_core(original_node, compiled_node)

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

    except Exception:
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
    acceptance gate's node_count_delta / whole-symphony-did-not-grow signal,
    RULING 2).

    Iterative (explicit stack) — mirrors symphony_schema.py's established
    pattern (P2-1, frreview finding) — so the operator's real 8,000+ node
    trees (which can be deep, not just wide) never trigger RecursionError.

    REVERTED (DE-FR-SIMPLIFY-001 Revise 4, R4-2): Revise 3's RULING 3 had
    extended this walk to also descend a compound condition's ``condition``/
    ``conditions`` DATA fields. Superseded — this function's ONLY remaining
    consumer is RULING 2's whole-symphony ``node_count_delta`` gate (a
    coarse did-it-grow-at-all check), and clause-aware counting now lives in
    the dedicated ``frontrunner_detector._count_clause_aware_signal_logic``
    (imported, never duplicated) for the SIMPLIFY-path operands that
    actually need it. Children-only, matching this module's original,
    simpler contract.
    """
    if not isinstance(node, dict):
        return 0
    count = 0
    stack: list = [node]
    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue
        count += 1
        for child in current.get("children") or []:
            stack.append(child)
    return count


def _unwrap_single_compiled_child(compiled_root) -> dict | None:
    """Return the sole top-level child of a compiled ``plan_tree_compiler``
    root (the raw compiled if/if_compound node), or None if the root isn't
    shaped as exactly one child. The same unwrap ``splice_candidate_into_
    symphony`` performs on its own ``compile_plan`` result — factored out so
    both it and ``_count_overlay_node_count`` apply it identically to either
    an already-compiled tree they were HANDED or one they compiled
    themselves.

    F8 (DE-FR-SIMPLIFY-001 Revise 3): a strict dict-or-None contract for ANY
    input shape, never raises — ``children`` must genuinely be a list of
    length 1, and the sole entry must itself be a dict, or this returns None.
    Never coerces a non-list ``children`` via truthy fallback (a caller
    passing a malformed shape gets an honest None, not an accidental
    non-dict return value).
    """
    if not isinstance(compiled_root, dict):
        return None
    children = compiled_root.get("children")
    if not isinstance(children, list) or len(children) != 1:
        return None
    sole = children[0]
    return sole if isinstance(sole, dict) else None


def _count_overlay_node_count(compiled_tree: dict | None = None) -> int | None:
    """Return the SIGNAL-LOGIC-ONLY node count of a build-plan-DSL overlay
    candidate (DE-FR-SIMPLIFY-001, AC-2's ``overlay_node_count`` operand),
    or None if it cannot be honestly determined.

    Parameters
    ----------
    compiled_tree : dict | None
        The ALREADY-compiled Composer tree for the candidate
        (``GenerationResult.compiled_tree``) — the SOLE input. ``generate_
        candidate_overlay`` always compiles the candidate once via
        ``plan_tree_compiler.compile_plan``; this function reuses that
        result rather than ever recompiling.

    REVISE 4 (R4-3/R4-6): the two fallback tiers (fresh-compile of a DSL
    candidate; already-`step`-shaped candidate) were DELETED — they were
    unreachable under the real production call graph (the sole caller,
    ``_run_build_for_symphony``, always has ``compiled_tree`` populated) and
    their continued existence duplicated a compile path this module's own
    no-redundant-compile invariant (F7) exists to eliminate. ``compiled_tree``
    absent now means an UNCONDITIONAL None, never a fallback compile.

    REVISE 5 (F6): the ``candidate`` parameter itself is DROPPED — it had
    been unused since R4-6 deleted both fallback tiers that ever read it
    (retained only for call-site signature compatibility until now). The
    sole caller, ``_run_build_for_symphony``, now passes a single
    ``compiled_tree`` argument.

    Overlay-side fire/continuation identification now reuses the existing
    production ``_find_terminal_else_child`` (R4-3) instead of a bespoke
    stub-marker search — a compiled overlay candidate is never detector-
    compacted, so its else slot is always identifiable via
    ``is-else-condition?==True`` alone; no stub-marker ambiguity can arise
    here (that ambiguity was specific to the CASCADE side, now resolved
    architecturally by reading ``cascade.signal_logic_node_count`` directly
    off the detector — see ``frontrunner_detector._compute_signal_logic_
    node_count``).

    Note (Revise 5): this function counts the fire branch's content
    INCLUDING any nested tier's own else (never excluded here) — genuinely
    DIFFERENT from frontrunner_detector._compute_signal_logic_node_count
    (the cascade-side counterpart), which excludes a nested tier's own
    continuation entirely at every level. This is intentional (trust
    asymmetry: a self-generated candidate's nested-tier else is provably
    never a placeholder by DSL/compiler construction, per
    _find_terminal_else_child's own docstring; a detected incumbent's
    nested continuation may genuinely be core-strategy bulk, per
    _is_internal_hedge_subgate's docstring). Do not make these two
    counters symmetric.

    The fire child is the explicit sibling `next(..., None)`
    lookup below (B1) — never a bare ``next()`` that could raise
    ``StopIteration`` on an aliased-duplicate-children shape and fall
    through to this function's outer except-all at DEBUG level only.

    Never raises (D-1) — returns None (never a fabricated 0) on any unwrap
    failure or unidentifiable if-child shape. The caller passes this
    straight through; the acceptance gate's own fail-closed guards decline
    SIMPLIFY on a None operand. Every EXPECTED None-degradation path below
    logs a WARNING (not just this function's outer except-all, which stays
    DEBUG for genuinely-unexpected errors per this module's established
    convention, A1).
    """
    try:
        if not isinstance(compiled_tree, dict):
            return None
        node = _unwrap_single_compiled_child(compiled_tree)
        if node is None:
            logger.warning(
                "_count_overlay_node_count: compiled_tree present but failed to "
                "unwrap to a single node — SIMPLIFY's overlay operand degrading "
                "to None for this candidate"
            )
            return None
        else_child = _find_terminal_else_child(node)
        if else_child is None:
            logger.warning(
                "_count_overlay_node_count: compiled overlay node has no "
                "identifiable terminal else child — cannot honestly identify the "
                "fire/continuation split; SIMPLIFY's overlay operand degrading to "
                "None for this candidate"
            )
            return None
        fire_child = next((c for c in node.get("children") or [] if c is not else_child), None)
        if fire_child is None:
            logger.warning(
                "_count_overlay_node_count: compiled overlay node's two children "
                "are indistinguishable (aliased/duplicate) — cannot honestly "
                "identify the fire branch; SIMPLIFY's overlay operand degrading "
                "to None for this candidate"
            )
            return None
        return 1 + _count_clause_aware_signal_logic(fire_child)
    except Exception:
        logger.debug("_count_overlay_node_count: unexpected error", exc_info=True)
        return None


def _gather_atlas_frontrunner_patterns(watched_tickers: list[str]) -> list[dict]:
    """AC-3: load the Atlas frontrunner corpus through the shared 7-day cache,
    identify frontrunner-shaped strategies (structural detection, reusing the
    same detector applied to live symphonies), and extract patterns (watched
    tickers, VIX/hedge instruments, RSI thresholds, basket shapes) for the
    Fable generation prompt's ``atlas_patterns`` slot.

    Parameters
    ----------
    watched_tickers : list[str]
        The CURRENT symphony's own detected cascade's watched tickers (the
        same list ``_run_build_for_symphony`` already computes for
        ``signal_context``). Accepted for future ticker-relevance scoping —
        no RED test requires filtering on it, so this batch applies none;
        every structurally frontrunner-shaped Atlas candidate contributes
        patterns regardless of ticker overlap.

    Returns
    -------
    list[dict]
        One pattern dict per DISTINCT detected cascade across the whole
        corpus (near-identical patterns collapsed — see below), each
        carrying ``vix_tickers`` (list[str]), ``rsi_thresholds`` (list[float]),
        ``watched_tickers`` (list[str], structurally extracted from the
        ATLAS candidate's own cascade — never the caller's ``watched_tickers``
        parameter), ``basket_node_count`` (int, via ``_count_tree_nodes`` —
        the "basket shapes" signal), and ``overlay_render`` (str, AC-4: the
        cascade's real STRUCTURE — nesting/tier content — via
        ``symphony_schema.render_rules_text``, not just the 4 flattened
        scalars above, which alone cannot distinguish a genuine multi-tier
        scale-in from N unrelated single-tier cascades merged). NEVER
        carries ``oos_metrics``/``sharpe`` — every field is derived purely
        from structural detection, per AC-3's "never trusts incoming
        oos_metrics.sharpe". AC-4 dedup: cascades whose full pattern
        signature (all 5 fields above) exactly matches one already
        collected are skipped, so a corpus of near-identical candidates
        does not surface as N raw near-duplicate entries. Empty list when
        Atlas is unavailable, the corpus is empty, no candidate is
        frontrunner-shaped, or any failure occurs (D-1 — never raises).
    """
    try:
        from advisors import community_strats, frontrunner_detector

        result = community_strats.load_community_strategies()
        if not result.get("available"):
            return []

        patterns: list[dict] = []
        seen_signatures: set[tuple] = set()
        for doc in result.get("candidates") or []:
            try:
                tree = doc.get("tree") if isinstance(doc, dict) else None
                if not isinstance(tree, dict):
                    continue
                detection = frontrunner_detector.detect_frontrunner_cascades(tree)
                for cascade in detection.cascades:
                    overlay_tree = cascade.overlay_tree
                    pattern = {
                        "vix_tickers": sorted(cascade.vix_tickers),
                        "rsi_thresholds": list(cascade.rsi_thresholds),
                        "watched_tickers": sorted(_collect_step_keyed_signal_tickers(overlay_tree)),
                        "basket_node_count": _count_tree_nodes(overlay_tree),
                        "overlay_render": symphony_schema.render_rules_text(overlay_tree),
                    }
                    signature = (
                        tuple(pattern["vix_tickers"]),
                        tuple(pattern["rsi_thresholds"]),
                        tuple(pattern["watched_tickers"]),
                        pattern["basket_node_count"],
                        pattern["overlay_render"],
                    )
                    if signature in seen_signatures:
                        continue
                    seen_signatures.add(signature)
                    patterns.append(pattern)
            except Exception:
                # One malformed/undetectable candidate doc must never abort
                # the rest of the corpus (D-1) — skip it and continue.
                logger.debug(
                    "_gather_atlas_frontrunner_patterns: skipping malformed candidate doc",
                    exc_info=True,
                )
                continue

        return patterns

    except Exception:
        logger.debug("_gather_atlas_frontrunner_patterns: unexpected error", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# frontrunner-proposal-identity (2026-08-20): honest proposal-identity
# construction. A frontrunner proposal's candidate_tree is a FULL standalone
# copy of the incumbent symphony with one cascade replaced by a generated
# overlay -- these helpers make every Composer-facing name/description say
# so explicitly, instead of the old generic "Frontrunner Candidate — <hash>"
# label. Consumed by _run_build_for_symphony's persist site (below) and by
# approve_frontrunner_proposal's save_symphony call.
# ---------------------------------------------------------------------------

# AC-3 fallback: any overlay shape that doesn't yield a specific, derivable
# flat-condition summary (compound conditions, malformed/missing condition,
# non-dict input) degrades to this exact literal — never a fabricated guess.
_OVERLAY_SUMMARY_FALLBACK: str = "compound condition overlay"

# F10 (Revise 2): shared AC-6 fallback text -- the SINGLE source of truth for
# both this module's own description builder (below) and app.py's template
# context (threaded in as a Jinja variable), closing a drift risk between the
# Python copy and the template's own copy of the same honest-degrade phrase.
# Public (no leading underscore) -- app.py imports it directly. No trailing
# period: callers append their own sentence-ending punctuation as needed.
OVERLAY_NOT_RECORDED_TEXT: str = "overlay not recorded for this proposal"

# Defensive bound on the overlay_summary snippet embedded in a proposal
# description -- keeps the assembled description well under
# MAX_PROPOSAL_DESCRIPTION_CHARS even for a pathological (e.g. very long)
# summarize_overlay output; the final assembly is also hard-capped as
# belt-and-suspenders.
_MAX_OVERLAY_SUMMARY_CHARS_IN_DESCRIPTION: int = 300

# F7 (Revise 2): bounds display_name itself before embedding it in the
# description (the name builder already bounded display_name; the
# description builder previously did not) -- independent of the overall
# MAX_PROPOSAL_DESCRIPTION_CHARS truncation-with-safety-sentence-preserved
# restructure below, which is the second, structural half of the F7 fix.
_MAX_DISPLAY_NAME_CHARS_IN_DESCRIPTION: int = 200

# The universal safety sentence every description carries, regardless of
# source. F7: built and appended LAST (after any needed truncation of
# everything else) so it always survives intact, instead of risking
# truncation-from-the-tail chopping it off a sufficiently long description.
_SAFETY_SENTENCE: str = "Undeployed candidate — review before investing."


def resolve_incumbent_display_name(bot_state: dict, symphony_id: str) -> str:
    """F8 (Revise 2): hash->name lookup against ``bot_state``; honest
    fallback to ``symphony_id`` itself (never fabricates, never raises) when
    unresolvable -- the single shared source of truth for BOTH the Composer
    upload name/description (``approve_frontrunner_proposal``, below) and the
    dashboard card identity line (``app.py``'s ``ai_advisor_tab()`` prefetch
    loop), closing an honesty-drift risk between the two surfaces. Public (no
    leading underscore) -- app.py imports it directly."""
    entry = bot_state.get(symphony_id) if isinstance(bot_state, dict) else None
    if isinstance(entry, dict) and "name" in entry:
        return entry["name"]
    return symphony_id


def _truncate_display_name_to_fit(display_name: str, formatter, max_chars: int) -> str:
    """Return a (possibly truncated + ellipsis-marked) display_name such that
    ``formatter(display_name)`` fits within ``max_chars``. ``formatter`` is a
    callable that embeds ``display_name`` exactly once into a larger fixed
    template -- only the display_name portion is ever shortened, so any fixed
    suffix in the template (e.g. a trailing "#{proposal_id})" token) always
    survives intact."""
    full = formatter(display_name)
    if len(full) <= max_chars:
        return display_name
    fixed_len = len(full) - len(display_name)
    # Reserve 1 char for the "…" ellipsis marker appended below.
    available = max(0, max_chars - fixed_len - 1)
    return display_name[:available] + "…"


def build_proposal_symphony_name(display_name: str, proposal_id: int, source: str) -> str:
    """AC-1/AC-7: the Composer-facing symphony name for an approved
    frontrunner proposal. Two locked formats depending on ``source`` (never
    resolves hash-vs-name itself -- that's the caller's job, see
    approve_frontrunner_proposal); bounded to MAX_PROPOSAL_NAME_CHARS by
    truncating display_name (never the "#{proposal_id})" suffix, which
    always survives intact). F2 (Revise 2): a falsy (empty-string)
    display_name -- a real production condition, since
    strategy_builder_scheduler keys every observation to symphony_id="" --
    OMITS the name-slot segment entirely rather than rendering a blank
    identity slot (leading/double space)."""
    if source == "strategy_builder_retrofit":

        def _fmt(name: str) -> str:
            prefix = (
                f"Strategy Builder candidate for {name} " if name else "Strategy Builder candidate "
            )
            return f"{prefix}(from scratch, #{proposal_id})"
    else:

        def _fmt(name: str) -> str:
            prefix = f"{name} + " if name else ""
            return f"{prefix}FR overlay (full copy, #{proposal_id})"

    fitted_name = _truncate_display_name_to_fit(display_name, _fmt, MAX_PROPOSAL_NAME_CHARS)
    return _fmt(fitted_name)


def build_proposal_symphony_description(
    *,
    display_name: str,
    incumbent_hash: str,
    proposal_id: int,
    created_at: str,
    source: str,
    replaced_node_id: str | None = None,
    overlay_summary: str | None = None,
) -> str:
    """AC-2/AC-6/AC-7: the Composer-facing symphony description for an
    approved proposal. Always carries display_name/incumbent_hash/
    proposal_id/created_at plus the universal review-before-investing
    sentence. For source="frontrunner_builder" the "Full standalone copy —
    the original symphony is not modified." sentence is UNCONDITIONAL -- a
    property of the splice itself, not of whether the overlay-identity
    metrics were recorded; only the specific replaced-node/overlay-summary
    detail degrades to the shared OVERLAY_NOT_RECORDED_TEXT fallback when
    either is None (AC-6 legacy row -- never a bare Python "None" leaking via
    naive f-string formatting). For source="strategy_builder_retrofit" the
    full-copy sentence never appears (false for a from-scratch candidate) --
    a from-scratch/does-not-contain-the-incumbent's-logic sentence is used
    instead. F4 (Revise 2): the FIRST sentence is itself source-branched --
    the retrofit branch's first sentence never claims "Frontrunner Builder
    candidate" or an "incumbent" relationship. F2 (Revise 2): a falsy
    (empty-string) display_name omits the blank "for {name}" identity slot
    from whichever first sentence applies. F7 (Revise 2): display_name is
    independently bounded before embedding, AND the universal safety
    sentence is built and appended LAST so it always survives intact
    regardless of how long any other field is. Bounded to
    MAX_PROPOSAL_DESCRIPTION_CHARS."""
    bounded_display_name = display_name
    if len(bounded_display_name) > _MAX_DISPLAY_NAME_CHARS_IN_DESCRIPTION:
        bounded_display_name = bounded_display_name[:_MAX_DISPLAY_NAME_CHARS_IN_DESCRIPTION] + "…"

    if source == "strategy_builder_retrofit":
        first_sentence = (
            f"Strategy Builder candidate for {bounded_display_name}, "
            f"proposal #{proposal_id}, created {created_at}."
            if bounded_display_name
            else f"Strategy Builder candidate, proposal #{proposal_id}, created {created_at}."
        )
        parts: list[str] = [
            first_sentence,
            "From-scratch Strategy Builder candidate — does not contain the incumbent's logic.",
        ]
    else:
        first_sentence = (
            f"Frontrunner Builder candidate for {bounded_display_name} "
            f"(incumbent {incumbent_hash}), proposal #{proposal_id}, created {created_at}."
            if bounded_display_name
            else f"Frontrunner Builder candidate (incumbent {incumbent_hash}), "
            f"proposal #{proposal_id}, created {created_at}."
        )
        parts = [first_sentence]
        if replaced_node_id is not None and overlay_summary is not None:
            bounded_summary = overlay_summary
            if len(bounded_summary) > _MAX_OVERLAY_SUMMARY_CHARS_IN_DESCRIPTION:
                bounded_summary = bounded_summary[:_MAX_OVERLAY_SUMMARY_CHARS_IN_DESCRIPTION] + "…"
            parts.append(f"Replaces node {replaced_node_id}: {bounded_summary}.")
        else:
            parts.append(f"{OVERLAY_NOT_RECORDED_TEXT}.")
        # Unconditional for frontrunner_builder source (team-lead ruling,
        # plan-approval correction): the full-copy property belongs to the
        # SPLICE, not to whether the overlay-identity metrics were recorded
        # -- a legacy row is still a full spliced copy.
        parts.append("Full standalone copy — the original symphony is not modified.")

    body = " ".join(parts)
    # F7(b): bound everything ELSE to fit, then append the safety sentence
    # last -- guarantees description.endswith(_SAFETY_SENTENCE) regardless of
    # how long display_name/overlay_summary are, instead of truncating the
    # fully-assembled string from the tail (which could chop the safety
    # sentence off).
    max_body_chars = max(0, MAX_PROPOSAL_DESCRIPTION_CHARS - len(_SAFETY_SENTENCE) - 1)
    if len(body) > max_body_chars:
        body = body[:max_body_chars]
    return f"{body} {_SAFETY_SENTENCE}"


def _find_first_asset_ticker(nodes) -> str | None:
    """Recursively walk a build-plan-DSL then/else node list for the FIRST
    ``{"kind": "asset", "ticker": ...}`` leaf, however nested inside
    weight/group wrapper nodes OR a nested if/if_compound node's OWN
    then/else branches. Grounded directly in this module's own
    _EXAMPLE_OVERLAY shapes: a scheme='equal' weight node's "children" is a
    bare list of asset nodes; a scheme='specified' weight node's "children"
    is a list of {"node": ASSET_NODE, "pct": N} wrappers; either shape may
    also appear as a bare asset node directly in the list (no weight
    wrapper). F1 (Revise 2): a 2-tier scale-in overlay -- the generation
    prompt's own flagship worked example (HARD REQUIREMENT #4: nested
    if-nodes inside the OUTER node's "then" branch) -- nests ANOTHER
    {"kind":"if"/"if_compound", "then":[...], "else":[...]} node inside a
    then/else list; that nested node has neither "children" nor "node", so
    it is descended into explicitly (its own "then" first, then "else") --
    same "the first asset leaf found by walking, however nested" contract.
    Never raises; returns None if nothing derivable is found."""
    if not isinstance(nodes, list):
        return None
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("kind") == "asset" and node.get("ticker"):
            return node.get("ticker")
        if node.get("kind") in ("if", "if_compound"):
            found = _find_first_asset_ticker(node.get("then"))
            if found:
                return found
            found = _find_first_asset_ticker(node.get("else"))
            if found:
                return found
            continue
        wrapped = node.get("node")
        if isinstance(wrapped, dict):
            found = _find_first_asset_ticker([wrapped])
            if found:
                return found
        children = node.get("children")
        if isinstance(children, list):
            found = _find_first_asset_ticker(children)
            if found:
                return found
    return None


def summarize_overlay(overlay_tree: dict | None) -> str:
    """AC-3: a short, specific, human-readable one-liner describing what a
    generated frontrunner overlay DOES -- persisted into metrics_json and
    rendered on proposal cards. Operates on the raw pre-compile build-plan-
    DSL overlay node (kind="if"/"if_compound", a "condition" dict, "then"/
    "else" lists of DSL nodes) -- the same shape generate_candidate_overlay
    returns, NEVER the compiled Composer step-shape.

    A flat condition (``condition`` carries an "lhs_fn" key, a non-None
    "window", and a non-None ``rhs["fixed"]``) yields a specific summary
    naming both the signal ticker (condition["lhs_ticker"]) and the
    fire-branch ticker (the first asset leaf found by walking "then", now
    genuinely recursive through nested if-structure via
    _find_first_asset_ticker -- F1, Revise 2). Any other shape -- compound
    condition, missing/malformed "condition", missing "window"/rhs["fixed"]
    (F6, Revise 2: required, never interpolated as a literal "None"),
    non-dict input, None -- degrades to the exact literal
    _OVERLAY_SUMMARY_FALLBACK. Never raises for any input.
    """
    try:
        if not isinstance(overlay_tree, dict):
            return _OVERLAY_SUMMARY_FALLBACK
        condition = overlay_tree.get("condition")
        if not isinstance(condition, dict) or "lhs_fn" not in condition:
            return _OVERLAY_SUMMARY_FALLBACK
        signal_ticker = condition.get("lhs_ticker")
        if not signal_ticker:
            return _OVERLAY_SUMMARY_FALLBACK
        window = condition.get("window")
        rhs = condition.get("rhs")
        rhs_val = rhs.get("fixed") if isinstance(rhs, dict) else None
        if window is None or rhs_val is None:
            return _OVERLAY_SUMMARY_FALLBACK
        fire_ticker = _find_first_asset_ticker(overlay_tree.get("then"))
        if not fire_ticker:
            return _OVERLAY_SUMMARY_FALLBACK

        fn = condition.get("lhs_fn", "")
        comparator = condition.get("comparator", "")
        return f"{fn}({signal_ticker},{window}) {comparator} {rhs_val} rotates into {fire_ticker}"
    except Exception:
        logger.debug("summarize_overlay: unexpected error — degrading to fallback", exc_info=True)
        return _OVERLAY_SUMMARY_FALLBACK


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

    cascades = detection.cascades
    if len(cascades) > MAX_CASCADES_PER_SYMPHONY_RUN:
        # AC-12: Fable API budget cap — never a silent drop, always logged.
        logger.info(
            "_run_build_for_symphony: symphony_id=%s %d cascades exceed "
            "MAX_CASCADES_PER_SYMPHONY_RUN (%d) — processing first %d, skipping %d",
            symphony_id,
            len(cascades),
            MAX_CASCADES_PER_SYMPHONY_RUN,
            MAX_CASCADES_PER_SYMPHONY_RUN,
            len(cascades) - MAX_CASCADES_PER_SYMPHONY_RUN,
        )
        cascades = cascades[:MAX_CASCADES_PER_SYMPHONY_RUN]

    # AC-3/AC-12: the Atlas corpus is run-wide (weekly-cached, not
    # cascade-specific) — load it ONCE per symphony run, never per cascade.
    # Loading it inside the loop below made every unmocked test attempt a
    # live Atlas fetch once per cascade (up to MAX_CASCADES_PER_SYMPHONY_RUN
    # times) — the exact "hitting Mongo" failure mode this hoist fixes, on
    # top of being the correct production pattern regardless of test
    # exposure (a warm weekly cache still doesn't need N redundant reads).
    #
    # watched_tickers=[] is INTENTIONAL, not a placeholder to fill in later:
    # _gather_atlas_frontrunner_patterns' watched_tickers param is currently
    # UNUSED (no ticker-relevance filtering implemented — see that function's
    # own docstring), so [] costs nothing today. LANDMINE (frreview P2-2):
    # this call is now run-scoped, not cascade-scoped — if ticker-relevance
    # filtering is ever wired up against that param, THIS call site is the
    # one that must be updated to pass real tickers (e.g. the union of every
    # cascade's watched_tickers, computed before this call), or filtering
    # will silently no-op forever with an empty list.
    atlas_patterns = _gather_atlas_frontrunner_patterns(watched_tickers=[])

    # AC-5 signals hoist (Cluster D wiring): mirrors the atlas_patterns hoist
    # above — load Atlas frontrunner-signals ONCE per symphony run, extract
    # this incumbent's OWN FR-checks ONCE (extract_fr_checks walks the whole
    # tree, not per-cascade), classify ONCE. AC-R2 de-productization
    # (2026-07-16, operator ruling — the classification tab/warehouse
    # persistence was a PM-initiated addition never asked for; the actual ask
    # was the builder consuming live signal data): classification is still
    # computed EVERY run (filter_positive_edge_signal_keys /
    # candidate_contains_tier1_remove_key / build_signal_provenance below),
    # it is simply no longer persisted or rendered on a dashboard tab.
    from advisors import frontrunner_signals  # noqa: PLC0415 - CC-2 lazy

    signals_result = frontrunner_signals.load_frontrunner_signals()
    signal_rows = signals_result.get("signals") or []

    fr_checks = frontrunner_detector.extract_fr_checks(tree)
    classification_rows = _build_classification_rows_from_fr_checks(fr_checks, signal_rows)

    positive_edge_keys = filter_positive_edge_signal_keys(classification_rows)
    edge_signal_context = build_signal_provenance(positive_edge_keys, classification_rows)

    for cascade in cascades:
        watched_tickers = sorted(_collect_step_keyed_signal_tickers(cascade.overlay_tree))
        signal_context = {
            "watched_tickers": watched_tickers,
            "atlas_patterns": atlas_patterns,
            "edge_signals": edge_signal_context,
        }
        result = generate_candidate_overlay(signal_context)
        if result.candidate is None:
            logger.info(
                "_run_build_for_symphony: symphony_id=%s candidate generation failed (%s)",
                symphony_id,
                result.error,
            )
            continue

        # AC-5(b): veto a candidate watching a Tier-1 "remove"-classified
        # fr_key BEFORE any backtest spend — checked on the DSL candidate
        # itself, before splicing/backtesting either side.
        candidate_fr_key = _derive_flat_candidate_fr_key(result.candidate)
        if candidate_fr_key and candidate_contains_tier1_remove_key(
            {"fr_key": candidate_fr_key}, classification_rows
        ):
            logger.info(
                "_run_build_for_symphony: symphony_id=%s candidate watches a "
                "Tier-1 remove-classified fr_key (%s) — vetoed before "
                "backtest spend",
                symphony_id,
                candidate_fr_key,
            )
            continue

        spliced = splice_candidate_into_symphony(
            tree, cascade, result.candidate, compiled_tree=result.compiled_tree
        )
        if spliced is None:
            logger.info(
                "_run_build_for_symphony: symphony_id=%s splice failed — skipping candidate",
                symphony_id,
            )
            continue

        # DE-FR-SIMPLIFY-001 (AC-2, RULING 1; Revise 4 R4-1): the real
        # delta-scoped SIGNAL-LOGIC-ONLY SIMPLIFY operands. The cascade-side
        # operand is now read VERBATIM off the detector's own
        # cascade.signal_logic_node_count — computed once, at detection
        # time, on the pre-stub original subtree — never re-derived here
        # from cascade.overlay_tree (the padded compact overlay, whose
        # stub-marker-based reconstruction Revise 3's own fix was built on
        # and Revise 4 replaces architecturally, since a multi-tier cascade
        # can make the marker search disprovable). Never the whole-tree
        # incumbent/candidate counts (stay ~98-100% of each other for any
        # single-cascade splice). result.compiled_tree (already compiled by
        # generate_candidate_overlay) is passed as the sole argument (F6,
        # Revise 5: the unused ``candidate`` param was dropped) so
        # _count_overlay_node_count reuses it instead of redundantly
        # re-compiling from scratch.
        replaced_cascade_node_count = cascade.signal_logic_node_count
        overlay_node_count = _count_overlay_node_count(result.compiled_tree)

        accepted, metrics = _gate_and_accept_candidate(
            symphony_id=symphony_id,
            incumbent_tree=tree,
            candidate_tree=spliced,
            overlay_node_count=overlay_node_count,
            replaced_cascade_node_count=replaced_cascade_node_count,
            fire_is_else_branch=cascade.fire_is_else_branch,
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

        # AC-5(c): attach the signal provenance (keys + edge stats) this
        # accepted candidate actually watches to the persisted metrics_json.
        # An empty dict (never fabricated) when no flat fr_key is derivable
        # (e.g. a compound candidate) or the watched key has no classified
        # row.
        metrics["signal_provenance"] = (
            build_signal_provenance([candidate_fr_key], classification_rows)
            if candidate_fr_key
            else {}
        )

        # frontrunner-proposal-identity (AC-3): persist the SMALL pre-compile/
        # pre-graft DSL candidate node (result.candidate) exactly -- never
        # `spliced`, whose else-branch contains the ENTIRE incumbent core.
        # cascade/result are already in scope from this same loop iteration --
        # zero change to splice_candidate_into_symphony's signature/return.
        metrics["overlay_tree"] = result.candidate
        metrics["replaced_node_id"] = (
            cascade.overlay_tree.get("id") if isinstance(cascade.overlay_tree, dict) else None
        )
        metrics["overlay_summary"] = summarize_overlay(result.candidate)

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
    overlay_node_count: int | None = None,
    replaced_cascade_node_count: int | None = None,
    fire_is_else_branch: bool = False,
) -> tuple[bool, dict]:
    """AC-6/AC-7: independently re-backtest BOTH incumbent and candidate,
    run them through evaluate_candidate_batch (mandatory, never bypassed),
    and — only for the candidate if it's the gate's ADOPT_CANDIDATE survivor
    — apply the Calmar acceptance gate.

    Parameters
    ----------
    overlay_node_count, replaced_cascade_node_count : int | None
        DE-FR-SIMPLIFY-001 (AC-2): the delta-scoped SIMPLIFY-path operands
        (the real generated overlay vs. the real replaced cascade subtree),
        threaded straight through to ``evaluate_calmar_acceptance``. Additive
        keyword-only, default None — an omitted value makes the SIMPLIFY
        path structurally unreachable in the acceptance gate (fail-closed),
        never a silent fallback to the whole-tree node counts.
    fire_is_else_branch : bool
        DE-FR-SIMPLIFY-001 Revise 4's final pin: the detector-stamped
        ``cascade.fire_is_else_branch`` polarity marker, threaded verbatim
        through to ``evaluate_calmar_acceptance`` — never re-derived here.
        Default False (never SIMPLIFY-declining by default on an omitted
        value).

    Returns (accepted, metrics). On acceptance, metrics is a dict suitable
    for frontrunner_proposals.metrics_json (incumbent-vs-candidate Calmar/
    CAGR/MDD/node-count deltas, AC-8). On a gate or Calmar rejection, metrics
    carries 'reject_reason' PLUS the same incumbent-vs-candidate CAGR/MDD/
    node-count deltas (AC-11: "rejected item w/ reason+deltas") — the caller
    uses their presence (e.g. "candidate_cagr" in metrics) to decide whether
    a rejected-candidate audit record is persistable. A2 (Revise 3 addendum):
    all three of these outcomes (accept/gate-reject/calmar-reject) ALSO carry
    the raw ``overlay_node_count``/``replaced_cascade_node_count`` params
    verbatim — the admission/rejection BASIS, so an accepted or rejected
    proposal is never indistinguishable from one decided on stale/wrong
    operands. A pre-gate failure (either backtest call erroring) has no
    valid comparison data yet, so its metrics dict carries ONLY
    'reject_reason' — same for the catch-all unexpected-error path. Never
    raises (D-1).
    """
    try:
        from advisors.backtest_gate_engine import (
            BacktestCandidate,
            _fold_transform_single,
            evaluate_candidate_batch,
        )
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
        # AC-G2-1: the baseline is fold-matched (_fold_transform_single) — the
        # incumbent's validation-fold sum via the IDENTICAL 60/20/20 +
        # PURGE_DAYS/EMBARGO_DAYS transform the gate applies to the candidate
        # internally (backtest_gate_engine.py:551-552) — not a full-history
        # sum. A fold-vs-full mismatch previously biased Gate#2 toward
        # KEEP_INCUMBENT for any profitable incumbent regardless of how much
        # better the candidate's per-day return was (fold-vs-full defect;
        # same defect class already fixed the same way in
        # logic_change_engine.py's H6/RC-1).
        #
        # AC-G2-6: the fold-transform itself can degenerate on a short
        # incumbent series — _fold_transform_single returns oos_alpha=0.0
        # (its hardcoded thin-series sentinel) whenever purge_integrity_ok is
        # False or thin_window is True, silently collapsing Gate#2 to a
        # "beat zero" bar (fail-OPEN — a regression this fix itself would
        # otherwise introduce, since the pre-AC-G2-1 full-series sum at least
        # produced a real number). The candidate side already hard-vetoes on
        # its own purge_integrity_ok/thin_window ("never fabricate a pass for
        # a thin series") — an incumbent baseline built on an equally
        # purge-broken/thin fold is equally untrustworthy, so it gets the
        # same conservative-withhold treatment: float("inf") makes
        # `oos_alpha <= incumbent_oos_alpha` (acceptance_gate.py's
        # Stage-2 OOS-superiority check) always true, i.e. KEEP_INCUMBENT —
        # never a silent fallback to beats-zero. Mirrors
        # _SPY_UNAVAILABLE_DEFAULT_OOS_ALPHA's existing edge-14 pattern
        # (backtest_gate_engine.py:196). Both flags are read from the fold
        # result itself, never re-derived locally, and the sentinel stays a
        # local variable — it is never written into a persisted metrics dict.
        incumbent_fold = _fold_transform_single(incumbent_returns_pct)
        incumbent_oos_alpha = (
            float("inf")
            if not incumbent_fold.purge_integrity_ok or incumbent_fold.thin_window
            else incumbent_fold.oos_alpha
        )
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
                # OVERLAY_BACKTEST_SELECTION (not the autotuner's own
                # BACKTEST_SELECTION) — the actual isolation mechanism, see
                # _DOF_LEDGER_SPEC_BUNDLE_SENTINEL's module-level comment.
                evidence_source="OVERLAY_BACKTEST_SELECTION",
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
        if (
            candidate_gate_result is None
            or candidate_gate_result.verdict.decision != "ADOPT_CANDIDATE"
        ):
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
                # A2 (Revise 3 addendum): the delta-scoped SIMPLIFY operands
                # themselves — the admission/rejection basis — persisted on
                # every outcome, not just the acceptance path.
                "overlay_node_count": overlay_node_count,
                "replaced_cascade_node_count": replaced_cascade_node_count,
            }

        # AC-7: Calmar acceptance — gate survival is necessary but not
        # sufficient; the candidate must ALSO improve or preserve+simplify.
        acceptance = evaluate_calmar_acceptance(
            incumbent_metrics,
            candidate_metrics,
            incumbent_node_count=incumbent_node_count,
            candidate_node_count=candidate_node_count,
            overlay_node_count=overlay_node_count,
            replaced_cascade_node_count=replaced_cascade_node_count,
            fire_is_else_branch=fire_is_else_branch,
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
                # A2: same rationale as the gate-reject branch above.
                "overlay_node_count": overlay_node_count,
                "replaced_cascade_node_count": replaced_cascade_node_count,
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
            # A2: the admission basis, persisted on the accepted path too.
            "overlay_node_count": overlay_node_count,
            "replaced_cascade_node_count": replaced_cascade_node_count,
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


# Composer's real asset-class enum for a symphony holdings/score tree, per
# docs/research/composer/baseline__2026-05-12.md:86 ("asset_class":
# "EQUITIES|CRYPTO|OPTIONS", verbatim from the holdings response schema).
# Must stay consistent with composer_draft_client._DEFAULT_ASSET_CLASS /
# the Composer create contract -- both currently hardcode "EQUITIES"
# independently, with no shared source-of-truth reference between them.
_COMPOSER_ASSET_CLASSES: tuple[str, ...] = ("EQUITIES", "CRYPTO", "OPTIONS")


def _resolve_draft_asset_class(candidate_tree: dict | None) -> str:
    """Derive the Composer asset_class for a draft symphony from its
    candidate_tree. D-1 never-raises -- any malformed/None/non-dict tree or
    internal read error degrades to "EQUITIES" (composer_draft_client's own
    default), never propagated as an exception.

    Precedence: a present top-level `asset_class` string is DECISIVE -- a
    case-exact member of `_COMPOSER_ASSET_CLASSES` is used verbatim; an
    out-of-enum string still decides the outcome (EQUITIES), and either way
    the `asset_classes` array is never consulted. The array is consulted
    ONLY when the top-level value is absent, empty, or non-string -- a
    non-empty array whose elements are all the SAME in-enum string is used;
    a mixed, out-of-enum, empty, or non-list array falls back to EQUITIES,
    as does an array containing any non-string element (a shape
    malformation, not a discarded value).

    Observability: a WARNING is logged when a present, non-empty,
    recognizable-shape value is discarded to the EQUITIES fallback (an
    out-of-enum top-level string, or a mixed/out-of-enum asset_classes
    array of strings), and again whenever the final derived result is
    non-EQUITIES (CRYPTO/OPTIONS) -- live Composer acceptance of a derived
    non-EQUITIES value is unverified pending an operator-gated task-zero
    live-create test. The normal absent-key/empty-array/empty-string cases
    and shape malformations (non-list asset_classes, an asset_classes array
    containing any non-string element, non-string top-level asset_class)
    stay silent -- those are absence/malformation, not a discarded value.
    """
    try:
        if not isinstance(candidate_tree, dict):
            return "EQUITIES"

        top_level = candidate_tree.get("asset_class")
        if isinstance(top_level, str) and top_level:
            if top_level in _COMPOSER_ASSET_CLASSES:
                if top_level != "EQUITIES":
                    logger.warning(
                        "_resolve_draft_asset_class: forwarding non-EQUITIES "
                        "asset_class=%r to draft creation (unverified against "
                        "live Composer acceptance)",
                        top_level,
                    )
                return top_level
            logger.warning(
                "_resolve_draft_asset_class: discarding unrecognized top-level "
                "asset_class=%r, falling back to EQUITIES",
                top_level,
            )
            # A present top-level string is decisive even when out-of-enum
            # -- the asset_classes array must never be consulted once a
            # present string was found (AC-4: "array consulted ONLY when
            # top-level absent").
            return "EQUITIES"

        array = candidate_tree.get("asset_classes")
        # A non-string element (unhashable or merely non-string) is a SHAPE
        # MALFORMATION, same category as a non-list asset_classes or a
        # non-string top-level asset_class -- silent EQUITIES fallback, no
        # warning. `all(isinstance(x, str) ...)` is checked explicitly
        # BEFORE dedup so an unhashable element can never raise TypeError
        # out of set(array) here.
        if isinstance(array, list) and array and all(isinstance(x, str) for x in array):
            distinct = set(array)
            if len(distinct) == 1:
                (only,) = distinct
                if only in _COMPOSER_ASSET_CLASSES:
                    if only != "EQUITIES":
                        logger.warning(
                            "_resolve_draft_asset_class: forwarding non-EQUITIES "
                            "asset_class=%r (derived from asset_classes array) to "
                            "draft creation (unverified against live Composer "
                            "acceptance)",
                            only,
                        )
                    return only
            logger.warning(
                "_resolve_draft_asset_class: discarding unusable "
                "asset_classes=%r, falling back to EQUITIES",
                array,
            )

        return "EQUITIES"
    except Exception:
        return "EQUITIES"


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

        # AC-12: self-imposed local-count runaway-creation guard — checked
        # BEFORE every save_symphony call (never for the idempotent no-op
        # above, which creates nothing new). Fails CLOSED (skip) if the count
        # itself can't be determined — same posture as verify_undeployed's
        # own fail-closed-on-any-error contract; an approval this module
        # can't confidently bound is not one it silently lets through.
        try:
            uploaded_count = database.count_uploaded_frontrunner_proposals()
        except Exception as exc:
            logger.warning(
                "approve_frontrunner_proposal: proposal_id=%s upload-count check failed "
                "(%s) — failing closed, refusing to create",
                proposal_id,
                type(exc).__name__,
            )
            return ApprovalResult(success=False, error=type(exc).__name__)

        if uploaded_count >= MAX_FRONTRUNNER_UPLOADS_PENDING_REVIEW:
            database.update_frontrunner_proposal_status(
                proposal_id,
                approval_status="approved",
                error_message=(
                    f"upload cap reached ({uploaded_count}/"
                    f"{MAX_FRONTRUNNER_UPLOADS_PENDING_REVIEW}) — manual review required"
                ),
            )
            logger.warning(
                "approve_frontrunner_proposal: proposal_id=%s upload cap reached "
                "(%d/%d) — refusing to create",
                proposal_id,
                uploaded_count,
                MAX_FRONTRUNNER_UPLOADS_PENDING_REVIEW,
            )
            return ApprovalResult(success=False, error="upload_cap_reached")

        candidate_tree = proposal["candidate_tree"]

        # frontrunner-proposal-identity (AC-1/AC-2/AC-6/AC-7): resolve an
        # honest display name (falls back to the raw Composer hash when the
        # incumbent isn't found in bot_state) and build the locked name/
        # description formats -- replaces the old generic
        # "Frontrunner Candidate — <hash>" label. `database` is already
        # lazy-imported above; reused here, never a second import. Any
        # failure in this block is caught by this function's own outer
        # try/except (D-1). F8 (Revise 2): display-name resolution now routes
        # through the shared resolve_incumbent_display_name -- the SAME
        # function app.py's ai_advisor_tab() prefetch loop uses -- instead of
        # this function's own previously-duplicated inline logic.
        bot_state = database.load_state() or {}
        display_name = resolve_incumbent_display_name(bot_state, proposal["symphony_id"])
        # F3 (Revise 2): a TRUTHY non-dict metrics_json (a stored JSON array)
        # is not falsy, so the old `proposal.get("metrics_json") or {}` guard
        # never fired and the subsequent .get() calls raised AttributeError
        # (caught by this function's outer except, but no error_message was
        # persisted first, unlike every other failure branch). isinstance
        # guard mirrors app.py's own equivalent AC-6 guard -- never raises.
        raw_metrics = proposal.get("metrics_json")
        proposal_metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
        name = build_proposal_symphony_name(
            display_name, proposal["id"], proposal["proposal_source"]
        )
        description = build_proposal_symphony_description(
            display_name=display_name,
            incumbent_hash=proposal["symphony_id"],
            proposal_id=proposal["id"],
            created_at=proposal["created_at"],
            source=proposal["proposal_source"],
            replaced_node_id=proposal_metrics.get("replaced_node_id"),
            overlay_summary=proposal_metrics.get("overlay_summary"),
        )

        asset_class = _resolve_draft_asset_class(candidate_tree)

        draft_result = composer_draft_client.save_symphony(
            name=name,
            description=description,
            color="#4287f5",
            hashtag="#frontrunner",
            raw_value=candidate_tree,
            already_uploaded_symphony_id=proposal.get("created_symphony_id"),
            asset_class=asset_class,
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


# ---------------------------------------------------------------------------
# AC-5: builder generation gating on signal data + the crossover/vs
# classification-row-building functions (PM ruling extension — crossover/vs
# FRChecks are always represented in the in-memory classification_rows list,
# never silently dropped).
# ---------------------------------------------------------------------------


def filter_positive_edge_signal_keys(classification_rows: list[dict]) -> list[str]:
    """AC-5(a): return only the fr_keys classified "keep" — the sole
    positive-edge tier (cagr>0 and sharpe>0 by classify_fr_checks'
    construction). Candidate generation may only propose from these keys.
    Never raises — malformed rows are skipped, never fabricated."""
    return [
        row["fr_key"]
        for row in classification_rows or []
        if isinstance(row, dict) and row.get("classification") == "keep" and row.get("fr_key")
    ]


def candidate_contains_tier1_remove_key(candidate: dict, classification_rows: list[dict]) -> bool:
    """AC-5(b): True if the candidate's watched fr_key is classified "remove"
    (Tier 1) in the current classification snapshot — the caller vetoes the
    candidate BEFORE any backtest spend. Pure lookup, no I/O — never calls
    the backtest seam itself."""
    fr_key = candidate.get("fr_key") if isinstance(candidate, dict) else None
    if not fr_key:
        return False
    remove_keys = {
        row["fr_key"]
        for row in classification_rows or []
        if isinstance(row, dict) and row.get("classification") == "remove" and row.get("fr_key")
    }
    return fr_key in remove_keys


def build_signal_provenance(fr_keys: list[str], classification_rows: list[dict]) -> dict[str, dict]:
    """AC-5(c): {fr_key: {cagr, sharpe, classification, ...}} for the given
    fr_keys, sourced from the current classification snapshot — the
    provenance record attached to each candidate. Keys absent from
    classification_rows are simply omitted, never fabricated."""
    rows_by_key = {
        row["fr_key"]: row
        for row in classification_rows or []
        if isinstance(row, dict) and row.get("fr_key")
    }
    return {key: rows_by_key[key] for key in fr_keys or [] if key in rows_by_key}


# Three canonical display-identity formats (PM ruling — containment rule: all
# format construction lives HERE, in ONE place, so no ad-hoc format ever
# appears elsewhere). Non-collision invariant: a genuine Atlas fr_key's third
# segment is always a plain number (int or decimal); both display forms below
# always contain a letter + parenthesis, which can never appear in a genuine
# numeric segment — structurally disjoint string spaces.


def format_crossover_fr_key(*, ticker: str, window: int | None, rhs_fn: str, rhs_val: str) -> str:
    """Display-identity for a crossover FRCheck (fr_key=None in the pure
    dataclass) — reproducible byte-for-byte given the same node."""
    return f"{ticker}:{window}:xover({rhs_fn},{rhs_val})"


def format_vs_fr_key(*, ticker: str, window: int | None, rhs_ticker: str) -> str:
    """Display-identity for a ticker-vs-ticker FRCheck (fr_key=None in the
    pure dataclass). Plain f-string — a None window renders literally as the
    string "None" (no special-casing)."""
    return f"{ticker}:{window}:vs({rhs_ticker})"


def build_classification_row_for_crossover(
    *, ticker: str, window: int | None, rhs_fn: str, rhs_val: str, branch_path: list[str]
) -> dict:
    """A classification_rows-shaped dict for a crossover FRCheck — PM ruling:
    crossover checks are ALWAYS represented in the in-memory classification_rows
    list, never silently dropped. Always classification="no_edge_data"
    (nothing to join a crossover key to), every edge stat None."""
    return {
        "fr_key": format_crossover_fr_key(
            ticker=ticker, window=window, rhs_fn=rhs_fn, rhs_val=rhs_val
        ),
        "fn": None,
        "comparator": None,
        "branch_path": branch_path,
        "rsi_live": None,
        "rsi_live_at": None,
        "cagr": None,
        "sharpe": None,
        "sortino": None,
        "calmar": None,
        "max_drawdown": None,
        "classification": "no_edge_data",
        "signal_fetch_ts": None,
    }


# ---------------------------------------------------------------------------
# AC-5 wiring (Cluster D) — orchestration-level helpers consumed by
# _run_build_for_symphony. Every function above this section was already
# unit-tested but had zero production call sites; these two helpers are the
# glue that actually wires them into the real build path.
# ---------------------------------------------------------------------------


def _build_classification_rows_from_fr_checks(
    fr_checks: list, signal_rows: list[dict]
) -> list[dict]:
    """Convert one symphony run's ``extract_fr_checks`` output into
    classification rows consumed by ``filter_positive_edge_signal_keys`` /
    ``candidate_contains_tier1_remove_key`` / ``build_signal_provenance`` —
    dispatching each ``FRCheck`` to the right path per its own documented
    invariant (exactly one of ``fr_key`` or ``rhs_ticker`` populated):

      - ``fr_key`` populated (genuine fixed-threshold check): joined against
        ``signal_rows`` via the real ``classify_fr_checks`` (AC-4).
      - ``rhs_ticker`` populated (genuine ticker-vs-ticker crossover, never
        joinable — no fixed threshold to look up): represented directly as a
        ``no_edge_data`` row carrying a ``format_vs_fr_key`` display key (PM
        ruling: crossover checks are always represented, never silently
        dropped).
      - ``rhs_fn`` populated without ``rhs_ticker`` (the ``xover(...)``
        shape kept on the dataclass for shape stability — no live
        population path under the verdict-confirmed discriminator, see
        ``FRCheck``'s own docstring): routed through
        ``build_classification_row_for_crossover`` for completeness.
      - Neither populated (a malformed FRCheck violating its own
        invariant): skipped — never fabricated.

    Never raises (D-1) — ``classify_fr_checks`` is itself never-raising, and
    this function performs no I/O of its own.
    """
    from advisors import frontrunner_signals  # noqa: PLC0415 - CC-2 lazy

    classifiable: list[dict] = []
    prebuilt: list[dict] = []
    for check in fr_checks or []:
        if check.fr_key:
            classifiable.append(
                {
                    "fr_key": check.fr_key,
                    "fn": check.fn,
                    "comparator": check.comparator,
                    "branch_path": check.branch_path,
                }
            )
        elif check.rhs_ticker:
            prebuilt.append(
                {
                    "fr_key": format_vs_fr_key(
                        ticker=check.ticker, window=check.window, rhs_ticker=check.rhs_ticker
                    ),
                    "fn": check.fn,
                    "comparator": check.comparator,
                    "branch_path": check.branch_path,
                    "rsi_live": None,
                    "rsi_live_at": None,
                    "cagr": None,
                    "sharpe": None,
                    "sortino": None,
                    "calmar": None,
                    "max_drawdown": None,
                    "classification": "no_edge_data",
                    "signal_fetch_ts": None,
                }
            )
        elif check.rhs_fn:
            prebuilt.append(
                build_classification_row_for_crossover(
                    ticker=check.ticker,
                    window=check.window,
                    rhs_fn=check.rhs_fn,
                    rhs_val=check.rhs_val or "",
                    branch_path=check.branch_path,
                )
            )

    classified = frontrunner_signals.classify_fr_checks(classifiable, signal_rows)
    return classified + prebuilt


def _derive_flat_candidate_fr_key(candidate: dict) -> str | None:
    """Derive the ``TICKER:WINDOW:THRESHOLD`` display key a generated
    candidate's flat (``kind='if'``) condition watches — the same canonical
    format ``extract_fr_checks`` uses for a genuine fixed-threshold
    ``FRCheck``. Used to check a just-generated candidate against the
    current classification snapshot (AC-5(b) veto, AC-5(c) provenance).

    Only the flat shape is handled — a compound (``kind='if_compound'``)
    candidate's condition has no single watched ticker/window/threshold to
    key on. Never raises — a malformed/incomplete/compound condition
    degrades to None (never fabricated).
    """
    if not isinstance(candidate, dict) or candidate.get("kind") != "if":
        return None
    cond = candidate.get("condition")
    if not isinstance(cond, dict):
        return None
    ticker = cond.get("lhs_ticker")
    window = cond.get("window")
    rhs = cond.get("rhs")
    threshold = rhs.get("fixed") if isinstance(rhs, dict) else None
    if not isinstance(ticker, str) or not ticker or threshold is None:
        return None
    return f"{ticker}:{window}:{threshold}"
