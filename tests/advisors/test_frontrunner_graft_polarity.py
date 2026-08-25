"""RED tests -- SPLICE POLARITY DEFECT (pre-existing wave-2 code, tracked as
its own fix cycle per DE-FR-SIMPLIFY-001 Revise 4's note in
test_frontrunner_detector_r4_signal_logic.py).

Module under test: advisors.frontrunner_builder._graft_incumbent_core and
advisors.frontrunner_builder.splice_candidate_into_symphony.

THE DEFECT: ``_graft_incumbent_core`` (frontrunner_builder.py:1114-1157)
selects the incumbent's "real core" to preserve during a splice by hardcoding
a search for the ``is-else-condition?==True`` child -- i.e. it assumes the
else side is always the core. But fire/continuation selection
(``frontrunner_detector._select_fire_and_continuation``, detector.py:616-652)
is purely SIZE-based (fire = the smaller of the two direct children),
independent of which side carries the ``is-else-condition?`` marker. On an
INVERTED-POLARITY cascade -- fire content genuinely lands on the
``is-else-condition?==True`` side because it happens to be the smaller
branch (the real_tree_09/n2oo class, team-lead's framing) -- the graft reads
"is-else==True" as "the real core" and grafts the FIRE content in as if it
were core, while the actual (larger) real core on the OTHER side is silently
dropped. ``symphony_schema.validate_tree`` does not catch this: the
corrupted tree is still structurally well-formed, just semantically wrong
(confirmed empirically below -- validate_tree returns [] on both the
pre-fix corrupted output and the fixed output).

ORACLE INDEPENDENCE: the two fixture builders below are LOCAL to this file
(not imported from test_frontrunner_detector_r4_signal_logic.py) --
hand-derived from the raw pieces the same way that file's own
``_build_inverted_polarity_fixture``/``_build_normal_polarity_fixture`` are,
per this module's established "know the answer by construction, never by
asking selection code" convention. Each fixture builder returns
``(if_node, real_core_tickers)`` so every assertion below compares against
the ACTUAL asset tickers used to build the fixture, never a re-typed
literal.

NON-VACUITY (pre-verified against the unmodified, pre-fix code before this
file was written): Test 1 grafts ``{VXX, UVIX}`` (the fire side) instead of
the real core; Test 2's full splice similarly drops SVXY/HEDGEPAD* and
retains VXX/UVIX. Tests 3 and 4 (normal-polarity, no-regression) both PASS
unmodified today, since for that fixture the detector's continuation_child
and the current is-else-condition?==True search happen to agree.

SCOPE: this file calls advisors.frontrunner_builder (under test) and reads
advisors.frontrunner_detector.detect_frontrunner_cascades /
Cascade.fire_is_else_branch (already-shipped, unchanged) purely to build a
realistic detection result -- no diff to the detector, acceptance layer, or
any engine/analytics module.
"""

from __future__ import annotations

import pytest

from advisors import symphony_schema as ss


@pytest.fixture(scope="module")
def fbld():
    """Import and return the frontrunner_builder module."""
    import advisors.frontrunner_builder as _fbld  # noqa: PLC0415

    return _fbld


# ---------------------------------------------------------------------------
# Local, oracle-independent fixture builders -- each returns
# (if_node, real_core_tickers), where real_core_tickers is the exact ticker
# set of the content that a correct graft must preserve.
# ---------------------------------------------------------------------------


def _build_inverted_polarity_incumbent() -> tuple[dict, frozenset[str]]:
    """Outer RSI(SPY,10)>80 cascade, real_tree_09/n2oo class: the
    ``is-else-condition?==False`` (cond) side is LARGE (SVXY + 20 hedge-pad
    tickers = 21 leaves) and is the incumbent's genuine core strategy
    content; the ``is-else-condition?==True`` (else) side is SMALL (VXX +
    UVIX = 2 leaves) and is the genuine fire/hedge content. Because
    fire/continuation selection is purely size-based, fire lands on the
    is-else==True side here -- the polarity the pre-existing graft code
    doesn't expect."""
    core_assets = [ss.make_asset("SVXY")] + [
        ss.make_asset(f"HEDGEPAD{i:02d}") for i in range(20)
    ]
    fire_assets = [ss.make_asset("VXX"), ss.make_asset("UVIX")]
    cond = ss.make_condition(
        ss.make_indicator("relative-strength-index", "SPY", window=10), "gt", 80
    )
    node = ss.make_if(cond, then_children=core_assets, else_children=fire_assets)
    real_core_tickers = frozenset(a["ticker"] for a in core_assets)
    return node, real_core_tickers


def _build_normal_polarity_incumbent() -> tuple[dict, frozenset[str]]:
    """Baseline contrast fixture: fire (VIXY + BTAL, 2 leaves) genuinely
    lands on the is-else-condition?==False side; the real core (30
    CORE_ASSET_* leaves) is on the is-else-condition?==True side -- the
    common/normal case the current is-else-based graft happens to get
    right."""
    fire_assets = [ss.make_asset("VIXY"), ss.make_asset("BTAL")]
    core_assets = [ss.make_asset(f"CORE_ASSET_{i:03d}") for i in range(30)]
    cond = ss.make_condition(
        ss.make_indicator("relative-strength-index", "SPY", window=10), "gt", 80
    )
    node = ss.make_if(cond, then_children=fire_assets, else_children=core_assets)
    real_core_tickers = frozenset(a["ticker"] for a in core_assets)
    return node, real_core_tickers


def _build_candidate_node(
    fire_ticker: str = "SOXL", placeholder_ticker: str = "PLACEHOLDER_STUB"
) -> dict:
    """A hand-built, already-compiled (``step``-keyed) candidate if-node,
    accepted directly by ``splice_candidate_into_symphony`` via its
    ``"step" in candidate`` branch -- no plan_tree_compiler round-trip
    needed, isolating this defect from compiler concerns. Its terminal else
    branch carries a placeholder ticker distinguishable from both the
    incumbent's real core and its fire content, so a grafted-vs-untouched
    terminal else is always unambiguous."""
    cond = ss.make_condition(
        ss.make_indicator("relative-strength-index", "SPY", window=10), "gt", 85
    )
    return ss.make_if(
        cond,
        then_children=[ss.make_asset(fire_ticker)],
        else_children=[ss.make_asset(placeholder_ticker)],
    )


# ---------------------------------------------------------------------------
# Test 1 -- PRIMARY RED (AC-1, AC-4): _graft_incumbent_core, unit-level.
# ---------------------------------------------------------------------------


def test_graft_incumbent_core_preserves_real_core_on_inverted_polarity_cascade(fbld):
    """On an inverted-polarity incumbent (fire content genuinely on the
    is-else-condition?==True side), _graft_incumbent_core must preserve the
    REAL core -- the detector's continuation_child (the larger, non-fire
    side) -- not the fire side. Exact-set (not subset) assertion: an
    over-inclusive graft that keeps BOTH sides also fails this.

    Non-vacuity (probed against the unmodified pre-fix code): the current
    is-else-condition?==True search grafts {VXX, UVIX} here instead."""
    original_node, real_core_tickers = _build_inverted_polarity_incumbent()
    compiled_node = _build_candidate_node()

    grafted = fbld._graft_incumbent_core(original_node, compiled_node)
    terminal_else = fbld._find_terminal_else_child(grafted)
    assert terminal_else is not None, (
        "fixture setup: the grafted candidate must retain a terminal else slot"
    )

    grafted_tickers = {
        c.get("ticker") for c in (terminal_else.get("children") or []) if isinstance(c, dict)
    }
    assert grafted_tickers == set(real_core_tickers), (
        f"_graft_incumbent_core grafted {grafted_tickers} as the incumbent's core on an "
        f"inverted-polarity cascade -- expected exactly the real core "
        f"{set(real_core_tickers)} (the detector's continuation_child, the LARGER "
        f"non-fire side). A hardcoded is-else-condition?==True search picks the FIRE "
        f"side here and silently drops the real core."
    )


# ---------------------------------------------------------------------------
# Test 2 -- WIRING-LEVEL RED (AC-1): full splice_candidate_into_symphony
# path, proving the fix actually reaches the real call site.
# ---------------------------------------------------------------------------


def test_splice_candidate_into_symphony_preserves_real_core_on_inverted_polarity_incumbent(
    fbld,
):
    """End-to-end proof through the real call path (not just the isolated
    graft helper): splicing a candidate into an inverted-polarity incumbent
    must yield a symphony containing the candidate's own fire ticker PLUS
    the incumbent's real core -- never the incumbent's old fire content
    (which the candidate is replacing) mistaken for core.

    Non-vacuity (probed against the unmodified pre-fix code): the spliced
    tree is missing SVXY/HEDGEPAD* entirely and wrongly retains VXX/UVIX;
    validate_tree still returns [] on that corrupted output -- schema
    validation alone cannot catch this defect."""
    from advisors import frontrunner_detector

    cascade_node, real_core_tickers = _build_inverted_polarity_incumbent()
    tree = ss.make_root("Inverted Polarity Incumbent", "daily", [cascade_node])

    detection = frontrunner_detector.detect_frontrunner_cascades(tree)
    assert len(detection.cascades) == 1, (
        f"fixture setup: expected exactly one detected cascade, got {len(detection.cascades)} "
        f"(skip_reason={detection.skip_reason!r})"
    )
    cascade = detection.cascades[0]
    assert cascade.fire_is_else_branch is True, (
        "fixture setup: this fixture must exercise the inverted-polarity (fire on "
        "is-else-condition?==True) class -- fire_is_else_branch must be True"
    )

    candidate = _build_candidate_node(fire_ticker="SOXL", placeholder_ticker="PLACEHOLDER_STUB")
    spliced = fbld.splice_candidate_into_symphony(tree, cascade, candidate)
    assert spliced is not None, "splice_candidate_into_symphony returned None unexpectedly"

    errors = ss.validate_tree(spliced)
    assert errors == [], f"spliced symphony failed validate_tree: {errors}"

    spliced_tickers = ss.extract_tickers(spliced)
    expected_tickers = set(real_core_tickers) | {"SOXL"}
    assert spliced_tickers == expected_tickers, (
        f"splice_candidate_into_symphony produced {spliced_tickers} on an inverted-polarity "
        f"incumbent -- expected exactly {expected_tickers} (the candidate's own fire ticker "
        f"plus the incumbent's real core, SVXY/HEDGEPAD*). If VXX/UVIX (the incumbent's old "
        f"fire content) appear instead of the real core, the graft's polarity defect reaches "
        f"the real call path, not just the isolated unit."
    )


# ---------------------------------------------------------------------------
# Tests 3 & 4 -- NO-REGRESSION pins (AC-2): normal polarity, unit + wiring.
# Both PASS unmodified today; must keep passing after the Option C fix.
# ---------------------------------------------------------------------------


def test_graft_incumbent_core_normal_polarity_unchanged(fbld):
    """On a normal-polarity incumbent (fire already on the
    is-else-condition?==False side), the graft must keep preserving exactly
    the same real core it does today -- Option C's detector-sourced
    continuation_child must agree with the current is-else-condition?==True
    search on this fixture (they select the identical child here)."""
    original_node, real_core_tickers = _build_normal_polarity_incumbent()
    compiled_node = _build_candidate_node()

    grafted = fbld._graft_incumbent_core(original_node, compiled_node)
    terminal_else = fbld._find_terminal_else_child(grafted)
    assert terminal_else is not None, (
        "fixture setup: the grafted candidate must retain a terminal else slot"
    )

    grafted_tickers = {
        c.get("ticker") for c in (terminal_else.get("children") or []) if isinstance(c, dict)
    }
    assert grafted_tickers == set(real_core_tickers), (
        f"NO-REGRESSION: normal-polarity graft output changed -- got {grafted_tickers}, "
        f"expected the unchanged real core {set(real_core_tickers)}"
    )


def test_splice_candidate_into_symphony_normal_polarity_unchanged(fbld):
    """Wiring-level mirror of the unit-level no-regression pin above: the
    full splice output on a normal-polarity incumbent must be unchanged by
    the Option C fix."""
    from advisors import frontrunner_detector

    cascade_node, real_core_tickers = _build_normal_polarity_incumbent()
    tree = ss.make_root("Normal Polarity Incumbent", "daily", [cascade_node])

    detection = frontrunner_detector.detect_frontrunner_cascades(tree)
    assert len(detection.cascades) == 1, (
        f"fixture setup: expected exactly one detected cascade, got {len(detection.cascades)} "
        f"(skip_reason={detection.skip_reason!r})"
    )
    cascade = detection.cascades[0]
    assert cascade.fire_is_else_branch is False, (
        "fixture setup: this fixture must exercise the normal-polarity (fire on "
        "is-else-condition?==False) class -- fire_is_else_branch must be False"
    )

    candidate = _build_candidate_node(fire_ticker="SOXL", placeholder_ticker="PLACEHOLDER_STUB")
    spliced = fbld.splice_candidate_into_symphony(tree, cascade, candidate)
    assert spliced is not None, "splice_candidate_into_symphony returned None unexpectedly"

    errors = ss.validate_tree(spliced)
    assert errors == [], f"spliced symphony failed validate_tree: {errors}"

    spliced_tickers = ss.extract_tickers(spliced)
    expected_tickers = set(real_core_tickers) | {"SOXL"}
    assert spliced_tickers == expected_tickers, (
        f"NO-REGRESSION: normal-polarity splice output changed -- got {spliced_tickers}, "
        f"expected the unchanged {expected_tickers}"
    )
