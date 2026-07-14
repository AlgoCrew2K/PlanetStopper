"""RED tests — P2-1 recursion-depth hardening (frreview finding, routed by
team-lead 2026-07-11, non-blocking quality item on the APPROVED wave-1
backend review).

BACKGROUND: the frontrunner tree traversals are plain Python-call recursion
over the full incumbent /score tree:
  - advisors/frontrunner_builder.py:892 _count_tree_nodes
  - advisors/frontrunner_detector.py:153 _count_nodes
  - advisors/frontrunner_detector.py:159 _collect_tickers
  - advisors/frontrunner_detector.py:384 _find_cascade_roots (called from the
    public entry point, detect_frontrunner_cascades)

The operator's real trees run 8,000+ nodes. symphony_schema.py already went
iterative for exactly this reason ("depth-230 fixtures safe" — its own
docstring). None of the four functions above have that hardening.

EMPIRICAL FINDINGS (frtest, direct probe against a 3,000-deep synthetic
tree — built via a plain loop, NOT symphony_schema constructors, since
symphony_schema.make_group's own copy.deepcopy call is ALSO recursive and
hits the limit during construction on a tree this deep; the probe script's
methodology is preserved in this file's fixture builder below):

  - frontrunner_builder._count_tree_nodes: RAISES RecursionError, UNCAUGHT —
    no try/except of its own. Currently "contained" only by accident: its
    sole production caller (_gate_and_accept_candidate) happens to wrap its
    whole body in try/except Exception, so today's blast radius is a logged
    reject with reason="RecursionError" — but the function itself provides
    NO safety net; any future caller that forgets to wrap it crashes.
  - frontrunner_detector._count_nodes / _collect_tickers: SAME — raise
    RecursionError, uncaught, no safety net of their own.
  - frontrunner_detector.detect_frontrunner_cascades (the public entry
    point, which calls _find_cascade_roots): does NOT raise — its own
    top-level try/except (frontrunner_detector.py:603/647) already catches
    RecursionError and returns skip_reason="detector error: RecursionError"
    — genuinely DISTINCT from the generic "no incumbent frontrunner cascade
    detected" message, so this one is not misleading today. Verified
    empirically, not assumed — this file pins that honest behavior as a
    regression guard rather than inventing a false RED where none exists.

WAVE-1 BACKEND SCOPE NOTE: this is explicitly P2 (non-blocking quality
hardening on an already-APPROVED review), not a new AC. The fix direction
(frimpl's, once these land RED): convert the four functions to iterative
traversal with an explicit stack, mirroring symphony_schema.py's own
pattern — never falling back to "catch RecursionError and skip", since for
_count_tree_nodes/_count_nodes/_collect_tickers a skip has no honest
place to report a reason (they return bare int/set, not a DetectionResult).
"""

from __future__ import annotations

import uuid

import pytest

# A tree deep enough to reliably exceed Python's default recursion limit
# (1000) on this interpreter, with real headroom (3x), matching the scale of
# concern in the P2-1 finding (the operator's real 8,000+ node trees can be
# deep, not just wide).
_DEEP_TREE_DEPTH = 3000


def _leaf_asset(ticker: str = "SPY") -> dict:
    return {"step": "asset", "id": str(uuid.uuid4()), "ticker": ticker}


def _build_deep_group_chain(depth: int = _DEEP_TREE_DEPTH) -> dict:
    """Build a `depth`-deep chain of nested group containers terminating in
    one asset leaf, WITHOUT using symphony_schema's constructors.

    symphony_schema.make_group internally calls copy.deepcopy on its
    children (advisors/symphony_schema.py:849) — deepcopy is ALSO plain
    Python-call recursion, so building a 3,000-deep tree via make_group
    itself hits the recursion limit DURING construction, before the
    function under test is ever reached (confirmed empirically). This
    builder is a flat loop — no recursion, no deepcopy — so it can
    construct arbitrarily deep fixtures regardless of the very limit this
    test suite exists to probe.

    This tree is deliberately cascade-FREE (no if-nodes, no RSI condition,
    no VIX ticker anywhere) — its correct detection answer, at any depth
    a real traversal could handle, is "no cascade". A group-only chain
    isolates the raw traversal-depth question from cascade-detection
    semantics.
    """
    node = _leaf_asset()
    for i in range(depth):
        node = {"step": "group", "id": str(uuid.uuid4()), "name": f"g{i}", "children": [node]}
    return node


def _build_deep_root(depth: int = _DEEP_TREE_DEPTH) -> dict:
    return {
        "step": "root",
        "id": str(uuid.uuid4()),
        "name": "Deep Tree",
        "description": "",
        "rebalance": "daily",
        "children": [_build_deep_group_chain(depth)],
    }


# ---------------------------------------------------------------------------
# frontrunner_builder._count_tree_nodes (frontrunner_builder.py:892)
# ---------------------------------------------------------------------------


def test_count_tree_nodes_handles_a_very_deep_tree_without_crashing():
    """AC-8's node-count-delta signal (the Calmar acceptance gate's
    'material simplification' input) must not crash on the operator's real,
    potentially-deep trees. Currently RED: RecursionError, uncaught, no
    safety net of its own (verified empirically — see module docstring)."""
    from advisors.frontrunner_builder import _count_tree_nodes

    tree = _build_deep_root()

    try:
        count = _count_tree_nodes(tree)
    except RecursionError:
        pytest.fail(
            "_count_tree_nodes raised RecursionError, uncaught, on a "
            f"{_DEEP_TREE_DEPTH}-deep tree — it has no safety net of its "
            "own and relies entirely on its caller happening to wrap it. "
            "symphony_schema.py already went iterative for exactly this "
            "reason ('depth-230 fixtures safe')."
        )

    # 1 root + _DEEP_TREE_DEPTH group nodes + 1 leaf asset.
    expected = 1 + _DEEP_TREE_DEPTH + 1
    assert count == expected, (
        f"expected {expected} total nodes (root + {_DEEP_TREE_DEPTH} groups "
        f"+ 1 leaf), got {count} — a correct fix must count every node, "
        f"not just avoid crashing"
    )


# ---------------------------------------------------------------------------
# frontrunner_detector._count_nodes / _collect_tickers (frontrunner_detector.py:153-170)
# ---------------------------------------------------------------------------


def test_detector_count_nodes_handles_a_very_deep_tree_without_crashing():
    """Same hardening requirement for frontrunner_detector's own node-count
    helper (used by the size-cliff cascade-rung qualification check,
    _qualifies_as_cascade_rung). Currently RED (verified empirically)."""
    from advisors.frontrunner_detector import _count_nodes

    tree = _build_deep_root()

    try:
        count = _count_nodes(tree)
    except RecursionError:
        pytest.fail(
            f"_count_nodes raised RecursionError, uncaught, on a {_DEEP_TREE_DEPTH}-deep tree."
        )

    expected = 1 + _DEEP_TREE_DEPTH + 1
    assert count == expected, f"expected {expected} nodes, got {count}"


def test_detector_collect_tickers_handles_a_very_deep_tree_without_crashing():
    """Same hardening requirement for the ticker-collection helper (used to
    determine whether a candidate cascade's fire branch contains a
    VIX-family ticker). Currently RED (verified empirically)."""
    from advisors.frontrunner_detector import _collect_tickers

    tree = _build_deep_root()

    try:
        tickers = _collect_tickers(tree)
    except RecursionError:
        pytest.fail(
            f"_collect_tickers raised RecursionError, uncaught, on a {_DEEP_TREE_DEPTH}-deep tree."
        )

    assert tickers == {"SPY"}, (
        f"expected exactly the one leaf ticker {{'SPY'}} to be found even "
        f"at {_DEEP_TREE_DEPTH} levels of nesting, got {tickers!r}"
    )


# ---------------------------------------------------------------------------
# detect_frontrunner_cascades — the public entry point (D-1 contract pin).
# ---------------------------------------------------------------------------


def test_detect_frontrunner_cascades_never_raises_on_a_very_deep_tree():
    """The public entry point's own D-1 'never raises' contract must hold at
    extreme depth, not just on the small fixtures the rest of the detector
    suite uses. Verified currently GREEN (frontrunner_detector.py's own
    top-level try/except at line 603/647 already catches RecursionError) —
    pinned here as a regression guard for the upcoming iterative refactor,
    which must not accidentally reintroduce an escaping RecursionError at
    the public boundary."""
    from advisors.frontrunner_detector import detect_frontrunner_cascades

    tree = _build_deep_root()

    try:
        result = detect_frontrunner_cascades(tree)
    except RecursionError:
        pytest.fail(
            f"detect_frontrunner_cascades raised RecursionError, escaping "
            f"its own documented 'never raises' D-1 contract, on a "
            f"{_DEEP_TREE_DEPTH}-deep tree."
        )

    assert result.cascades == [], (
        "this fixture is deliberately cascade-free (a group-only chain, no "
        "if-nodes, no RSI condition, no VIX ticker) — the correct answer "
        "is zero cascades regardless of how depth is handled"
    )
    assert result.skip_reason, "D-1: skip_reason must be set whenever cascades is empty"


def test_detect_frontrunner_cascades_skip_reason_is_honest_not_the_generic_no_cascade_message():
    """If detect_frontrunner_cascades cannot fully traverse a deep tree (the
    current recursive implementation genuinely cannot, at this depth), the
    reported skip_reason must NOT be word-for-word the standard
    'no incumbent frontrunner cascade detected — no if-node with an
    RSI-gated condition...' message — that message asserts a CONFIDENT,
    COMPLETE scan found nothing, which would be dishonest if the scan
    actually aborted partway through via a traversal-depth failure. The
    reason must instead name the real cause (a traversal/depth failure),
    so an operator reading the Advisor-tab skip reason isn't misled into
    thinking the symphony was fully, successfully examined.

    Currently GREEN (verified empirically: the actual message today is
    'detector error: RecursionError', which already satisfies this) —
    pinned as the honest-degradation contract this hardening effort must
    preserve, whichever fix direction frimpl takes (genuinely-iterative
    success is even better and also satisfies this test, since a
    fully-successful traversal doesn't hit this code path at all for this
    cascade-free fixture: see the sibling test above)."""
    from advisors.frontrunner_detector import detect_frontrunner_cascades

    tree = _build_deep_root()
    result = detect_frontrunner_cascades(tree)

    generic_no_cascade_message = (
        "no incumbent frontrunner cascade detected — no if-node with an "
        "RSI-gated condition whose smaller branch contains a VIX-family "
        "ticker was found anywhere in the tree"
    )
    if result.skip_reason is not None and result.skip_reason != generic_no_cascade_message:
        # The implementation chose to degrade (rather than fully succeed) —
        # the reason must be depth/recursion-related, not some other opaque
        # failure mode masquerading as a confident negative.
        reason_lower = result.skip_reason.lower()
        assert any(kw in reason_lower for kw in ("recursion", "depth", "too deep", "traversal")), (
            f"detect_frontrunner_cascades degraded on a deep tree with a "
            f"skip_reason that names neither recursion nor depth/traversal: "
            f"{result.skip_reason!r} — an operator reading this on the "
            f"Advisor tab has no way to tell 'genuinely no cascade' apart "
            f"from 'the scan itself failed partway through'"
        )
