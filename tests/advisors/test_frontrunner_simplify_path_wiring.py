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

REVISE 3 (PR #128 /code-review, CRITICAL F1, 2026-08-24): the PRIOR revision
of this file certified an asymmetry it never actually tested for, and is the
direct cause of a merge-blocking finding. It hand-built a synthetic
``Cascade(overlay_tree=copy.deepcopy(if_node))`` bypassing the REAL
``frontrunner_detector.detect_frontrunner_cascades``/``_build_cascade_overlay``
path entirely -- so it never exercised ``_compact_if_node``'s real behavior:
the continuation/large branch is padded with synthetic
``_STUBBED_CORE_CONTINUATION`` leaves SIZED TO THE ORIGINAL CORE CONTENT
(frontrunner_detector.py:678-705), specifically so downstream fire/
continuation size comparisons stay correct. Counting the WHOLE
``cascade.overlay_tree`` (what the prior revision's fixture made look
correct) therefore counts core-sized stub padding, not real signal logic --
inverting SIMPLIFY from "never fires" to "fires on the vast majority of real
cascades, admitting overlays LARGER than the logic they replace." RULING 1
(Revise 3): BOTH operands must count SIGNAL LOGIC ONLY -- condition + fire/
then branch, EXCLUDING the else/continuation on both sides (cascade: no
stub/core padding; overlay: no placeholder-else leaf). This file's fixture
builder now drives the REAL detector, never a hand-built Cascade, and every
expected value is independently derived (never a hand-typed literal) via a
test-local helper that mirrors the CORRECT signal-logic-only semantics.

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
# RULING 1 test-local helpers: the CORRECT "signal logic only" semantics,
# independently derived (never asserted to be identical CODE to whatever
# fps-impl writes — only identical MATH). Applies uniformly to both operand
# sides:
#   - cascade side (detector-compacted): excludes whichever if-child contains
#     a _STUBBED_CORE_CONTINUATION marker anywhere in its subtree (position
#     and is-else-condition? are BOTH unreliable here — _compact_if_node's
#     fire/continuation split is explicitly SIZE-based, not direction-based;
#     see that function's own docstring in frontrunner_detector.py).
#   - overlay side (freshly compiled, never detector-compacted, no stub
#     marker exists): excludes whichever if-child has is-else-condition? is
#     True — reliable here since it's NOT detector output (mirrors the
#     EXISTING production _find_terminal_else_child in frontrunner_builder.py,
#     used by _graft_incumbent_core for the identical "find the placeholder
#     else slot" purpose).
# ---------------------------------------------------------------------------


def _contains_stub_marker(node) -> bool:
    """True if node (or anything nested under it) is one of
    _build_cascade_overlay's synthetic _STUBBED_CORE_CONTINUATION leaves."""
    if not isinstance(node, dict):
        return False
    if node.get("ticker") == "_STUBBED_CORE_CONTINUATION":
        return True
    return any(_contains_stub_marker(c) for c in (node.get("children") or []))


def _expected_signal_logic_count(fbld_module, if_node: dict) -> int:
    """The CORRECT 'signal logic only' node count for a raw_value if-node —
    1 (the if-node) + the FULL subtree of whichever if-child is the real
    fire/then branch, EXCLUDING the other if-child (continuation/else)
    entirely. Never presumes a fixed position (children[0] vs [1]).

    RIDER (team-lead ruling, post-Revise-3): when a well-formed 2-child
    if-node matches NEITHER identification heuristic (no stub marker on
    either side AND no ``is-else-condition?==True`` on either side), this
    helper now asserts loudly rather than falling back to a positional
    guess (``children[-1]``) — mirrors the production
    ``_count_signal_logic_nodes`` rider that flips the same branch to
    ``return None``. Dual-verified unreachable by every real fixture this
    file builds (cascade side always carries the stub marker by
    construction of ``_build_cascade_overlay``; overlay side always carries
    ``is-else-condition?`` via the real ``symphony_schema`` constructors) —
    if this ever fires, it means a fixture stopped being honest, not that
    the helper should silently guess."""
    children = [c for c in (if_node.get("children") or []) if isinstance(c, dict)]
    if len(children) != 2:
        # Malformed/unexpected shape — fall back to the honest whole count
        # rather than guess which half to drop.
        return fbld_module._count_tree_nodes(if_node)

    stub_children = [c for c in children if _contains_stub_marker(c)]
    if stub_children:
        exclude = stub_children[0]
    else:
        else_children = [c for c in children if c.get("is-else-condition?") is True]
        if not else_children:
            raise AssertionError(
                "_expected_signal_logic_count: ambiguous 2-child if-node — "
                "neither child carries a stub marker nor is-else-condition?==True. "
                "This branch is dual-verified unreachable by every real fixture; "
                "a fixture that hits it is no longer honest and must be fixed, "
                "never a positional guess."
            )
        exclude = else_children[0]

    fire = next(c for c in children if c is not exclude)
    return 1 + fbld_module._count_tree_nodes(fire)


def _dsl_overlay_candidate(n_assets: int, vix_ticker: str = "UVXY") -> dict:
    """The build-plan-DSL overlay shape used both by the mocked Fable client
    AND by the independent expected-value derivation below — a SINGLE
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


def _expected_overlay_signal_logic_count(fbld_module, n_assets: int) -> int:
    """Compiles the SAME DSL shape _mocked_fable_overlay_client produces
    (via _dsl_overlay_candidate) and derives its signal-logic-only count via
    _expected_signal_logic_count — the overlay side has no stub marker (it's
    freshly generated, not detector output), so the helper falls through to
    its is-else-condition?-based exclusion."""
    from advisors import plan_tree_compiler

    candidate = _dsl_overlay_candidate(n_assets)
    plan_envelope = {
        "plan_id": "test-independent-derivation",
        "objective": "cut_drawdown",
        "name": "Test",
        "rebalance": "daily",
        "root": candidate,
    }
    compile_result = plan_tree_compiler.compile_plan(plan_envelope)
    assert compile_result.tree is not None, "sanity: fixture candidate must compile"
    compiled_children = compile_result.tree.get("children") or []
    assert len(compiled_children) == 1, "sanity: compiled root must have exactly one child"
    return _expected_signal_logic_count(fbld_module, compiled_children[0])


# ---------------------------------------------------------------------------
# Fixture builder — a REAL incumbent symphony with a genuine frontrunner
# cascade, run through the REAL frontrunner_detector.detect_frontrunner_
# cascades (never a hand-built Cascade — the exact mistake Revise 3 fixes).
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
    fire_hedge_ticker_count=45 produces ~49, a genuinely sprawling cascade).

    The else/continuation branch is a large CORE_ASSET_-prefixed placeholder
    basket, deliberately larger (by node count) than the fire branch so the
    detector's size-based fire/continuation split (frontrunner_detector.py:
    669-670) picks the SAME side as fire — mirroring a real symphony's shape
    (a small, genuine hedge overlay vs a much larger core allocation).

    continuation_placeholder_count: explicit override (default None —
    auto-computed with generous headroom above the fire branch). Exposed so
    a caller can hold fire content IDENTICAL while varying ONLY the
    continuation/stub padding size — the ADDENDUM A4 cross-module pin's
    exact need (proving the signal-logic-only count is insensitive to
    padding size).
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
        "Simplify-Path Revise-3 Test Symphony", "daily", [if_node]
    )

    detection = frontrunner_detector.detect_frontrunner_cascades(incumbent_symphony)
    assert detection.cascades, (
        f"fixture sanity: the constructed tree was not detected as a "
        f"frontrunner cascade at all (skip_reason={detection.skip_reason!r}) "
        f"-- the fixture is broken, not the production code under test"
    )
    cascade = detection.cascades[0]

    expected_signal_logic_count = _expected_signal_logic_count(fbld_module, cascade.overlay_tree)
    return incumbent_symphony, cascade, expected_signal_logic_count


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
# AC-2 / RULING 1: the builder call site threads the REAL, SIGNAL-LOGIC-ONLY
# delta-scoped counts (never the whole stub-padded/placeholder-else branch).
# ---------------------------------------------------------------------------


def test_run_build_threads_the_real_signal_logic_only_counts_into_the_acceptance_call(fbld):
    """Revise 3 (CRITICAL F1, RULING 1): evaluate_calmar_acceptance must
    receive EXACT signal-logic-only counts, independently derived — not just
    'some positive int smaller than the other one' (a directional bound the
    pre-Revise-3 defect could ALSO satisfy, since a small overlay is still
    smaller than a core-sized stub-padded cascade; that weaker assertion is
    exactly what let this defect ship). replaced_cascade_node_count must
    equal the cascade's fire-branch-only count (excluding the
    _STUBBED_CORE_CONTINUATION-padded branch entirely); overlay_node_count
    must equal the overlay's condition+then-branch-only count (excluding its
    placeholder-else branch)."""
    from advisors import frontrunner_detector
    from advisors.frontrunner_acceptance import evaluate_calmar_acceptance as _real_eval

    incumbent_symphony, cascade, expected_cascade_signal_count = _build_real_cascade_via_detector(
        fbld, fire_hedge_ticker_count=45
    )
    n_overlay_assets = 12
    expected_overlay_signal_count = _expected_overlay_signal_logic_count(fbld, n_overlay_assets)

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

    assert call_kwargs.get("replaced_cascade_node_count") == expected_cascade_signal_count, (
        f"expected replaced_cascade_node_count={expected_cascade_signal_count} "
        f"(the SIGNAL-LOGIC-ONLY count of the detected cascade's fire branch, "
        f"excluding the stub-padded continuation entirely), got "
        f"{call_kwargs.get('replaced_cascade_node_count')!r} — a much larger "
        f"value here means the builder is still counting core-sized stub "
        f"padding (the exact CRITICAL defect Revise 3 fixes)"
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
    """Revise 3, RULING 1 direct pin (PM directive: 'pin BOTH directions
    with real-detector-derived fixtures'): the SAME overlay must DECLINE
    against a small (honest, real-detector-derived) cascade signal-logic
    count and ACCEPT against a large one — proving the fix is a genuine
    ratio against the CORRECT operands, not merely 'this specific number
    happened to be smaller'. Uses evaluate_calmar_acceptance directly (not
    the full orchestration) with counts derived from REAL detector output +
    a REAL compiled overlay, isolating this proof from the numerically
    fragile BHY/FDR gate math (out of scope)."""
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

    overlay_signal_count = _expected_overlay_signal_logic_count(fbld, n_assets=12)

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
    _count_tree_nodes(spliced) - _count_tree_nodes(incumbent_symphony) — the
    SAME whole-tree formula as before this fix, proven against the REAL
    spliced tree object — the display metric's semantics are untouched by
    this cycle even though the acceptance clause's own semantics changed."""
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

    # AC-6: whole-tree node_count_delta is UNCHANGED — same formula, derived
    # from the REAL spliced tree this exact call actually persisted.
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
        f"to stay on the OLD whole-tree formula, untouched by RULING 1's "
        f"signal-logic-only SIMPLIFY operands"
    )


# ---------------------------------------------------------------------------
# fps-reviewer INFO finding, folded in per team-lead's preference (2026-08-24):
# _count_overlay_node_count must reuse GenerationResult.compiled_tree.
# Revise 3, F7: the SAME invariant must hold for splice_candidate_into_
# symphony too — on the successful-generation happy path, compile_plan
# should be called EXACTLY ONCE for the candidate (inside
# generate_candidate_overlay itself), never a second time inside
# _count_overlay_node_count NOR a third time inside
# splice_candidate_into_symphony.
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
        f"plan_id marker on a successful generation path -- F7 (Revise 3) "
        f"requires splice to ALSO reuse the already-compiled tree instead of "
        f"independently re-compiling result.candidate a third time; "
        f"observed plan_ids: {seen_plan_ids!r}"
    )


# ---------------------------------------------------------------------------
# Fallback-path coverage (team-lead contract preservation, post-fold-in):
# compiled_tree=None must NOT silently degrade to a missing operand -- it
# must fall back to the ORIGINAL pure-compile-of-candidate path and still
# produce a real (or honestly-None-on-failure) count. Revise 3: expected
# values below are now SIGNAL-LOGIC-ONLY (RULING 1 applies to the fallback
# path too — _count_overlay_node_count must exclude the placeholder-else
# branch regardless of which tier produced its input).
# ---------------------------------------------------------------------------


def test_count_overlay_node_count_falls_back_to_fresh_compile_when_compiled_tree_is_none(
    fbld,
):
    """When compiled_tree is None/absent, _count_overlay_node_count must
    fall back to the ORIGINAL pure-compile-of-candidate path and return the
    real SIGNAL-LOGIC-ONLY count (RULING 1 applies uniformly across all
    three tiers) — not silently degrade to None, and not the whole compiled
    tree including its placeholder-else branch."""
    candidate = _dsl_overlay_candidate(n_assets=1)
    expected_count = _expected_overlay_signal_logic_count(fbld, n_assets=1)
    assert expected_count > 0, "sanity: a real compiled overlay must have >0 signal-logic nodes"

    result = fbld._count_overlay_node_count(candidate, compiled_tree=None)
    assert result == expected_count, (
        f"expected the fallback pure-compile path to produce the "
        f"SIGNAL-LOGIC-ONLY count {expected_count} (excluding the "
        f"placeholder-else branch, derived independently), got {result!r} "
        f"-- compiled_tree=None must still fall back to compiling candidate "
        f"fresh and applying the SAME exclusion the other tiers use"
    )


def test_count_overlay_node_count_returns_none_when_fallback_compile_fails(fbld):
    """D-1: when compiled_tree is None AND candidate is malformed/
    uncompileable, the fallback path must degrade to None, never raise,
    never fabricate a count."""
    malformed_candidate = {"kind": "not-a-real-kind"}
    result = fbld._count_overlay_node_count(malformed_candidate, compiled_tree=None)
    assert result is None


# ---------------------------------------------------------------------------
# Revise 3, F8 (PR #128 /code-review): harden _unwrap_single_compiled_child
# -- never raise, always dict-or-None -- and confirm it is a SHARED
# implementation (not a duplicated inline copy in splice_candidate_into_
# symphony).
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
    """F8: confirms the SHARED helper (not a private duplicate) is genuinely
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
# Revise 3 ADDENDUM A1: every None-degradation path in
# _count_overlay_node_count is currently SILENT (debug-only logging on the
# outer except; no logging at all on the explicit `if x is None: return
# None` branches) while the surrounding _run_build_for_symphony loop logs
# every OTHER skip -- a future counting failure recreates "SIMPLIFY silently
# unreachable" with zero operator-visible signal, the ORIGINAL CRITICAL
# defect's exact survival mode. At least one None-degradation path must log
# at WARNING with the reason.
# ---------------------------------------------------------------------------


def test_count_overlay_node_count_logs_a_warning_on_a_none_degradation_path(fbld, caplog):
    """A compile failure on the fallback path (compiled_tree=None, a
    malformed candidate) must emit a WARNING-level log record, not just the
    outer except's DEBUG-level catch-all -- silent degradation here is
    exactly how the CRITICAL finding's class of defect goes undetected in
    production."""
    import logging

    malformed_candidate = {"kind": "not-a-real-kind"}
    with caplog.at_level(logging.WARNING):
        result = fbld._count_overlay_node_count(malformed_candidate, compiled_tree=None)

    assert result is None, "sanity: the malformed candidate must still degrade to None"
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, (
        "_count_overlay_node_count degraded to None on a compile failure "
        "but emitted NO warning-level log record -- a future counting "
        "failure would silently recreate the CRITICAL defect's exact "
        "survival mode (SIMPLIFY unreachable, zero operator-visible signal)"
    )


# ---------------------------------------------------------------------------
# Revise 3 ADDENDUM A2: an accepted OR rejected SIMPLIFY proposal's metrics
# dict must carry BOTH overlay_node_count and replaced_cascade_node_count --
# the evidence of its admission/rejection basis (same audit-trail-loss class
# as audit #118 Findings D1/D2). Tests call _gate_and_accept_candidate
# directly (mirroring test_frontrunner_gate_wiring.py's own AC-G2 pattern)
# to isolate this from the full orchestration.
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
# Revise 3 ADDENDUM A4: cross-module pin on the detector seam -- after
# RULING 1's fix, replaced_cascade_node_count must derive from the
# cascade's condition+fire subtree and be INSENSITIVE to
# _STUBBED_CORE_CONTINUATION padding SIZE. Without this pin, the metric is
# load-bearing on an unpinned detector internal (the padding-size formula
# in _build_cascade_overlay could change and this test would be the only
# thing to notice a regression back toward size-sensitivity).
# ---------------------------------------------------------------------------


def test_replaced_cascade_signal_logic_count_is_insensitive_to_stub_padding_size(fbld):
    """The SAME fire-branch content, detected with two DRAMATICALLY
    different continuation/core padding sizes, must produce the SAME
    signal-logic-only replaced_cascade_node_count through the REAL
    orchestration wiring."""
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
        "fixture sanity: the test-local expected-value helper itself must "
        "be insensitive to padding size (identical fire content) -- if not, "
        "the fixture is broken, not the production code"
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
        f"replaced_cascade_node_count differs by padding size alone -- "
        f"small padding (500 leaves) produced {small_padding_result!r}, "
        f"large padding (5000 leaves) produced {large_padding_result!r}, "
        f"expected BOTH to equal {expected!r} (the fire-branch-only count, "
        f"identical between the two fixtures) -- the denominator must be "
        f"load-bearing on the fire branch alone, never on stub padding size"
    )
