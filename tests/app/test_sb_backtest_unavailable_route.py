"""RED tests -- feature-plans/advisor-outage-degrade.md AC-4/AC-5 (route
layer): POST /ai-advisor/strategy-builder/run must surface the engine's
honest `backtest_unavailable` outage signal in its JSON response, distinctly
from a normal "0 passed the gate" result and from a genuine top-level error.

CONTRACT (converged with dg-fe, see .claude/tdd-handoff.md): the route reads
`ProposalRun.backtest_unavailable` / `.backtest_unavailable_count` DIRECTLY
off `run` (same pattern as the existing `run.error`/`run.error_category`
reads) -- it does NOT recount over `run.candidates` (that would silently
read 0 in exactly the outage scenario this cycle exists to catch; see
tests/advisors/test_strategy_builder_engine_degrade.py's crux test for the
engine-level proof of why). Response fields:
  - `backtest_unavailable`: bool
  - `backtest_unavailable_count`: int
  - `backtest_unavailable_notice`: str | None -- non-empty operator-readable
    prose when count > 0, None otherwise (mirrors the existing `mode_notice`/
    `screens_skipped_reason` honest-empty-state pattern).

WHAT IS MOCKED: strategy_builder_engine.propose_strategies is replaced with a
spy returning a caller-constructed ProposalRun -- isolates the route's JSON-
assembly logic from the full generation/gate chain, same pattern as
tests/advisors/test_r1_sb_repair_and_screens.py. No live network, no live
Composer call.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture()
def client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _empty_gated_batch():
    from advisors.backtest_gate_engine import HARVEY_LIU_FDR_Q, GatedBatch

    return GatedBatch(results=[], survivors=[], n_candidates=0, fdr_q=HARVEY_LIU_FDR_Q)


def _proposal_run(**overrides):
    import advisors.strategy_builder_engine as sbe

    defaults = dict(
        candidates=[],
        gated_batch=_empty_gated_batch(),
        screened_survivors=[],
        observations_written=0,
    )
    defaults.update(overrides)
    return sbe.ProposalRun(**defaults)


def _post(client, **overrides):
    import advisors.strategy_builder_engine as sbe

    run = _proposal_run(**overrides)
    with (
        patch.object(sbe, "propose_strategies", return_value=run) as mock_propose,
        patch("advisors.build_plan_generator.load_atlas_candidates", return_value=[]),
    ):
        resp = client.post(
            "/ai-advisor/strategy-builder/run", json={"objective": "diversify", "universe": []}
        )
    return resp, mock_propose


# ===========================================================================
# AC-4: outage surfaced honestly, distinct from a normal empty/rejected run.
# ===========================================================================


def test_ac4_route_surfaces_backtest_unavailable_true_and_count_from_proposalrun(client):
    """MUST FAIL pre-fix: the route today has no backtest_unavailable key at
    all. The route must read the flag/count straight off `run` (mocked
    here), not recompute anything from run.candidates."""
    resp, _ = _post(client, backtest_unavailable=True, backtest_unavailable_count=3)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("backtest_unavailable") is True
    assert body.get("backtest_unavailable_count") == 3


def test_ac4_route_notice_is_a_real_non_empty_string_referencing_the_count(client):
    """When backtest_unavailable is True, backtest_unavailable_notice must be
    a genuine explanatory string (not a bare boolean, not fabricated
    boilerplate) -- and it must reference the actual count, not a hardcoded
    number, mirroring the screens_skipped_reason rigor already established
    in this route (test_r1_sb_repair_and_screens.py)."""
    resp, _ = _post(client, backtest_unavailable=True, backtest_unavailable_count=5)

    body = resp.get_json()
    notice = body.get("backtest_unavailable_notice")
    assert isinstance(notice, str) and len(notice) > 10, (
        f"AC-4 GAP: backtest_unavailable_notice is not a genuine explanatory string. "
        f"Got: {notice!r}"
    )
    assert "5" in notice, (
        f"the notice must reference the ACTUAL count (5), not a hardcoded/generic "
        f"figure. Got: {notice!r}"
    )


def test_ac5_route_omits_backtest_unavailable_notice_on_a_healthy_run(client):
    """AC-5 honest empty-state: on a normal run (no outage), the notice must
    be None/absent -- never a fabricated string. backtest_unavailable/_count
    must be the honest False/0 default, not omitted-and-therefore-ambiguous."""
    resp, _ = _post(client, backtest_unavailable=False, backtest_unavailable_count=0)

    body = resp.get_json()
    assert body.get("backtest_unavailable") is False
    assert body.get("backtest_unavailable_count") == 0
    assert body.get("backtest_unavailable_notice") is None


def test_ac5_route_backtest_unavailable_absent_or_false_on_run_error_branch(client):
    """When ProposalRun.error is set (the route's existing early-return error
    branch, app.py ~4819-4836), the outage fields must not fabricate a True/
    notice -- that branch already has its own distinct error surface
    (error/error_category) and must not also claim an outage that wasn't
    necessarily the (engine-level) cause."""
    resp, _ = _post(client, error="Composer API key not configured")

    body = resp.get_json()
    assert not body.get("backtest_unavailable"), (
        "the run.error early-return branch must not fabricate backtest_unavailable=True"
    )
    assert not body.get("backtest_unavailable_notice")


# ===========================================================================
# Self-guard: prove the spy/mock plumbing itself works (candidate_id survives
# through the route unrelated to the outage fields) -- distinguishes "field
# genuinely missing" from "the whole mock harness broke".
# ===========================================================================


def test_self_guard_mocked_proposalrun_reaches_the_route_json(client):
    resp, mock_propose = _post(client, backtest_unavailable=True, backtest_unavailable_count=1)
    assert mock_propose.called, "self-guard failed: propose_strategies was never called"
    assert resp.status_code == 200
