"""Frontrunner cascade detector — locates a symphony's incumbent frontrunner
overlay(s) in a live Composer ``/score`` tree (feature-plans/frontrunner-builder.md
AC-2).

A frontrunner overlay is a leading cascade of ``RSI(core-ticker) gt threshold``
``if``-nodes that, when triggered, fire a small VIX/hedge basket ahead of the
symphony's core strategy logic. The cascade->core boundary is a clean SIZE
CLIFF: the fire-basket branch is a small minority of nodes (single-digit to
low-double-digit) versus the sibling branch that continues toward thousands
of nodes of core logic.

Detection signature (derived from direct inspection of the operator's 11 real
captured trees — see feature plan grounding note):
  - a candidate ``if`` node has two ``if-child`` entries: one marked
    ``is-else-condition?: False`` (the condition branch) carrying a flat
    ``lhs-fn`` / ``lhs-val`` / ``comparator`` / ``rhs-val`` RSI-family
    condition, and one marked ``is-else-condition?: True`` (the continuation
    branch, no comparator).
  - whichever branch is SMALLER by node count is the candidate fire branch;
    it is a genuine cascade rung only if it contains >=1 VIX-family ticker.
  - the cascade may recurse into the small branch (scale-in tiers: a nested
    ``if`` firing a heavier hedge at a higher RSI threshold) — the whole
    tiered chain is reported as ONE cascade, its overlay_tree spanning every
    tier.
  - a candidate whose SMALL branch's condition watches the hedge/inverse-VIX
    instrument's OWN indicator (self-referential — e.g. an SVXY-timing gate
    keyed on SVXY's own RSI) is NOT a frontrunner cascade — it is an internal
    inverse-VIX TIMING sub-strategy and is excluded (AC-2).

The detector is fail-loud: it never guesses. A tree with no if-nodes, or with
if-nodes but no small-branch VIX-family ticker anywhere, reports zero cascades
with an explicit ``skip_reason`` — never a fabricated boundary.

Public surface
--------------
Cascade : dataclass
    One detected cascade. Fields: ``overlay_tree`` (the compiled ``if`` subtree
    spanning every tier of this cascade, structurally identical to the source
    tree's node shape), ``rsi_thresholds`` (list of float thresholds across all
    tiers), ``vix_tickers`` (set of VIX-family tickers in the fire basket(s)),
    ``group_name`` (the enclosing parallel sub-strategy's group name, or None
    at the tree root).

DetectionResult : dataclass
    Returned by ``detect_frontrunner_cascades``. Fields: ``cascades`` (list of
    ``Cascade``, possibly empty), ``skip_reason`` (str | None — set whenever
    ``cascades`` is empty, explaining WHY: "no incumbent frontrunner" vs
    "ambiguous — no size-cliff signature found").

detect_frontrunner_cascades(tree: dict) -> DetectionResult
    Walk the tree, find every leading cascade root, resolve scale-in tiers,
    exclude inverse-VIX timing sub-strategies, and return one Cascade per
    detected leading overlay (one per parallel sub-strategy group that has
    one). Never raises (D-1) — a malformed tree degrades to an empty result
    with skip_reason set.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named constants — no magic numbers.
# ---------------------------------------------------------------------------

# VIX-family tickers recognized as hedge/fire-basket instruments (feature plan
# grounding: "fire baskets always contain >=1 VIX-family instrument but not
# always VIXY"). Kept as an explicit named set so the whole detector's
# vocabulary is auditable in one place.
VIX_FAMILY_TICKERS: frozenset[str] = frozenset(
    {"VIXY", "VIXM", "UVXY", "UVIX", "VXX", "SVXY", "SVIX"}
)

# Indicator function names recognized as RSI-family (grounding: "RSI(ticker)
# gt ~77-82.5"). A bare substring match on the fn string tolerates minor
# naming drift ("relative-strength-index" is the observed real value).
_RSI_FN_SUBSTRINGS: tuple[str, ...] = ("relative-strength-index", "rsi")

# Placeholder ticker prefix used by the trimmed test fixtures to mark stubbed
# core-strategy content. A cascade overlay containing this placeholder has
# swallowed core logic across the size-cliff boundary — a hard bug signal.
_CORE_PLACEHOLDER_PREFIX = "CORE_ASSET_"

# The synthetic placeholder-leaf ticker _build_cascade_overlay stamps onto the
# stubbed continuation/core branch it replaces (never real tree content).
# PUBLIC (not underscore-prefixed): consumers outside this module (the
# Frontrunner Builder's SIMPLIFY-path signal-logic-only counter,
# DE-FR-SIMPLIFY-001 Revise 3 RULING 1) must identify this exact marker to
# exclude the stub branch from a node count — importing this constant instead
# of duplicating the literal means a future rename here can never silently
# break that exclusion (the stub would stop matching, get counted again, and
# the CRITICAL SIMPLIFY-inflation defect this constant's consumer fixes would
# return with no warning).
STUBBED_CORE_CONTINUATION_TICKER = "_STUBBED_CORE_CONTINUATION"

# AC-6 REBUILD (2026-07-16): the size-cliff ratio/absolute checks below and
# the overbought-range floor matched 0 real trees (44/456 recovery even after
# fixing direction) — empirically falsified against the operator's own real
# fixture trees (SPY:10:31 threshold=31 and SPY:21:30 threshold=30 both fail
# _RSI_OVERBOUGHT_MIN=50 regardless of direction; the Paragons tree's genuine
# fire branch is on the LARGER side by node count, inverting the ratio
# assumption). _qualifies_as_cascade_rung no longer uses either constant —
# the cascade-root criterion is now the same TRUE-branch-reaches-VIX rule
# extract_fr_checks uses (AC-3), with the classification-quality judgment
# (is this threshold actually a good frontrunner signal) moved to AC-4's
# data-driven join against real Atlas edge stats, never a structural
# heuristic here. Kept defined (unused by the qualifying path) ONLY to anchor
# the regression test proving the old signature's premise was mathematically
# blind (SPY:10:31's 31.0 < _RSI_OVERBOUGHT_MIN=50.0) — never reintroduce
# either as the sole gate (AC-6's own regression guard, team-lead-approved
# 2026-07-16).

# Maximum small-branch/large-branch node-count ratio for an if-node to qualify
# as a genuine cascade rung (the size-cliff signature, AC-2). Calibrated
# against the operator's 11 real trees: every confirmed genuine rung has a
# local ratio <= ~0.29 (the widest observed legit rung is a 226-vs-779-node
# hedge sleeve, ratio ~0.29); a near-balanced if-node (e.g. an unrelated core
# RSI regime gate with a ~253-vs-227 split, ratio ~1.11) is NOT a cascade rung
# even though its smaller side happens to contain a VIX-family ticker
# somewhere deep inside it. 0.30 sits just above the calibrated legit ceiling.
_SIZE_CLIFF_MAX_RATIO: float = 0.30

# Absolute ceiling on the fire branch's own node count, used as an ALTERNATIVE
# qualifying signal alongside the ratio check above. A hand-built small tree
# (e.g. a 2-node fire basket vs a 2-node core leaf) can never produce a large
# numeric ratio no matter how genuine the cascade is — there simply isn't
# enough tree for a "cliff" to manifest. Calibrated against the operator's 11
# real trees: the widest observed legit fire-basket rung is 33 nodes; the
# smallest confirmed FALSE positive (an unrelated near-balanced core RSI gate)
# had a 253-node "smaller" side. 40 sits comfortably above the real ceiling and
# well below the false-positive floor.
_SIZE_CLIFF_MAX_ABSOLUTE_FIRE_NODES: int = 40

# Plausible RSI-overbought threshold range (grounding note: "RSI(ticker) gt
# ~77-82.5"). A frontrunner cascade's trigger is an OVERBOUGHT condition; a
# low threshold (e.g. "gt 31") is not an overbought signal and marks a
# different-purpose gate (e.g. a volatility-adjusted allocation switch) that
# happens to nest a VIX-family ticker deeper inside one of its own branches —
# excluding these prevents the outer node from being mistaken for a cascade
# root just because a VIX ticker is reachable somewhere beneath it.
_RSI_OVERBOUGHT_MIN: float = 50.0
_RSI_OVERBOUGHT_MAX: float = 100.0

_STEP_IF = "if"
_STEP_IF_CHILD = "if-child"
_STEP_ASSET = "asset"
_STEP_GROUP = "group"

# AC-3/AC-6 fixed-threshold discriminator — RESOLVED (fr-falsifier2's verdict,
# .claude/fr-signals-inputs/mirror-pattern-verdict.md, 2026-07-16). An earlier
# analysis-layer hypothesis ("rhs-fn key presence means the condition is an
# indicator-vs-indicator crossover") was proposed and then FALSIFIED: verdict
# fr-falsifier2's exceptionless full-population sweep (~1,900 rhs-fn-bearing
# if-child nodes across the 11 real trees) proved `rhs-fn`/`rhs-window-days`
# are VESTIGIAL ECHOES from a non-canonical Composer export pathway — present
# on both genuine fixed-threshold nodes (e.g. Paragons: RSI(SPY,10) gt
# moving-average-return(31), rhs-fixed-value?=True, numeric rhs-val="31" — the
# "31" IS the real threshold, not a window; rhs-window-days="1" is the actual
# stale window field) AND genuine ticker-vs-ticker crossovers alike, with zero
# correlation to which one a node actually is. The airtight, zero-exception
# discriminator is simply: does `rhs-val` parse as a number (equivalently,
# `rhs-fixed-value?` truthy, no nested rhs operand)? Numeric = fixed threshold.
# Ticker string = genuine crossover. `rhs-fn` is NEVER consulted for this
# question — see `_parse_rsi_threshold` below, which already implemented this
# correctly before the false rhs-fn hypothesis was ever proposed.


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class Cascade:
    """One detected leading frontrunner cascade (possibly multi-tier)."""

    overlay_tree: dict
    rsi_thresholds: list[float] = field(default_factory=list)
    vix_tickers: set[str] = field(default_factory=set)
    group_name: str | None = None
    signal_logic_node_count: int | None = None
    fire_is_else_branch: bool = False


@dataclass
class DetectionResult:
    """Result of ``detect_frontrunner_cascades``. Never None."""

    cascades: list[Cascade] = field(default_factory=list)
    skip_reason: str | None = None


@dataclass
class FRCheck:
    """One extracted FR-check (AC-3). Returned by ``extract_fr_checks`` —
    one per condition node whose TRUE branch reaches a VIX-family ticker.

    INVARIANT (explicit, hostile-tested, per mirror-pattern-verdict.md):
    exactly one of ``{fr_key populated}`` or ``{rhs_ticker populated}`` is
    true for any FRCheck instance — never zero, never two. Which one tells
    the caller (AC-4's classifier, the builder's persist call site) what KIND
    of condition this is: a genuine fixed-threshold check (joinable to Atlas
    signal data) or a ticker-vs-ticker relative-strength comparison (never
    joinable — no fixed threshold to look up). ``rhs_fn`` MAY additionally be
    populated ALONGSIDE ``rhs_ticker`` (never alongside ``fr_key``) — the RHS
    indicator function genuinely applied to ``rhs_ticker`` (e.g.
    "relative-strength-index" for ``RSI(LQD) gt RSI(XLV)``); it is an
    optional descriptive enrichment, never load-bearing for joinability.
    ``rhs_val`` has no live population path under the verdict-confirmed
    discriminator (kept on the dataclass for shape stability; always None in
    practice — the "indicator-vs-indicator crossover with a numeric RHS
    value" case it was for was proposed, then proven not to exist in real
    data, mirror-pattern-verdict.md).
    """

    fr_key: str | None
    ticker: str
    fn: str
    window: int | None
    comparator: str
    threshold: float | None
    vix_tickers: frozenset[str]
    branch_path: list[str]
    node_id: str | None
    group_name: str | None
    rhs_fn: str | None = None
    rhs_val: str | None = None
    rhs_ticker: str | None = None


# ---------------------------------------------------------------------------
# Tree-walk primitives
# ---------------------------------------------------------------------------


def _count_nodes(node) -> int:
    """Total node count of a subtree. Iterative (explicit stack) — mirrors
    symphony_schema.py's established pattern (P2-1, frreview finding) — so
    a very deep real tree never triggers RecursionError."""
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


def _collect_tickers(node, out: set[str] | None = None) -> set[str]:
    """Collect every asset ticker in a subtree into ``out`` (a fresh set when
    not supplied) and return it. Iterative (explicit stack) — mirrors
    symphony_schema.py's established pattern (P2-1, frreview finding) — so a
    very deep real tree never triggers RecursionError."""
    if out is None:
        out = set()
    if not isinstance(node, dict):
        return out
    stack: list = [node]
    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue
        if current.get("step") == _STEP_ASSET:
            ticker = current.get("ticker")
            if isinstance(ticker, str) and ticker:
                out.add(ticker)
        for child in current.get("children") or []:
            stack.append(child)
    return out


def _is_if_child(node) -> bool:
    return isinstance(node, dict) and node.get("step") == _STEP_IF_CHILD


def _carries_condition(child: dict) -> bool:
    """Return True if this if-child carries an explicit condition — either the
    flat shape (``lhs-fn`` present) or a compound-condition block (``condition``
    present). This is the fallback signal used when ``is-else-condition?`` is
    absent entirely (real trees always set it; a hand-built node may not)."""
    return isinstance(child.get("lhs-fn"), str) or isinstance(child.get("condition"), dict)


def _get_condition_branch_pair(node: dict) -> tuple[dict, dict] | None:
    """Return (condition_child, continuation_child) for an ``if`` node.

    Real trees explicitly mark the condition branch ``is-else-condition?:
    False`` and the continuation branch ``is-else-condition?: True`` — that is
    the primary signal. When neither if-child carries this key (a hand-built
    node may omit it), fall back to identifying the condition branch as
    whichever child actually carries a condition (flat ``lhs-fn`` or a
    compound ``condition`` block) — the other is the continuation branch.

    Returns None if the node does not have exactly this shape, or if neither
    signal can unambiguously identify the condition branch (defensive — never
    guesses on a malformed if-node).
    """
    children = node.get("children") or []
    if_children = [c for c in children if _is_if_child(c)]
    if len(if_children) != 2:
        return None

    cond_child = next((c for c in if_children if c.get("is-else-condition?") is False), None)
    if cond_child is not None:
        else_child = next((c for c in if_children if c is not cond_child), None)
        if else_child is not None:
            return cond_child, else_child

    # Fallback: neither child has an explicit is-else-condition? marker —
    # identify by which one carries an actual condition.
    condition_bearing = [c for c in if_children if _carries_condition(c)]
    if len(condition_bearing) != 1:
        return None
    cond_child = condition_bearing[0]
    else_child = next(c for c in if_children if c is not cond_child)
    return cond_child, else_child


def _is_rsi_condition(cond_child: dict) -> bool:
    lhs_fn = cond_child.get("lhs-fn")
    if not isinstance(lhs_fn, str):
        return False
    lowered = lhs_fn.lower()
    return any(sub in lowered for sub in _RSI_FN_SUBSTRINGS)


def _get_window(node: dict) -> int | None:
    """Resolve a flat condition node's LHS window, handling both real-tree
    conventions: `lhs-fn-params.window` (int) and the `lhs-window-days`
    STRING fallback (fr-test's extraction gotcha, 2026-07-16 — some real
    nodes carry the window this way instead; a walk that only reads
    lhs-fn-params silently mis-keys or misses these checks). Never raises —
    an unparseable value degrades to None."""
    params = node.get("lhs-fn-params")
    if isinstance(params, dict) and params.get("window") is not None:
        try:
            return int(params["window"])
        except (TypeError, ValueError):
            return None
    raw = node.get("lhs-window-days")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _is_ticker_comparison(cond_child: dict) -> bool:
    """True for a genuine ticker-vs-ticker condition (e.g. RSI(LQD) gt
    RSI(XLV)) — `rhs-fixed-value?` is anything OTHER than explicitly True.
    This is the verdict-confirmed (mirror-pattern-verdict.md, E4/X1)
    discriminator: exceptionless across ~1,900 real nodes, ONLY
    `rhs-fixed-value?==True` pairs with a numeric `rhs-val` (166/166 — a
    fixed threshold); BOTH `rhs-fixed-value?==False` (385 nodes) AND the key
    being ABSENT entirely (`rhs-fixed-value?==None`, 510 nodes) pair with a
    ticker `rhs-val` (895/895 combined — a genuine crossover). A real-tree
    node's `rhs-fixed-value?` key is routinely absent altogether rather than
    explicitly `False` (confirmed on a real BND-vs-SH node in real_tree_04,
    id 74083377-ec89-4844-8ec1-d80bc2aae07c, rhs-val="SH", no
    rhs-fixed-value? key at all) — an `is False` check missed that
    population, silently dropping genuine ticker-comparison FRChecks. A
    ticker-vs-ticker node commonly ALSO carries an `rhs-fn` key (mirroring
    the LHS function, since the RHS genuinely IS another indicator
    computation, e.g. real_tree_06's LQD-vs-XLV node: rhs-fixed-value?=False,
    rhs-fn="relative-strength-index", rhs-val="XLV") — that `rhs-fn` is real,
    meaningful data for THIS case (which indicator applies to the RHS
    ticker), unlike the fixed-threshold case below, where any rhs-fn present
    is proven vestigial noise."""
    return cond_child.get("rhs-fixed-value?") is not True


def _parse_rsi_threshold(cond_child: dict) -> float | None:
    """Extract the numeric RSI threshold from a flat condition's rhs-val.

    rhs-val is stored as a string in the real trees ("79", "82.5"). A
    ticker-comparison rhs (rhs-fixed-value? is False, rhs-val is a ticker
    string) is not a fixed threshold — returns None. `rhs-fn`/`rhs-window-days`
    are NEVER consulted here — verdict-confirmed vestigial noise for the
    fixed-threshold question (mirror-pattern-verdict.md); an earlier
    hypothesis that rhs-fn presence alone signaled a crossover was proposed
    and then falsified against the full ~1,900-node population (a node with
    `rhs-fixed-value?=True` and numeric `rhs-val` is ALWAYS a genuine fixed
    threshold, zero exceptions, regardless of any rhs-fn echo).
    """
    if _is_ticker_comparison(cond_child):
        return None
    raw = cond_child.get("rhs-val")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _is_self_referential_timing_gate(cond_child: dict) -> bool:
    """Return True if this if-node's condition watches a VIX-family instrument
    as its own SUBJECT (AC-2/AC-3: exclude internal inverse-VIX timing
    sub-strategies).

    A genuine frontrunner cascade watches an unrelated CORE signal ticker
    (e.g. SPY, QQQ, a sector ETF) and fires a hedge basket; an inverse-VIX
    timing sub-strategy instead watches a hedge/inverse-VIX instrument's OWN
    momentum to decide an internal allocation — the watched (lhs) ticker is
    ITSELF VIX-family. AC-3 simplification (2026-07-16): the subject-ticker
    test alone is the discriminator — the size-based "or watches a ticker
    also present in the small/fire branch" extension from the old size-cliff
    era no longer applies once branch size stops being load-bearing.
    """
    lhs_val = cond_child.get("lhs-val")
    return isinstance(lhs_val, str) and lhs_val in VIX_FAMILY_TICKERS


def _qualifies_as_cascade_rung(node: dict, *, is_nested_tier: bool = False) -> bool:
    """Return True if ``node`` (an ``if`` node) qualifies as a genuine
    frontrunner cascade rung.

    AC-6 REBUILD (2026-07-16): the qualifying criterion is now the SAME
    direction-explicit rule extract_fr_checks (AC-3) uses — does the
    condition's TRUE branch (never inferred from branch size) reach a
    VIX-family ticker — replacing the old size-cliff ratio/absolute-size
    signature and the RSI-overbought-range floor, both of which were
    empirically falsified against the operator's own real trees (see the
    named-constant comments above). The quality judgment (is this a GOOD
    frontrunner signal) is AC-4's job now, not a structural heuristic here.

    Used in two contexts, distinguished by ``is_nested_tier``:
      - Top-level root scan (``is_nested_tier=False``, the default):
        an if-node's condition must NOT be self-referential (must not watch a
        VIX-family ticker's own indicator) to independently qualify as a NEW
        leading cascade — otherwise an internal hedge-timing gate (e.g. an
        RSI(VIXY)-gated weighting between two hedge sub-blends) would be
        mistaken for an unrelated symphony's own leading trigger.
      - Nested-tier scan inside an ALREADY-confirmed fire branch
        (``is_nested_tier=True``): a nested if-node here is a
        SCALE-IN escalation of the SAME cascade (grounding note: "RSI>80->VIX
        blend, >82.5->heavier UVXY tranche") — its condition legitimately CAN
        watch the hedge instrument's own momentum to decide whether to
        escalate further (e.g. RSI(UVXY) gt 28 deciding whether to add more
        UVXY+GLD on top of an already-fired UVXY blend). Excluding these here
        would misclassify a genuine scale-in tier as "unrelated logic" and
        leave its core-content else-branch un-stubbed (a leak, not a fix).

    A qualifying rung requires ALL of:
      - a flat RSI-family condition, comparator 'gt', a parseable numeric
        threshold (via _parse_rsi_threshold, which refuses a ticker-vs-ticker
        RHS — the verdict-confirmed rhs-val-numeric discriminator, reused
        here; rhs-fn is never consulted, mirror-pattern-verdict.md).
      - the condition's TRUE branch (cond_child) contains >=1 VIX-family
        ticker ANYWHERE in its subtree — direction-explicit, size-independent.
      - (root scan only) the condition is not self-referential.

    Never raises — a malformed node degrades to False.
    """
    pair = _get_condition_branch_pair(node)
    if pair is None:
        return False
    cond_child, _else_child = pair
    if not _is_rsi_condition(cond_child):
        return False
    if cond_child.get("comparator") != "gt":
        return False
    threshold = _parse_rsi_threshold(cond_child)
    if threshold is None:
        return False
    if not (_collect_tickers(cond_child) & VIX_FAMILY_TICKERS):
        return False
    return is_nested_tier or not _is_self_referential_timing_gate(cond_child)


def _is_internal_hedge_subgate(node: dict) -> bool:
    """Return True if ``node`` (an ``if`` node reached INSIDE an already-
    confirmed fire branch) is internal hedge-basket logic — a de-escalation
    or weighting sub-gate that is not itself a reportable RSI cascade tier,
    but whose smaller branch is still genuinely hedge content (not core).

    Real trees nest sub-gates keyed on indicators OTHER than RSI inside a
    fire branch — e.g. ``cumulative-return(UVXY) lt 5.5`` deciding whether to
    de-escalate a "Volmageddon protection" blend. These do not qualify as
    cascade RUNGS (``_qualifies_as_cascade_rung`` requires an RSI condition,
    since only RSI-gated rungs are reportable overbought triggers), but their
    own small side is still hedge content that must be compacted (large side
    stubbed) rather than copied verbatim — copying verbatim would leak
    whatever core content sits in that sub-gate's OWN large/else branch.

    Unlike ``_qualifies_as_cascade_rung``, this check does NOT require an RSI
    condition, does NOT require comparator='gt', and does NOT require a
    non-self-referential condition — any indicator function, comparator, or
    self-reference is acceptable here, since this node's role is purely
    "is this hedge-internal machinery that needs its large side stubbed",
    not "is this a reportable cascade trigger".

    FALSIFIED-AND-REVERTED ATTEMPT (2026-07-16, fr-test's cluster-3 finding,
    commit 7ca7c0c6 — independently re-derived and then reverted again in
    this same file by fr-engine before reading that finding): a
    direction-explicit rewrite of this function (checking only cond_child's
    TRUE branch for VIX, matching ``_qualifies_as_cascade_rung``) was tried
    and DIRECTLY FALSIFIED — it introduces NEW failures (real_tree_04,
    real_tree_09 on the VIX-ticker-presence test) by mislabeling a genuinely
    STUBBED/continuation branch as "fire" on cascades where the raw
    if-node's condition-side happens to be the larger, continuation-bound
    side. Root cause: this function's own overlay-construction role
    (``_compact_if_node``) is INTENTIONALLY size-based — a DIFFERENT model
    from ``extract_fr_checks``'s AC-3 direction-explicit walk, which answers
    a different question (does this node QUALIFY, not which physical side
    is fire for compaction). Applying the AC-3 model here was a category
    error conflating two distinct, intentionally-different functions — kept
    size-based, unchanged from wave-1. The real AC-2 leak this was mistaken
    for (a real BND-vs-SH sub-gate in real_tree_04 whose CORE_ASSET_-laden
    branch went unstubbed) is fixed instead at its actual root: the
    verbatim-copy fallback in ``_compact_subtree`` (see its docstring).
    """
    pair = _get_condition_branch_pair(node)
    if pair is None:
        return False
    cond_child, else_child = pair
    cond_n = _count_nodes(cond_child)
    else_n = _count_nodes(else_child)
    small, large = (cond_child, else_child) if cond_n <= else_n else (else_child, cond_child)
    small_n = _count_nodes(small)
    large_n = _count_nodes(large)
    small_tickers = _collect_tickers(small)
    if not (small_tickers & VIX_FAMILY_TICKERS):
        return False
    ratio_qualifies = large_n > 0 and (small_n / large_n) <= _SIZE_CLIFF_MAX_RATIO
    absolute_qualifies = small_n <= _SIZE_CLIFF_MAX_ABSOLUTE_FIRE_NODES
    return ratio_qualifies or absolute_qualifies


# ---------------------------------------------------------------------------
# Cascade-root detection
# ---------------------------------------------------------------------------


def _find_cascade_roots(node, group_name: str | None, out: list[tuple[dict, str | None]]) -> None:
    """Find every candidate cascade-root ``if`` node in the tree.

    A candidate is an ``if`` node whose TRUE branch reaches a VIX-family
    ticker (per the rebuilt ``_qualifies_as_cascade_rung``, AC-6). Once a
    cascade root is found, the walk does NOT re-descend into that node's OWN
    fire (TRUE) branch — its nested tiers are resolved separately by
    ``_build_cascade_overlay``'s own tier-walk, and re-scanning it here would
    duplicate-report a scale-in tier as an independent top-level cascade.

    AC-6 REGRESSION FIX (2026-07-16, defect #3 — the early-continue bug):
    the walk DOES continue into the root's CONTINUATION (ELSE) branch. Real
    trees chain many independent FR gates as sibling if/elif-via-else
    ladders — the OLD behavior (stopping at BOTH branches once any root was
    found) orphaned every sibling gate further down the chain from ever
    being scanned, which was the largest single contributor to the shipped
    detector's ~91% miss rate against the operator's real trees. The walk
    also continues into a node's children when the node itself is not a
    candidate root (e.g. group/weight containers, or an if-node that fails
    the VIX-reachability signature — which may still contain a genuine
    cascade nested inside its own branches, most commonly a different
    parallel sub-strategy).

    Iterative (explicit stack, carrying (node, group_name) pairs — mirrors
    symphony_schema.py's established (node, depth) stack pattern) — P2-1,
    frreview finding — so a very deep real tree never triggers
    RecursionError. Children are pushed in REVERSED order so the LIFO pop
    order matches the original left-to-right, depth-first recursion exactly
    — ``out``'s append order (and therefore cascade-detection order on
    multi-cascade real trees) is unchanged.
    """
    if not isinstance(node, dict):
        return

    stack: list[tuple[dict, str | None]] = [(node, group_name)]
    while stack:
        current, current_group_name = stack.pop()
        if not isinstance(current, dict):
            continue

        step = current.get("step")
        next_group_name = current_group_name
        if step == _STEP_GROUP:
            name = current.get("name")
            if isinstance(name, str) and name:
                next_group_name = name

        if step == _STEP_IF:
            if _qualifies_as_cascade_rung(current):
                out.append((current, next_group_name))
                # AC-6 fix: keep scanning the CONTINUATION (else) branch for
                # sibling cascades chained further down the if/elif ladder —
                # never re-scan the FIRE (true) branch, which is already
                # resolved by _build_cascade_overlay's own tier-walk.
                pair = _get_condition_branch_pair(current)
                if pair is not None:
                    _cond_child, else_child = pair
                    stack.append((else_child, next_group_name))
                continue

            # Not a valid/qualifying if-node (or ambiguous shape) — keep
            # scanning both branches for a nested cascade (e.g. this if-node
            # is itself deep inside another sub-strategy's core, or is an
            # unrelated core RSI gate like the "QQQ lt 30" regime check
            # observed in real trees).
            for child in reversed(current.get("children") or []):
                stack.append((child, next_group_name))
            continue

        for child in reversed(current.get("children") or []):
            stack.append((child, next_group_name))


def _select_fire_and_continuation(
    node: dict, *, pair: tuple[dict, dict] | None = None
) -> tuple[dict, dict] | None:
    """Shared fire/continuation selection helper (single source of truth,
    DE-FR-SIMPLIFY-001 Revise 4). Given a 2-child if-node, returns
    ``(fire_child, continuation_child)`` via the size-based rule this module
    ratified for overlay construction — fire is the smaller of the two
    direct children by raw node count (see ``_compact_if_node``'s docstring
    for the falsified-and-reverted direction-explicit alternative that was
    tried and rejected). Returns ``None`` on a malformed/unidentifiable
    2-child shape.

    Called by BOTH ``_compact_if_node`` (refactored to consume this rather
    than reimplementing the split inline) and the signal-logic node-count
    computation below — the selection algorithm itself must never be
    duplicated between them (team-lead ruling, Revise 4).

    ``pair`` : tuple[dict, dict] | None
        F5 (DE-FR-SIMPLIFY-001 Revise 5): optional PRE-COMPUTED
        ``_get_condition_branch_pair(node)`` result, keyword-only. When
        provided (``_compact_if_node`` already computed its own pair for the
        RSI-threshold parse), it is reused instead of calling
        ``_get_condition_branch_pair`` a second time on the same node —
        eliminating a redundant call, efficiency-only, no correctness
        impact. Defaults to ``None``, in which case this function computes
        the pair itself exactly as before (every OTHER caller, which has no
        pre-computed pair to hand in, is unaffected)."""
    if pair is None:
        pair = _get_condition_branch_pair(node)
    if pair is None:
        return None
    cond_child, else_child = pair
    cond_n = _count_nodes(cond_child)
    else_n = _count_nodes(else_child)
    fire_child = cond_child if cond_n <= else_n else else_child
    continuation_child = else_child if fire_child is cond_child else cond_child
    return fire_child, continuation_child


def _count_clause_aware_signal_logic(node) -> int:
    """General-purpose, non-exclusion node counter — descends ``children``
    AND a compound condition's ``condition``/``conditions`` DATA fields (a
    compound-condition object is never itself exclusion-relevant — it holds
    no further Composer "step"-shaped if-nodes needing fire/continuation
    selection, only clause data). Iterative (explicit stack), never
    recursive, matching this module's own ``_count_nodes``/``_collect_
    tickers`` convention. The single source of truth for clause-aware
    counting — imported verbatim by ``frontrunner_builder`` for the overlay
    SIMPLIFY operand (B3, DE-FR-SIMPLIFY-001 Revise 4), never reimplemented
    there."""
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
        condition_block = current.get("condition")
        if isinstance(condition_block, dict):
            stack.append(condition_block)
        conditions_list = current.get("conditions")
        if isinstance(conditions_list, list):
            for clause in conditions_list:
                if isinstance(clause, dict):
                    stack.append(clause)
    return count


def _compute_signal_logic_node_count(
    if_node: dict, *, selection: tuple[dict, dict] | None = None
) -> int | None:
    """Honest signal-logic node count for the cascade rooted at ``if_node``
    (DE-FR-SIMPLIFY-001 Revise 4, R4-1): the if-node itself, plus its fire
    branch counted for real — a NESTED qualifying if-node (a further
    scale-in tier, or an internal hedge subgate) has its OWN continuation
    excluded via the SAME ``_select_fire_and_continuation`` selection rule
    applied inline, never a fixed stub-marker search (which Revise 3 proved
    disprovable on multi-tier cascades). Computed on the PRE-stub original
    subtree, never the padded compact overlay.

    Iterative (single explicit stack) — no direct or mutual recursion with
    ``_select_fire_and_continuation``/``_count_clause_aware_signal_logic``,
    both of which are one-directional callees, never callers back into this
    function (structurally enforced, see the R4-4 no-cycle test). Mutual
    "tier" recursion between this function and itself is bounded by the
    number of nested cascade tiers (empirically <=2 across all real
    fixtures), not tree size.

    ``selection`` : tuple[dict, dict] | None
        Optional pre-computed ``_select_fire_and_continuation(if_node)``
        result for THIS root ``if_node``, keyword-only. When supplied AND
        non-None, it is reused directly instead of deriving it again from
        scratch — avoiding the redundant second root-level derivation on
        the well-formed path. Defaults to None, in which case (or when the
        caller's own derivation was already None, e.g. a malformed root)
        this function derives it itself exactly as before — same eventual
        result either way, just without the efficiency gain on that
        degenerate path. Applies ONLY to the root-level derivation above —
        every nested if-node the walk below encounters still computes its
        own selection independently and never inherits this one.

    Returns ``None`` when ``if_node`` isn't a valid 2-child if-node —
    never fabricates a count for an unidentifiable shape."""
    if not isinstance(if_node, dict) or if_node.get("step") != _STEP_IF:
        return None
    if selection is None:
        selection = _select_fire_and_continuation(if_node)
    if selection is None:
        return None
    fire_child, _ = selection

    count = 1  # the root if-node itself
    stack: list = [fire_child]
    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue
        if current.get("step") == _STEP_IF and (
            _qualifies_as_cascade_rung(current, is_nested_tier=True)
            or _is_internal_hedge_subgate(current)
        ):
            inner_selection = _select_fire_and_continuation(current)
            count += 1  # this nested if-node itself
            if inner_selection is not None:
                stack.append(inner_selection[0])
            else:
                # Can't determine which side is fire for this qualifying
                # node -- count everything honestly rather than guessing or
                # silently dropping content.
                for child in current.get("children") or []:
                    stack.append(child)
            continue
        count += 1
        for child in current.get("children") or []:
            stack.append(child)
        condition_block = current.get("condition")
        if isinstance(condition_block, dict):
            count += _count_clause_aware_signal_logic(condition_block)
    return count


def _compute_fire_is_else_branch(
    if_node: dict, *, selection: tuple[dict, dict] | None = None
) -> bool:
    """True when the fire (signal) side of the ROOT cascade if-node lands on
    the ``is-else-condition?==True`` child -- inverted polarity, per
    DE-FR-SIMPLIFY-001 Revise 4's final pin (the acceptance layer declines
    SIMPLIFY unconditionally when this is True, since ``_graft_incumbent_
    core``'s core-preservation logic assumes normal polarity). Reads the
    SAME selection the node-count walk above uses (never a second,
    independent derivation) — root-level only; nested-tier polarity is not
    evaluated, since only the root's fire selection feeds the acceptance
    gate. Returns ``False`` (never-True default) on a malformed shape.

    ``selection`` : tuple[dict, dict] | None
        Optional pre-computed ``_select_fire_and_continuation(if_node)``
        result for this SAME root node, keyword-only. When supplied AND
        non-None, reused directly instead of deriving it again — avoiding
        the redundant second root-level derivation on the well-formed path
        (e.g. when the caller already computed it for the node count
        above). Defaults to None, in which case (or when the caller's own
        derivation was already None, e.g. a malformed root) this function
        derives it itself exactly as before — same eventual result either
        way, just without the efficiency gain on that degenerate path."""
    if selection is None:
        selection = _select_fire_and_continuation(if_node)
    if selection is None:
        return False
    fire_child, _ = selection
    return fire_child.get("is-else-condition?") is True


def _build_cascade_overlay(root_if_node: dict) -> tuple[dict, list[float], set[str]]:
    """Build a COMPACT overlay subtree for a cascade root, resolving scale-in tiers.

    Reconstructs a new ``if`` node containing ONLY the condition + the fire
    (small) branch chain — the large/continuation branch (which leads toward
    thousands of nodes of core strategy logic past this cascade's own
    boundary) is replaced with a minimal empty stub. This is essential: the
    ORIGINAL root_if_node's large branch is core content, not part of the
    overlay, and including it verbatim would violate the size-cliff invariant
    (AC-2) and leak core-strategy structure/placeholder tickers into the
    reported overlay.

    Recurses into the fire branch's OWN children to find a NESTED qualifying
    ``if`` node (a scale-in tier — e.g. RSI>80 fires a light hedge blend,
    nested inside which RSI>82.5 fires a heavier tranche) and, if found,
    compacts that nested tier the same way (its own large branch stubbed too).
    Every tier's if-node structure is preserved as its own nested if (never
    flattened to a single OR) per AC-4(d). Non-if content in the fire branch
    (the basket's asset/group/weight leaves — the actual hedge holdings) is
    kept verbatim since it IS the cascade signal.

    Returns (overlay_tree, rsi_thresholds, vix_tickers) aggregated across every
    tier found in this cascade chain.
    """
    thresholds: list[float] = []
    vix_tickers: set[str] = set()

    def _compact_if_node(node: dict) -> dict:
        """Reconstruct one if-node: fire branch recursively compacted (in case
        it contains a further nested tier), large/continuation branch stubbed.

        FALSIFIED-AND-REVERTED ATTEMPT (2026-07-16, fr-test's cluster-3
        finding, commit 7ca7c0c6 — independently re-derived and then
        reverted again in this same file by fr-engine before reading that
        finding): a direction-explicit rewrite of fire/continuation selection
        here (fire = cond_child always, matching AC-3's TRUE-branch rule) was
        tried and DIRECTLY FALSIFIED — it introduces NEW failures
        (real_tree_04, real_tree_09 on the VIX-ticker-presence test) by
        mislabeling a genuinely STUBBED/continuation branch as "fire" on
        cascades where the raw if-node's condition-side happens to be the
        larger, continuation-bound side. This function's overlay-construction
        role is INTENTIONALLY size-based — a DIFFERENT model from
        ``extract_fr_checks``'s AC-3 direction-explicit walk, which answers a
        different question (does this node QUALIFY, not which physical side
        is fire for compaction). Kept size-based, unchanged from wave-1.
        """
        pair = _get_condition_branch_pair(node)
        if pair is None:
            # Defensive: shouldn't happen for a confirmed cascade/tier root.
            return copy.deepcopy(node)

        # RSI-threshold parsing reads cond_child specifically (the side that
        # actually carries a comparator/threshold), regardless of which side
        # ends up being fire — a genuinely separate concern from fire/
        # continuation SELECTION, so this _get_condition_branch_pair lookup
        # is computed here (DE-FR-SIMPLIFY-001 Revise 4) and reused below via
        # _select_fire_and_continuation's ``pair`` param (F5, Revise 5)
        # rather than computed a second time.
        cond_child, _else_child = pair
        if _is_rsi_condition(cond_child) and cond_child.get("comparator") == "gt":
            threshold = _parse_rsi_threshold(cond_child)
            # AC-6 REBUILD: report every parseable threshold, root or nested
            # tier alike — the plausible-overbought-range filter that used to
            # gate this (_RSI_OVERBOUGHT_MIN/MAX) is dropped for the same
            # reason it was dropped from _qualifies_as_cascade_rung: it
            # rejected genuine real thresholds (SPY:10:31's 31.0 < 50.0) and
            # the "is this a good signal" judgment belongs to AC-4's
            # data-driven classification against real Atlas edge stats, never
            # a structural heuristic here.
            if threshold is not None:
                thresholds.append(threshold)

        # Fire/continuation SELECTION itself is delegated to the shared
        # helper (team-lead ruling, Revise 4) — never reimplemented inline
        # here, so the signal-logic node-count computation elsewhere in this
        # module can never disagree with this function's own choice of fire
        # branch. F5 (Revise 5): the branch pair computed above (for the
        # RSI-threshold parse) is reused here via the keyword-only ``pair``
        # param, rather than letting the helper compute it again from
        # scratch on the same node.
        selection = _select_fire_and_continuation(node, pair=pair)
        if selection is None:
            # Unreachable in practice (pair already succeeded above), but
            # defensive rather than silently trusting a duplicated
            # assumption about the helper's internals.
            return copy.deepcopy(node)
        fire_child, continuation_child = selection

        vix_tickers.update(_collect_tickers(fire_child) & VIX_FAMILY_TICKERS)

        rebuilt_fire = {k: v for k, v in fire_child.items() if k != "children"}
        rebuilt_fire["children"] = [_compact_subtree(c) for c in fire_child.get("children") or []]
        fire_node_count = _count_nodes(rebuilt_fire)

        # The continuation (large) branch is core content past this cascade's
        # boundary — its real content is never included verbatim (no real
        # tickers/structure leak through). It IS still deliberately kept
        # LARGER than the fire branch by node count: callers (including the
        # detector's own consumers) derive "which of the two direct branches
        # is the fire branch" from relative size, so collapsing the
        # continuation stub to near-zero would invert that comparison and
        # misidentify the stub itself as the fire branch. The stub's size is
        # therefore anchored to the ORIGINAL continuation branch's real node
        # count (always >= the fire branch's, by construction of "fire =
        # smaller side") using synthetic placeholder leaves — never a
        # placeholder for scale that could be mistaken for a real ticker.
        original_continuation_n = max(_count_nodes(continuation_child), fire_node_count + 1)
        placeholder_leaf_count = original_continuation_n - 1  # -1 for the if-child node itself
        stub_continuation = {
            "step": _STEP_IF_CHILD,
            "id": continuation_child.get("id"),
            "is-else-condition?": continuation_child.get("is-else-condition?"),
            "children": [
                {
                    "step": _STEP_ASSET,
                    "ticker": STUBBED_CORE_CONTINUATION_TICKER,
                    "id": f"{continuation_child.get('id')}-stub-{i}",
                    "children": [],
                }
                for i in range(placeholder_leaf_count)
            ],
        }

        new_children = []
        for c in node.get("children") or []:
            if c is fire_child:
                new_children.append(rebuilt_fire)
            elif c is continuation_child:
                new_children.append(stub_continuation)
            else:
                new_children.append(copy.deepcopy(c))

        out = {k: v for k, v in node.items() if k != "children"}
        out["children"] = new_children
        return out

    def _compact_subtree(node) -> dict:
        """Copy a fire-branch subtree verbatim, EXCEPT any nested if-node that
        needs its own large/continuation branch stubbed:

          - A node that fully qualifies as a cascade rung (RSI-gated,
            VIX-bearing fire side, size-cliff — self-reference allowed here,
            see ``_qualifies_as_cascade_rung``'s docstring): a genuine
            scale-in TIER of the SAME cascade. Contributes its own threshold
            (if parseable) and VIX tickers.
          - A node that is internal hedge-basket machinery keyed on a
            NON-RSI indicator (``_is_internal_hedge_subgate`` — e.g.
            ``cumulative-return(UVXY) lt 5.5`` deciding de-escalation): its
            own small side is still genuinely hedge content, so it must be
            compacted (large side stubbed) even though it isn't a reportable
            cascade tier. Reuses ``_compact_if_node`` — the threshold-append
            branch inside it only fires for an RSI condition, so a non-RSI
            sub-gate simply contributes no threshold, correctly.

        Any OTHER node is recursed into generically.

        AC-6 REGRESSION FIX (2026-07-16, defect #5 — fr-test's INfCn/hvPi/
        real_tree_04/06 finding, commit 7ca7c0c6 root-cause narrative):
        after each child is recursively compacted (which already correctly
        handles any deeper qualifying tier nested within it — a rung or
        hedge subgate several levels down still gets its OWN continuation
        stubbed by the two branches above, unaffected by this fix), the
        RESULT is checked: if it still carries a CORE_ASSET_ placeholder
        anywhere (``_has_core_placeholder``) AND has NO VIX-family ticker
        anywhere within it, that one child specifically is replaced with a
        single stub leaf instead of being kept.

        This is deliberately a LOCAL, PER-CHILD, SYMMETRIC check — not a
        "pick the fire side" decision, and not a change to
        ``_compact_if_node``/``_is_internal_hedge_subgate``'s ratified
        size-based selection model (a direction-based rewrite of THAT model
        was tried and falsified, see ``_is_internal_hedge_subgate``'s
        docstring). It never discards a child that has ANY VIX content
        (however deeply nested), so it cannot reproduce that falsified
        rewrite's failure mode (mislabeling a genuine, VIX-bearing branch as
        disposable). It resolves three confirmed real cases without content
        loss:
          - INfCn/hvPi: a nested if-node (neither a rung nor a hedge
            subgate) whose TRUE side is CORE_ASSET_ content and whose ELSE
            side is unrelated real tickers (GLD/SLV/DBC, no VIX) — its
            CORE_ASSET_ side gets stubbed, its ELSE side is untouched.
          - real_tree_04/06 (fr-test's original finding): a BND-vs-SH
            ticker-crossover if-node (fails ``_qualifies_as_cascade_rung`` —
            ticker RHS, no fixed threshold) whose TRUE side genuinely
            contains VIXY/BTAL/TMF (plus a deeper properly-compacted nested
            tier) and whose ELSE side is un-gated CORE_ASSET_ leaf content
            with no VIX anywhere — only the ELSE side gets stubbed; the
            genuine TRUE-side hedge content, including the deeper tier, is
            fully preserved.
          - real_tree_09/n2oo: an inverted-polarity node whose VIX content
            (VXX/UVXY) sits in its ELSE branch and whose CORE_ASSET_ content
            sits in its TRUE branch — the TRUE side gets stubbed, the ELSE
            side (genuine hedge content) is fully preserved. Resolved as a
            side effect of this per-child design, without needing to decide
            "which side is fire" for this node at all.
        """
        if not isinstance(node, dict):
            return node
        if node.get("step") == _STEP_IF and (
            _qualifies_as_cascade_rung(node, is_nested_tier=True)
            or _is_internal_hedge_subgate(node)
        ):
            return _compact_if_node(node)
        out = {k: v for k, v in node.items() if k != "children"}
        children = node.get("children")
        if isinstance(children, list):
            compacted_children = []
            for c in children:
                compacted_c = _compact_subtree(c)
                if (
                    isinstance(compacted_c, dict)
                    and _has_core_placeholder(compacted_c)
                    and not (_collect_tickers(compacted_c) & VIX_FAMILY_TICKERS)
                ):
                    compacted_c = {
                        "step": _STEP_ASSET,
                        "ticker": STUBBED_CORE_CONTINUATION_TICKER,
                        "id": f"{compacted_c.get('id', 'unknown')}-unrelated-stub",
                        "children": [],
                    }
                compacted_children.append(compacted_c)
            out["children"] = compacted_children
        else:
            out = copy.deepcopy(node)
        return out

    overlay = _compact_if_node(root_if_node)
    return overlay, thresholds, vix_tickers


def _has_core_placeholder(node: dict) -> bool:
    return any(t.startswith(_CORE_PLACEHOLDER_PREFIX) for t in _collect_tickers(node))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def detect_frontrunner_cascades(tree: dict) -> DetectionResult:
    """Detect every leading frontrunner cascade in a Composer ``/score`` tree.

    Parameters
    ----------
    tree:
        The full symphony decision-tree dict (``raw_value`` shape).

    Returns
    -------
    DetectionResult
        ``.cascades`` — one ``Cascade`` per detected leading overlay (one per
        qualifying parallel sub-strategy). May be empty.
        ``.skip_reason`` — set whenever ``.cascades`` is empty, explaining why
        (no incumbent frontrunner found vs. ambiguous — no size-cliff
        signature). Never both empty AND None (D-1: always explicit).

    Never raises. A malformed tree degrades to an empty result with a reason.
    """
    try:
        if not isinstance(tree, dict):
            return DetectionResult(cascades=[], skip_reason="invalid tree: not a dict")

        candidate_roots: list[tuple[dict, str | None]] = []
        _find_cascade_roots(tree, group_name=None, out=candidate_roots)

        if not candidate_roots:
            return DetectionResult(
                cascades=[],
                skip_reason=(
                    "no incumbent frontrunner cascade detected — no if-node with an "
                    "RSI-gated condition whose smaller branch contains a VIX-family "
                    "ticker was found anywhere in the tree"
                ),
            )

        cascades: list[Cascade] = []
        for root_node, group_name in candidate_roots:
            overlay_tree, thresholds, vix_tickers = _build_cascade_overlay(root_node)
            if len(_collect_tickers(overlay_tree) & VIX_FAMILY_TICKERS) == 0:
                # Defensive: an overlay with NO VIX-family ticker anywhere is
                # not a real cascade, full stop — skip it rather than report
                # a non-hedge basket as a frontrunner.
                #
                # AC-6 REGRESSION FIX (2026-07-16, defect #6): this used to
                # ALSO require _has_core_placeholder(overlay_tree) — a proxy
                # for "corrupted", back when a size-vs-direction disagreement
                # on the root (it qualifies because its TRUE branch reaches
                # VIX somewhere, but _compact_if_node's size-based fire pick
                # chooses the OTHER, non-VIX-bearing side, entirely stubbing
                # away the genuine VIX content as "continuation") reliably
                # left a raw CORE_ASSET_ leak behind as a side effect. Once
                # _compact_subtree's per-child purification (see its
                # docstring) started cleaning that leak up BEFORE this check
                # runs, the proxy silently stopped firing on exactly these
                # cases — 3 confirmed instances in real_tree_09/n2oo where a
                # root qualifies on direction but its compacted overlay ends
                # up with zero VIX content (a leveraged-ETF basket like
                # QQQU/SOXL/TECL/TQQQ/UPRO, no hedge ticker at all) would
                # otherwise slip through as a false-positive cascade. The "no
                # VIX anywhere" condition alone is the real invariant a
                # legitimate cascade must satisfy; checking it directly,
                # without the now-unreliable core-placeholder proxy, closes
                # this gap without depending on _compact_subtree's internal
                # cleanup behavior.
                continue
            # Root-level fire/continuation selection, derived once here and
            # handed to both fields below so each one reuses it rather than
            # re-deriving it independently on the same root_node. On a
            # malformed root where this is itself None, both fields still
            # fall back to their own internal derivation — same result,
            # just without the efficiency gain in that edge case. Neither
            # function's own nested-tier walk ever sees or inherits this
            # value — it is consumed only for each function's root-level
            # derivation.
            root_selection = _select_fire_and_continuation(root_node)
            cascades.append(
                Cascade(
                    overlay_tree=copy.deepcopy(overlay_tree),
                    rsi_thresholds=thresholds,
                    vix_tickers=vix_tickers,
                    group_name=group_name,
                    # Computed on the PRE-stub root_node, never the padded
                    # overlay_tree, per R4-1's explicit ruling (DE-FR-
                    # SIMPLIFY-001 Revise 4).
                    signal_logic_node_count=_compute_signal_logic_node_count(
                        root_node, selection=root_selection
                    ),
                    fire_is_else_branch=_compute_fire_is_else_branch(
                        root_node, selection=root_selection
                    ),
                )
            )

        if not cascades:
            return DetectionResult(
                cascades=[],
                skip_reason="candidate cascade roots were found but all failed validation",
            )

        return DetectionResult(cascades=cascades, skip_reason=None)

    except Exception as exc:  # pragma: no cover - defensive; never-raises contract
        logger.debug("detect_frontrunner_cascades: unexpected error", exc_info=True)
        return DetectionResult(cascades=[], skip_reason=f"detector error: {type(exc).__name__}")


# ---------------------------------------------------------------------------
# AC-3: extract_fr_checks — per-symphony FR-check extraction, direction-explicit
# ---------------------------------------------------------------------------


@dataclass
class _ConditionTuple:
    """Internal — one resolved (ticker, fn, window, comparator, rhs-shape)
    tuple from a single condition-bearing node. A binary-compound broadcast
    yields several of these (one per ticker) from ONE physical node."""

    ticker: str
    fn: str
    window: int | None
    comparator: str
    fr_key: str | None
    threshold: float | None
    rhs_fn: str | None
    rhs_ticker: str | None


def _resolve_flat_tuple(node: dict) -> _ConditionTuple | None:
    """Resolve the flat if-child shape (lhs-fn directly on the node) into a
    _ConditionTuple. Never raises — a malformed/incomplete node yields None
    (no tuple, never fabricated)."""
    ticker = node.get("lhs-val")
    fn = node.get("lhs-fn")
    comparator = node.get("comparator")
    if not isinstance(ticker, str) or not ticker:
        return None
    if not isinstance(fn, str) or not fn:
        return None
    if not isinstance(comparator, str) or not comparator:
        return None
    window = _get_window(node)

    if _is_ticker_comparison(node):
        rhs_ticker = node.get("rhs-val")
        if not isinstance(rhs_ticker, str) or not rhs_ticker:
            return None
        rhs_fn = node.get("rhs-fn")
        return _ConditionTuple(
            ticker=ticker,
            fn=fn,
            window=window,
            comparator=comparator,
            fr_key=None,
            threshold=None,
            rhs_fn=rhs_fn if isinstance(rhs_fn, str) and rhs_fn else None,
            rhs_ticker=rhs_ticker,
        )

    threshold = _parse_rsi_threshold(node)
    if threshold is None:
        return None
    raw = node.get("rhs-val")
    return _ConditionTuple(
        ticker=ticker,
        fn=fn,
        window=window,
        comparator=comparator,
        fr_key=f"{ticker}:{window}:{raw}",
        threshold=threshold,
        rhs_fn=None,
        rhs_ticker=None,
    )


def _build_nested_tuple(condition: dict, lhs: dict, ticker: str) -> _ConditionTuple | None:
    """Resolve one ticker's tuple from a nested binary/binary-compound
    condition dict. Only a pure ``{"constant": <number>}`` rhs is treated as
    a genuine fixed threshold — any other/unrecognized nested rhs shape is
    skipped (no tuple), never fabricated. No real ticker-vs-ticker or
    crossover example has been observed in the nested shape across the
    operator's 11 real trees (all 113 sampled nested rhs dicts are pure
    ``{"constant": N}``) — AC-3's "0 observed != impossible" posture means
    this stays a defensive skip, not an assumption that no other shape can
    ever occur."""
    fn = lhs.get("fn")
    comparator = condition.get("comparator")
    if not isinstance(fn, str) or not fn:
        return None
    if not isinstance(comparator, str) or not comparator:
        return None
    params = lhs.get("params") or {}
    raw_window = params.get("window")
    try:
        window = int(raw_window) if raw_window is not None else None
    except (TypeError, ValueError):
        window = None
    rhs = condition.get("rhs")
    if not isinstance(rhs, dict) or set(rhs.keys()) != {"constant"}:
        return None
    try:
        threshold = float(rhs["constant"])
    except (TypeError, ValueError):
        return None
    return _ConditionTuple(
        ticker=ticker,
        fn=fn,
        window=window,
        comparator=comparator,
        fr_key=f"{ticker}:{window}:{threshold:g}",
        threshold=threshold,
        rhs_fn=None,
        rhs_ticker=None,
    )


def _resolve_nested_condition(condition: dict, out: list[_ConditionTuple]) -> None:
    """Resolve a nested ``condition`` dict (binary / binary-compound /
    compound) into zero or more _ConditionTuple entries, appended to *out*.
    ``compound`` recurses into its own ``conditions`` list (an AND/OR
    container, never a leaf itself). Never raises — an unrecognized shape
    contributes nothing."""
    if not isinstance(condition, dict):
        return
    ctype = condition.get("condition-type")
    if ctype == "compound":
        for sub in condition.get("conditions") or []:
            _resolve_nested_condition(sub, out)
        return
    if ctype == "binary":
        lhs = condition.get("lhs") or {}
        ticker = lhs.get("ticker")
        if isinstance(ticker, str) and ticker and ticker != "%":
            tup = _build_nested_tuple(condition, lhs, ticker)
            if tup is not None:
                out.append(tup)
        return
    if ctype == "binary-compound":
        lhs = condition.get("lhs") or {}
        for ticker in condition.get("tickers") or []:
            if isinstance(ticker, str) and ticker:
                tup = _build_nested_tuple(condition, lhs, ticker)
                if tup is not None:
                    out.append(tup)
        return
    # Unrecognized condition-type — no tuples, never fabricated.


def _resolve_condition_tuples(cond_child: dict) -> list[_ConditionTuple]:
    """Resolve every (ticker, fn, window, comparator, rhs-shape) tuple on a
    condition-bearing if-child, dispatching across every real condition
    shape: flat (``lhs-fn`` directly on the if-child) and nested (a
    ``condition`` dict with condition-type binary / binary-compound /
    compound). A binary-compound broadcasts ONE condition over N tickers —
    yields N tuples sharing fn/window/comparator/rhs-classification. Never
    raises — an unrecognized/malformed shape yields no tuples."""
    if isinstance(cond_child.get("lhs-fn"), str):
        tup = _resolve_flat_tuple(cond_child)
        return [tup] if tup is not None else []
    condition = cond_child.get("condition")
    out: list[_ConditionTuple] = []
    if isinstance(condition, dict):
        _resolve_nested_condition(condition, out)
    return out


def extract_fr_checks(tree: dict) -> list[FRCheck]:
    """AC-3: walk the tree; every condition node whose TRUE branch reaches a
    VIX-family ticker yields ONE FRCheck (direction read directly off each
    ancestor if-node's ``is-else-condition?`` — never inferred, never
    size-based). Self-referential VIX-timing gates (subject ticker itself
    VIX-family) yield NO FRCheck at all — silent omission, not a
    partial/flagged record.

    Fn-agnostic and comparator-agnostic by design (AC-4 filters at join
    time) — dispatches across flat, binary, binary-compound, and compound
    condition shapes. Full traversal: descends into BOTH branches of every
    if-node unconditionally, so a qualifying check is never a stopping
    point — arbitrarily many FR-checks chained via sibling if/elif ladders
    or nested scale-in tiers are all found.

    Never raises (D-1) — malformed/None input degrades to [].
    """
    try:
        if not isinstance(tree, dict):
            return []

        checks: list[FRCheck] = []
        # Stack entries: (node, branch_path_so_far, group_name). branch_path
        # is the root-to-node ancestry of "true"/"false" hops, extended only
        # when crossing INTO an if-child from its parent if-node.
        stack: list[tuple[dict, list[str], str | None]] = [(tree, [], None)]
        while stack:
            node, path, group_name = stack.pop()
            if not isinstance(node, dict):
                continue

            step = node.get("step")
            next_group_name = group_name
            if step == _STEP_GROUP:
                name = node.get("name")
                if isinstance(name, str) and name:
                    next_group_name = name

            if step == _STEP_IF:
                pair = _get_condition_branch_pair(node)
                if pair is not None:
                    cond_child, _else_child = pair
                    vix_hits = frozenset(_collect_tickers(cond_child) & VIX_FAMILY_TICKERS)
                    if vix_hits:
                        for tup in _resolve_condition_tuples(cond_child):
                            if tup.ticker in VIX_FAMILY_TICKERS:
                                # Self-referential VIX-timing gate — silent
                                # omission (AC-3), not a partial/flagged record.
                                continue
                            checks.append(
                                FRCheck(
                                    fr_key=tup.fr_key,
                                    ticker=tup.ticker,
                                    fn=tup.fn,
                                    window=tup.window,
                                    comparator=tup.comparator,
                                    threshold=tup.threshold,
                                    vix_tickers=vix_hits,
                                    branch_path=path + ["true"],
                                    node_id=cond_child.get("id"),
                                    group_name=next_group_name,
                                    rhs_fn=tup.rhs_fn,
                                    rhs_ticker=tup.rhs_ticker,
                                )
                            )
                # Unconditional descent into BOTH if-children, direction-tagged
                # — a qualifying (or non-qualifying, or ambiguous-shape) node
                # is never a stopping point.
                for child in reversed(node.get("children") or []):
                    if isinstance(child, dict) and child.get("step") == _STEP_IF_CHILD:
                        direction = "false" if child.get("is-else-condition?") is True else "true"
                        stack.append((child, path + [direction], next_group_name))
                    else:
                        stack.append((child, path, next_group_name))
                continue

            for child in reversed(node.get("children") or []):
                stack.append((child, path, next_group_name))

        return checks

    except Exception:  # pragma: no cover - defensive; never-raises contract
        logger.debug("extract_fr_checks: unexpected error", exc_info=True)
        return []
