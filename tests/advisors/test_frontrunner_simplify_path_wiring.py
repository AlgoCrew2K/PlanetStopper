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
real in AC-3/AC-6) -- it is exactly what this cycle changes.

DESIGN NOTE on overlay_node_count precision: result.candidate (the Fable
overlay) is build-plan-DSL-shaped ('kind'/'then'/'else'), not the compiled
Composer raw_value shape ('step'/'children') _count_tree_nodes walks -- so
this file does NOT assert an exact expected value for the captured
overlay_node_count kwarg (that would presuppose which intermediate object
the implementer chooses to count, an unstated implementation detail). It
asserts the OBSERVABLE CONTRACT instead: a small positive int, strictly
smaller than the real replaced_cascade_node_count (which IS independently,
exactly derivable -- cascade.overlay_tree is raw_value-shaped and directly
countable). This is not a weaker test -- it still cannot be satisfied by a
wiring that passes the whole-tree counts, a hardcoded constant, or omits the
kwargs entirely.
"""

from __future__ import annotations

import copy
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
# Fixture builders — a REAL, large (>=100 node) incumbent cascade embedded in
# a minimal incumbent symphony, so _replace_node_by_id can genuinely find and
# splice it (never a synthetic id-only stub).
# ---------------------------------------------------------------------------

# Sized comfortably above the AC-3 plan magnitude floor ("cascade >= 100
# nodes") via a padded weight-equal else-branch of distinct placeholder
# assets -- the exact resulting count is derived from the REAL counter below,
# never hand-computed/hardcoded.
_CASCADE_PADDING_ASSET_COUNT = 120


def _build_incumbent_with_large_cascade(fbld_module):
    """Returns (incumbent_symphony, cascade, replaced_cascade_node_count).

    cascade.overlay_tree is the SAME dict object (by id) embedded inside
    incumbent_symphony -- required for the real _replace_node_by_id splice
    step to find and replace it.
    """
    from advisors import frontrunner_detector, symphony_schema

    if_node = symphony_schema.make_if(
        symphony_schema.make_condition(
            symphony_schema.make_indicator("relative-strength-index", "SPY", window=10),
            "gt",
            80,
        ),
        then_children=[symphony_schema.make_weight_equal([symphony_schema.make_asset("VIXY")])],
        else_children=[
            symphony_schema.make_weight_equal(
                [
                    symphony_schema.make_asset(f"CORE_ASSET_{i:04d}")
                    for i in range(_CASCADE_PADDING_ASSET_COUNT)
                ]
            )
        ],
    )
    incumbent_symphony = symphony_schema.make_root(
        "Simplify-Path Wiring Test Symphony", "daily", [if_node]
    )
    cascade = frontrunner_detector.Cascade(overlay_tree=copy.deepcopy(if_node))
    replaced_cascade_node_count = fbld_module._count_tree_nodes(cascade.overlay_tree)
    assert replaced_cascade_node_count >= 100, (
        f"fixture sanity: the constructed cascade must be >=100 nodes (AC-3's "
        f"realistic magnitude floor), got {replaced_cascade_node_count}"
    )
    return incumbent_symphony, cascade, replaced_cascade_node_count


def _mocked_fable_overlay_client(vix_ticker: str = "UVXY") -> MagicMock:
    """A small, valid overlay candidate — the same DSL shape as the
    established test_frontrunner_gate_wiring.py idiom."""
    overlay = {
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
                "children": [{"kind": "asset", "ticker": vix_ticker}],
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
    block = MagicMock()
    block.type = "tool_use"
    block.input = {"overlay": overlay}
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [block]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


def _patched_fable(fbld_module):
    return patch.object(fbld_module, "_build_client", return_value=_mocked_fable_overlay_client())


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
# AC-2: the builder call site threads the REAL delta-scoped counts.
# ---------------------------------------------------------------------------


def test_run_build_threads_the_real_overlay_and_replaced_cascade_counts_into_the_acceptance_call(
    fbld,
):
    """The real _run_build_for_symphony orchestration must call
    evaluate_calmar_acceptance with overlay_node_count/
    replaced_cascade_node_count reflecting the REAL detected cascade subtree
    and the REAL generated overlay — NOT the whole-tree incumbent/candidate
    counts (which stay ~98-100% of each other for a single-cascade splice,
    the exact defect this cycle fixes)."""
    from advisors import frontrunner_detector
    from advisors.frontrunner_acceptance import evaluate_calmar_acceptance as _real_eval

    incumbent_symphony, cascade, replaced_cascade_node_count = _build_incumbent_with_large_cascade(
        fbld
    )
    # Identical shaped series on both sides -> Calmar EXACTLY preserved ->
    # the IMPROVE path structurally cannot fire, isolating admission to
    # whichever path SIMPLIFY reaches (if wired correctly).
    shape_pct = [0.10, -0.05, 0.08, -0.10, 0.12, -0.03, 0.05, -0.08, 0.10, -0.02]
    shared_result = _make_shaped_result(shape_pct, n_days=100)

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch(
            "advisors.frontrunner_detector.detect_frontrunner_cascades",
            return_value=frontrunner_detector.DetectionResult(cascades=[cascade]),
        ),
        _patched_fable(fbld),
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

    assert call_kwargs.get("replaced_cascade_node_count") == replaced_cascade_node_count, (
        f"expected replaced_cascade_node_count={replaced_cascade_node_count} "
        f"(the REAL node count of the detected cascade subtree, derived via "
        f"the shared _count_tree_nodes counter), got "
        f"{call_kwargs.get('replaced_cascade_node_count')!r} — the builder "
        f"is not threading the real cascade operand"
    )

    overlay_node_count = call_kwargs.get("overlay_node_count")
    assert isinstance(overlay_node_count, int) and overlay_node_count > 0, (
        f"expected a positive int overlay_node_count, got {overlay_node_count!r} "
        f"— the builder is not threading a real overlay operand at all"
    )
    assert overlay_node_count < replaced_cascade_node_count, (
        f"overlay_node_count={overlay_node_count} is not smaller than "
        f"replaced_cascade_node_count={replaced_cascade_node_count} — a small "
        f"generated overlay candidate must count as far fewer nodes than the "
        f"large cascade it replaces; this looks like a whole-tree count "
        f"leaking into the delta-scoped operand"
    )
    # A genuinely small Fable-generated overlay is never anywhere near the
    # scale of a 100+-node cascade — bounds the operand away from an
    # accidental whole-tree value (which here would be >=126).
    assert overlay_node_count <= 50, (
        f"overlay_node_count={overlay_node_count} is implausibly large for "
        f"the small mocked Fable candidate used in this fixture — this "
        f"looks like a whole-tree (incumbent/candidate) count was passed "
        f"instead of the real overlay's own count"
    )


# ---------------------------------------------------------------------------
# AC-3: SIMPLIFY admission is reachable end-to-end (impossible pre-fix).
# AC-6: the whole-tree node_count_delta display metric stays unchanged.
# ---------------------------------------------------------------------------


def test_a_calmar_preserved_small_overlay_over_a_large_cascade_is_accepted_via_simplify_only(
    fbld,
):
    """AC-3 reachability proof: a candidate whose overlay is small relative
    to the large incumbent cascade it replaces, and whose Calmar is exactly
    PRESERVED (not improved), must be ACCEPTED via the real
    evaluate_calmar_acceptance and tagged {'simplification'} ONLY (never
    'performance', since Calmar did not improve) — a candidate this shape is
    admitted through _run_build_for_symphony's real orchestration end-to-end.
    Pre-fix, this candidate is unconditionally UNREACHABLE via SIMPLIFY (the
    whole-tree candidate/incumbent ratio for a single-cascade splice is
    always ~98-100%, never <=50%) — this is the exact defect DE-FR-SIMPLIFY-
    001 closes.

    AC-6 (same test, same run): the whole-tree node_count_delta persisted in
    metrics_json must still equal
    _count_tree_nodes(spliced) - _count_tree_nodes(incumbent_symphony) — the
    SAME whole-tree formula as before this fix, proven against the REAL
    spliced tree object captured off the insert_frontrunner_proposal call
    (never a hand-guessed magnitude) — the display metric's semantics are
    untouched by this cycle even though the acceptance clause's own semantics
    changed."""
    incumbent_symphony, cascade, replaced_cascade_node_count = _build_incumbent_with_large_cascade(
        fbld
    )
    shape_pct = [0.10, -0.05, 0.08, -0.10, 0.12, -0.03, 0.05, -0.08, 0.10, -0.02]
    shared_result = _make_shaped_result(shape_pct, n_days=100)

    from advisors import frontrunner_detector

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch(
            "advisors.frontrunner_detector.detect_frontrunner_cascades",
            return_value=frontrunner_detector.DetectionResult(cascades=[cascade]),
        ),
        _patched_fable(fbld),
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
        "SIMPLIFY admission remains unreachable end-to-end (the exact defect "
        "this cycle fixes)"
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
        f"to stay on the OLD whole-tree formula, untouched by the new "
        f"delta-scoped SIMPLIFY operands"
    )
