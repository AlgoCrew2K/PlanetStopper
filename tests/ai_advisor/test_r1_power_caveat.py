"""RED tests — AC-9 (F3, Gap C): statistical-power caveat on survivor cards.

CONTRACT (locked by team-lead, r1-fe's approval thread): `MIN_POWER_FOLD_DAYS`
lives in app.py (NOT backtest_gate_engine.py — deliberate collision-avoidance
with r1-engine's concurrent AC-4/5/17 work there), value + source comment
citing audit F3 (T=13 verified floor + T=121 fixture anchor, see the golden
fixture's own "source" field). The ROUTE computes a `low_power: true/false`
boolean into the response JSON from the gate result's `validation_days`
field (CandidateGateResult.validation_days, already computed engine-side);
JS/Jinja render OFF THAT FLAG ONLY — never receive or duplicate the numeric
threshold. Applies to all three result surfaces per the plan's "Survivor
cards carry a statistical-power caveat" (not SB-only): Strategy Builder,
Asset Swaps, Logic Changes.

TEST STRATEGY: rather than driving each route through a full backtest/gate
chain (slow, and orthogonal to what AC-9 tests — the ROUTE's threshold
comparison, not how validation_days got computed), each route's underlying
propose_* call is mocked to return a controlled result object carrying a
specific `validation_days` value, isolating the field under test. Golden
fixture (tests/fixtures/math/min_power_fold_days_caveat.json) drives the
boundary cases via OFFSETS from app.MIN_POWER_FOLD_DAYS (never an absolute
day count), so this file needs no update if the threshold's chosen value
changes — only the comparison DIRECTION is pinned.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

try:
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st

    _HAS_HYPOTHESIS = True
except ImportError:  # pragma: no cover - project already depends on hypothesis elsewhere
    _HAS_HYPOTHESIS = False

_FIXTURE_PATH = (
    Path(__file__).parents[1] / "fixtures" / "math" / "min_power_fold_days_caveat.json"
)


@pytest.fixture(scope="module")
def golden_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ===========================================================================
# Constant existence + no-magic-number source-comment check (project rule:
# "No magic numbers in math_engine.py — every constant named + source
# comment" — applied here to app.py's AC-9 constant per the locked contract).
# ===========================================================================


def test_ac9_min_power_fold_days_constant_exists_in_app_with_source_comment():
    """MUST FAIL pre-fix: MIN_POWER_FOLD_DAYS does not exist in app.py yet."""
    import app as app_module

    value = getattr(app_module, "MIN_POWER_FOLD_DAYS", None)
    assert value is not None, (
        "AC-9 GAP: app.MIN_POWER_FOLD_DAYS does not exist. Per the locked "
        "contract it must live in app.py (not backtest_gate_engine.py)."
    )
    assert isinstance(value, int) and value > 0, (
        f"AC-9 GAP: app.MIN_POWER_FOLD_DAYS must be a positive int; got {value!r}"
    )

    import inspect

    source = inspect.getsource(app_module)
    # Locate the constant's own assignment line and require a comment citing
    # audit F3 within a few lines (source-comment requirement, math-layer rule).
    lines = source.splitlines()
    const_line_idx = next(
        (i for i, line in enumerate(lines) if line.strip().startswith("MIN_POWER_FOLD_DAYS")),
        None,
    )
    assert const_line_idx is not None, "could not locate the MIN_POWER_FOLD_DAYS assignment line in app.py source"
    context = "\n".join(lines[max(0, const_line_idx - 5) : const_line_idx + 1])
    assert "F3" in context or "audit" in context.lower(), (
        f"AC-9 GAP: no source comment citing audit F3 near the "
        f"MIN_POWER_FOLD_DAYS assignment. Context:\n{context}"
    )


# ===========================================================================
# Route-level boundary tests (golden fixture, all three surfaces).
# ===========================================================================


def _sb_survivor_with_validation_days(validation_days: int):
    """A minimal ProposalRun-shaped mock carrying one survivor with the given
    validation_days — isolates the route's threshold-comparison logic from
    the full backtest/gate chain."""
    from advisors.backtest_gate_engine import AcceptanceVerdict, CandidateGateResult, GatedBatch
    from advisors.strategy_builder_engine import CandidateInfo, ProposalRun

    verdict = AcceptanceVerdict(vetoes_passed=True, panel_score=1.0, panel_breakdown={}, decision="ADOPT_CANDIDATE")
    gr = CandidateGateResult(
        candidate_id="power-cand",
        verdict=verdict,
        validation_days=validation_days,
        oos_alpha=5.0,
        caveats=[],
        winner_p_adj=0.01,
        rejection_reason=None,
    )
    info = CandidateInfo(candidate_id="power-cand", tree={}, template_id="built-new", params={})
    return ProposalRun(
        candidates=[info],
        gated_batch=GatedBatch(results=[gr], survivors=[gr], n_candidates=1, fdr_q=0.05),
        screened_survivors=[gr],
        observations_written=0,
    )


@pytest.mark.parametrize("case_name", [
    "one_day_below_threshold", "exactly_at_threshold", "one_day_above_threshold",
    "far_below_threshold", "far_above_threshold",
])
def test_ac9_sb_route_low_power_flag_matches_golden_fixture(client, golden_fixture, case_name):
    """MUST FAIL pre-fix: the SB route response carries no low_power field at
    all today. Golden-fixture-driven boundary cases."""
    import app as app_module

    case = next(c for c in golden_fixture["cases"] if c["name"] == case_name)
    threshold = getattr(app_module, "MIN_POWER_FOLD_DAYS", 65)  # fallback keeps the route callable pre-fix
    validation_days = threshold + case["validation_days_offset_from_threshold"]

    with (
        patch("advisors.strategy_builder_engine.propose_strategies", return_value=_sb_survivor_with_validation_days(validation_days)),
        patch("advisors.build_plan_generator.load_atlas_candidates", return_value=[]),
    ):
        resp = client.post("/ai-advisor/strategy-builder/run", json={"objective": "diversify", "universe": []})

    assert resp.status_code == 200
    body = resp.get_json()
    survivors = body.get("survivors") or []
    assert survivors, f"expected at least one survivor in the route response; got {body}"
    assert survivors[0].get("low_power") == case["expected_low_power"], (
        f"AC-9 GAP (case={case_name}, validation_days={validation_days}, "
        f"threshold={threshold}): expected low_power={case['expected_low_power']}, "
        f"got {survivors[0].get('low_power')!r}. Full survivor: {survivors[0]}"
    )


def test_ac9_survivor_caveat_text_present_when_low_power(client, golden_fixture):
    """A True low_power flag must be paired with an actual rendered caveat
    somewhere reachable to the operator — the flag alone (silently present in
    JSON, never surfaced) would not satisfy 'survivor cards carry a
    statistical-power caveat'."""
    import app as app_module

    threshold = getattr(app_module, "MIN_POWER_FOLD_DAYS", 65)
    with (
        patch("advisors.strategy_builder_engine.propose_strategies", return_value=_sb_survivor_with_validation_days(threshold - 1)),
        patch("advisors.build_plan_generator.load_atlas_candidates", return_value=[]),
    ):
        resp = client.post("/ai-advisor/strategy-builder/run", json={"objective": "diversify", "universe": []})
    body = resp.get_json()
    survivors = body.get("survivors") or []
    assert survivors and survivors[0].get("low_power") is True
    caveats = survivors[0].get("caveats") or []
    assert any("power" in str(c).lower() for c in caveats), (
        f"AC-9 GAP: low_power=True but no caveat text mentions statistical "
        f"power. caveats={caveats}"
    )


# ===========================================================================
# Asset Swaps / Logic Changes — the plan applies AC-9 to survivor cards
# broadly, not SB-only (mirrors the AC-7 SB-only gap already flagged).
# ===========================================================================


def test_ac9_asset_swap_evaluate_response_carries_low_power_field():
    """MUST FAIL pre-fix: the asset-swap evaluate route's gate_result dict has
    no low_power field."""
    import advisors.asset_swap_engine as ase
    import app as app_module

    threshold = getattr(app_module, "MIN_POWER_FOLD_DAYS", 65)
    from advisors.backtest_gate_engine import AcceptanceVerdict, CandidateGateResult, GatedBatch

    verdict = AcceptanceVerdict(vetoes_passed=True, panel_score=1.0, panel_breakdown={}, decision="ADOPT_CANDIDATE")
    gr = CandidateGateResult(
        candidate_id="HASH1:AAA->CAND0", verdict=verdict, validation_days=threshold - 1,
        oos_alpha=5.0, caveats=[], winner_p_adj=0.01, rejection_reason=None,
    )
    shell = ase.SwapProposalResult(
        candidate_id="HASH1:AAA->CAND0", symphony_id="HASH1", incumbent_asset="AAA", candidate_asset="CAND0",
        objective=ase.SwapObjective(objective_type="reduce_correlation", target_pair=None, measured_value=0.0),
        objective_rationale="test", baseline_stats={}, variant_stats={}, apply_guidance="test", data_warnings=[],
    )
    shell.gate_result = gr
    shell.caveats = list(gr.caveats)
    run_result = ase.SwapRunResult(
        gate_batch=GatedBatch(results=[gr], survivors=[gr], n_candidates=1, fdr_q=0.05),
        proposals=[shell], survivors=[shell], rejected_candidates=[], message="1 swap survived the gate", objective=shell.objective,
    )

    import database

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
    body = resp.get_json()
    assert "low_power" in (body.get("gate_result") or {}), (
        f"AC-9 GAP: asset-swap evaluate response's gate_result has no "
        f"low_power field. Response: {body}"
    )
    assert body["gate_result"]["low_power"] is True


def test_ac9_logic_change_evaluate_response_carries_low_power_field():
    """MUST FAIL pre-fix — identical gap, logic_change_engine sibling."""
    import advisors.logic_change_engine as lce
    import app as app_module

    threshold = getattr(app_module, "MIN_POWER_FOLD_DAYS", 65)
    from advisors.backtest_gate_engine import AcceptanceVerdict, CandidateGateResult, GatedBatch

    verdict = AcceptanceVerdict(vetoes_passed=True, panel_score=1.0, panel_breakdown={}, decision="ADOPT_CANDIDATE")
    tweak = lce.LogicTweak(node_path=["children", 0], param_key="window", old_value=20, new_value=16, node_description="test")
    gr = CandidateGateResult(
        candidate_id="HASH1:window:20->16", verdict=verdict, validation_days=threshold - 1,
        oos_alpha=5.0, caveats=[], winner_p_adj=0.01, rejection_reason=None,
    )
    shell = lce.LogicChangeProposalResult(
        candidate_id="HASH1:window:20->16", symphony_id="HASH1", tweak=tweak,
        objective=lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0, rationale="test"),
        objective_rationale="test", baseline_stats={}, variant_stats={}, apply_guidance="test", data_warnings=[],
    )
    shell.gate_result = gr
    shell.caveats = list(gr.caveats)
    run_result = lce.LogicChangeRunResult(
        gate_batch=GatedBatch(results=[gr], survivors=[gr], n_candidates=1, fdr_q=0.05),
        proposals=[shell], survivors=[shell], rejected_candidates=[], message="1 logic change survived the gate", objective=shell.objective,
    )

    import database

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
                json={"symphony_id": "Test Symphony", "change_description": "Reduce window from 20d to 16d"},
            )

    assert resp.status_code == 200
    body = resp.get_json()
    gate_result = body.get("gate_result") or {}
    assert body.get("low_power") is not None or gate_result.get("low_power") is not None, (
        f"AC-9 GAP: logic-change evaluate response carries no low_power field, "
        f"top-level or nested under gate_result. Response: {body}"
    )


# ===========================================================================
# Property test: low_power is a monotonic function of validation_days — never
# flips True again once it has gone False as validation_days increases. Fast,
# bounded example count (route-level, not a hot loop).
# ===========================================================================


@pytest.mark.skipif(not _HAS_HYPOTHESIS, reason="hypothesis not installed")
@given(validation_days=st.integers(min_value=0, max_value=300))
@settings(
    max_examples=20,
    deadline=None,
    # The `client` fixture is a stateless Flask test client — reused across
    # generated examples is safe here (no per-example state leaks between
    # requests), so the function-scoped-fixture health check is a false
    # positive for this specific test, not a real risk.
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_ac9_low_power_flag_is_monotonic_in_validation_days(client, validation_days):
    """MUST FAIL pre-fix (no low_power field at all): for any validation_days,
    low_power must equal exactly (validation_days < MIN_POWER_FOLD_DAYS) —
    a strict-less-than comparison against a fixed constant is monotonic by
    construction, so this single correctness property IS the monotonicity
    guarantee."""
    import app as app_module

    threshold = getattr(app_module, "MIN_POWER_FOLD_DAYS", None)
    if threshold is None:
        pytest.fail("AC-9 GAP: app.MIN_POWER_FOLD_DAYS does not exist — cannot evaluate the property.")

    with (
        patch("advisors.strategy_builder_engine.propose_strategies", return_value=_sb_survivor_with_validation_days(validation_days)),
        patch("advisors.build_plan_generator.load_atlas_candidates", return_value=[]),
    ):
        resp = client.post("/ai-advisor/strategy-builder/run", json={"objective": "diversify", "universe": []})

    body = resp.get_json()
    survivors = body.get("survivors") or []
    assert survivors, f"expected a survivor for validation_days={validation_days}; got {body}"
    expected = validation_days < threshold
    assert survivors[0].get("low_power") == expected, (
        f"AC-9 GAP: validation_days={validation_days}, threshold={threshold} "
        f"-> expected low_power={expected}, got {survivors[0].get('low_power')!r}"
    )
