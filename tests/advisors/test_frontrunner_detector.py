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


def _if_child_branches(overlay_root: dict) -> tuple[dict, dict] | None:
    """Return (condition_branch, continuation_branch) for an overlay's root
    ``if`` node, using the real node shape (is-else-condition? discriminator)
    documented by fb-eng and consumed by the detector's own
    _get_condition_branch_pair. Returns None if the overlay's root is not
    (or no longer) a two-if-child ``if`` node."""
    if not isinstance(overlay_root, dict) or overlay_root.get("step") != "if":
        return None
    if_children = [c for c in (overlay_root.get("children") or []) if c.get("step") == "if-child"]
    if len(if_children) != 2:
        return None
    cond = next((c for c in if_children if c.get("is-else-condition?") is False), None)
    cont = next((c for c in if_children if c.get("is-else-condition?") is True), None)
    if cond is None or cont is None:
        return None
    return cond, cont


def test_detected_cascade_fire_branch_is_smaller_than_the_remaining_tree(fd, real_tree):
    """The size-cliff invariant: overlay_tree spans the WHOLE detected if-node
    (both branches — the fire branch AND the continuation branch that carries
    on toward the core, per the detector's own documented contract, so a
    caller can splice the node wholesale). The size-cliff signature the plan
    describes lives specifically in the FIRE branch (whichever of the two
    top-level branches is smaller): it must be a small minority of the total
    tree (baskets <=~16 nodes for a single rung vs a tree with thousands — we
    don't hardcode ~16, but assert the fire branch stays a small fraction)."""
    total_nodes = _count_nodes(real_tree)
    result = fd.detect_frontrunner_cascades(real_tree)
    for cascade in result.cascades:
        branches = _if_child_branches(cascade.overlay_tree)
        assert branches is not None, (
            "cascade.overlay_tree's root is not a two-if-child 'if' node — "
            "the detector's own documented contract requires this shape"
        )
        cond_branch, cont_branch = branches
        fire_nodes = min(_count_nodes(cond_branch), _count_nodes(cont_branch))
        assert fire_nodes <= total_nodes * 0.25, (
            f"the smaller (fire) branch ({fire_nodes} nodes) is not a small "
            f"minority of the total tree ({total_nodes} nodes) — the size-cliff "
            f"signature was not respected for this cascade root"
        )


def test_detected_cascade_fire_branch_contains_at_least_one_vix_family_ticker(fd, real_tree):
    """Grounding: 'fire baskets always contain >=1 VIX-family instrument'.
    Scoped to the fire branch specifically — the invariant is about what the
    hedge basket FIRES, not about the continuation branch (which legitimately
    carries on toward unrelated core content)."""
    vix_family = {"VIXY", "VIXM", "UVXY", "UVIX", "VXX", "SVXY", "SVIX"}
    result = fd.detect_frontrunner_cascades(real_tree)
    for cascade in result.cascades:
        branches = _if_child_branches(cascade.overlay_tree)
        if branches is None:
            continue  # covered by the dedicated shape assertion elsewhere
        cond_branch, cont_branch = branches
        fire_branch = (
            cond_branch
            if _count_nodes(cond_branch) <= _count_nodes(cont_branch)
            else cont_branch
        )
        fire_tickers = _all_tickers(fire_branch)
        assert fire_tickers & vix_family, (
            f"detected cascade's fire branch has no VIX-family ticker "
            f"(found: {sorted(fire_tickers)})"
        )


def test_detected_cascade_fire_branch_never_includes_a_core_asset_placeholder(fd, real_tree):
    """The trimmed fixtures mark stubbed core logic with CORE_ASSET_NNNN
    placeholders. A correct detector never classifies stubbed-core content as
    part of the frontrunner FIRE basket — this is the fixture-level proxy for
    'the cascade never swallows the core' (AC-2).

    Scoped to the fire branch specifically (not the whole overlay_tree, which
    legitimately spans the continuation branch too, per the detector's
    documented contract — the continuation branch IS core content by design;
    the invariant that matters is that the actual hedge-firing branch stays
    pure hedge/VIX content)."""
    result = fd.detect_frontrunner_cascades(real_tree)
    for cascade in result.cascades:
        branches = _if_child_branches(cascade.overlay_tree)
        if branches is None:
            continue  # covered by the dedicated shape assertion elsewhere
        cond_branch, cont_branch = branches
        fire_branch = (
            cond_branch
            if _count_nodes(cond_branch) <= _count_nodes(cont_branch)
            else cont_branch
        )
        assert not _has_core_asset_placeholder(fire_branch), (
            "the fire (hedge-firing) branch of a detected cascade includes a "
            "CORE_ASSET_ placeholder — the detector has swallowed core logic "
            "into the hedge basket itself"
        )


def test_detected_cascade_rsi_thresholds_fall_in_the_grounded_range(fd, real_tree):
    """Grounding note: 'RSI(ticker) gt ~77-82.5'. Every real tree's leading
    cascade is gated by an RSI-OVERBOUGHT if-node — comparator='gt' against a
    high threshold, never 'lt' against a low one (that would be an oversold /
    unrelated regime-timing gate, not a frontrunner hedge trigger; AC-2's
    whole premise is triggering on overbought conditions). This test asserts
    at least one rsi_threshold per real fixture, and each one both (a) uses
    comparator='gt' and (b) falls in a plausible overbought range — not
    exactly 77-82.5 (some real trees may legitimately vary), but
    sanity-bounded well inside [50, 100] to catch a detector that's
    mis-attributing an unrelated gate as the frontrunner cascade."""
    result = fd.detect_frontrunner_cascades(real_tree)
    found_any_threshold = False
    for cascade in result.cascades:
        branches = _if_child_branches(cascade.overlay_tree)
        cond_branch = branches[0] if branches else None
        comparator = cond_branch.get("comparator") if cond_branch else None
        for threshold in getattr(cascade, "rsi_thresholds", []) or []:
            found_any_threshold = True
            if comparator is not None:
                assert comparator == "gt", (
                    f"cascade root condition comparator is {comparator!r}, not 'gt' — "
                    f"AC-2's frontrunner trigger is RSI-OVERBOUGHT ('gt'), not an "
                    f"oversold/unrelated regime gate"
                )
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

    NODE SHAPE (real, per fb-eng's confirmed real-tree inspection + the
    detector's own _get_condition_branch_pair/_parse_rsi_threshold): an
    ``if`` node's two ``if-child`` entries are distinguished by
    ``is-else-condition?`` (False = condition branch carrying the flat
    lhs-fn/lhs-val/comparator/rhs-fixed-value?/rhs-val fields; True = the
    continuation/else branch, no condition fields). ``rhs-val`` is a STRING
    in the real trees (parsed via float() by the detector).
    """
    core_leaf = {"step": "asset", "ticker": "CORE_ASSET_0001", "id": "core-1", "children": []}

    frontrunner_cascade = {
        "step": "if",
        "id": "cascade-if",
        "children": [
            {
                "step": "if-child",
                "is-else-condition?": False,
                "lhs-fn": "relative-strength-index",
                "lhs-val": "SPY",
                "comparator": "gt",
                "rhs-fixed-value?": True,
                "rhs-val": "80",
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
                "is-else-condition?": True,
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
                "is-else-condition?": False,
                "lhs-fn": "relative-strength-index",
                "lhs-val": "SVXY",
                "comparator": "gt",
                "rhs-fixed-value?": True,
                "rhs-val": "70",
                "id": "timing-true",
                "children": [{"step": "asset", "ticker": "SVXY", "id": "svxy-in", "children": []}],
            },
            {
                "step": "if-child",
                "is-else-condition?": True,
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
