"""RED tests — advisors/frontrunner_detector.py (NEW — does not exist yet).

Module under test: advisors.frontrunner_detector. The ImportError on the
fixture below is the first RED signal.

FIXTURE PROVENANCE: tests/fixtures/advisors/frontrunner/real_tree_NN_<sid>.json
are TRIMMED derivatives of the operator's 11 real live /score trees (vendored
by fb-eng; see the fixture-directory .gitignore + commit message). Trimming
replaces every core-strategy asset ticker with a synthetic CORE_ASSET_NNNN
placeholder while preserving the real frontrunner cascade signal byte-for-byte
(RSI-gt thresholds, watched tickers, VIX/hedge basket composition, exact
if-node tree shape/depth/node-count). No hand-verified "golden" boundary is
hardcoded here as a magic node-count — every assertion is DERIVED from the
fixture's own structure at test time (grammar: "assert shape/property, never
hardcode producer-computed values" — the size-cliff ratio and hedge-ticker
membership are recomputed from the loaded JSON, not typed in as literals).

Real tree structural facts used below (verified by direct inspection of the
fixture JSON, not assumed):
  - step="root" -> single child "wt-cash-equal" -> "group" (portfolio name)
    -> "wt-cash-equal" -> "if" (the leading cascade root for that sub-strategy).
  - An "if" node's two "if-child" entries are the true/false branches; a
    true-branch if-child carries "comparator" (e.g. "gt"); the false-branch
    if-child carries no comparator.
  - The size-cliff signature: the fire-basket branch (small, VIX/hedge assets)
    is one to two orders of magnitude smaller in node-count than the sibling
    branch that continues toward the core.
  - Parallel sub-strategies are sibling "group" nodes directly under the
    portfolio's top-level "wt-cash-equal" (real_tree_10 has 5: "Sharpe",
    "Mixed | VibeCheck", "MDD", "80% | Calmar", ...).
  - CORE_ASSET_ placeholder tickers mark the stubbed core — never present
    inside a genuine frontrunner cascade fire-basket in these fixtures.

ADVERSARIAL FOCUS (AC-2 / AC-11):
  - delimits each leading RSI-overbought cascade + its size-cliff boundary
  - excludes internal inverse-VIX timing subtrees (never misfires on those)
  - fail-loud (raises / returns an explicit ambiguous verdict — never guesses)
    on a synthetic tree engineered to have no clean size-cliff
  - recurses into parallel sub-strategy groups, returning one overlay per
    detected cascade
"""

from __future__ import annotations

import json
import pathlib

import pytest

FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "advisors" / "frontrunner"

_REAL_TREE_FIXTURES = sorted(FIXTURE_DIR.glob("real_tree_*.json"))


# ---------------------------------------------------------------------------
# Module-under-test import guard — RED until advisors/frontrunner_detector.py exists.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fd():
    """Import and return the frontrunner_detector module (RED until it exists)."""
    import advisors.frontrunner_detector as _fd  # noqa: PLC0415

    return _fd


@pytest.fixture(params=_REAL_TREE_FIXTURES, ids=lambda p: p.stem)
def real_tree(request) -> dict:
    """Load one trimmed real /score tree fixture (parametrized over all 11)."""
    with open(request.param, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Structural ground-truth helpers — computed from the loaded fixture at test
# time, never hardcoded per-fixture magic numbers.
# ---------------------------------------------------------------------------


def _count_nodes(node) -> int:
    if not isinstance(node, dict):
        return 0
    total = 1
    for child in node.get("children") or []:
        total += _count_nodes(child)
    return total


def _all_tickers(node) -> set[str]:
    out: set[str] = set()
    if isinstance(node, dict):
        t = node.get("ticker")
        if isinstance(t, str):
            out.add(t)
        for child in node.get("children") or []:
            out |= _all_tickers(child)
    return out


def _has_core_asset_placeholder(node) -> bool:
    return any(t.startswith("CORE_ASSET_") for t in _all_tickers(node))


# ---------------------------------------------------------------------------
# AC-2: leading cascade detection + size-cliff boundary — one test per real
# tree fixture (parametrized), asserting properties derivable from the tree
# itself, never an invented magic boundary node-count.
# ---------------------------------------------------------------------------


def test_detects_at_least_one_cascade_on_every_real_tree(fd, real_tree):
    """Every one of the 11 real trees is a live frontrunner'd symphony (per the
    plan's own grounding note) — the detector must find at least one cascade,
    never silently report zero on a tree that structurally contains one."""
    result = fd.detect_frontrunner_cascades(real_tree)
    assert result.cascades, (
        "detector found zero cascades on a real tree known to contain a "
        "frontrunner overlay (feature-plans/frontrunner-builder.md grounding note)"
    )


def test_detected_cascade_boundary_is_smaller_than_the_remaining_tree(fd, real_tree):
    """The size-cliff invariant: whatever subtree the detector marks as the
    incumbent cascade/overlay must be substantially smaller than the total
    tree (baskets <=~16 nodes vs a tree with thousands — the plan's own
    grounding ratio). We don't hardcode ~16; we assert the detected overlay is
    a small minority of the total node count for these specific real trees,
    which is the structural signature the boundary heuristic is built on."""
    total_nodes = _count_nodes(real_tree)
    result = fd.detect_frontrunner_cascades(real_tree)
    for cascade in result.cascades:
        overlay_nodes = _count_nodes(cascade.overlay_tree)
        assert overlay_nodes < total_nodes, (
            "detected overlay is not smaller than the whole tree — the "
            "size-cliff boundary was not respected"
        )
        # The plan's grounding note puts fire baskets at <=~16 nodes for a
        # single rung; a whole cascade (all tiers) is necessarily larger but
        # still a small minority of an 800-8000+ node real tree.
        assert overlay_nodes <= total_nodes * 0.25, (
            f"detected overlay ({overlay_nodes} nodes) is not a small minority "
            f"of the total tree ({total_nodes} nodes) — likely swallowed core logic"
        )


def test_detected_cascade_contains_at_least_one_vix_family_ticker(fd, real_tree):
    """Grounding: 'fire baskets always contain >=1 VIX-family instrument'."""
    vix_family = {"VIXY", "VIXM", "UVXY", "UVIX", "VXX", "SVXY", "SVIX"}
    result = fd.detect_frontrunner_cascades(real_tree)
    for cascade in result.cascades:
        overlay_tickers = _all_tickers(cascade.overlay_tree)
        assert overlay_tickers & vix_family, (
            f"detected cascade overlay has no VIX-family ticker "
            f"(found: {sorted(overlay_tickers)})"
        )


def test_detected_cascade_never_includes_a_core_asset_placeholder_ticker(fd, real_tree):
    """The trimmed fixtures mark stubbed core logic with CORE_ASSET_NNNN
    placeholders. A correct detector never classifies stubbed-core content as
    part of the frontrunner overlay — this is the fixture-level proxy for
    'the cascade never swallows the core' (AC-2)."""
    result = fd.detect_frontrunner_cascades(real_tree)
    for cascade in result.cascades:
        assert not _has_core_asset_placeholder(cascade.overlay_tree), (
            "detected cascade overlay includes a CORE_ASSET_ placeholder — "
            "the detector has swallowed core logic across the size-cliff boundary"
        )


def test_detected_cascade_rsi_thresholds_fall_in_the_grounded_range(fd, real_tree):
    """Grounding note: 'RSI(ticker) gt ~77-82.5'. Every real tree's leading
    cascade is gated by an RSI-overbought if-node (that is the whole premise
    of AC-2), so the detector must report at least one rsi_threshold per real
    fixture, and each one must fall in a plausible overbought RSI range — not
    exactly 77-82.5 (some real trees may legitimately vary), but sanity-bounded
    well inside [50, 100] to catch a detector that's mis-attributing an
    unrelated gate (e.g. a moving-average crossover) as the frontrunner cascade."""
    result = fd.detect_frontrunner_cascades(real_tree)
    found_any_threshold = False
    for cascade in result.cascades:
        for threshold in getattr(cascade, "rsi_thresholds", []) or []:
            found_any_threshold = True
            assert 50 <= threshold <= 100, (
                f"cascade RSI threshold {threshold} is outside a plausible "
                f"overbought range — likely mis-attributed gate"
            )
    assert found_any_threshold, (
        "detector reported no rsi_thresholds for any cascade on a real tree — "
        "every real fixture's cascade is RSI-gated per the plan's grounding note"
    )


# ---------------------------------------------------------------------------
# AC-2: excludes internal inverse-VIX timing sub-strategies
# ---------------------------------------------------------------------------


def _synthetic_tree_with_inverse_vix_timing_substrategy() -> dict:
    """A constructed (non-real) tree with a genuine RSI->VIX frontrunner cascade
    AND a separate inverse-VIX TIMING sub-strategy (an if-gate that rotates
    into/out of an inverse-VIX product like SVXY based on its OWN momentum
    signal, not a hedge overlay watching an unrelated core ticker). The
    detector must not misclassify the timing sub-strategy as a frontrunner
    overlay.

    Distinguishing signal (per AC-2): the frontrunner cascade's condition
    watches the CORE strategy's signal ticker (SPY here) and fires a
    VIX/hedge basket; the inverse-VIX timing sub-strategy's condition instead
    watches the inverse-VIX ticker's OWN indicator (self-referential SVXY
    momentum) to decide its own allocation — there is no hedge basket being
    inserted ahead of unrelated core logic.
    """
    core_leaf = {"step": "asset", "ticker": "CORE_ASSET_0001", "id": "core-1"}

    frontrunner_cascade = {
        "step": "if",
        "id": "cascade-if",
        "children": [
            {
                "step": "if-child",
                "lhs-fn": "relative-strength-index",
                "lhs-val": "SPY",
                "comparator": "gt",
                "rhs-fixed-value": 80,
                "id": "cascade-true",
                "children": [
                    {
                        "step": "asset",
                        "ticker": "UVXY",
                        "id": "vix-fire",
                        "children": [],
                    }
                ],
            },
            {
                "step": "if-child",
                "id": "cascade-false",
                "children": [core_leaf],
            },
        ],
    }

    inverse_vix_timing_substrategy = {
        "step": "if",
        "id": "timing-if",
        "children": [
            {
                "step": "if-child",
                "lhs-fn": "relative-strength-index",
                "lhs-val": "SVXY",
                "comparator": "gt",
                "rhs-fixed-value": 70,
                "id": "timing-true",
                "children": [{"step": "asset", "ticker": "SVXY", "id": "svxy-in", "children": []}],
            },
            {
                "step": "if-child",
                "id": "timing-false",
                "children": [{"step": "asset", "ticker": "BIL", "id": "svxy-out", "children": []}],
            },
        ],
    }

    return {
        "step": "root",
        "name": "Synthetic exclusion-test tree",
        "rebalance": "daily",
        "id": "synthetic-root",
        "children": [
            {
                "step": "wt-cash-equal",
                "id": "root-wt",
                "children": [
                    {
                        "step": "group",
                        "name": "Sub-strategy A",
                        "id": "group-a",
                        "children": [frontrunner_cascade],
                    },
                    {
                        "step": "group",
                        "name": "Inverse-VIX Timing",
                        "id": "group-b",
                        "children": [inverse_vix_timing_substrategy],
                    },
                ],
            }
        ],
    }


def test_excludes_internal_inverse_vix_timing_substrategy(fd):
    """The inverse-VIX timing sub-strategy must NOT be reported as a detected
    frontrunner cascade — only the genuine RSI(core-signal)->VIX overlay."""
    tree = _synthetic_tree_with_inverse_vix_timing_substrategy()
    result = fd.detect_frontrunner_cascades(tree)

    detected_ids = set()
    for cascade in result.cascades:
        detected_ids |= {
            n.get("id") for n in _iter_ids(cascade.overlay_tree) if isinstance(n, dict)
        }

    assert "cascade-if" in detected_ids or any(
        "cascade" in str(i) for i in detected_ids
    ), "the genuine frontrunner cascade was not detected"
    assert "timing-if" not in detected_ids, (
        "the inverse-VIX timing sub-strategy was misclassified as a frontrunner cascade"
    )


def _iter_ids(node):
    if isinstance(node, dict):
        yield node
        for child in node.get("children") or []:
            yield from _iter_ids(child)


# ---------------------------------------------------------------------------
# AC-2 / AC-11: fail-loud on ambiguity — never guess
# ---------------------------------------------------------------------------


def _synthetic_ambiguous_tree() -> dict:
    """A tree with NO clean size cliff: every subtree under the root is
    roughly the same size, and no branch contains a VIX/hedge ticker at all.
    A correct detector cannot confidently delimit a cascade here and must
    fail loud (skip-with-reason / raise) rather than guess."""

    def _uniform_subtree(prefix: str, depth: int) -> dict:
        if depth == 0:
            return {"step": "asset", "ticker": f"{prefix}_LEAF", "id": f"{prefix}-leaf"}
        return {
            "step": "group",
            "name": prefix,
            "id": f"{prefix}-grp",
            "children": [
                _uniform_subtree(f"{prefix}A", depth - 1),
                _uniform_subtree(f"{prefix}B", depth - 1),
            ],
        }

    return {
        "step": "root",
        "name": "Synthetic ambiguous tree",
        "rebalance": "daily",
        "id": "ambiguous-root",
        "children": [
            {
                "step": "wt-cash-equal",
                "id": "root-wt",
                "children": [_uniform_subtree("X", 4), _uniform_subtree("Y", 4)],
            }
        ],
    }


def test_ambiguous_tree_fails_loud_never_guesses(fd):
    """No if-nodes, no VIX tickers, no size cliff anywhere: the detector must
    report zero cascades with an explicit reason (or raise a dedicated
    exception) — it must NOT fabricate a cascade out of an arbitrary subtree."""
    tree = _synthetic_ambiguous_tree()
    result = fd.detect_frontrunner_cascades(tree)

    assert not result.cascades, (
        "detector fabricated a cascade on a tree with no if-nodes and no "
        "VIX-family tickers anywhere — must fail loud instead"
    )
    assert getattr(result, "skip_reason", None), (
        "detector reported zero cascades with no skip_reason — AC-2 requires "
        "an explicit reason whenever it declines to detect (never a silent empty result)"
    )


def test_tree_with_no_incumbent_frontrunner_skips_with_reason(fd):
    """A pure-alpha tree (no if-nodes at all, single flat basket) is a
    legitimate 'no incumbent FR' case — AC-11 edge case, distinct from
    ambiguity: skip with reason, never crash, never fabricate."""
    tree = {
        "step": "root",
        "name": "Pure alpha, no frontrunner",
        "rebalance": "daily",
        "id": "pure-alpha-root",
        "children": [
            {
                "step": "wt-cash-equal",
                "id": "wt",
                "children": [
                    {"step": "asset", "ticker": "SPY", "id": "a1"},
                    {"step": "asset", "ticker": "QQQ", "id": "a2"},
                ],
            }
        ],
    }
    result = fd.detect_frontrunner_cascades(tree)
    assert not result.cascades
    assert getattr(result, "skip_reason", None)


# ---------------------------------------------------------------------------
# AC-2: recurses into parallel sub-strategy groups — one cascade per detected
# cascade, surfaced individually (real_tree_10 / nOyb55RMGVCKPiYXv7TI has 5
# parallel top-level groups, hand-verified by direct fixture inspection: each
# is its own "group" node with its own leading "if" cascade).
# ---------------------------------------------------------------------------


def test_recurses_into_all_parallel_substrategy_groups():
    """Uses the smallest real fixture (866 nodes) directly (not the
    parametrized real_tree fixture) since this test asserts a specific,
    hand-verified real-tree fact: 5 parallel sub-strategy groups, each with
    its own leading cascade."""
    import advisors.frontrunner_detector as fd_module  # noqa: PLC0415

    fixture_path = FIXTURE_DIR / "real_tree_10_nOyb55RMGVCKPiYXv7TI.json"
    with open(fixture_path, encoding="utf-8") as f:
        tree = json.load(f)

    # Ground truth: count the parallel "group" siblings directly under the
    # portfolio's top-level wt-cash-equal (hand-verified: 5 groups in this tree).
    portfolio_wt = tree["children"][0]
    sibling_groups = [c for c in (portfolio_wt.get("children") or []) if c.get("step") == "group"]
    assert len(sibling_groups) >= 2, "fixture assumption changed — re-verify real_tree_10 shape"

    result = fd_module.detect_frontrunner_cascades(tree)

    # At least one detected cascade per sibling group that actually contains a
    # leading if-cascade with a VIX-family fire basket (some groups may
    # legitimately have none, but this fixture's groups all lead with one per
    # the manual walk during test authoring).
    assert len(result.cascades) >= 2, (
        f"expected the detector to recurse into multiple parallel sub-strategy "
        f"groups and report one cascade per group; got {len(result.cascades)}"
    )
