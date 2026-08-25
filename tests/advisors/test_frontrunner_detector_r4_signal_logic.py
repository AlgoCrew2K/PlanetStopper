"""RED tests — DE-FR-SIMPLIFY-001 Revise 4 (R4-1): ``Cascade`` gains two
additive fields computed AT DETECTION TIME on the PRE-stub original subtree.

Module under test: advisors.frontrunner_detector.Cascade.

WHY THIS FILE EXISTS (the Revise-3 failure this architecture fixes): Revise
3's ``frontrunner_builder._count_signal_logic_nodes`` tried to reverse-
engineer "which if-child is the real fire branch" from OUTSIDE the detector,
by searching for the ``STUBBED_CORE_CONTINUATION_TICKER`` marker. PR #128's
round-3 `/code-review` found a runnable repro disproving this: on any
cascade whose FIRE branch itself contains a nested, already-compacted tier
(a genuine multi-tier scale-in), BOTH if-children end up "containing a stub
marker somewhere" (the continuation IS the stub; the fire branch contains
ITS OWN nested stub, buried inside it) -- so the marker search cannot
distinguish them, and picks whichever comes first positionally. Revise 3's
own test suite could not catch this because its "expected value" helper
mirrored the SAME marker-search heuristic the production code used -- a
mirror, not an independent oracle.

RULING (team-lead, Revise 4): the root flaw is architectural -- the builder
was reverse-engineering information the DETECTOR had first-hand and threw
away. The detector's own ``_compact_if_node`` ALREADY knows, unambiguously,
which if-child is fire and which is continuation at every level of nesting
(it decides this to build the overlay tree in the first place) -- no
downstream marker-search can ever be as reliable as the detector's own
firsthand knowledge. So the fix is: the detector computes and stamps the
honest values onto ``Cascade`` directly; the builder consumes them, and
``frontrunner_builder._count_signal_logic_nodes``'s cascade-side heuristics
(``_contains_stub_marker`` used from the cascade side) are DELETED.

TWO NEW ADDITIVE FIELDS on ``Cascade``:
  - ``signal_logic_node_count: int | None`` -- 1 (the cascade root if-node)
    + the RECURSIVELY honest node count of the real fire-branch chain, with
    EVERY continuation excluded ENTIRELY at EVERY nesting level (never
    padded, never counted at all -- see the "padding-preserving is not
    honest" note below). This is the ONLY sanctioned source for the
    SIMPLIFY-path denominator.
  - ``fire_is_else_branch: bool`` -- True when the cascade root's fire
    content (chosen by ``_compact_if_node``'s existing, unchanged, size-
    based selection) ends up on the ``is-else-condition?==True`` side. This
    happens because fire/continuation selection is PURELY size-based
    (``fire_child = cond_child if cond_n <= else_n else else_child``,
    unchanged, wave-1 design) -- independent of which side the
    ``is-else-condition?`` marker calls "the else". See
    ``test_inverted_polarity...`` below for why this matters: the
    PRE-EXISTING (not this cycle's) ``_graft_incumbent_core`` always reads
    "the incumbent's real ``is-else-condition?==True`` child" as "the real
    core to preserve" -- for an inverted-polarity cascade, that child is
    actually the FIRE content, and the genuine core (on the OTHER side)
    gets silently dropped by the graft. That splice-polarity defect is
    PRE-EXISTING wave-2 code, tracked as a SEPARATE backlog item -- this
    file does not exercise or characterize the graft itself, only proves
    ``fire_is_else_branch`` correctly reports the polarity so the
    ACCEPTANCE layer (frontrunner_acceptance.py) can fail-closed decline
    SIMPLIFY on it (see tests/advisors/test_frontrunner_acceptance.py).

CRITICAL correction vs. an earlier (wrong) assumption made while drafting
this file: ``_compact_if_node``'s existing local ``fire_node_count`` (used
only to size the stub padding, then discarded) is NOT the honest value --
it is PADDING-SIZE-PRESERVING (the stub always pads to
``max(real_continuation_size, fire_node_count + 1)``), so for a MULTI-TIER
cascade it still numerically includes the nested tier's OWN (same-size,
just relabeled) stub bulk. Simply threading that existing local variable
onto ``Cascade`` would relocate the bug, not fix it -- confirmed empirically
below (``test_multitier_dramatic_gap_between_padding_preserving_and_honest``
pins BOTH numbers side by side on the SAME fixture). The correct fix has
RECURSIVELY-DEFINED semantics (exclude continuations at every nesting
depth, never materialize or count their padding) but MUST be implemented
as an ITERATIVE, explicit-stack walk per this module's own established
convention (``_count_nodes``/``_collect_tickers``, both already iterative
"so a very deep real tree never triggers RecursionError") -- see
``test_new_signal_logic_counter_and_shared_selection_helper_have_no_recursion_cycle``
below, which pins this structurally via a source-scan, not a deep fixture
(``symphony_schema.make_if``'s own ``copy.deepcopy`` hits Python's
recursion ceiling before a fixture deep enough to trigger this empirically
could even be constructed -- a pre-existing, out-of-scope, suite-wide
constraint). Computed alongside (not instead of) the existing
tree-construction pass.

ORACLE METHODOLOGY (why these fixtures are genuinely independent, not a
Revise-3-style mirror): every expected value below is HAND-DERIVED from the
RAW fixture pieces BEFORE they are ever passed through the detector -- by
construction, I know which branch is fire (I put the RSI/VIX-bearing
content there deliberately) and count it directly via the plain node-walker
(``fd._count_nodes``, unchanged, non-controversial), never by asking any
selection code "which child is fire". For the two real-fixture pins, the
exact-value one is hand-traced node-by-node from a printed dump of the real
tree (shown in each test's own comment); the bound one asserts a structural
inequality true for ANY correct implementation, not a guessed number.
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib

import pytest

from advisors import symphony_schema as ss

FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "advisors" / "frontrunner"


@pytest.fixture(scope="module")
def fd():
    from advisors import frontrunner_detector as _fd

    return _fd


# ---------------------------------------------------------------------------
# Synthetic fixture builders -- every branch's role (fire vs continuation) is
# known by construction, never inferred. Real-looking tickers only (no
# CORE_ASSET_ marker where a real ticker would do), following the same
# pattern test_frontrunner_detector.py's
# test_real_looking_core_tickers_do_not_leak_into_watched_tickers established.
# ---------------------------------------------------------------------------


def _build_multitier_dramatic_fixture() -> dict:
    """Outer RSI(SPY,10)>80 cascade; fire branch = a nested RSI(SPY,10)>82.5
    tier (fire=[VIXY,BTAL], continuation=150 CORE_ASSET_ leaves); outer
    continuation = 200 CORE_ASSET_OUTER_ leaves.

    Empirically verified (probe, not guessed) against the REAL, unmodified
    detector:
      - Outer-level pick is CORRECT (raw cond_n=156 <= raw else_n=201, so
        fire_child=cond_child, no positional confusion at the top level) --
        this is deliberately the "correct top-level pick, still wrong"
        sub-case, not a positional-mis-pick sub-case (that mechanism was
        specific to Revise-3's deleted marker-search code, not applicable
        to this architecture).
      - Currently-computed padding-preserving value (what naively threading
        out the existing ``fire_node_count`` local would produce):
        1 (outer if-node) + 156 (outer overlay fire-side count, which still
        numerically includes the nested tier's own same-size stub bulk)
        = 157.
      - Genuinely honest value (hand-derived from the RAW pre-detector
        pieces below, excluding every continuation entirely, at every
        level): 1 (outer if) + 1 (outer fire if-child wrapper) + 1 (nested
        if) + 1 (nested fire if-child wrapper) + 2 (VIXY + BTAL) = 6.
    """
    inner_fire_assets = [ss.make_asset("VIXY"), ss.make_asset("BTAL")]
    inner_continuation_assets = [ss.make_asset(f"CORE_ASSET_{i:03d}") for i in range(150)]
    inner_cond = ss.make_condition(
        ss.make_indicator("relative-strength-index", "SPY", window=10), "gt", 82.5
    )
    inner_if = ss.make_if(
        inner_cond, then_children=inner_fire_assets, else_children=inner_continuation_assets
    )

    outer_continuation_assets = [ss.make_asset(f"CORE_ASSET_OUTER_{i:03d}") for i in range(200)]
    outer_cond = ss.make_condition(
        ss.make_indicator("relative-strength-index", "SPY", window=10), "gt", 80
    )
    return ss.make_if(outer_cond, then_children=[inner_if], else_children=outer_continuation_assets)


def _build_unrelated_stub_fixture() -> dict:
    """Outer RSI(SPY,10)>80 cascade; fire branch = [VIXY leaf, a
    NON-QUALIFYING nested if-node (ticker-vs-ticker crossover, no fixed
    threshold -- fails ``_qualifies_as_cascade_rung``)] whose ELSE side is 4
    CORE_ASSET_-prefixed leaves with NO VIX ticker anywhere -- each of those
    4 leaves independently triggers ``_compact_subtree``'s per-child
    "-unrelated-stub" fallback (verified empirically: 4 distinct
    ``*-unrelated-stub`` ids appear in the overlay, one per leaf -- this
    fallback is per-LEAF, not per-branch).

    This is a DIFFERENT correctness dimension than the multi-tier fixture:
    it does not create an under-count risk (the leaf-for-leaf stub swap is
    count-preserving and the non-qualifying if-node is NOT itself a
    fire/continuation split, so ALL of it is genuine fire-branch content,
    nothing to exclude) -- it targets an OVER-exclusion risk instead: any
    new implementation that naively "excludes anything containing the
    STUBBED_CORE_CONTINUATION_TICKER marker" (rather than the real,
    qualification-based rule) would WRONGLY exclude this legitimate content,
    since it now (post-compaction) contains 4 stub leaves despite never
    having a fire/continuation split at all.

    Verified honest count (hand-derived from the RAW pieces, before any
    detector processing -- and confirmed unchanged after compaction, since
    the leaf swap doesn't alter node count): outer fire-side wrapper(1) +
    VIXY(1) + non_qualifying(8: its own if-child(1) + fire wrapper(1) +
    MSFT(1) + else wrapper(1) + 4 CORE_ASSET_ leaves(4)) = 1+1+8 = 10.
    signal_logic_node_count = 1 (outer if) + 10 = 11.
    """
    non_qualifying = ss.make_if(
        ss.make_condition(
            ss.make_indicator("relative-strength-index", "BND", window=20),
            "gt",
            "SH",
            rhs_indicator=ss.make_indicator("relative-strength-index", "SH", window=60),
        ),
        then_children=[ss.make_asset("MSFT")],
        else_children=[ss.make_asset(f"CORE_ASSET_{i:03d}") for i in range(4)],
    )
    fire_content = [ss.make_asset("VIXY"), non_qualifying]
    outer_cond = ss.make_condition(
        ss.make_indicator("relative-strength-index", "SPY", window=10), "gt", 80
    )
    outer_continuation = [ss.make_asset(f"CORE_ASSET_OUTER_{i:03d}") for i in range(30)]
    return ss.make_if(outer_cond, then_children=fire_content, else_children=outer_continuation)


def _build_inverted_polarity_fixture() -> dict:
    """Outer RSI(SPY,10)>80 cascade whose ``is-else-condition?==False``
    (cond_child) side is LARGE and contains SVXY (qualification checks
    cond_child specifically, so this qualifies the root as a candidate
    cascade); its ``is-else-condition?==True`` (else_child) side is SMALL
    and ALSO contains VIX-family tickers (VXX, UVIX).

    Because ``_compact_if_node``'s fire-pick is purely size-based
    (unrelated to which side qualified the root), and else_child (3 raw
    nodes) is smaller than cond_child (22 raw nodes), fire_child=else_child
    -- the GENUINE, correctly-represented fire content ends up on the
    ``is-else-condition?==True`` side. Verified empirically against the
    real, unmodified detector: this produces exactly ONE cascade (not
    discarded -- the overlay's surviving fire content genuinely has VIX, so
    the detector's own zero-VIX-overlay defensive discard, line ~874, does
    NOT fire here; that discard only fires on the DIFFERENT, degenerate
    case where compaction picks a fire side with NO VIX at all, confirmed
    by a separate probe run that produced skip_reason="candidate cascade
    roots were found but all failed validation").

    This is the real_tree_09/n2oo class case (team-lead's framing): a
    cascade whose fire content is genuine and correctly represented, but
    sits on the ``is-else-condition?==True`` side -- the polarity the
    PRE-EXISTING (out-of-scope) ``_graft_incumbent_core`` code doesn't
    expect, since it hardcodes "graft the real
    ``is-else-condition?==True`` child" as "the real core to preserve".

    Honest count: 1 (outer if) + 1 (fire if-child wrapper) + VXX(1) +
    UVIX(1) = 4. fire_is_else_branch = True.
    """
    cond_side_assets = [ss.make_asset("SVXY")] + [
        ss.make_asset(f"HEDGEPAD{i:02d}") for i in range(20)
    ]
    else_side_assets = [ss.make_asset("VXX"), ss.make_asset("UVIX")]
    outer_cond = ss.make_condition(
        ss.make_indicator("relative-strength-index", "SPY", window=10), "gt", 80
    )
    return ss.make_if(outer_cond, then_children=cond_side_assets, else_children=else_side_assets)


def _build_normal_polarity_fixture() -> dict:
    """Baseline contrast fixture for the polarity field: a simple,
    unambiguous, single-tier cascade where fire genuinely ends up on the
    ``is-else-condition?==False`` side (the common/normal case). Honest
    count: 1 (outer if) + 1 (fire wrapper) + VIXY(1) + BTAL(1) = 4.
    fire_is_else_branch = False.
    """
    fire_assets = [ss.make_asset("VIXY"), ss.make_asset("BTAL")]
    continuation_assets = [ss.make_asset(f"CORE_ASSET_{i:03d}") for i in range(30)]
    outer_cond = ss.make_condition(
        ss.make_indicator("relative-strength-index", "SPY", window=10), "gt", 80
    )
    return ss.make_if(outer_cond, then_children=fire_assets, else_children=continuation_assets)


# ---------------------------------------------------------------------------
# signal_logic_node_count -- correctness on synthetic, hand-countable fixtures
# ---------------------------------------------------------------------------


def test_signal_logic_node_count_is_honest_on_a_flat_single_tier_cascade(fd):
    """Baseline sanity: a flat (no nesting) cascade's signal_logic_node_count
    equals 1 (root if) + 1 (fire wrapper) + real fire leaf count -- no
    exclusion machinery is exercised here, just confirms the field exists
    and the trivial case is right before testing the adversarial ones."""
    tree = _build_normal_polarity_fixture()
    result = fd.detect_frontrunner_cascades(tree)
    assert len(result.cascades) == 1
    casc = result.cascades[0]
    assert casc.signal_logic_node_count == 4


def test_multitier_dramatic_gap_between_padding_preserving_and_honest(fd):
    """THE CRITICAL correctness pin. Reproduces PR #128's round-3 defect
    class ("correct top-level pick, still wrong due to unstripped nested
    padding") with a fixture I hand-derived independently, never chasing
    the review agent's own repro numbers (team-lead ruling: those numbers
    are not derivable/held by anyone on this team; a smaller hand-countable
    synthetic repro of the identical STRUCTURAL defect is the better test).

    Asserts BOTH numbers side by side so a reviewer can see this is a real
    adversarial gap, not an arbitrary literal: the honest value (6) must be
    what signal_logic_node_count actually reports; the padding-preserving
    value (157) is what a naive "just thread out the existing
    fire_node_count local" implementation would wrongly produce -- any such
    implementation fails this test."""
    tree = _build_multitier_dramatic_fixture()
    result = fd.detect_frontrunner_cascades(tree)
    assert len(result.cascades) == 1
    casc = result.cascades[0]

    honest_expected = 6
    padding_preserving_wrong_value = 157
    assert honest_expected != padding_preserving_wrong_value, (
        "sanity: the whole point of this test is that these two differ"
    )
    assert casc.signal_logic_node_count == honest_expected
    assert casc.signal_logic_node_count != padding_preserving_wrong_value


def test_unrelated_stub_content_is_not_wrongly_excluded_from_signal_logic(fd):
    """The OVER-exclusion adversarial case: fire-branch content that
    happens to contain stub markers (via the unrelated, per-leaf
    "-unrelated-stub" compaction fallback, NOT a genuine fire/continuation
    split) must be COUNTED, not excluded. A naive "exclude anything
    containing STUBBED_CORE_CONTINUATION_TICKER" implementation would fail
    this test by under-counting."""
    tree = _build_unrelated_stub_fixture()
    result = fd.detect_frontrunner_cascades(tree)
    assert len(result.cascades) == 1
    casc = result.cascades[0]
    assert casc.signal_logic_node_count == 11


# ---------------------------------------------------------------------------
# fire_is_else_branch -- polarity field
# ---------------------------------------------------------------------------


def test_fire_is_else_branch_false_on_normal_polarity_cascade(fd):
    tree = _build_normal_polarity_fixture()
    result = fd.detect_frontrunner_cascades(tree)
    casc = result.cascades[0]
    assert casc.fire_is_else_branch is False


def test_fire_is_else_branch_true_on_inverted_polarity_cascade(fd):
    """The real_tree_09/n2oo class case. Also confirms the cascade is
    genuinely detected (not discarded by the detector's own zero-VIX-
    overlay defensive check) and that vix_tickers is still correctly
    populated from the (correctly-identified, just is-else=True-sided) fire
    branch."""
    tree = _build_inverted_polarity_fixture()
    result = fd.detect_frontrunner_cascades(tree)
    assert len(result.cascades) == 1
    casc = result.cascades[0]
    assert casc.fire_is_else_branch is True
    assert casc.vix_tickers == {"VXX", "UVIX"}
    assert casc.signal_logic_node_count == 4


def test_fire_is_else_branch_never_raises_and_is_always_a_bool(fd):
    """D-1: the field is always a real bool on every genuinely-detected
    cascade, never None/missing -- unlike signal_logic_node_count (which
    can legitimately be None on an unidentifiable shape), polarity is
    always determinable for anything that survived detection at all, since
    detection itself already resolved is-else-condition? identity."""
    for tree in (
        _build_normal_polarity_fixture(),
        _build_inverted_polarity_fixture(),
        _build_multitier_dramatic_fixture(),
        _build_unrelated_stub_fixture(),
    ):
        result = fd.detect_frontrunner_cascades(tree)
        for casc in result.cascades:
            assert isinstance(casc.fire_is_else_branch, bool)


# ---------------------------------------------------------------------------
# Real-fixture secondary pin (team-lead-approved combination: one hand-
# verified exact value + one order-of-magnitude structural bound)
# ---------------------------------------------------------------------------


def test_signal_logic_node_count_exact_value_on_a_real_multitier_cascade(fd):
    """ONE hand-traced exact value from ONE real fixture's genuine
    multi-tier cascade (real_tree_04, group "2060 FTLT OG", the
    RSI(SPY,10)>80 / RSI(SPY,10)>82.5 two-tier cascade). Node-by-node dump
    (probe output, reproduced here for the reviewer, not re-derived from
    any detector selection code):

        if-child (outer fire, is-else=False)         n=17
          group                                       n=16
            wt-cash-equal                              n=15
              if (nested tier)                          n=14
                if-child (nested fire, is-else=False)     n=6
                  group                                     n=5
                    wt-cash-equal                             n=4
                      asset UVIX                                 n=1
                      asset UVIX                                 n=1
                      asset VIXM                                 n=1
                if-child (nested continuation, is-else=True) n=7  -- EXCLUDED
                  6x asset _STUBBED_CORE_CONTINUATION (already
                  stubbed by the detector's own, unchanged,
                  compaction -- excluded here regardless of its
                  padded size, per the honest-count definition)

    Honest count, hand-summed top-down: outer if-child wrapper(1) +
    group(1) + wt-cash-equal(1) + nested-if(1) + nested-fire-side(6,
    already totals its own subtree) = 10. signal_logic_node_count = 1
    (the cascade ROOT if-node itself, one level above the outer if-child
    shown above) + 10 = 11.

    Currently-computed padding-preserving value for contrast (what a naive
    threaded-out fire_node_count would produce): 1 + 17 = 18 -- the outer
    fire-side's CURRENT count (17) already includes the nested tier's own
    same-size stub bulk (7), so it overcounts by exactly that stub's size.
    """
    tree = json.loads(
        (FIXTURE_DIR / "real_tree_04_INfCn3eKsu6i4oTTqdUp.json").read_text(encoding="utf-8")
    )
    result = fd.detect_frontrunner_cascades(tree)
    target = next(
        c
        for c in result.cascades
        if c.rsi_thresholds == [80.0, 82.5] and c.group_name == "2060 FTLT OG"
    )
    assert target.signal_logic_node_count == 11
    assert target.signal_logic_node_count != 18  # the padding-preserving wrong value


def test_signal_logic_node_count_is_order_of_magnitude_below_whole_overlay_on_real_trees(fd):
    """Structural (not exact-number) regression bound across every real
    fixture: the honest signal-logic count can NEVER exceed the whole
    overlay's total node count (it is a strict subset by definition -- the
    continuation is excluded, never included), and for any cascade whose
    whole overlay is large (>=100 nodes -- i.e. genuinely has meaningful
    continuation padding to exclude), the honest count must be
    MEANINGFULLY smaller (this project's convention: <=50% -- the same
    order-of-magnitude relationship MATERIAL_SIMPLIFICATION_MAX_RATIO
    already encodes elsewhere), never suspiciously close to the whole-tree
    size (which would indicate padding is leaking into the count again).
    Proves the invariant holds across ALL real cascades in these fixtures,
    not just the one hand-traced above."""
    checked_a_large_one = False
    for path in sorted(FIXTURE_DIR.glob("real_tree_*.json")):
        tree = json.loads(path.read_text(encoding="utf-8"))
        result = fd.detect_frontrunner_cascades(tree)
        for casc in result.cascades:
            if casc.signal_logic_node_count is None:
                continue
            whole = fd._count_nodes(casc.overlay_tree)
            assert casc.signal_logic_node_count <= whole, (
                f"{path.stem}/{casc.group_name}: signal_logic_node_count "
                f"({casc.signal_logic_node_count}) exceeded the whole overlay "
                f"count ({whole}) -- a subset can never be bigger than its whole"
            )
            if whole >= 100:
                checked_a_large_one = True
                assert casc.signal_logic_node_count <= whole * 0.5, (
                    f"{path.stem}/{casc.group_name}: signal_logic_node_count "
                    f"({casc.signal_logic_node_count}) is not meaningfully smaller "
                    f"than the whole overlay ({whole}) -- padding may be leaking "
                    f"into the count again"
                )
    assert checked_a_large_one, (
        "fixture sanity: expected at least one real cascade with a >=100-node "
        "overlay across all 11 real_tree fixtures to exercise the bound"
    )


# ---------------------------------------------------------------------------
# R4-1's own non-regression guard: detection behavior itself is byte-
# unchanged (additive field only) -- overlay_tree/rsi_thresholds/vix_tickers
# must not shift for ANY real fixture as a side effect of adding the two new
# fields.
# ---------------------------------------------------------------------------


def test_detection_output_unchanged_for_existing_fields_across_all_real_fixtures(fd):
    """R4-1 is additive-only: overlay_tree, rsi_thresholds, vix_tickers, and
    group_name must be byte-identical (same structure, same values) to
    whatever detect_frontrunner_cascades already produced before this
    cycle -- proven here by asserting internal consistency invariants that
    would break if _compact_if_node's tree-construction logic were touched
    (rather than diffing against a frozen golden capture, since this repo's
    tests never hardcode producer-computed literals): every overlay_tree
    still has exactly 2 top-level if-children, one is-else=False and one
    is-else=True, and rsi_thresholds/vix_tickers stay non-empty for every
    surviving cascade (the same invariants the pre-existing detector suite
    already enforces elsewhere -- this is a smoke-level confirmation that
    adding the two new fields didn't perturb the existing construction
    path, not a full re-specification of detector correctness)."""
    for path in sorted(FIXTURE_DIR.glob("real_tree_*.json")):
        tree = json.loads(path.read_text(encoding="utf-8"))
        result = fd.detect_frontrunner_cascades(tree)
        for casc in result.cascades:
            top_children = casc.overlay_tree.get("children") or []
            polarities = sorted(c.get("is-else-condition?") for c in top_children)
            assert polarities == [False, True], (
                f"{path.stem}/{casc.group_name}: overlay_tree's top-level "
                f"if-children polarity shape changed"
            )
            assert casc.rsi_thresholds, f"{path.stem}/{casc.group_name}: rsi_thresholds is empty"
            assert casc.vix_tickers, f"{path.stem}/{casc.group_name}: vix_tickers is empty"


# ---------------------------------------------------------------------------
# R4-2/B3 companion: the DEDICATED clause-aware counter (the shared
# single-source-of-truth this module uses to stamp signal_logic_node_count,
# and frontrunner_builder imports verbatim for the overlay operand per B3 --
# see tests/advisors/test_frontrunner_simplify_path_wiring.py's identity
# pin) has the OPPOSITE behavior from the shared, general-purpose,
# REVERTED _count_tree_nodes (which tests/advisors/test_frontrunner_
# acceptance.py's own reversion pin proves does NOT descend clauses
# anymore): this dedicated counter MUST genuinely descend a compound
# condition's clause list, since it is what makes clause count load-bearing
# on the overlay SIMPLIFY operand's honesty (a 2-clause vs a 12-clause
# compound condition are genuinely different amounts of signal logic).
# ---------------------------------------------------------------------------


def test_dedicated_clause_aware_counter_genuinely_descends_compound_condition_clauses(fd):
    """A 12-clause compound condition must count MORE than a 2-clause one
    via the DEDICATED clause-aware counter (name TBD by fps-impl -- see
    test_frontrunner_simplify_path_wiring.py's identity pin for how this
    test's sibling locates it without presupposing the name), with
    otherwise IDENTICAL then/else allocation content -- the opposite
    assertion from the shared _count_tree_nodes's own reversion pin,
    confirming R4-2's split landed as two genuinely different counters,
    not a single counter that (wrongly) either always or never descends."""
    from advisors import symphony_schema as ss

    def _make_if_compound_with_n_clauses(n: int) -> dict:
        conditions = [
            ss.make_binary_condition(
                ss.make_condition_operand("relative-strength-index", f"TICKER_{i:02d}", window=10),
                "gt",
                ss.make_constant_rhs(80),
            )
            for i in range(n)
        ]
        compound_condition = ss.make_compound_condition("any", conditions)
        return ss.make_if_compound(
            compound_condition,
            then_children=[ss.make_weight_equal([ss.make_asset("VIXY")])],
            else_children=[ss.make_weight_equal([ss.make_asset("CORE_ASSET_0001")])],
        )

    tree_2 = _make_if_compound_with_n_clauses(2)
    tree_12 = _make_if_compound_with_n_clauses(12)

    counter = getattr(fd, "_count_clause_aware_signal_logic", None) or getattr(
        fd, "count_clause_aware_signal_logic", None
    )
    assert counter is not None, (
        "expected frontrunner_detector to expose the dedicated clause-aware "
        "counter (name TBD by fps-impl) -- not found under either "
        "candidate name"
    )

    count_2 = counter(tree_2)
    count_12 = counter(tree_12)
    assert count_12 > count_2, (
        f"a 12-clause compound condition counted {count_12} via the "
        f"dedicated clause-aware counter, a 2-clause one counted "
        f"{count_2} -- this counter must genuinely descend compound "
        f"condition clauses (both trees have IDENTICAL then/else "
        f"allocation content, so any equal-count result means it isn't)"
    )


# ---------------------------------------------------------------------------
# Team-lead's refinement on option (a)/R4-1 (2026-08-24, after my padding-
# preserving finding): "mirrors the selection logic" must not mean a SECOND
# COPY of it. fps-impl must EXTRACT the fire-vs-continuation SELECTION
# decision (same size-based split + qualification checks _compact_if_node
# already performs) into ONE shared helper that BOTH _compact_if_node (for
# compaction/padding) and the new signal_logic_node_count-computing
# function (for exclusion) call -- each applies its OWN treatment to the
# SAME selection result. This keeps _compact_if_node's tested padding
# behavior untouched and kills the mirror-drift class (else-slot copies,
# marker literals, oracle mirrors) this entire cycle has been eliminating.
# ---------------------------------------------------------------------------


def _find_shared_selection_helper(fd_module):
    """Locate the shared fire/continuation selection helper under either
    plausible name (fps-impl's naming choice) -- mirrors the same
    getattr-fallback pattern the B3 counter-identity tests use elsewhere in
    this cycle, so this test doesn't presuppose an exact name."""
    return getattr(fd_module, "_select_fire_and_continuation", None) or getattr(
        fd_module, "select_fire_and_continuation", None
    )


def test_shared_selection_helper_exists_and_is_a_real_callable(fd):
    """The shared selection helper must exist as a real, callable, named
    module-level function -- a prerequisite for the identity pin below (an
    AttributeError here is the legitimate RED signal before it's added)."""
    helper = _find_shared_selection_helper(fd)
    assert helper is not None, (
        "expected advisors.frontrunner_detector to expose the shared "
        "fire/continuation SELECTION helper (name TBD by fps-impl, tried "
        "_select_fire_and_continuation and select_fire_and_continuation) "
        "-- team-lead's ruling: _compact_if_node's selection decision must "
        "be extracted into ONE shared helper, not mirrored/duplicated by "
        "the new signal_logic_node_count-computing function"
    )
    assert callable(helper), f"expected a callable, got {helper!r}"


def test_compact_if_node_resolves_selection_through_the_shared_helper(fd):
    """AST-based identity pin (team-lead: 'pin the identity where
    practical'): _compact_if_node's own function body must contain a call
    to the shared selection helper BY NAME (found via inspecting the
    helper's real __name__ once located, never a guessed string) -- proving
    _compact_if_node was refactored to CALL the shared helper, not that a
    same-named-but-independently-implemented copy happens to exist
    somewhere. Uses the exact same scoped-ast.walk-after-finding-the-
    FunctionDef-by-name technique this project's own
    tests/synthetic_history/test_bounded_replay_parallelism.py established
    for an analogous single-source-of-truth call-site pin."""
    helper = _find_shared_selection_helper(fd)
    assert helper is not None, (
        "the shared selection helper must exist before its call sites can "
        "be verified -- see test_shared_selection_helper_exists_and_is_a_"
        "real_callable"
    )
    helper_name = helper.__name__

    module_path = inspect.getfile(fd)
    tree = ast.parse(pathlib.Path(module_path).read_text(encoding="utf-8"))

    compact_if_node_def = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_compact_if_node"
        ),
        None,
    )
    assert compact_if_node_def is not None, (
        "_compact_if_node function definition not found in "
        "frontrunner_detector.py's source -- ast.walk finds nested closures "
        "too, so this should locate it regardless of whether it stays "
        "nested inside _build_cascade_overlay"
    )

    calls_to_helper = [
        node
        for node in ast.walk(compact_if_node_def)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == helper_name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == helper_name)
        )
    ]
    assert calls_to_helper, (
        f"_compact_if_node's own body contains no call to "
        f"{helper_name!r} (the shared selection helper) -- team-lead's "
        f"ruling requires _compact_if_node to CALL the shared helper for "
        f"its fire/continuation selection decision, never reimplement the "
        f"same size-based split + qualification checks inline or in a "
        f"second, independently-written copy"
    )


# ---------------------------------------------------------------------------
# Team-lead's iterative-convention ruling (2026-08-24, after I deprioritized
# a deep-fixture stress test as impractical to construct): don't leave the
# explicit-stack convention as prose-only -- an adversarial SOURCE-SCAN
# pinning it structurally, mirroring this project's own established
# blast-radius pattern (tests/synthetic_history/test_bounded_replay_
# parallelism.py's no-bare-Parallel(-1) guard), is the enforceable form.
# ---------------------------------------------------------------------------


def _direct_calls_within(
    func_def: ast.FunctionDef | ast.AsyncFunctionDef, candidate_names: set[str]
) -> set[str]:
    """Return the subset of candidate_names that func_def's OWN body
    (excluding any NESTED function definitions inside it, which have their
    own independent call graph) directly calls."""
    called: set[str] = set()
    for node in ast.walk(func_def):
        if node is not func_def and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # A nested function's body is scanned by its OWN entry in the
            # call graph, not folded into the outer function's.
            continue
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in candidate_names:
                called.add(name)
    return called


def _find_cycle(call_graph: dict[str, set[str]]) -> list[str] | None:
    """DFS cycle detection over a directed call graph (name -> names it
    directly calls). Returns the cycle as a list of names (e.g.
    ['a', 'b', 'a'] for a 2-cycle, ['a', 'a'] for a self-loop) or None if
    acyclic. Generalizes beyond a simple self-loop check because a
    per-function self-call check alone MISSES mutual recursion (A calls B,
    B calls A) -- confirmed a real risk on this exact cycle: fps-impl's
    reviewed implementation plan proposed exactly this shape (a
    fire_count/subtree_count mutual-recursion pair) between the new
    counter and the shared selection helper, which would pass a
    self-call-only check while still being genuine, stack-depth-risking
    Python recursion."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(call_graph, WHITE)
    path: list[str] = []

    def _visit(node: str) -> list[str] | None:
        color[node] = GRAY
        path.append(node)
        for neighbor in call_graph.get(node, ()):
            if neighbor not in color:
                # A call to a function outside the target set -- not part
                # of this cycle-detection's scope.
                continue
            if color[neighbor] == GRAY:
                cycle_start = path.index(neighbor)
                return [*path[cycle_start:], neighbor]
            if color[neighbor] == WHITE:
                result = _visit(neighbor)
                if result is not None:
                    return result
        path.pop()
        color[node] = BLACK
        return None

    for start in call_graph:
        if color[start] == WHITE:
            found = _visit(start)
            if found is not None:
                return found
    return None


def test_new_signal_logic_counter_and_shared_selection_helper_have_no_recursion_cycle(fd):
    """Structural (AST-based) pin on the explicit-stack/iterative convention
    this module already established for _count_nodes/_collect_tickers ("so
    a very deep real tree never triggers RecursionError") -- the NEW
    signal-logic-counting function (whichever tier-1-approved name fps-impl
    picks: tried _count_clause_aware_signal_logic and
    count_clause_aware_signal_logic, the same candidates the B3 identity
    tests use) and the shared selection helper above must NOT form a
    recursion cycle among themselves -- covers BOTH self-recursion (a
    function calling itself directly) AND MUTUAL recursion (function A
    calling function B, which calls A back) -- an explicit-stack/worklist
    implementation is the only pattern that passes this check; ordinary
    Python recursion, direct or mutual, does not.

    Deliberately does NOT attempt to construct a fixture deep enough to
    trigger an actual RecursionError (symphony_schema.make_if's own
    copy.deepcopy hits Python's recursion ceiling around ~1000 levels
    BEFORE such a fixture could even be built, and every real multi-tier
    cascade across all 11 real fixtures tops out at 2 tiers of nesting) --
    this is the STRUCTURAL, enforceable substitute team-lead's ruling asked
    for instead."""
    module_path = inspect.getfile(fd)
    tree = ast.parse(pathlib.Path(module_path).read_text(encoding="utf-8"))

    counter = getattr(fd, "_count_clause_aware_signal_logic", None) or getattr(
        fd, "count_clause_aware_signal_logic", None
    )
    selection_helper = _find_shared_selection_helper(fd)

    targets = {}
    if counter is not None:
        targets["the new signal-logic counter"] = counter.__name__
    if selection_helper is not None:
        targets["the shared selection helper"] = selection_helper.__name__

    assert targets, (
        "neither the new signal-logic counter nor the shared selection "
        "helper exists yet under any of their candidate names -- this "
        "test has nothing to check until at least one of them is "
        "implemented (a legitimate RED-by-absence signal, matching this "
        "cycle's other name-agnostic identity checks)"
    )

    target_names = set(targets.values())
    func_defs_by_name: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for label, func_name in targets.items():
        func_def = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == func_name
            ),
            None,
        )
        assert func_def is not None, (
            f"{label} ({func_name!r}) is importable from the module but its "
            f"FunctionDef was not found by the SAME name in the module's own "
            f"source AST -- unexpected mismatch between the runtime object "
            f"and its source location"
        )
        func_defs_by_name[func_name] = func_def

    call_graph = {
        name: _direct_calls_within(func_def, target_names)
        for name, func_def in func_defs_by_name.items()
    }
    cycle = _find_cycle(call_graph)
    assert cycle is None, (
        f"a recursion cycle exists among the target functions: "
        f"{' -> '.join(cycle)} -- this project's established convention "
        f"(_count_nodes/_collect_tickers, both explicitly iterative 'so a "
        f"very deep real tree never triggers RecursionError') requires an "
        f"explicit-stack/worklist walk here too, never ordinary Python "
        f"recursion -- this includes MUTUAL recursion between two "
        f"functions (e.g. a fire-count/subtree-count pair calling each "
        f"other back and forth), not just a function calling itself "
        f"directly, since both classes carry the identical stack-depth risk"
    )
