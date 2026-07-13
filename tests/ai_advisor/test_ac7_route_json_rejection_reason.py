"""RED tests — AC-7 route-JSON extension (Asset Swaps / Logic Changes / SB
live-run).

test_r1_gate_transparency.py's AC-7 coverage is SB-only (Jinja-rendered
sb_withheld cards). Asset Swaps and Logic Changes render their Evaluate
results client-side from route JSON (static/ai_advisor_asset_swaps.js,
static/ai_advisor_logic_changes.js) — not Jinja — so the equivalent
"rejection reasons are distinguishable" requirement has to be proven at the
route-JSON level (per team-lead's approval, msg a434a3a3: "the two JS
surfaces need route-JSON-field tests").

**Checkpoint-3 BLOCK continuation (team-lead ruling):** the SB Jinja
coverage above proves the PERSISTED-history render path only. The SB
LIVE-RUN JSON response (`POST /ai-advisor/strategy-builder/run`, consumed
by `sbRunAnalysis()` in static/ai_advisor.js — see
test_r1_sb_live_run_field_consumption.py) is a DIFFERENT surface and was
never covered: `_gate_result_to_dict` (app.py, shared by this route's
`survivors_list` and `rejected_list`) never serializes `rejection_reason`
at all today, confirmed independently by both r1-fe and r1-test by direct
read. Wiring the JS to reference `r.rejection_reason` without this route
fix would be a false-GREEN — the field would be `undefined` on every real
run. The "Strategy Builder — live-run route JSON" section below closes
that gap, mirroring the Asset-Swaps/Logic-Changes sections' structure
exactly.

CONFIRMED BY READING app.py DIRECTLY BEFORE WRITING:
  - `grep -n "rejection_reason" app.py` returns ZERO matches — the field is
    computed and persisted on every CandidateGateResult
    (advisors/backtest_gate_engine.py:948) but neither
    ai_advisor_asset_swaps_evaluate (app.py:4336) nor
    ai_advisor_logic_changes_evaluate (app.py:4482) ever reads it into the
    JSON response.
  - Asset-swaps' response `gate_result` dict (app.py:4447-4457) carries
    decision/validation_days/oos_alpha/winner_p_adj/low_power — no
    rejection_reason.
  - Logic-changes' response has an analogous top-level `gate_result` dict
    (app.py:4643-4653, same 5 keys) AND a `gate_reason` field inside
    `_proposal_to_dict` (app.py:4610-4615) — but `gate_reason` is a COARSE
    humanized decision title ("Keep Incumbent") or a flat "veto failed"
    string, not the granular rejection_reason token; it collapses
    pbo_veto/below_spy_alpha into "veto failed" and fdr_not_winner/
    oos_inferior_to_incumbent into "Keep Incumbent" — the exact "blanket
    string per bucket, not per cause" defect AC-7 exists to close, just
    manifesting as two buckets instead of SB's original one.
  - static/ai_advisor_asset_swaps.js:186 reads `result.gate_reason`, which
    the asset-swaps route (unlike logic-changes) doesn't even populate today
    — that span always renders empty. Not this file's fix to design, but
    the gap is real and adjacent; noted for the implementer.

TEST STRATEGY: mirrors test_r1_power_caveat.py's AC-9 asset-swap/logic-change
route tests exactly (same shell/run-result construction, same
database.load_state / symphony_logic.fetch_symphony_score /
_has_composer_key / propose_operator_* mocking) — isolates the route's
JSON-serialization logic from the full backtest/gate chain. Controls
CandidateGateResult.rejection_reason directly rather than driving the real
gate math, so each test targets exactly one cause.
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


def _gate_result(bge, *, rejection_reason: str | None, vetoes_passed: bool, candidate_id: str):
    verdict = bge.AcceptanceVerdict(
        vetoes_passed=vetoes_passed,
        panel_score=0.5,
        panel_breakdown={},
        decision="ADOPT_CANDIDATE" if rejection_reason is None else "KEEP_INCUMBENT",
    )
    return bge.CandidateGateResult(
        candidate_id=candidate_id,
        verdict=verdict,
        validation_days=90,
        oos_alpha=1.0,
        caveats=[],
        winner_p_adj=0.01,
        rejection_reason=rejection_reason,
    )


# ===========================================================================
# Asset Swaps — /ai-advisor/asset-swaps/evaluate.
# ===========================================================================


def _asset_swap_response(rejection_reason: str | None, *, vetoes_passed: bool = False):
    import advisors.asset_swap_engine as ase
    import advisors.backtest_gate_engine as bge
    import app as app_module
    import database

    gr = _gate_result(
        bge,
        rejection_reason=rejection_reason,
        vetoes_passed=vetoes_passed,
        candidate_id="HASH1:AAA->CAND0",
    )
    shell = ase.SwapProposalResult(
        candidate_id="HASH1:AAA->CAND0",
        symphony_id="HASH1",
        incumbent_asset="AAA",
        candidate_asset="CAND0",
        objective=ase.SwapObjective(
            objective_type="reduce_correlation", target_pair=None, measured_value=0.0
        ),
        objective_rationale="test",
        baseline_stats={},
        variant_stats={},
        apply_guidance="test",
        data_warnings=[],
    )
    shell.gate_result = gr
    shell.caveats = list(gr.caveats)
    run_result = ase.SwapRunResult(
        gate_batch=bge.GatedBatch(
            results=[gr],
            survivors=[gr] if rejection_reason is None else [],
            n_candidates=1,
            fdr_q=0.05,
        ),
        proposals=[shell],
        survivors=[shell] if rejection_reason is None else [],
        rejected_candidates=[] if rejection_reason is None else [shell],
        message="swap evaluated",
        objective=shell.objective,
    )

    with (
        patch.object(database, "load_state", lambda: {"HASH1": {"name": "Test Symphony"}}),
        patch("symphony_logic.fetch_symphony_score", lambda h: {"ticker": None, "children": []}),
        patch.object(ase, "_has_composer_key", return_value=True),
        patch.object(ase, "propose_operator_swap", return_value=run_result),
    ):
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            resp = c.post(
                "/ai-advisor/asset-swaps/evaluate",
                json={"symphony_id": "Test Symphony", "from_ticker": "AAA", "to_ticker": "CAND0"},
            )
    assert resp.status_code == 200
    return resp.get_json()


def test_ac7_asset_swap_evaluate_response_carries_pbo_veto_rejection_reason():
    """MUST FAIL pre-fix: asset-swaps' gate_result dict has no
    rejection_reason field at all — pbo_veto is silently dropped."""
    body = _asset_swap_response("pbo_veto")
    assert (body.get("gate_result") or {}).get("rejection_reason") == "pbo_veto", (
        f"AC-7 GAP: asset-swap evaluate response's gate_result carries no "
        f"rejection_reason (or the wrong value). Response: {body}"
    )


def test_ac7_asset_swap_evaluate_response_carries_oos_inferior_to_incumbent_rejection_reason():
    """The real 4th honest class (team-lead msg a443e570 / a434a3a3): a
    KEEP_INCUMBENT-on-OOS-grounds result must be distinguishable, not
    indistinguishable from a genuine FDR non-winner."""
    body = _asset_swap_response("oos_inferior_to_incumbent", vetoes_passed=True)
    assert (body.get("gate_result") or {}).get("rejection_reason") == "oos_inferior_to_incumbent", (
        f"AC-7 GAP: asset-swap evaluate response does not distinguish the "
        f"OOS-inferior-to-incumbent class. Response: {body}"
    )


def test_ac7_asset_swap_evaluate_distinguishes_pbo_veto_from_below_spy_alpha():
    """Adversarial core (mirrors the SB Jinja test's structure exactly): two
    DIFFERENT rejection causes must produce two DIFFERENT rejection_reason
    values in the JSON — a fix that maps every rejection to one constant
    string still fails this."""
    pbo_body = _asset_swap_response("pbo_veto")
    spy_body = _asset_swap_response("below_spy_alpha")
    pbo_reason = (pbo_body.get("gate_result") or {}).get("rejection_reason")
    spy_reason = (spy_body.get("gate_result") or {}).get("rejection_reason")
    assert pbo_reason is not None and spy_reason is not None, (
        f"AC-7 GAP: rejection_reason missing entirely. pbo_body={pbo_body}, spy_body={spy_body}"
    )
    assert pbo_reason != spy_reason, (
        "AC-7 GAP: pbo_veto and below_spy_alpha asset-swap rejections render "
        f"IDENTICAL rejection_reason ({pbo_reason!r}) — not distinguishable to the operator."
    )


def test_ac7_asset_swap_evaluate_survivor_carries_no_rejection_reason():
    """Regression guard: a genuine ADOPT_CANDIDATE survivor must NOT carry a
    fabricated rejection_reason — a fix that always populates the field
    (even for survivors) would be a new honesty defect, not a fix."""
    body = _asset_swap_response(None)
    assert (body.get("gate_result") or {}).get("rejection_reason") is None, (
        f"AC-7 REGRESSION: a survivor's gate_result carries a non-null "
        f"rejection_reason. Response: {body}"
    )


# ===========================================================================
# Logic Changes — /ai-advisor/logic-changes/evaluate.
# ===========================================================================


def _logic_change_response(rejection_reason: str | None, *, vetoes_passed: bool = False):
    import advisors.backtest_gate_engine as bge
    import advisors.logic_change_engine as lce
    import app as app_module
    import database

    gr = _gate_result(
        bge,
        rejection_reason=rejection_reason,
        vetoes_passed=vetoes_passed,
        candidate_id="HASH1:window:20->16",
    )
    tweak = lce.LogicTweak(
        node_path=["children", 0],
        param_key="window",
        old_value=20,
        new_value=16,
        node_description="test",
    )
    shell = lce.LogicChangeProposalResult(
        candidate_id="HASH1:window:20->16",
        symphony_id="HASH1",
        tweak=tweak,
        objective=lce.LogicChangeObjective(
            objective_type="reduce_drawdown", measured_value=0.0, rationale="test"
        ),
        objective_rationale="test",
        baseline_stats={},
        variant_stats={},
        apply_guidance="test",
        data_warnings=[],
    )
    shell.gate_result = gr
    shell.caveats = list(gr.caveats)
    run_result = lce.LogicChangeRunResult(
        gate_batch=bge.GatedBatch(
            results=[gr],
            survivors=[gr] if rejection_reason is None else [],
            n_candidates=1,
            fdr_q=0.05,
        ),
        proposals=[shell],
        survivors=[shell] if rejection_reason is None else [],
        rejected_candidates=[] if rejection_reason is None else [shell],
        message="logic change evaluated",
        objective=shell.objective,
    )

    with (
        patch.object(database, "load_state", lambda: {"HASH1": {"name": "Test Symphony"}}),
        patch("symphony_logic.fetch_symphony_score", lambda h: {"type": "root", "children": []}),
        patch.object(lce, "_has_composer_key", return_value=True),
        patch.object(lce, "propose_operator_logic_change", return_value=run_result),
    ):
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            resp = c.post(
                "/ai-advisor/logic-changes/evaluate",
                json={
                    "symphony_id": "Test Symphony",
                    "change_description": "Reduce window from 20d to 16d",
                },
            )
    assert resp.status_code == 200
    return resp.get_json()


def test_ac7_logic_change_evaluate_response_carries_pbo_veto_rejection_reason():
    """MUST FAIL pre-fix — identical gap, logic_change_engine sibling.
    Checks both the top-level gate_result dict and the rejected_detail
    per-candidate dict (either satisfies — mirrors the AC-9 either/or
    pattern already established for this route)."""
    body = _logic_change_response("pbo_veto")
    top = (body.get("gate_result") or {}).get("rejection_reason")
    rejected_detail = body.get("rejected_detail") or []
    nested = rejected_detail[0].get("rejection_reason") if rejected_detail else None
    assert top == "pbo_veto" or nested == "pbo_veto", (
        f"AC-7 GAP: logic-change evaluate response carries no pbo_veto "
        f"rejection_reason, top-level or in rejected_detail. Response: {body}"
    )


def test_ac7_logic_change_evaluate_response_carries_oos_inferior_to_incumbent_rejection_reason():
    """The real 4th honest class, logic_change_engine sibling."""
    body = _logic_change_response("oos_inferior_to_incumbent", vetoes_passed=True)
    top = (body.get("gate_result") or {}).get("rejection_reason")
    rejected_detail = body.get("rejected_detail") or []
    nested = rejected_detail[0].get("rejection_reason") if rejected_detail else None
    assert top == "oos_inferior_to_incumbent" or nested == "oos_inferior_to_incumbent", (
        f"AC-7 GAP: logic-change evaluate response does not distinguish the "
        f"OOS-inferior-to-incumbent class. Response: {body}"
    )


def test_ac7_logic_change_evaluate_distinguishes_pbo_veto_from_below_spy_alpha():
    """Adversarial core, logic_change_engine sibling."""
    pbo_body = _logic_change_response("pbo_veto")
    spy_body = _logic_change_response("below_spy_alpha")

    def _reason(body):
        top = (body.get("gate_result") or {}).get("rejection_reason")
        if top is not None:
            return top
        rejected_detail = body.get("rejected_detail") or []
        return rejected_detail[0].get("rejection_reason") if rejected_detail else None

    pbo_reason = _reason(pbo_body)
    spy_reason = _reason(spy_body)
    assert pbo_reason is not None and spy_reason is not None, (
        f"AC-7 GAP: rejection_reason missing entirely. pbo_body={pbo_body}, spy_body={spy_body}"
    )
    assert pbo_reason != spy_reason, (
        "AC-7 GAP: pbo_veto and below_spy_alpha logic-change rejections render "
        f"IDENTICAL rejection_reason ({pbo_reason!r}) — not distinguishable to the operator."
    )


def test_ac7_logic_change_evaluate_survivor_carries_no_rejection_reason():
    """Regression guard: a genuine ADOPT_CANDIDATE survivor must NOT carry a
    fabricated rejection_reason, logic_change_engine sibling."""
    body = _logic_change_response(None)
    top = (body.get("gate_result") or {}).get("rejection_reason")
    survivors_detail = body.get("survivors_detail") or []
    nested = survivors_detail[0].get("rejection_reason") if survivors_detail else None
    assert top is None and nested is None, (
        f"AC-7 REGRESSION: a survivor carries a non-null rejection_reason "
        f"(top={top!r}, nested={nested!r}). Response: {body}"
    )


# ===========================================================================
# Strategy Builder — POST /ai-advisor/strategy-builder/run (LIVE-RUN route
# JSON, distinct from the persisted-history Jinja surface test_r1_gate_
# transparency.py already covers). Checkpoint-3 BLOCK continuation.
# ===========================================================================


def _sb_run_response(rejection_reason: str | None, *, vetoes_passed: bool = False):
    import advisors.backtest_gate_engine as bge
    from advisors.strategy_builder_engine import CandidateInfo, ProposalRun

    gr = _gate_result(
        bge,
        rejection_reason=rejection_reason,
        vetoes_passed=vetoes_passed,
        candidate_id="sb-cand",
    )
    info = CandidateInfo(candidate_id="sb-cand", tree={}, template_id="built-new", params={})
    run = ProposalRun(
        candidates=[info],
        gated_batch=bge.GatedBatch(
            results=[gr],
            survivors=[gr] if rejection_reason is None else [],
            n_candidates=1,
            fdr_q=0.05,
        ),
        screened_survivors=[gr] if rejection_reason is None else [],
        observations_written=0,
    )

    with (
        patch("advisors.strategy_builder_engine.propose_strategies", return_value=run),
        patch("advisors.build_plan_generator.load_atlas_candidates", return_value=[]),
    ):
        import app as app_module

        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            resp = c.post(
                "/ai-advisor/strategy-builder/run", json={"objective": "diversify", "universe": []}
            )
    assert resp.status_code == 200
    return resp.get_json()


def test_ac7_sb_run_response_carries_pbo_veto_rejection_reason():
    """MUST FAIL pre-fix: SB's own _gate_result_to_dict (shared by
    survivors_list and rejected_list) has no rejection_reason key at all —
    confirmed by direct read by both r1-fe and r1-test independently."""
    body = _sb_run_response("pbo_veto")
    rejected = body.get("rejected") or []
    assert rejected and rejected[0].get("rejection_reason") == "pbo_veto", (
        f"AC-7 GAP: SB run response's rejected-candidate entry carries no "
        f"pbo_veto rejection_reason. Response: {body}"
    )


def test_ac7_sb_run_response_carries_oos_inferior_to_incumbent_rejection_reason():
    """The real 4th honest class, SB live-run sibling."""
    body = _sb_run_response("oos_inferior_to_incumbent", vetoes_passed=True)
    rejected = body.get("rejected") or []
    assert rejected and rejected[0].get("rejection_reason") == "oos_inferior_to_incumbent", (
        f"AC-7 GAP: SB run response does not distinguish the OOS-inferior-"
        f"to-incumbent class. Response: {body}"
    )


def test_ac7_sb_run_distinguishes_pbo_veto_from_below_spy_alpha():
    """Adversarial core, SB live-run sibling: two DIFFERENT rejection causes
    must produce two DIFFERENT rejection_reason values — a fix that maps
    every rejection to one constant string still fails this."""
    pbo_body = _sb_run_response("pbo_veto")
    spy_body = _sb_run_response("below_spy_alpha")
    pbo_rejected = pbo_body.get("rejected") or []
    spy_rejected = spy_body.get("rejected") or []
    pbo_reason = pbo_rejected[0].get("rejection_reason") if pbo_rejected else None
    spy_reason = spy_rejected[0].get("rejection_reason") if spy_rejected else None
    assert pbo_reason is not None and spy_reason is not None, (
        f"AC-7 GAP: rejection_reason missing entirely. pbo_body={pbo_body}, spy_body={spy_body}"
    )
    assert pbo_reason != spy_reason, (
        "AC-7 GAP: pbo_veto and below_spy_alpha SB rejections render "
        f"IDENTICAL rejection_reason ({pbo_reason!r}) — not distinguishable to the operator."
    )


def test_ac7_sb_run_survivor_carries_no_rejection_reason():
    """Regression guard: a genuine ADOPT_CANDIDATE survivor must NOT carry a
    fabricated rejection_reason, SB live-run sibling."""
    body = _sb_run_response(None)
    survivors = body.get("survivors") or []
    assert survivors and survivors[0].get("rejection_reason") is None, (
        f"AC-7 REGRESSION: a SB survivor carries a non-null rejection_reason. Response: {body}"
    )
