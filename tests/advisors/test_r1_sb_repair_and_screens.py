"""RED tests — AC-12 (F5, Gap E): SB dead-code revival — backtest_fn threading
+ live_returns population/explicit-skip indicator.

Confirmed by reading advisors/strategy_builder_engine.py:_generate_candidate_trees
(compile_plan called at :379 with NO backtest_fn — the AC-16 tradeability-
repair loop in plan_tree_compiler.compile_plan(plan, *, backtest_fn=None) is
dead on the reachable path since backtest_fn defaults to None) and app.py's
SB route (live_returns=[] hardcoded at :4646) directly before writing.

WHAT IS MOCKED for (a): build_plan_generator.generate_build_plans (controlled
plan list) and plan_tree_compiler.compile_plan (inspected for call kwargs) —
drives the REAL _generate_candidate_trees so the call-site wiring proof is
against production code, not a re-implementation.

WHAT IS MOCKED for (b): strategy_builder_engine.propose_strategies (route-
level, mirrors the AC-11 sibling file's pattern) — isolates the route's
live_returns/screens_skipped decision from the full generation chain.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


# ===========================================================================
# (a) backtest_fn threaded into compile_plan — call-site proof, not a trust-
# the-flag check (per the established AC-13/AC-4/5 pattern in this suite).
# ===========================================================================


def test_ac12_compile_plan_called_with_backtest_fn_from_real_generation_path():
    """MUST FAIL pre-fix: compile_plan is called with backtest_fn=None
    (the keyword-default, i.e. never explicitly passed) via the real
    _generate_candidate_trees path today — the AC-16 tradeability-repair
    loop is structurally dead."""
    import advisors.build_plan_generator as bpg
    import advisors.plan_tree_compiler as compiler
    import advisors.strategy_builder_engine as sbe

    plan = {"plan_id": "ac12-plan-1", "provenance": "built-new", "objective": "diversify", "root": {}}
    gen_result = bpg.GeneratorResult(plans=[plan], reason=None)
    compile_result = compiler.CompileResult(tree={"step": "root", "children": []}, reason=None)

    with (
        patch.object(bpg, "generate_build_plans", return_value=gen_result),
        patch.object(compiler, "compile_plan", return_value=compile_result) as mock_compile,
    ):
        sbe._generate_candidate_trees(sbe.Objective.diversify, universe=["AAA", "BBB"])

    assert mock_compile.called, "compile_plan was never called — self-guard failed"
    _, kwargs = mock_compile.call_args
    assert kwargs.get("backtest_fn") is not None, (
        f"AC-12 GAP: compile_plan was called without a real backtest_fn "
        f"(kwargs={kwargs!r}) — the AC-16 tradeability-repair loop stays dead "
        f"on the reachable production path."
    )


# ===========================================================================
# (b) live_returns populated OR explicit "screens skipped" indicator —
# either/or RED per the plan's own wording (mirrors AC-10's either/or pattern
# elsewhere in this suite).
# ===========================================================================


@pytest.fixture()
def client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_ac12_sb_route_populates_live_returns_or_indicates_screens_skipped(client):
    """MUST FAIL pre-fix: live_returns=[] is hardcoded at the route
    (app.py:4646) and no 'screens skipped' indicator exists anywhere in the
    response — the drawdown/Pearson screens silently skip with zero operator
    visibility."""
    import advisors.strategy_builder_engine as sbe

    captured_calls = []

    def _spy_propose_strategies(**kwargs):
        captured_calls.append(kwargs)
        from advisors.backtest_gate_engine import GatedBatch

        return sbe.ProposalRun(
            candidates=[],
            gated_batch=GatedBatch(results=[], survivors=[], n_candidates=0, fdr_q=0.05),
            screened_survivors=[],
            observations_written=0,
        )

    with (
        patch.object(sbe, "propose_strategies", side_effect=_spy_propose_strategies),
        patch("advisors.build_plan_generator.load_atlas_candidates", return_value=[]),
    ):
        resp = client.post("/ai-advisor/strategy-builder/run", json={"objective": "diversify", "universe": []})

    assert resp.status_code == 200
    assert captured_calls, "propose_strategies was never called — self-guard failed"
    live_returns_passed = captured_calls[0].get("live_returns")

    if live_returns_passed:
        # Populated — satisfies the first disjunct. Nothing further to check.
        return

    body = resp.get_json()
    assert body.get("screens_skipped") is True and body.get("screens_skipped_reason"), (
        f"AC-12 GAP: live_returns is empty AND no explicit screens_skipped/"
        f"screens_skipped_reason indicator is present in the response — "
        f"the drawdown/Pearson screens skip silently. Response: {body}"
    )


def test_ac12_screens_skipped_reason_is_a_real_string_not_a_bare_true(client):
    """When screens_skipped is present, screens_skipped_reason must be a
    genuine, non-empty explanatory string — a bare boolean flag alone doesn't
    tell the operator WHY (e.g. 'no live returns at route time')."""
    import advisors.strategy_builder_engine as sbe

    def _spy_propose_strategies(**kwargs):
        from advisors.backtest_gate_engine import GatedBatch

        return sbe.ProposalRun(
            candidates=[],
            gated_batch=GatedBatch(results=[], survivors=[], n_candidates=0, fdr_q=0.05),
            screened_survivors=[],
            observations_written=0,
        )

    with (
        patch.object(sbe, "propose_strategies", side_effect=_spy_propose_strategies),
        patch("advisors.build_plan_generator.load_atlas_candidates", return_value=[]),
    ):
        resp = client.post("/ai-advisor/strategy-builder/run", json={"objective": "diversify", "universe": []})

    body = resp.get_json()
    if not body.get("screens_skipped"):
        pytest.skip("route populated live_returns instead of skipping — the other disjunct, covered by the sibling test")
    reason = body.get("screens_skipped_reason")
    assert isinstance(reason, str) and len(reason) > 10, (
        f"AC-12 GAP: screens_skipped_reason is not a genuine explanatory "
        f"string. Got: {reason!r}"
    )
