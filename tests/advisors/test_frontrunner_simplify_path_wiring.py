"""RED tests — DE-FR-SIMPLIFY-001 AC-2/AC-3/AC-6 wiring at the REAL
advisors.frontrunner_builder._run_build_for_symphony orchestration seam.

Module under test: advisors.frontrunner_builder (the cascade-processing loop
that calls _gate_and_accept_candidate, which in turn calls
advisors.frontrunner_acceptance.evaluate_calmar_acceptance). The unit-level
truth table for the acceptance clause itself lives in
tests/advisors/test_frontrunner_acceptance.py -- THIS file proves the real
builder call site actually THREADS the real delta-scoped node counts (the
detected cascade subtree the candidate replaces, and the small generated
overlay) into that call, and that SIMPLIFY admission is reachable end-to-end
through the real orchestration.

REVISE 4 (PR #128 round-3 /code-review, 2026-08-24): Revise 3's own fix
(RULING 1, marker-search-based cascade-side exclusion in
frontrunner_builder._count_signal_logic_nodes) was ITSELF disproven by a
runnable repro: on any cascade whose FIRE branch contains its own nested,
already-compacted tier (a genuine multi-tier scale-in), BOTH if-children end
up "containing a stub marker somewhere" (the continuation IS the stub; the
fire branch contains ITS OWN nested stub buried inside it) -- the marker
search cannot distinguish them and picks whichever comes first positionally.
Revise 3's own test suite could not catch this because its "expected value"
helper MIRRORED the same marker-search heuristic the production code used.

RULING (team-lead, Revise 4): architectural, not another heuristic patch.
The detector (advisors.frontrunner_detector) already knows, firsthand and
unambiguously, which if-child is fire and which is continuation at every
nesting level -- it decides this to build the overlay tree. No downstream
reconstruction can ever be as reliable. So:
  - Cascade side: frontrunner_detector.Cascade gains an additive
    ``signal_logic_node_count: int | None`` field, computed AT DETECTION
    TIME (see tests/advisors/test_frontrunner_detector_r4_signal_logic.py
    for the detector-level correctness proofs, hand-derived oracles, and
    the CRITICAL finding that the pre-existing local ``fire_node_count`` is
    itself NOT honest -- it is padding-size-preserving, not exclusion-
    based). THIS file's tests now read that field directly wherever the
    cascade-side count is needed -- frontrunner_builder no longer computes
    it at all; the old marker-search heuristic
    (``_contains_stub_marker``/the cascade-side priority in
    ``_count_signal_logic_nodes``) is DELETED from production, and
    correspondingly deleted from this file's test-local oracle below.
  - Overlay side: unification (B3, review round 3) -- the SAME dedicated
    clause-aware node counter the detector uses to stamp
    ``signal_logic_node_count`` is imported and reused by the builder for
    the overlay operand (never two independently-implemented same-purpose
    counters). Else-identification uses the EXISTING production
    ``_find_terminal_else_child`` as PRIMARY (R4-3), killing what was a
    third duplicate copy of the same "find the placeholder else" logic.
  - RULING 3 (Revise 3's compound-condition-clause descent) is REVERTED out
    of the shared, general-purpose ``_count_tree_nodes`` (restoring its
    original children-only display-delta semantics -- AC-6's
    ``node_count_delta`` display metric was never meant to change) and
    reimplemented as a DEDICATED clause-aware counter used ONLY for the
    overlay operand (see above).
  - R4-6: ``_count_overlay_node_count``'s tiers 2 (already-``step``-shaped
    candidate) and 3 (fresh-compile fallback when ``compiled_tree`` is
    absent) are DELETED entirely -- both were "unreachable under the
    current call graph, second-layer defense" by the project's own prior
    ruling, and round-3 review found tier 2 additionally SKIPS the unwrap
    step (would miscount if ever reached) -- project convention is unused/
    broken code gets deleted, not kept-and-labeled. ``_count_overlay_node_
    count`` now has exactly ONE path: reuse ``compiled_tree`` when present,
    else return None (never a fallback compile).

MOCKING STRATEGY (mirrors tests/advisors/test_frontrunner_gate_wiring.py and
test_frontrunner_builder_signal_wiring.py's established idiom exactly): same
incumbent-symphony construction pattern (symphony_schema constructors), same
"patch collaborators at their ORIGIN module" philosophy, same autouse
no-live-Atlas guard. The overfitting/BHY gate (backtest_gate_engine.
evaluate_candidate_batch) is explicitly OUT of this cycle's scope (feature
plan Scope Boundaries: "OUT: ... detector/splice/gate-engine") -- it is
mocked to return a controlled ADOPT_CANDIDATE verdict so these tests exercise
exactly the layer this cycle touches (the Calmar/SIMPLIFY acceptance clause
and its builder-side wiring) without depending on the numerically fragile
BHY/FDR/Gate#2 fold math test_frontrunner_gate_wiring.py's own docstrings
describe extensively probing to tune. advisors.frontrunner_acceptance.
evaluate_calmar_acceptance itself is NEVER mocked (wraps=real in AC-2, fully
real elsewhere) -- it is exactly what this cycle changes.

ORACLE METHODOLOGY (why this file's fixtures are genuinely independent, not
a Revise-3-style mirror): this file's own fixtures are deliberately FLAT
(no nested tiers -- multi-tier correctness lives entirely in
test_frontrunner_detector_r4_signal_logic.py's dedicated fixtures), so the
expected signal-logic count is PURE ARITHMETIC on the fixture's own
construction parameters (empirically verified via probe against the real,
unmodified detector/compiler before being hardcoded here, never re-derived
via any selection code):
  - cascade fire branch: 4 + fire_hedge_ticker_count (1 if-node + 1 if-child
    wrapper + 1 weight-equal wrapper + len(fire_tickers) asset leaves).
  - compiled overlay then-branch: 3 + n_assets (1 if-node + 1 if-child
    wrapper + 1 weight-equal wrapper + n_assets asset leaves).
Both formulas are held to a fixture-level sanity assertion against the REAL
cascade.signal_logic_node_count wherever the fixture builder runs the real
detector, so a formula/detector disagreement fails loud at the fixture
itself, not silently propagating a wrong expected value downstream.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared fixtures — mirror test_frontrunner_gate_wiring.py's established idiom.
# ---------------------------------------------------------------------------


@pytest.fixture
def fbld():
    import advisors.frontrunner_builder as _fbld  # noqa: PLC0415

    return _fbld


@pytest.fixture(autouse=True)
def _no_live_atlas_calls():
    """Same guard as test_frontrunner_gate_wiring.py / test_frontrunner_builder_
    signal_wiring.py — _run_build_for_symphony calls the real
    community_strats.load_community_strategies AND
    frontrunner_signals.load_frontrunner_signals unless patched, either of
    which can attempt a genuine MongoClient connection with no fresh cache row."""
    with (
        patch(
            "advisors.community_strats.load_community_strategies",
            return_value={
                "available": False,
                "candidates": [],
                "stats": {},
                "source": "captplanet",
            },
        ),
        patch(
            "advisors.frontrunner_signals.load_frontrunner_signals",
            return_value={
                "available": False,
                "reason": "TestGuardNoLiveAtlas",
                "signals": [],
                "stats": {},
                "source": "captplanet",
            },
        ),
    ):
        yield


# ---------------------------------------------------------------------------
# Revise 4: pure-arithmetic expected-value derivation. Both formulas below
# are empirically verified (probe, before being hardcoded) against the REAL
# detector/compiler on the exact fixture shapes this file builds — never
# re-derived by walking a compiled tree and asking "which child is fire",
# the Revise-3 mistake this file no longer repeats.
# ---------------------------------------------------------------------------


def _expected_flat_fire_signal_logic_count(fire_hedge_ticker_count: int) -> int:
    """1 (if-node) + 1 (if-child wrapper) + 1 (weight-equal wrapper) +
    (1 + fire_hedge_ticker_count) asset leaves (VIXY + the hedge tickers) —
    verified: fire_hedge_ticker_count=0 -> 4, =45 -> 49."""
    return 4 + fire_hedge_ticker_count


def _expected_overlay_signal_logic_count(n_assets: int) -> int:
    """1 (if-node) + 1 (if-child wrapper) + 1 (weight-equal wrapper) +
    n_assets asset leaves — verified: n_assets=1 -> 4, =12 -> 15."""
    return 3 + n_assets


def _dsl_overlay_candidate(n_assets: int, vix_ticker: str = "UVXY") -> dict:
    """The build-plan-DSL overlay shape used both by the mocked Fable client
    AND by the pure-arithmetic expected-value formula above — a SINGLE
    source of truth for the candidate shape so the two never drift apart.
    then-branch holds vix_ticker plus (n_assets - 1) additional distinct
    filler assets; n_assets=1 reproduces the original single-VIX-ticker
    fixture exactly (backward compatible with the pre-Revise-3 shape)."""
    then_tickers = [vix_ticker] + [f"OVERLAY_ASSET_{i:02d}" for i in range(max(n_assets - 1, 0))]
    return {
        "kind": "if",
        "condition": {
            "lhs_fn": "relative-strength-index",
            "lhs_ticker": "SPY",
            "window": 10,
            "comparator": "gt",
            "rhs": {"fixed": 81},
        },
        "then": [
            {
                "kind": "weight",
                "scheme": "equal",
                "children": [{"kind": "asset", "ticker": t} for t in then_tickers],
            }
        ],
        "else": [
            {
                "kind": "weight",
                "scheme": "equal",
                "children": [{"kind": "asset", "ticker": "CORE_ASSET_0001"}],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Fixture builder — a REAL incumbent symphony with a genuine frontrunner
# cascade, run through the REAL frontrunner_detector.detect_frontrunner_
# cascades (never a hand-built Cascade — the exact mistake Revise 3 fixed
# for the reachability proof, and the exact mistake Revise 4's oracle
# methodology fixes for the expected-VALUE derivation).
# ---------------------------------------------------------------------------


def _build_real_cascade_via_detector(
    fbld_module,
    *,
    fire_hedge_ticker_count: int,
    continuation_placeholder_count: int | None = None,
):
    """Returns (incumbent_symphony, cascade, expected_signal_logic_count).

    fire_hedge_ticker_count controls the SIGNAL LOGIC size: the fire/then
    branch is a weight-equal basket of one VIX-family ticker (VIXY, required
    for detection) plus fire_hedge_ticker_count additional distinct
    non-core-placeholder tickers — real signal content, honestly sized
    (fire_hedge_ticker_count=0 empirically produces a signal-logic count of
    4, matching the PM's own real-tree probe's cited "median 4 nodes";
    fire_hedge_ticker_count=45 produces 49, a genuinely sprawling cascade).

    The else/continuation branch is a large CORE_ASSET_-prefixed placeholder
    basket, deliberately larger (by node count) than the fire branch so the
    detector's size-based fire/continuation split (frontrunner_detector.py)
    picks the SAME side as fire — mirroring a real symphony's shape (a
    small, genuine hedge overlay vs a much larger core allocation).

    continuation_placeholder_count: explicit override (default None —
    auto-computed with generous headroom above the fire branch). Exposed so
    a caller can hold fire content IDENTICAL while varying ONLY the
    continuation/stub padding size (the padding-insensitivity pin below).

    expected_signal_logic_count is computed via the PURE-ARITHMETIC formula
    (never by walking the detector's output), then cross-checked against
    the real cascade.signal_logic_node_count as a fixture-level sanity
    assertion — a formula/detector disagreement fails loud HERE, not
    silently downstream in whichever test happens to use this fixture.
    """
    from advisors import frontrunner_detector, symphony_schema

    fire_tickers = ["VIXY"] + [f"HEDGE_TICKER_{i:02d}" for i in range(fire_hedge_ticker_count)]
    if continuation_placeholder_count is None:
        # Continuation must out-count the fire branch (by real node count)
        # for the detector's size-based split to correctly identify fire as
        # the smaller side — sized with generous headroom, never tuned to a
        # knife-edge boundary.
        continuation_placeholder_count = (len(fire_tickers) + 1) * 10

    if_node = symphony_schema.make_if(
        symphony_schema.make_condition(
            symphony_schema.make_indicator("relative-strength-index", "SPY", window=10),
            "gt",
            80,
        ),
        then_children=[
            symphony_schema.make_weight_equal([symphony_schema.make_asset(t) for t in fire_tickers])
        ],
        else_children=[
            symphony_schema.make_weight_equal(
                [
                    symphony_schema.make_asset(f"CORE_ASSET_{i:04d}")
                    for i in range(continuation_placeholder_count)
                ]
            )
        ],
    )
    incumbent_symphony = symphony_schema.make_root(
        "Simplify-Path Revise-4 Test Symphony", "daily", [if_node]
    )

    detection = frontrunner_detector.detect_frontrunner_cascades(incumbent_symphony)
    assert detection.cascades, (
        f"fixture sanity: the constructed tree was not detected as a "
        f"frontrunner cascade at all (skip_reason={detection.skip_reason!r}) "
        f"-- the fixture is broken, not the production code under test"
    )
    cascade = detection.cascades[0]

    expected_signal_logic_count = _expected_flat_fire_signal_logic_count(fire_hedge_ticker_count)
    assert cascade.signal_logic_node_count == expected_signal_logic_count, (
        f"fixture sanity: cascade.signal_logic_node_count "
        f"({cascade.signal_logic_node_count!r}) does not match the "
        f"independently hand-derived expected value "
        f"({expected_signal_logic_count!r}) for fire_hedge_ticker_count="
        f"{fire_hedge_ticker_count} -- either the detector's field is wrong "
        f"(the thing under test elsewhere) or this fixture's formula is "
        f"wrong; this assertion exists so that disagreement fails loud HERE"
    )
    return incumbent_symphony, cascade, expected_signal_logic_count


def _build_inverted_polarity_cascade_via_detector(fbld_module):
    """Returns (incumbent_symphony, cascade) for a genuine, correctly-
    detected real_tree_09/n2oo-class inverted-polarity cascade — fire
    content (VXX/UVIX) sits on the is-else-condition?==True side, while the
    is-else-condition?==False side (SVXY + 20 hedge-pad filler, deliberately
    LARGER by node count) is what qualifies the root at all (qualification
    checks the is-else=False side specifically for VIX content, independent
    of which side compaction later selects as fire by size).

    Empirically verified (probe, not guessed) against the REAL, unmodified
    detector: this produces exactly ONE cascade (never discarded by the
    detector's own zero-VIX-overlay defensive check, since the SIZE-
    selected fire side genuinely has VIX content here — a DIFFERENT,
    correctly-detected case from the degenerate "size-vs-direction
    disagreement, zero VIX survives" shape that check exists to discard).
    Mirrors tests/advisors/test_frontrunner_detector_r4_signal_logic.py's
    own inverted-polarity fixture, adapted to this file's make_weight_equal
    wrapping convention (re-verified via probe that wrapping doesn't shift
    which side compaction selects as fire)."""
    from advisors import frontrunner_detector, symphony_schema

    cond_side_assets = [symphony_schema.make_asset("SVXY")] + [
        symphony_schema.make_asset(f"HEDGEPAD{i:02d}") for i in range(20)
    ]
    else_side_assets = [symphony_schema.make_asset("VXX"), symphony_schema.make_asset("UVIX")]

    if_node = symphony_schema.make_if(
        symphony_schema.make_condition(
            symphony_schema.make_indicator("relative-strength-index", "SPY", window=10),
            "gt",
            80,
        ),
        then_children=[symphony_schema.make_weight_equal(cond_side_assets)],
        else_children=[symphony_schema.make_weight_equal(else_side_assets)],
    )
    incumbent_symphony = symphony_schema.make_root(
        "Inverted Polarity Wiring Test Symphony", "daily", [if_node]
    )

    detection = frontrunner_detector.detect_frontrunner_cascades(incumbent_symphony)
    assert detection.cascades, (
        f"fixture sanity: the constructed inverted-polarity tree was not "
        f"detected as a frontrunner cascade at all (skip_reason="
        f"{detection.skip_reason!r}) -- the fixture is broken, not the "
        f"production code under test"
    )
    cascade = detection.cascades[0]
    assert cascade.fire_is_else_branch is True, (
        f"fixture sanity: expected the detector to report "
        f"fire_is_else_branch=True for this construction (fire content on "
        f"the is-else-condition?==True side), got "
        f"{cascade.fire_is_else_branch!r} -- either the fixture is broken "
        f"or the detector's own polarity field is wrong (a DIFFERENT, "
        f"already-covered concern in test_frontrunner_detector_r4_signal_"
        f"logic.py, not what this wiring test targets)"
    )
    return incumbent_symphony, cascade


def _mocked_fable_overlay_client(n_assets: int = 1, vix_ticker: str = "UVXY") -> MagicMock:
    """A small, valid overlay candidate — build-plan-DSL shape, same
    established idiom as test_frontrunner_gate_wiring.py. n_assets=1
    reproduces the original single-VIX-ticker fixture exactly."""
    overlay = _dsl_overlay_candidate(n_assets, vix_ticker)
    block = MagicMock()
    block.type = "tool_use"
    block.input = {"overlay": overlay}
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [block]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


def _patched_fable(fbld_module, n_assets: int = 1):
    return patch.object(
        fbld_module, "_build_client", return_value=_mocked_fable_overlay_client(n_assets)
    )


def _make_shaped_result(shape_pct: list, n_days: int = 100):
    """Verbatim pattern from test_frontrunner_gate_wiring.py — hand-placed
    up/down days so the series' Sortino ratio is genuinely computable."""
    from advisors.composer_backtest_client import BacktestResult

    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(n_days):
        returns[d.isoformat()] = shape_pct[i % len(shape_pct)] / 100.0
        d += timedelta(days=1)

    return BacktestResult(
        stats={"sharpe": 0.5, "cagr": 0.08}, data_warnings=[], daily_returns=returns
    )


def _controlled_adopt_candidate_batch():
    """A hand-constructed GatedBatch whose single result is ADOPT_CANDIDATE —
    used to bypass the numerically fragile BHY/FDR/Gate#2 fold math (out of
    THIS cycle's scope) so these tests exercise only the Calmar/SIMPLIFY
    acceptance clause and its wiring, which real evaluate_calmar_acceptance
    is exercised against for real."""
    from acceptance_gate import AcceptanceVerdict
    from advisors.backtest_gate_engine import CandidateGateResult, GatedBatch

    verdict = AcceptanceVerdict(
        vetoes_passed=True, panel_score=1.0, panel_breakdown={}, decision="ADOPT_CANDIDATE"
    )
    result = CandidateGateResult(
        candidate_id="candidate",
        verdict=verdict,
        validation_days=20,
        oos_alpha=5.0,
        caveats=["synthetic-test-caveat"],
        winner_p_adj=0.01,
        rejection_reason=None,
    )
    return GatedBatch(results=[result], survivors=[result], n_candidates=1, fdr_q=0.05)


# ---------------------------------------------------------------------------
# AC-2 / R4-1: the builder call site threads cascade.signal_logic_node_count
# DIRECTLY (no re-derivation) as replaced_cascade_node_count.
# ---------------------------------------------------------------------------


def test_run_build_threads_the_detector_stamped_cascade_count_into_the_acceptance_call(fbld):
    """R4-1: evaluate_calmar_acceptance must receive
    cascade.signal_logic_node_count VERBATIM as replaced_cascade_node_count
    — read directly off the Cascade object the (mocked) detector returned,
    never re-derived by the builder via any marker-search/reconstruction.
    Independently cross-checked against the pure-arithmetic expected value
    too, so this test fails whether the builder mis-threads the field OR
    the fixture's own formula disagrees with the real detector."""
    from advisors import frontrunner_detector
    from advisors.frontrunner_acceptance import evaluate_calmar_acceptance as _real_eval

    incumbent_symphony, cascade, expected_cascade_signal_count = _build_real_cascade_via_detector(
        fbld, fire_hedge_ticker_count=45
    )
    n_overlay_assets = 12
    expected_overlay_signal_count = _expected_overlay_signal_logic_count(n_overlay_assets)

    shape_pct = [0.10, -0.05, 0.08, -0.10, 0.12, -0.03, 0.05, -0.08, 0.10, -0.02]
    shared_result = _make_shaped_result(shape_pct, n_days=100)

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch(
            "advisors.frontrunner_detector.detect_frontrunner_cascades",
            return_value=frontrunner_detector.DetectionResult(cascades=[cascade]),
        ),
        _patched_fable(fbld, n_assets=n_overlay_assets),
        patch("advisors.composer_backtest_client.run_backtest", return_value=shared_result),
        patch(
            "advisors.backtest_gate_engine.evaluate_candidate_batch",
            return_value=_controlled_adopt_candidate_batch(),
        ),
        patch(
            "advisors.frontrunner_acceptance.evaluate_calmar_acceptance",
            wraps=_real_eval,
        ) as mock_acceptance,
        patch("database.insert_frontrunner_proposal"),
        patch("database.insert_dof_ledger_row"),
        patch("database.insert_advisor_observation"),
    ):
        fbld._run_build_for_symphony("test-symphony-id")

    assert mock_acceptance.called, (
        "evaluate_calmar_acceptance was never reached — the gate mock or "
        "fixture wiring is broken upstream of the layer this test targets"
    )
    _, call_kwargs = mock_acceptance.call_args

    assert call_kwargs.get("replaced_cascade_node_count") == cascade.signal_logic_node_count, (
        f"the builder passed replaced_cascade_node_count="
        f"{call_kwargs.get('replaced_cascade_node_count')!r}, which does NOT "
        f"match cascade.signal_logic_node_count={cascade.signal_logic_node_count!r} "
        f"-- R4-1 requires the builder to read the detector-stamped field "
        f"VERBATIM, never re-derive it"
    )
    assert call_kwargs.get("replaced_cascade_node_count") == expected_cascade_signal_count, (
        f"expected replaced_cascade_node_count={expected_cascade_signal_count} "
        f"(the independently hand-derived signal-logic-only count), got "
        f"{call_kwargs.get('replaced_cascade_node_count')!r}"
    )
    assert call_kwargs.get("overlay_node_count") == expected_overlay_signal_count, (
        f"expected overlay_node_count={expected_overlay_signal_count} (the "
        f"SIGNAL-LOGIC-ONLY count of the overlay's condition+then branch, "
        f"excluding its placeholder-else branch), got "
        f"{call_kwargs.get('overlay_node_count')!r}"
    )


# ---------------------------------------------------------------------------
# RULING 1 direct pin: the SAME overlay must decline against a genuinely
# small cascade and accept against a genuinely large one.
# ---------------------------------------------------------------------------


def test_a_fixed_overlay_declines_against_a_tiny_cascade_but_accepts_against_a_sprawling_one(
    fbld,
):
    """RULING 1 direct pin: the SAME overlay must DECLINE against a small
    (honest, real-detector-derived) cascade signal-logic count and ACCEPT
    against a large one — proving the fix is a genuine ratio against the
    CORRECT operands, not merely 'this specific number happened to be
    smaller'. Uses evaluate_calmar_acceptance directly (not the full
    orchestration) with counts derived from REAL detector output + a REAL
    compiled overlay, isolating this proof from the numerically fragile
    BHY/FDR gate math (out of scope)."""
    from advisors.frontrunner_acceptance import evaluate_calmar_acceptance

    _tiny_symphony, _tiny_cascade, tiny_signal_count = _build_real_cascade_via_detector(
        fbld, fire_hedge_ticker_count=0
    )
    _large_symphony, _large_cascade, large_signal_count = _build_real_cascade_via_detector(
        fbld, fire_hedge_ticker_count=45
    )
    assert tiny_signal_count < large_signal_count, (
        "fixture sanity: the 'tiny' cascade must genuinely be smaller than the 'large' one"
    )

    overlay_signal_count = _expected_overlay_signal_logic_count(n_assets=12)

    # incumbent_node_count == candidate_node_count (whole-tree delta == 0)
    # so this pin is independent of RULING 2's separate no-growth gate.
    metrics = {"annualized_return": 0.16, "max_drawdown": -0.08}

    declined = evaluate_calmar_acceptance(
        metrics,
        metrics,
        incumbent_node_count=999,
        candidate_node_count=999,
        overlay_node_count=overlay_signal_count,
        replaced_cascade_node_count=tiny_signal_count,
    )
    assert declined.accepted is False, (
        f"a {overlay_signal_count}-node overlay was accepted via SIMPLIFY "
        f"against a {tiny_signal_count}-node cascade — an overlay this "
        f"large relative to what it replaces is NOT a material "
        f"simplification"
    )

    accepted = evaluate_calmar_acceptance(
        metrics,
        metrics,
        incumbent_node_count=999,
        candidate_node_count=999,
        overlay_node_count=overlay_signal_count,
        replaced_cascade_node_count=large_signal_count,
    )
    assert accepted.accepted is True, (
        f"a {overlay_signal_count}-node overlay was declined via SIMPLIFY "
        f"against a {large_signal_count}-node cascade — SIMPLIFY must be "
        f"reachable when a genuinely large signal-logic cascade collapses "
        f"into a small overlay (the feature plan's own 'collapse hundreds "
        f"of flat rungs' case)"
    )
    assert "simplification" in accepted.tags


# ---------------------------------------------------------------------------
# AC-3: SIMPLIFY admission is reachable end-to-end (impossible pre-fix).
# AC-6: the whole-tree node_count_delta display metric stays unchanged.
# ---------------------------------------------------------------------------


def test_a_calmar_preserved_small_overlay_over_a_large_cascade_is_accepted_via_simplify_only(
    fbld,
):
    """AC-3 reachability proof: a candidate whose overlay is small relative
    to the large incumbent cascade's SIGNAL LOGIC (never the whole
    stub-padded branch), and whose Calmar is exactly PRESERVED (not
    improved), must be ACCEPTED via the real evaluate_calmar_acceptance and
    tagged {'simplification'} ONLY — admitted through
    _run_build_for_symphony's real orchestration end-to-end, using a REAL
    detector-derived cascade (never a hand-built one).

    AC-6 (same test, same run): the whole-tree node_count_delta persisted in
    metrics_json must still equal
    _count_tree_nodes(spliced) - _count_tree_nodes(incumbent_symphony) —
    the SAME whole-tree formula as before this fix, computed via the
    REVERTED (R4-2), original children-only _count_tree_nodes — proven
    against the REAL spliced tree object. The display metric's semantics
    are untouched by RULING 1's/R4-1's signal-logic-only SIMPLIFY operands."""
    from advisors import frontrunner_detector

    incumbent_symphony, cascade, _expected_signal_count = _build_real_cascade_via_detector(
        fbld, fire_hedge_ticker_count=45
    )
    shape_pct = [0.10, -0.05, 0.08, -0.10, 0.12, -0.03, 0.05, -0.08, 0.10, -0.02]
    shared_result = _make_shaped_result(shape_pct, n_days=100)

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch(
            "advisors.frontrunner_detector.detect_frontrunner_cascades",
            return_value=frontrunner_detector.DetectionResult(cascades=[cascade]),
        ),
        _patched_fable(fbld, n_assets=12),
        patch("advisors.composer_backtest_client.run_backtest", return_value=shared_result),
        patch(
            "advisors.backtest_gate_engine.evaluate_candidate_batch",
            return_value=_controlled_adopt_candidate_batch(),
        ),
        patch("database.insert_frontrunner_proposal") as mock_insert,
        patch("database.insert_dof_ledger_row"),
        patch("database.insert_advisor_observation"),
    ):
        fbld._run_build_for_symphony("test-symphony-id")

    assert mock_insert.called, (
        "a Calmar-preserved, materially-simpler candidate was not queued — "
        "SIMPLIFY admission remains unreachable end-to-end against an "
        "honest, real-detector-derived cascade"
    )
    _, insert_kwargs = mock_insert.call_args
    metrics_json = insert_kwargs.get("metrics_json")
    assert isinstance(metrics_json, dict), "insert_frontrunner_proposal missing metrics_json"

    tags = metrics_json.get("tags")
    assert tags == ["simplification"], (
        f"expected the accepted candidate tagged ONLY ['simplification'] "
        f"(Calmar was exactly preserved, never improved — 'performance' "
        f"must not appear), got {tags!r}"
    )

    # AC-6: whole-tree node_count_delta is UNCHANGED — same (reverted,
    # children-only) formula, derived from the REAL spliced tree this exact
    # call actually persisted.
    spliced_tree = insert_kwargs.get("candidate_tree")
    assert isinstance(spliced_tree, dict), "insert_frontrunner_proposal missing candidate_tree"
    expected_node_count_delta = fbld._count_tree_nodes(spliced_tree) - fbld._count_tree_nodes(
        incumbent_symphony
    )
    assert metrics_json.get("node_count_delta") == expected_node_count_delta, (
        f"whole-tree node_count_delta={metrics_json.get('node_count_delta')!r} "
        f"does not match the real "
        f"_count_tree_nodes(spliced) - _count_tree_nodes(incumbent) "
        f"={expected_node_count_delta!r} — AC-6 requires this display metric "
        f"to stay on the OLD (R4-2-reverted) whole-tree formula, untouched "
        f"by RULING 1's/R4-1's signal-logic-only SIMPLIFY operands"
    )


# ---------------------------------------------------------------------------
# Redundant-compile guard (fps-reviewer INFO finding, folded in per
# team-lead's preference, then reconfirmed unaffected by Revise 4's R4-1
# deletion — this test never depended on the cascade-side heuristic that
# was deleted, only on GenerationResult.compiled_tree reuse for the OVERLAY
# side).
# ---------------------------------------------------------------------------


def test_no_redundant_compile_anywhere_in_the_flow_including_splice(fbld):
    """On the real, successful generation path (GenerationResult.compiled_tree
    already populated by generate_candidate_overlay), neither
    _count_overlay_node_count NOR splice_candidate_into_symphony may
    independently re-compile result.candidate from scratch — both must reuse
    the already-compiled tree. Proven by wrapping the REAL
    plan_tree_compiler.compile_plan (patched at its origin module) and
    asserting no call carries either consumer's own redundant plan_id
    marker."""
    from advisors import frontrunner_detector, plan_tree_compiler

    incumbent_symphony, cascade, _expected_signal_count = _build_real_cascade_via_detector(
        fbld, fire_hedge_ticker_count=45
    )
    shape_pct = [0.10, -0.05, 0.08, -0.10, 0.12, -0.03, 0.05, -0.08, 0.10, -0.02]
    shared_result = _make_shaped_result(shape_pct, n_days=100)

    _real_compile = plan_tree_compiler.compile_plan
    seen_plan_ids = []

    def _counting_compile(plan_envelope, *args, **kwargs):
        seen_plan_ids.append(
            plan_envelope.get("plan_id") if isinstance(plan_envelope, dict) else None
        )
        return _real_compile(plan_envelope, *args, **kwargs)

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch(
            "advisors.frontrunner_detector.detect_frontrunner_cascades",
            return_value=frontrunner_detector.DetectionResult(cascades=[cascade]),
        ),
        _patched_fable(fbld, n_assets=12),
        patch("advisors.composer_backtest_client.run_backtest", return_value=shared_result),
        patch(
            "advisors.backtest_gate_engine.evaluate_candidate_batch",
            return_value=_controlled_adopt_candidate_batch(),
        ),
        patch("database.insert_frontrunner_proposal"),
        patch("database.insert_dof_ledger_row"),
        patch("database.insert_advisor_observation"),
        patch("advisors.plan_tree_compiler.compile_plan", side_effect=_counting_compile),
    ):
        fbld._run_build_for_symphony("test-symphony-id")

    assert "frontrunner-overlay-node-count" not in seen_plan_ids, (
        f"compile_plan was called with _count_overlay_node_count's own "
        f"redundant plan_id marker on a successful generation path -- it is "
        f"re-compiling result.candidate from scratch instead of reusing "
        f"GenerationResult.compiled_tree; observed plan_ids: {seen_plan_ids!r}"
    )
    assert "frontrunner-splice-candidate" not in seen_plan_ids, (
        f"compile_plan was called with splice_candidate_into_symphony's own "
        f"plan_id marker on a successful generation path -- F7 requires "
        f"splice to ALSO reuse the already-compiled tree instead of "
        f"independently re-compiling result.candidate a third time; "
        f"observed plan_ids: {seen_plan_ids!r}"
    )


# ---------------------------------------------------------------------------
# R4-6: _count_overlay_node_count's tiers 2/3 are DELETED — only tier 1
# (compiled_tree reuse) survives. compiled_tree absent now means an
# unconditional None, never a fallback compile.
# ---------------------------------------------------------------------------


def test_count_overlay_node_count_returns_none_unconditionally_when_compiled_tree_is_absent(
    fbld,
):
    """R4-6: tiers 2 (already-step-shaped candidate) and 3 (fresh-compile
    fallback) are DELETED — both were unreachable under the real call graph
    (_run_build_for_symphony always has compiled_tree populated alongside a
    non-None candidate on the only real call path) and round-3 review found
    tier 2 additionally skips the unwrap step (a genuine miscount risk if
    ever reached). compiled_tree=None must degrade to None UNCONDITIONALLY.

    Revise 5, F6: this test previously varied a `candidate` argument
    alongside compiled_tree=None (two variants -- a valid DSL candidate and
    an already-step-shaped one) to prove the None result held "regardless of
    what shape candidate is". F6 drops the dead `candidate` parameter
    entirely (it was unused since R4-6 deleted both tiers that ever read
    it), so there is no longer an second axis to vary -- this test now has
    a single call, matching the single remaining parameter."""
    result = fbld._count_overlay_node_count(None)
    assert result is None, (
        f"expected an unconditional None when compiled_tree is absent (R4-6 "
        f"deletes the fallback-compile tier entirely; F6 additionally drops "
        f"the dead `candidate` param this test used to also vary), got "
        f"{result!r}"
    )


# ---------------------------------------------------------------------------
# F8 (kept from Revise 3, unaffected by Revise 4): harden
# _unwrap_single_compiled_child -- never raise, always dict-or-None -- and
# confirm it is a SHARED implementation reachable from a real build run.
# ---------------------------------------------------------------------------


def test_unwrap_single_compiled_child_never_raises_and_returns_dict_or_none(fbld):
    """Malformed input of every plausible shape must degrade to None, never
    raise — dict-or-None is the whole contract."""
    malformed_cases = [
        None,
        "not-a-dict",
        123,
        [],
        {},  # no children key at all
        {"children": "not-a-list"},
        {"children": []},  # zero children
        {"children": [{"a": 1}, {"b": 2}]},  # too many children
        {"children": [{"a": 1}, {"b": 2}, {"c": 3}]},
    ]
    for case in malformed_cases:
        result = fbld._unwrap_single_compiled_child(case)
        assert result is None, (
            f"expected None for malformed input {case!r}, got {result!r} -- "
            f"_unwrap_single_compiled_child must degrade to None on ANY "
            f"non-conforming shape, never raise"
        )

    valid = {"children": [{"step": "if", "id": "real-node"}]}
    result = fbld._unwrap_single_compiled_child(valid)
    assert result == {"step": "if", "id": "real-node"}, (
        f"expected the sole child dict back for a genuinely valid single-child root, got {result!r}"
    )


def test_unwrap_single_compiled_child_is_reachable_from_a_real_build_run(fbld):
    """Confirms the SHARED helper (not a private duplicate) is genuinely
    wired into the real orchestration -- patched with a counting wraps=real
    spy and confirmed to fire during a real _run_build_for_symphony flow."""
    from advisors import frontrunner_detector

    incumbent_symphony, cascade, _expected_signal_count = _build_real_cascade_via_detector(
        fbld, fire_hedge_ticker_count=45
    )
    shape_pct = [0.10, -0.05, 0.08, -0.10, 0.12, -0.03, 0.05, -0.08, 0.10, -0.02]
    shared_result = _make_shaped_result(shape_pct, n_days=100)

    _real_unwrap = fbld._unwrap_single_compiled_child
    call_count = {"n": 0}

    def _counting_unwrap(*args, **kwargs):
        call_count["n"] += 1
        return _real_unwrap(*args, **kwargs)

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch(
            "advisors.frontrunner_detector.detect_frontrunner_cascades",
            return_value=frontrunner_detector.DetectionResult(cascades=[cascade]),
        ),
        _patched_fable(fbld, n_assets=12),
        patch("advisors.composer_backtest_client.run_backtest", return_value=shared_result),
        patch(
            "advisors.backtest_gate_engine.evaluate_candidate_batch",
            return_value=_controlled_adopt_candidate_batch(),
        ),
        patch("database.insert_frontrunner_proposal"),
        patch("database.insert_dof_ledger_row"),
        patch("database.insert_advisor_observation"),
        patch.object(fbld, "_unwrap_single_compiled_child", side_effect=_counting_unwrap),
    ):
        fbld._run_build_for_symphony("test-symphony-id")

    assert call_count["n"] >= 1, (
        "_unwrap_single_compiled_child was never called during a real build "
        "run -- the shared helper isn't wired into any consumer at all"
    )


# ---------------------------------------------------------------------------
# A1 (kept from Revise 3, retargeted to a TIER-1 path since R4-6 deletes the
# tier-3 fallback-compile-failure path this test originally exercised): a
# None-degradation path in _count_overlay_node_count must log at WARNING.
# ---------------------------------------------------------------------------


def test_count_overlay_node_count_logs_a_warning_on_a_none_degradation_path(fbld, caplog):
    """A compiled_tree present but shaped so it fails to unwrap to a single
    node (tier 1's own None-degradation path — the only tier left after
    R4-6) must emit a WARNING-level log record, not just DEBUG-level
    catch-all logging -- silent degradation here is exactly how the
    CRITICAL finding's class of defect goes undetected in production."""
    import logging

    malformed_compiled_tree = {"children": []}  # zero children -- fails to unwrap
    with caplog.at_level(logging.WARNING):
        result = fbld._count_overlay_node_count(malformed_compiled_tree)

    assert result is None, "sanity: an unwrap-failing compiled_tree must still degrade to None"
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, (
        "_count_overlay_node_count degraded to None on an unwrap failure "
        "but emitted NO warning-level log record -- a future counting "
        "failure would silently recreate the CRITICAL defect's exact "
        "survival mode (SIMPLIFY unreachable, zero operator-visible signal)"
    )


# ---------------------------------------------------------------------------
# A2 (kept from Revise 3, unaffected by Revise 4): an accepted OR rejected
# SIMPLIFY proposal's metrics dict must carry BOTH overlay_node_count and
# replaced_cascade_node_count.
# ---------------------------------------------------------------------------


def test_gate_and_accept_candidate_persists_operands_in_metrics_on_accept(fbld):
    """An ACCEPTED candidate's metrics dict must carry both delta-scoped
    operands -- without this, an accepted proposal is indistinguishable
    from one admitted on stale/wrong operands."""
    shape_pct = [0.10, -0.05, 0.08, -0.10, 0.12, -0.03, 0.05, -0.08, 0.10, -0.02]
    shared_result = _make_shaped_result(shape_pct, n_days=100)

    with (
        patch("advisors.composer_backtest_client.run_backtest", return_value=shared_result),
        patch(
            "advisors.backtest_gate_engine.evaluate_candidate_batch",
            return_value=_controlled_adopt_candidate_batch(),
        ),
        patch("database.insert_dof_ledger_row"),
    ):
        accepted, metrics = fbld._gate_and_accept_candidate(
            symphony_id="test-symphony-id",
            incumbent_tree={"step": "root", "children": []},
            candidate_tree={"step": "root", "children": []},
            overlay_node_count=15,
            replaced_cascade_node_count=49,
        )

    assert accepted is True, f"fixture sanity: expected an accept, got metrics={metrics!r}"
    assert metrics.get("overlay_node_count") == 15, (
        f"accepted metrics is missing/wrong overlay_node_count: "
        f"{metrics.get('overlay_node_count')!r} -- the admission basis is "
        f"not recorded"
    )
    assert metrics.get("replaced_cascade_node_count") == 49, (
        f"accepted metrics is missing/wrong replaced_cascade_node_count: "
        f"{metrics.get('replaced_cascade_node_count')!r}"
    )


def test_gate_and_accept_candidate_persists_operands_in_metrics_on_gate_reject(fbld):
    """A GATE-rejected candidate's metrics dict (AC-11's 'rejected item w/
    reason+deltas') must ALSO carry both delta-scoped operands, not just
    the pre-existing CAGR/MDD/node_count_delta deltas."""
    from acceptance_gate import AcceptanceVerdict
    from advisors.backtest_gate_engine import CandidateGateResult, GatedBatch

    shape_pct = [0.10, -0.05, 0.08, -0.10, 0.12, -0.03, 0.05, -0.08, 0.10, -0.02]
    shared_result = _make_shaped_result(shape_pct, n_days=100)

    verdict = AcceptanceVerdict(
        vetoes_passed=False, panel_score=None, panel_breakdown={}, decision="REJECT_VETO_FAILED"
    )
    rejected_gate_result = CandidateGateResult(
        candidate_id="candidate",
        verdict=verdict,
        validation_days=20,
        oos_alpha=-5.0,
        caveats=[],
        winner_p_adj=0.5,
        rejection_reason="fdr_not_winner",
    )
    rejected_batch = GatedBatch(
        results=[rejected_gate_result], survivors=[], n_candidates=1, fdr_q=0.05
    )

    with (
        patch("advisors.composer_backtest_client.run_backtest", return_value=shared_result),
        patch(
            "advisors.backtest_gate_engine.evaluate_candidate_batch",
            return_value=rejected_batch,
        ),
        patch("database.insert_dof_ledger_row"),
    ):
        accepted, metrics = fbld._gate_and_accept_candidate(
            symphony_id="test-symphony-id",
            incumbent_tree={"step": "root", "children": []},
            candidate_tree={"step": "root", "children": []},
            overlay_node_count=15,
            replaced_cascade_node_count=49,
        )

    assert accepted is False, f"fixture sanity: expected a gate reject, got {metrics!r}"
    assert metrics.get("overlay_node_count") == 15, (
        f"gate-rejected metrics is missing/wrong overlay_node_count: "
        f"{metrics.get('overlay_node_count')!r}"
    )
    assert metrics.get("replaced_cascade_node_count") == 49, (
        f"gate-rejected metrics is missing/wrong replaced_cascade_node_count: "
        f"{metrics.get('replaced_cascade_node_count')!r}"
    )


def test_gate_and_accept_candidate_persists_operands_in_metrics_on_calmar_reject(fbld):
    """A CALMAR-rejected candidate (gate survived, acceptance clause
    declined) must ALSO carry both delta-scoped operands."""
    incumbent_shape = [0.20, -0.05, 0.20, -0.10, 0.20, -0.03, 0.20, -0.08, 0.20, -0.02]
    candidate_shape = [-0.20, -0.30, -0.25, -0.35, -0.15, -0.40, -0.20, -0.30, -0.25, -0.10]
    incumbent_result = _make_shaped_result(incumbent_shape, n_days=100)
    candidate_result = _make_shaped_result(candidate_shape, n_days=100)

    call_count = {"n": 0}

    def _side_effect(*args, **kwargs):
        call_count["n"] += 1
        return incumbent_result if call_count["n"] == 1 else candidate_result

    with (
        patch("advisors.composer_backtest_client.run_backtest", side_effect=_side_effect),
        patch(
            "advisors.backtest_gate_engine.evaluate_candidate_batch",
            return_value=_controlled_adopt_candidate_batch(),
        ),
        patch("database.insert_dof_ledger_row"),
    ):
        accepted, metrics = fbld._gate_and_accept_candidate(
            symphony_id="test-symphony-id",
            incumbent_tree={"step": "root", "children": []},
            candidate_tree={"step": "root", "children": []},
            overlay_node_count=180,  # NOT materially smaller -- ratio fails
            replaced_cascade_node_count=200,
        )

    assert accepted is False, f"fixture sanity: expected a Calmar reject, got {metrics!r}"
    assert metrics.get("overlay_node_count") == 180, (
        f"calmar-rejected metrics is missing/wrong overlay_node_count: "
        f"{metrics.get('overlay_node_count')!r}"
    )
    assert metrics.get("replaced_cascade_node_count") == 200, (
        f"calmar-rejected metrics is missing/wrong replaced_cascade_node_count: "
        f"{metrics.get('replaced_cascade_node_count')!r}"
    )


# ---------------------------------------------------------------------------
# Padding-insensitivity pin (kept from Revise 3, RETARGETED for Revise 4):
# after R4-1, replaced_cascade_node_count is read DIRECTLY off
# cascade.signal_logic_node_count -- this pin now proves that field itself
# is insensitive to continuation padding size, and that the builder
# threading is exact (no re-derivation, no drift).
# ---------------------------------------------------------------------------


def test_replaced_cascade_signal_logic_count_is_insensitive_to_stub_padding_size(fbld):
    """The SAME fire-branch content, detected with two DRAMATICALLY
    different continuation/core padding sizes, must produce the SAME
    cascade.signal_logic_node_count -- and the builder must thread that
    SAME value through the real orchestration wiring regardless of padding
    size."""
    from advisors import frontrunner_detector
    from advisors.frontrunner_acceptance import evaluate_calmar_acceptance as _real_eval

    small_padding_symphony, small_padding_cascade, expected = _build_real_cascade_via_detector(
        fbld, fire_hedge_ticker_count=45, continuation_placeholder_count=500
    )
    large_padding_symphony, large_padding_cascade, expected_large = (
        _build_real_cascade_via_detector(
            fbld, fire_hedge_ticker_count=45, continuation_placeholder_count=5000
        )
    )
    assert expected == expected_large, (
        "fixture sanity: the pure-arithmetic expected-value formula itself "
        "must be insensitive to padding size (identical fire content) -- if "
        "not, the fixture is broken, not the production code"
    )
    assert (
        small_padding_cascade.signal_logic_node_count
        == large_padding_cascade.signal_logic_node_count
    ), (
        f"cascade.signal_logic_node_count differs by padding size alone -- "
        f"small padding (500 leaves) gave "
        f"{small_padding_cascade.signal_logic_node_count!r}, large padding "
        f"(5000 leaves) gave {large_padding_cascade.signal_logic_node_count!r} "
        f"-- the field itself must be padding-size-insensitive"
    )

    shape_pct = [0.10, -0.05, 0.08, -0.10, 0.12, -0.03, 0.05, -0.08, 0.10, -0.02]
    shared_result = _make_shaped_result(shape_pct, n_days=100)

    captured = []
    for symphony, cascade in (
        (small_padding_symphony, small_padding_cascade),
        (large_padding_symphony, large_padding_cascade),
    ):
        with (
            patch("symphony_logic.fetch_symphony_score", return_value=symphony),
            patch(
                "advisors.frontrunner_detector.detect_frontrunner_cascades",
                return_value=frontrunner_detector.DetectionResult(cascades=[cascade]),
            ),
            _patched_fable(fbld, n_assets=12),
            patch("advisors.composer_backtest_client.run_backtest", return_value=shared_result),
            patch(
                "advisors.backtest_gate_engine.evaluate_candidate_batch",
                return_value=_controlled_adopt_candidate_batch(),
            ),
            patch(
                "advisors.frontrunner_acceptance.evaluate_calmar_acceptance",
                wraps=_real_eval,
            ) as mock_acceptance,
            patch("database.insert_frontrunner_proposal"),
            patch("database.insert_dof_ledger_row"),
            patch("database.insert_advisor_observation"),
        ):
            fbld._run_build_for_symphony("test-symphony-id")

        assert mock_acceptance.called
        _, call_kwargs = mock_acceptance.call_args
        captured.append(call_kwargs.get("replaced_cascade_node_count"))

    small_padding_result, large_padding_result = captured
    assert small_padding_result == large_padding_result == expected, (
        f"replaced_cascade_node_count differs by padding size alone through "
        f"the real orchestration -- small padding (500 leaves) produced "
        f"{small_padding_result!r}, large padding (5000 leaves) produced "
        f"{large_padding_result!r}, expected BOTH to equal {expected!r}"
    )


# ---------------------------------------------------------------------------
# B3 (review round 3): the overlay-side clause-aware node counter must be
# ONE implementation, imported by the builder from wherever the detector's
# own signal_logic_node_count-stamping code lives -- never two
# independently-written same-purpose counters. A function-identity check
# (not a behavioral proxy) so a future edit to one can never silently drift
# from the other.
# ---------------------------------------------------------------------------


def test_overlay_side_clause_aware_counter_is_the_same_function_the_detector_uses(fbld):
    """R4-2/B3: whatever dedicated clause-aware counter the builder consumes
    for the overlay operand must be the LITERAL SAME function object
    frontrunner_detector uses to stamp Cascade.signal_logic_node_count --
    an identity (`is`) check, not merely "produces the same numbers on this
    test's inputs" (which a parallel reimplementation could also satisfy
    today, then silently drift tomorrow). Mirrors this project's existing
    STUBBED_CORE_CONTINUATION_TICKER import-not-duplicate pattern.

    This test intentionally does NOT presuppose which module actually owns
    the shared function (team-lead's ruling allows either detector-side or
    a small shared module) -- it imports frontrunner_builder and
    frontrunner_detector fresh and asserts whatever counter object each
    module's own overlay-signal-logic-counting code path resolves to is the
    identical object, wherever it lives."""
    from advisors import frontrunner_builder as fb
    from advisors import frontrunner_detector as fdet

    # The exact attribute names are an implementation choice for fps-impl;
    # this test asserts the STRUCTURAL invariant (one shared object) via
    # whichever names exist post-implementation. Pre-implementation, this
    # fails loudly on AttributeError -- a legitimate RED signal that the
    # unification hasn't landed yet, not a false pass.
    detector_counter = getattr(fdet, "_count_clause_aware_signal_logic", None) or getattr(
        fdet, "count_clause_aware_signal_logic", None
    )
    builder_counter = getattr(fb, "_count_clause_aware_signal_logic", None) or getattr(
        fb, "count_clause_aware_signal_logic", None
    )
    assert detector_counter is not None, (
        "expected frontrunner_detector to expose the dedicated clause-aware "
        "signal-logic counter (name TBD by fps-impl) -- not found under "
        "either candidate name; this is the R4-2/B3 single-source-of-truth "
        "counter the detector uses to stamp Cascade.signal_logic_node_count"
    )
    assert builder_counter is not None, (
        "expected frontrunner_builder to IMPORT (not reimplement) the same "
        "counter for the overlay operand -- not found under either "
        "candidate name"
    )
    assert builder_counter is detector_counter, (
        f"frontrunner_builder's overlay-side clause-aware counter "
        f"({builder_counter!r}) is NOT the same function object as "
        f"frontrunner_detector's ({detector_counter!r}) -- B3 requires "
        f"exactly ONE implementation per purpose, imported not duplicated; "
        f"two independently-written same-purpose counters WILL drift"
    )


# ---------------------------------------------------------------------------
# B1 (review round 3): every next(...) selection call without a default
# must decline honestly on an aliased-duplicate children list, never let
# StopIteration escape through the documented never-raises contract.
# ---------------------------------------------------------------------------


def test_overlay_selection_declines_with_warning_on_aliased_duplicate_children(fbld, caplog):
    """B1: a compiled overlay if-node whose two 'children' list entries are
    the LITERAL SAME object (an aliased duplicate -- `children[0] is
    children[1]`, not merely structurally identical copies) must decline
    honestly via an EXPLICIT `next(..., None)` + WARNING log (team-lead's
    exact B1 pattern ruling) -- through whichever overlay-side selection
    code survives R4-3's consolidation onto _find_terminal_else_child.

    IMPORTANT test-design note: _count_overlay_node_count's whole body is
    already wrapped in a blanket `except Exception: logger.debug(...);
    return None` -- so merely asserting "returns None, never raises" is
    VACUOUSLY true today regardless of whether B1's specific fix (an
    explicit default on the inner next(...) call) exists, since ANY
    unhandled StopIteration would already be silently swallowed by that
    outer catch-all before ever reaching the caller. That is not a
    meaningful RED signal (confirmed empirically -- an earlier draft of
    this exact test passed against CURRENT, unfixed code for exactly this
    reason). The genuine, load-bearing distinguisher B1's own ruling
    specifies is the LOG LEVEL: the outer blanket catch-all logs at DEBUG
    (`_count_overlay_node_count: unexpected error`); B1's explicit fix
    must log at WARNING (matching the A1 convention). This test asserts
    the WARNING specifically -- it fails today (DEBUG-only, if anything)
    and will only pass once the explicit next(..., None)+WARNING pattern
    genuinely lands, not once an incidental safety net happens to catch
    the exception."""
    import logging

    # The aliasing must sit at the level _count_signal_logic_nodes (or
    # whatever it's renamed to) actually reads children from -- i.e. the
    # node _unwrap_single_compiled_child hands back, ONE level under
    # compiled_tree's own single wrapping child (verified empirically: an
    # earlier draft put the aliased pair a level too deep, inside a nested
    # if-node the outer selection code never descends into on this shape,
    # which made the test pass vacuously for the wrong reason).
    #
    # is-else-condition? MUST be True on the aliased child (verified
    # empirically -- False produced a DIFFERENT, already-fixed decline
    # path: the "ambiguous 2-child, neither side identifiable" rider,
    # which already logs WARNING for an unrelated reason and made an
    # earlier draft of this test pass vacuously again). B1's actual bug
    # needs BOTH list entries to satisfy the SAME selection predicate
    # (since they're the literal same object) so `else_children[0]` picks
    # one as `exclude`, and `next(c for c in children if c is not
    # exclude)` then finds NO match at all -- neither list entry `is not
    # exclude`, since both ARE exclude.
    aliased_child = {"step": "if-child", "is-else-condition?": True, "children": []}
    unwrapped_if_node = {"step": "if", "children": [aliased_child, aliased_child]}
    malformed_compiled_root = {"step": "if", "children": [unwrapped_if_node]}
    with caplog.at_level(logging.WARNING):
        result = fbld._count_overlay_node_count(malformed_compiled_root)

    assert result is None or isinstance(result, int), (
        f"expected an honest None or a real int, got {result!r} -- no "
        f"exception should escape this call regardless (D-1)"
    )
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, (
        "the aliased-duplicate case degraded without ANY warning-level log "
        "record -- B1's ruling requires next(..., None) + an EXPLICIT "
        "WARNING here (matching the A1 convention), never silent reliance "
        "on the outer blanket except's DEBUG-only catch-all"
    )


# ---------------------------------------------------------------------------
# Team-lead RED extension (2026-08-25, reviewing fps-impl's plan): unpinned
# wiring is how the original F1-class defects survived this whole cycle --
# a direct unit-level call to evaluate_calmar_acceptance with
# fire_is_else_branch hardcoded (see test_frontrunner_acceptance.py) proves
# the ACCEPTANCE clause itself is correct, but does NOT prove the real
# orchestration (_run_build_for_symphony -> _gate_and_accept_candidate ->
# evaluate_calmar_acceptance) actually THREADS cascade.fire_is_else_branch
# through end to end. This test closes that gap.
# ---------------------------------------------------------------------------


def test_run_build_threads_fire_is_else_branch_into_the_acceptance_call_and_declines(fbld):
    """An inverted-polarity cascade (fire content genuinely on the
    is-else-condition?==True side, real_tree_09/n2oo class), run through
    the REAL _run_build_for_symphony orchestration end to end, must:
      1. Pass cascade.fire_is_else_branch=True VERBATIM into
         evaluate_calmar_acceptance's real call (never dropped, never
         re-derived, never silently defaulted to False upstream of the
         acceptance clause).
      2. Result in the candidate NOT being queued for approval -- the
         acceptance layer's own fail-closed decline (proven at the unit
         level in test_frontrunner_acceptance.py) must actually take
         effect through the full orchestration, not just in isolation.

    Metrics are set up so Calmar is exactly PRESERVED (never improved) and
    the ratio/delta gates would OTHERWISE both pass -- isolating the
    polarity check as the ONLY reason for the decline, mirroring the same
    isolation discipline test_frontrunner_acceptance.py's own inverted-
    polarity unit test uses."""
    from advisors import frontrunner_detector
    from advisors.frontrunner_acceptance import evaluate_calmar_acceptance as _real_eval

    incumbent_symphony, cascade = _build_inverted_polarity_cascade_via_detector(fbld)
    shape_pct = [0.10, -0.05, 0.08, -0.10, 0.12, -0.03, 0.05, -0.08, 0.10, -0.02]
    shared_result = _make_shaped_result(shape_pct, n_days=100)

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch(
            "advisors.frontrunner_detector.detect_frontrunner_cascades",
            return_value=frontrunner_detector.DetectionResult(cascades=[cascade]),
        ),
        _patched_fable(fbld, n_assets=1),  # small overlay -- ratio would otherwise pass
        patch("advisors.composer_backtest_client.run_backtest", return_value=shared_result),
        patch(
            "advisors.backtest_gate_engine.evaluate_candidate_batch",
            return_value=_controlled_adopt_candidate_batch(),
        ),
        patch(
            "advisors.frontrunner_acceptance.evaluate_calmar_acceptance",
            wraps=_real_eval,
        ) as mock_acceptance,
        patch("database.insert_frontrunner_proposal") as mock_insert,
        patch("database.insert_dof_ledger_row"),
        patch("database.insert_advisor_observation"),
    ):
        fbld._run_build_for_symphony("test-symphony-id")

    assert mock_acceptance.called, (
        "evaluate_calmar_acceptance was never reached — the gate mock or "
        "fixture wiring is broken upstream of the layer this test targets"
    )
    _, call_kwargs = mock_acceptance.call_args
    assert call_kwargs.get("fire_is_else_branch") is True, (
        f"expected fire_is_else_branch=True threaded verbatim from "
        f"cascade.fire_is_else_branch into the real evaluate_calmar_"
        f"acceptance call, got {call_kwargs.get('fire_is_else_branch')!r} "
        f"-- the builder is not threading the detector-stamped polarity "
        f"field through the real orchestration"
    )

    assert not mock_insert.called, (
        "an inverted-polarity candidate was queued for approval despite "
        "the acceptance layer's fail-closed polarity decline — the "
        "unit-level decline (test_frontrunner_acceptance.py) is not "
        "actually taking effect through the real orchestration wiring"
    )


# ---------------------------------------------------------------------------
# Revise 5, F2/F4 (PR #128 round-4 /code-review): the OVERLAY-side operand
# for the SAME conceptual multi-tier content the cascade side pins in
# tests/advisors/test_frontrunner_detector_r4_signal_logic.py's
# test_signal_logic_node_count_excludes_nested_tiers_own_continuation_by_design
# (hand-derived to 6). This is the matched OTHER HALF of that pin -- see
# that test file's own header comment (immediately above its section) for
# the full trust-asymmetry derivation and the team-lead's BINDING
# do-not-unify-the-counters ruling. Do not "fix" the diff below by making
# either counter match the other.
# ---------------------------------------------------------------------------


def test_overlay_signal_logic_count_includes_nested_tiers_own_else_unlike_cascade_side(fbld):
    """F2/F4 pin (Revise 5): the OVERLAY-side counter's hand-derived value
    is 10 -- UNLIKE the cascade-side counter (pinned to 6, same tier
    structure/tickers/wrappers: UVXY fire, VIXM+BIL nested-tier-else, an
    outer placeholder), the overlay counter DOES count the nested tier's
    own else (4 nodes) -- because on THIS side (a self-generated
    candidate), the DSL/compiler convention structurally guarantees a
    nested tier's own else is never a placeholder (see
    _find_terminal_else_child's own docstring). The diff (10 - 6 = 4) is
    EXACTLY the nested tier's own else subtree.

    This call intentionally uses the POST-F6 single-argument keyword form
    (compiled_tree=...) -- against the CURRENT (pre-F6) two-argument
    signature this raises TypeError: missing required positional argument
    'candidate', a clear and correct RED signal (not a counting-logic bug)
    that resolves once F6 lands alongside this fix in the same GREEN pass."""
    from advisors import plan_tree_compiler

    overlay_candidate = {
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
                        "scheme": "equal",
                        "children": [{"kind": "asset", "ticker": "UVXY"}],
                    }
                ],
                "else": [
                    {
                        "kind": "weight",
                        "scheme": "equal",
                        "children": [
                            {"kind": "asset", "ticker": "VIXM"},
                            {"kind": "asset", "ticker": "BIL"},
                        ],
                    }
                ],
            }
        ],
        "else": [
            {
                "kind": "weight",
                "scheme": "equal",
                "children": [{"kind": "asset", "ticker": "CORE_STRATEGY_PLACEHOLDER"}],
            }
        ],
    }
    plan_envelope = {
        "plan_id": "revise-5-f2f4-overlay-pin",
        "objective": "cut_drawdown",
        "name": "Revise 5 F2/F4 overlay pin",
        "rebalance": "daily",
        "root": overlay_candidate,
    }
    compile_result = plan_tree_compiler.compile_plan(plan_envelope)
    assert compile_result.tree is not None, (
        f"fixture sanity: the DSL candidate must compile clean, got reason="
        f"{compile_result.reason!r}"
    )

    overlay_count = fbld._count_overlay_node_count(compiled_tree=compile_result.tree)

    # Cross-verified against the real detector in
    # test_frontrunner_detector_r4_signal_logic.py's
    # test_signal_logic_node_count_excludes_nested_tiers_own_continuation_by_design
    # -- kept as a plain int here (not imported) since that test file's
    # fixture builder is a module-private helper, matching this suite's
    # existing convention of self-contained test files.
    matched_cascade_signal_logic_count = 6

    assert overlay_count == 10, (
        f"expected 10 (1 + fire_child subtree of 9: outer_true_child + "
        f"inner_if + inner_true_child + inner_fire_wt + UVXY + "
        f"inner_else_child + inner_continuation_wt + VIXM + BIL -- the "
        f"nested tier's own else IS counted here), got {overlay_count!r}"
    )
    assert overlay_count - matched_cascade_signal_logic_count == 4, (
        f"the overlay/cascade diff must be exactly 4 -- the nested tier's "
        f"own else subtree (inner_else_child + wt-wrapper + VIXM + BIL) -- "
        f"got overlay={overlay_count!r}, cascade="
        f"{matched_cascade_signal_logic_count!r}, diff="
        f"{overlay_count - matched_cascade_signal_logic_count!r}"
    )


# ---------------------------------------------------------------------------
# Revise 5, F6 (PR #128 round-4 /code-review, LOW/cleanup): the `candidate`
# parameter on _count_overlay_node_count is dead -- unused since R4-6
# deleted both fallback tiers that ever read it (the docstring already says
# "Unused"). Drop it entirely; the sole remaining parameter is
# compiled_tree.
# ---------------------------------------------------------------------------


def test_count_overlay_node_count_has_single_compiled_tree_parameter(fbld):
    """The signature must have exactly one parameter, named compiled_tree
    (matching every existing doc reference to it)."""
    import inspect

    sig = inspect.signature(fbld._count_overlay_node_count)
    params = list(sig.parameters.values())
    assert len(params) == 1, (
        f"expected exactly ONE parameter (compiled_tree), got "
        f"{[p.name for p in params]!r} -- the dead `candidate` param must "
        f"be dropped"
    )
    assert params[0].name == "compiled_tree", (
        f"expected the sole remaining parameter to be named 'compiled_tree' "
        f"(matching every doc reference to it), got {params[0].name!r}"
    )


def test_count_overlay_node_count_call_site_passes_a_single_argument(fbld):
    """The sole production call site (inside _run_build_for_symphony) must
    pass exactly one argument -- an AST-based check (same
    ast.walk-after-locating-FunctionDef-by-name technique already
    established twice earlier in this cycle) so this survives a future
    refactor that moves the call site around, as long as it stays reachable
    from _run_build_for_symphony's own source."""
    import ast
    import inspect
    import pathlib

    module_path = inspect.getfile(fbld)
    tree = ast.parse(pathlib.Path(module_path).read_text(encoding="utf-8"))

    run_build_def = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_run_build_for_symphony"
        ),
        None,
    )
    assert run_build_def is not None, (
        "_run_build_for_symphony function definition not found in frontrunner_builder.py's source"
    )

    calls = [
        node
        for node in ast.walk(run_build_def)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "_count_overlay_node_count")
            or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "_count_overlay_node_count"
            )
        )
    ]
    assert calls, (
        "_run_build_for_symphony's own source contains no call to "
        "_count_overlay_node_count -- expected the sole production call "
        "site to live there (frontrunner_builder.py:2005 as of this "
        "cycle's brief)"
    )
    for call in calls:
        total_args = len(call.args) + len(call.keywords)
        assert total_args == 1, (
            f"expected the call to _count_overlay_node_count to pass "
            f"exactly ONE argument (the dead `candidate` param is gone), "
            f"found {total_args} at line {call.lineno}"
        )
