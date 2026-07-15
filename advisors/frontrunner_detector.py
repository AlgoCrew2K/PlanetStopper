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


@dataclass
class DetectionResult:
    """Result of ``detect_frontrunner_cascades``. Never None."""

    cascades: list[Cascade] = field(default_factory=list)
    skip_reason: str | None = None


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


def _parse_rsi_threshold(cond_child: dict) -> float | None:
    """Extract the numeric RSI threshold from a flat condition's rhs-val.

    rhs-val is stored as a string in the real trees ("79", "82.5"). A
    ticker-comparison rhs (rhs-fixed-value? is False, rhs-val is a ticker
    string) is not a fixed threshold — returns None.
    """
    if cond_child.get("rhs-fixed-value?") is False:
        return None
    raw = cond_child.get("rhs-val")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _is_self_referential_timing_gate(cond_child: dict, small_tickers: set[str]) -> bool:
    """Return True if this if-node's condition watches a VIX-family instrument
    (AC-2: exclude internal inverse-VIX timing sub-strategies).

    Distinguishing signal: a genuine frontrunner cascade watches an unrelated
    CORE signal ticker (e.g. SPY, QQQ, a sector ETF) and fires a hedge basket;
    an inverse-VIX timing sub-strategy instead watches a hedge/inverse-VIX
    instrument's OWN momentum to decide an internal allocation — the watched
    ticker is ITSELF VIX-family. This is broader than exact-ticker overlap
    (watching VIXY's RSI to weight between UVXY/VXX/etc. sub-blends is still
    self-referential even though the exact watched ticker isn't literally
    fired in that specific branch) — any VIX-family lhs ticker disqualifies
    the node as a genuine cascade trigger, since a real frontrunner condition
    never watches the hedge instrument class it is about to fire.
    """
    lhs_val = cond_child.get("lhs-val")
    if not isinstance(lhs_val, str) or not lhs_val:
        return False
    if lhs_val in VIX_FAMILY_TICKERS:
        return True
    return lhs_val in small_tickers


def _qualifies_as_cascade_rung(node: dict, *, is_nested_tier: bool = False) -> bool:
    """Return True if ``node`` (an ``if`` node) qualifies as a genuine
    frontrunner cascade rung.

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
      - a flat RSI-family condition (AC-2's whole premise).
      - the condition uses comparator 'gt' against a threshold in the
        plausible RSI-overbought range (AC-2's trigger is overbought, not an
        unrelated regime/volatility gate that happens to nest a VIX ticker
        somewhere beneath it via a different sub-condition).
      - the smaller of the two branches contains >=1 VIX-family ticker (the
        fire/hedge basket signature).
      - the size-cliff signature holds between the two branches (ratio or
        absolute-size — see the named constants above).
      - (root scan only) the condition is not self-referential.

    Never raises — a malformed node degrades to False.
    """
    pair = _get_condition_branch_pair(node)
    if pair is None:
        return False
    cond_child, else_child = pair
    if not _is_rsi_condition(cond_child):
        return False
    if cond_child.get("comparator") != "gt":
        return False
    threshold = _parse_rsi_threshold(cond_child)
    if threshold is None:
        return False
    # The plausible RSI-overbought range is the discriminator for a NEW
    # independent leading cascade (a root must be a genuine overbought
    # trigger, not an unrelated regime/volatility gate). A nested scale-in
    # TIER, once we're already inside a confirmed fire branch, is exempt from
    # this range — the operator's real trees use lower thresholds for some
    # escalation/de-escalation tiers (e.g. RSI(UVXY) gt 28 deciding whether to
    # add MORE hedge on top of an already-fired blend); the surrounding
    # context (already inside a qualifying fire branch) is what legitimizes
    # it, not the threshold's own overbought-ness.
    if not is_nested_tier and not (_RSI_OVERBOUGHT_MIN <= threshold <= _RSI_OVERBOUGHT_MAX):
        return False
    cond_n = _count_nodes(cond_child)
    else_n = _count_nodes(else_child)
    small, large = (cond_child, else_child) if cond_n <= else_n else (else_child, cond_child)
    small_n = _count_nodes(small)
    large_n = _count_nodes(large)
    small_tickers = _collect_tickers(small)

    if not (small_tickers & VIX_FAMILY_TICKERS):
        return False
    if not is_nested_tier and _is_self_referential_timing_gate(cond_child, small_tickers):
        return False
    ratio_qualifies = large_n > 0 and (small_n / large_n) <= _SIZE_CLIFF_MAX_RATIO
    absolute_qualifies = small_n <= _SIZE_CLIFF_MAX_ABSOLUTE_FIRE_NODES
    return ratio_qualifies or absolute_qualifies


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

    A candidate is an ``if`` node whose condition is RSI-gated and whose
    SMALLER branch (by node count) contains >=1 VIX-family ticker and is not
    a self-referential inverse-VIX timing gate. Once a cascade root is found,
    the walk does NOT descend further into that node's small branch (its
    nested tiers are resolved separately when building the overlay) nor its
    large branch (the large branch is core content past this cascade's own
    boundary for THIS rung — but a sibling rung deeper in the large branch,
    belonging to the SAME leading chain, is resolved by the tier-walk in
    ``_build_cascade_overlay``, not by this top-level scan). The walk DOES
    continue into a node's children when the node itself is not a candidate
    root (e.g. group/weight containers, or an if-node that fails the RSI/VIX
    signature — which may still contain a genuine cascade nested inside its
    own branches, most commonly a different parallel sub-strategy).

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
                # This node is a confirmed cascade root — its own subtree
                # (both branches) is fully consumed by the cascade-overlay
                # builder below; do not re-scan it as a nested candidate.
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
        """
        pair = _get_condition_branch_pair(node)
        if pair is None:
            # Defensive: shouldn't happen for a confirmed cascade/tier root.
            return copy.deepcopy(node)

        cond_child, else_child = pair
        if _is_rsi_condition(cond_child) and cond_child.get("comparator") == "gt":
            threshold = _parse_rsi_threshold(cond_child)
            # Only report thresholds that are themselves plausible overbought
            # triggers (AC-2's whole premise). A nested tier reached via
            # is_nested_tier=True may legitimately use a lower threshold for
            # internal hedge escalation/de-escalation logic (e.g. RSI(UVXY)
            # gt 28) — that is not itself an "overbought cascade trigger" and
            # is intentionally excluded from the reported rsi_thresholds so
            # callers see only genuine overbought signals here.
            if threshold is not None and _RSI_OVERBOUGHT_MIN <= threshold <= _RSI_OVERBOUGHT_MAX:
                thresholds.append(threshold)

        cond_n = _count_nodes(cond_child)
        else_n = _count_nodes(else_child)
        fire_child = cond_child if cond_n <= else_n else else_child
        continuation_child = else_child if fire_child is cond_child else cond_child

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
                    "ticker": "_STUBBED_CORE_CONTINUATION",
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
            (if in the overbought range) and VIX tickers.
          - A node that is internal hedge-basket machinery keyed on a
            NON-RSI indicator (``_is_internal_hedge_subgate`` — e.g.
            ``cumulative-return(UVXY) lt 5.5`` deciding de-escalation): its
            own small side is still genuinely hedge content, so it must be
            compacted (large side stubbed) even though it isn't a reportable
            cascade tier. Reuses ``_compact_if_node`` — the threshold-append
            branch inside it only fires for an RSI condition, so a non-RSI
            sub-gate simply contributes no threshold, correctly.

        Any OTHER nested if-node (e.g. an unrelated core-strategy gate whose
        own small side has no VIX ticker at all) is copied verbatim — it is
        genuinely not hedge-basket content, and compacting it would be
        fabricating a boundary that isn't there.
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
            out["children"] = [_compact_subtree(c) for c in children]
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
            if (
                _has_core_placeholder(overlay_tree)
                and len(_collect_tickers(overlay_tree) & VIX_FAMILY_TICKERS) == 0
            ):
                # Defensive: an overlay that swallowed core placeholder content
                # AND has no VIX ticker of its own is not a real cascade — skip
                # this one rather than report a corrupted overlay.
                continue
            cascades.append(
                Cascade(
                    overlay_tree=copy.deepcopy(overlay_tree),
                    rsi_thresholds=thresholds,
                    vix_tickers=vix_tickers,
                    group_name=group_name,
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
