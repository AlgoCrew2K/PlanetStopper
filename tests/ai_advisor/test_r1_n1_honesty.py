"""RED tests — AC-6: N=1 honesty on the operator Evaluate buttons.

WHY THIS IS A ROUTE-LEVEL TEST (per the plan's own Testing Strategy: "Route/
render tests (AC-1/2/3/6/7/8/9/11): route-level JSON field assertions"):
POST /ai-advisor/asset-swaps/evaluate and POST /ai-advisor/logic-changes/evaluate
are the two N=1 operator-Evaluate buttons audit F2 names. Both can reach an
ADOPT_CANDIDATE verdict through the SAME evaluate_candidate_batch machinery
used for N>1 weekly batches, and — critically — the SURVIVOR_OVERFITTING_CAVEAT
text unconditionally appended on ADOPT ("...statistical significance (BHY/
Yekutieli FDR)...") carries literal "FDR"/"Yekutieli" branding into the N=1
response's `caveats` field today, even though c(N=1)=1.0 makes the correction
a mathematical no-op. This file drives both routes through a REAL ADOPT
verdict (verified empirically below via a fixture that clears BOTH the FDR/OOS
checks AND the panel-score margin — see WHY _evaluate_single_variant IS MOCKED
below) so the "FDR/Yekutieli branding must be ABSENT at N=1" assertion is a
genuine RED test, not a vacuous pass against a REJECT/WITHHOLD response that
never had branding to begin with (the exact adversarial requirement from the
spawn brief: "AC-6's N=1 test must assert the FDR branding is ABSENT, not
merely that the new label is present").

WHY _evaluate_single_variant IS MOCKED (an empirically-discovered, orthogonal
finding — not an AC-6 target, and not worked around by fabricating success):
_evaluate_single_variant (both engines) constructs its BacktestCandidate with
candidate_params={}/incumbent_params={}/theory_prior_params={} unconditionally.
acceptance_gate._compute_panel_score gives an ALL-EMPTY candidate a stability
score of 0.5 (the "no info" neutral prior) while the incumbent's stability
score is HARDCODED to 1.0 ("the incumbent is stable against itself" —
backtest_gate_engine.py's evaluate_candidate_batch, `inc_stability = 1.0`).
With PANEL_ADOPT_MARGIN_THRESHOLD=0.0, candidate_panel_score(0.5) is then
structurally < incumbent_panel_score(0.75) for EVERY candidate built via the
real _evaluate_single_variant path, regardless of how strong its returns are
— confirmed empirically (a candidate with p_adj=0.0026, comfortably clearing
FDR, still returned KEEP_INCUMBENT). This means propose_operator_swap /
propose_operator_logic_change cannot reach ADOPT_CANDIDATE via their real,
unmodified candidate-construction path at all today — a real finding, but out
of AC-6's scope (AC-6 is about FDR/Yekutieli branding conditioning on N, not
panel-score reachability). Rather than silently mask this by picking a fixture
that "cheats" the real construction path, or asserting against an unreachable
state, these tests mock _evaluate_single_variant (an existing internal engine
seam) to return a BacktestCandidate with MATCHING non-empty params (so the
panel-score asymmetry is neutralized) while every other real mechanism
(evaluate_candidate_batch, the route's JSON assembly, the caveat/branding
logic under test) still runs for real. This is flagged in the tdd-handoff.md
Questions-for-User for the PM/r1-engine to decide whether the panel-score
asymmetry itself needs a separate ticket.

PAIRED REGRESSION GUARD: the N>1 weekly path (suggest_swaps /
suggest_logic_changes, exercised directly at the engine level since there is
no HTTP route for the weekly path — it runs off a scheduler) must KEEP FDR/
Yekutieli labeling on an ADOPT survivor. Without this pairing, a lazy fix
could globally strip FDR branding from SURVIVOR_OVERFITTING_CAVEAT (breaking
the legitimate N>1 disclosure) and still pass the N=1-only assertion.
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


_DATES_80 = [f"2026-{m:02d}-{d:02d}" for m in range(1, 9) for d in range(1, 11)]

# Predominantly positive with periodic small pullbacks — a strictly-constant or
# strictly-nonnegative series degenerates Sortino's downside deviation to 0
# (undefined ratio -> compute_sortino_tstat's conservative t=0.0 fallback,
# empirically confirmed while tuning this fixture). This cyclical pattern
# gives the bootstrap real downside variance to measure (p_adj ~0.0026,
# comfortably clearing HARVEY_LIU_FDR_Q=0.05) while keeping the cumulative
# sum strongly positive (oos_alpha ~20, comfortably beating the 0.0 baseline).
_STRONG_POSITIVE_RETURNS_PCT = {d: (1.5 if i % 10 != 9 else -0.2) for i, d in enumerate(_DATES_80)}

# Matching, non-empty, IDENTICAL params across candidate/incumbent/theory-prior
# so acceptance_gate's panel-score margin check (candidate_panel_score >=
# incumbent_panel_score + PANEL_ADOPT_MARGIN_THRESHOLD) is neutralized rather
# than structurally failed by the {} vs hardcoded-1.0 asymmetry documented in
# the module docstring above.
_MATCHING_PARAMS = {"window": 20.0}


def _matching_candidate(candidate_id: str, daily_returns_pct: list) -> object:
    from advisors.backtest_gate_engine import BacktestCandidate

    return BacktestCandidate(
        candidate_id=candidate_id,
        daily_returns_pct=daily_returns_pct,
        candidate_params=dict(_MATCHING_PARAMS),
        incumbent_params=dict(_MATCHING_PARAMS),
        theory_prior_params=dict(_MATCHING_PARAMS),
    )


# ===========================================================================
# Self-guard: prove our fixture genuinely produces ADOPT_CANDIDATE via the
# REAL gate, so the FDR-absence assertion below is non-vacuous.
# ===========================================================================


def test_self_guard_strong_positive_fixture_adopts_via_real_gate():
    from advisors.backtest_gate_engine import evaluate_candidate_batch

    cand = _matching_candidate("self-guard-n1", list(_STRONG_POSITIVE_RETURNS_PCT.values()))
    batch = evaluate_candidate_batch([cand], incumbent_oos_alpha=0.0, default_oos_alpha=0.0)
    assert batch.results[0].verdict.decision == "ADOPT_CANDIDATE", (
        f"self-guard fixture must ADOPT via the real gate so the N=1 FDR-absence "
        f"test below is non-vacuous; got {batch.results[0].verdict.decision} "
        f"(winner_p_adj={batch.results[0].winner_p_adj}, oos_alpha={batch.results[0].oos_alpha})"
    )
    assert any("Yekutieli" in c or "FDR" in c for c in batch.results[0].caveats), (
        "self-guard: SURVIVOR_OVERFITTING_CAVEAT must currently carry FDR/Yekutieli "
        "text on ADOPT (pre-AC-6 state) so removing it at N=1 is a real, observable "
        f"change. Got caveats: {batch.results[0].caveats}"
    )


# ===========================================================================
# AC-6 — Asset Swaps N=1 operator Evaluate route.
# ===========================================================================


def _mock_symphony_resolution(
    monkeypatch, raw_tree: dict, composer_hash: str = "HASH1", symphony_name: str = "Test Symphony"
):
    import database

    monkeypatch.setattr(database, "load_state", lambda: {composer_hash: {"name": symphony_name}})
    monkeypatch.setattr("symphony_logic.fetch_symphony_score", lambda h: raw_tree)


def test_ac6_asset_swap_n1_evaluate_response_omits_fdr_yekutieli_branding(client, monkeypatch):
    """MUST FAIL pre-fix: today the ADOPT caveats field carries the
    SURVIVOR_OVERFITTING_CAVEAT's literal 'FDR'/'Yekutieli' text at N=1."""
    import advisors.asset_swap_engine as ase

    raw_tree = {"ticker": None, "children": [{"ticker": "AAA", "children": []}]}
    _mock_symphony_resolution(monkeypatch, raw_tree)

    cand = _matching_candidate("HASH1:AAA->CAND0", list(_STRONG_POSITIVE_RETURNS_PCT.values()))
    shell = ase.SwapProposalResult(
        candidate_id="HASH1:AAA->CAND0",
        symphony_id="HASH1",
        incumbent_asset="AAA",
        candidate_asset="CAND0",
        objective=ase.SwapObjective(
            objective_type="reduce_correlation", target_pair=None, measured_value=0.0
        ),
        objective_rationale="test rationale",
        baseline_stats={"sharpe": 0.0},
        variant_stats={"sharpe": 2.0},
        apply_guidance="test apply guidance",
        data_warnings=[],
    )

    with (
        patch.object(ase, "_has_composer_key", return_value=True),
        # AC-13 (commit 82479560) extended _evaluate_single_variant's return
        # from a 3-tuple to a 4-tuple (baseline_returns_pct added — the
        # caller now reuses this list instead of a second run_backtest call
        # for the baseline fold). baseline_returns_pct is a flat, percent-
        # scale list[float] (matches _fold_transform_single's expected
        # shape, confirmed by reading the real call site at ase.py:1109) —
        # a dict would be the wrong type here.
        patch.object(
            ase,
            "_evaluate_single_variant",
            return_value=(cand, shell, {"sharpe": 0.0}, [0.0] * len(_DATES_80)),
        ),
        # The baseline-fold call inside propose_operator_swap itself (via
        # _backtest_returns_from_tree) still hits run_backtest — return a flat
        # near-zero baseline series so it does not perturb the ADOPT outcome.
        patch.object(ase, "run_backtest") as mock_run_backtest,
        patch.object(ase, "database") as mock_db,
    ):
        from advisors.composer_backtest_client import BacktestResult

        mock_run_backtest.return_value = BacktestResult(
            stats={}, data_warnings=[], daily_returns={d: 0.0 for d in _DATES_80}
        )
        mock_db.insert_advisor_observation.return_value = 1
        resp = client.post(
            "/ai-advisor/asset-swaps/evaluate",
            json={"symphony_id": "Test Symphony", "from_ticker": "AAA", "to_ticker": "CAND0"},
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("gate_decision") == "ADOPT_CANDIDATE", (
        f"expected the strong-positive matching-params fixture to ADOPT so this "
        f"is a non-vacuous RED test; got gate_decision={body.get('gate_decision')!r}, body={body}"
    )
    rendered_text = " ".join(str(v) for v in body.values())
    assert "FDR" not in rendered_text and "Yekutieli" not in rendered_text, (
        "AC-6 GAP: the N=1 asset-swap evaluate response still carries FDR/Yekutieli "
        f"branding. Response: {body}"
    )
    assert "N=1" in rendered_text or "single-candidate" in rendered_text.lower(), (
        "AC-6 GAP: the N=1 asset-swap evaluate response does not carry the "
        f"single-candidate-check honesty label. Response: {body}"
    )


# ===========================================================================
# AC-6 — Logic Changes N=1 operator Evaluate route.
# ===========================================================================


def test_ac6_logic_change_n1_evaluate_response_omits_fdr_yekutieli_branding(client, monkeypatch):
    """MUST FAIL pre-fix — identical gap and identical mocking rationale to the
    asset-swap sibling test (see module docstring)."""
    import advisors.logic_change_engine as lce

    raw_tree = {"type": "root", "children": [{"type": "momentum", "window": 20, "children": []}]}
    _mock_symphony_resolution(monkeypatch, raw_tree)

    tweak = lce.LogicTweak(
        node_path=["children", 0],
        param_key="window",
        old_value=20,
        new_value=16,
        node_description="window=20 -> 16 at path [children, 0]",
    )
    cand = _matching_candidate(
        "HASH1:window@children>0:20->16", list(_STRONG_POSITIVE_RETURNS_PCT.values())
    )
    shell = lce.LogicChangeProposalResult(
        candidate_id="HASH1:window@children>0:20->16",
        symphony_id="HASH1",
        tweak=tweak,
        objective=lce.LogicChangeObjective(
            objective_type="reduce_drawdown", measured_value=0.0, rationale="test"
        ),
        objective_rationale="test rationale",
        baseline_stats={"sharpe": 0.0},
        variant_stats={"sharpe": 2.0},
        apply_guidance="test apply guidance",
        data_warnings=[],
    )

    with (
        patch.object(lce, "_has_composer_key", return_value=True),
        # AC-13 4-tuple contract change — see the asset_swap sibling test's
        # comment for the full rationale.
        patch.object(
            lce,
            "_evaluate_single_variant",
            return_value=(cand, shell, {"sharpe": 0.0}, [0.0] * len(_DATES_80)),
        ),
        patch.object(lce, "run_backtest") as mock_run_backtest,
        patch.object(lce, "database") as mock_db,
    ):
        from advisors.composer_backtest_client import BacktestResult

        mock_run_backtest.return_value = BacktestResult(
            stats={}, data_warnings=[], daily_returns={d: 0.0 for d in _DATES_80}
        )
        mock_db.insert_advisor_observation.return_value = 1
        resp = client.post(
            "/ai-advisor/logic-changes/evaluate",
            json={
                "symphony_id": "Test Symphony",
                "change_description": "Reduce window from 20d to 16d to improve drawdown reactivity",
            },
        )

    assert resp.status_code == 200
    body = resp.get_json()
    rendered_text = " ".join(str(v) for v in body.values())
    assert body.get("gate_decision") == "ADOPT_CANDIDATE", (
        f"expected the strong-positive matching-params fixture to ADOPT so this "
        f"is a non-vacuous RED test; got gate_decision={body.get('gate_decision')!r}, body={body}"
    )
    assert "N=1" in rendered_text or "single-candidate" in rendered_text.lower(), (
        f"AC-6 GAP: the N=1 logic-change evaluate response does not carry the "
        f"single-candidate-check honesty label. Response: {body}"
    )
    assert "FDR" not in rendered_text and "Yekutieli" not in rendered_text, (
        f"AC-6 GAP: the N=1 logic-change evaluate response still carries FDR/"
        f"Yekutieli branding. Response: {body}"
    )


# ===========================================================================
# Paired regression guard — N>1 weekly path KEEPS FDR/Yekutieli labeling on an
# ADOPT survivor (engine-level: no HTTP route exists for the scheduler path).
# ===========================================================================


def test_ac6_weekly_batch_survivor_retains_fdr_yekutieli_labeling():
    """Guards against a lazy AC-6 fix that strips FDR/Yekutieli branding
    globally from SURVIVOR_OVERFITTING_CAVEAT instead of conditioning it on
    N==1 — that would silently break the legitimate N>1 FDR disclosure."""
    from advisors.backtest_gate_engine import evaluate_candidate_batch

    # A second, weaker (but still matching-params) candidate alongside the
    # strong one so n_effective=2 (a genuine batch, not accidentally N=1).
    strong = _matching_candidate("weekly-strong", list(_STRONG_POSITIVE_RETURNS_PCT.values()))
    weak = _matching_candidate("weekly-weak", [0.01] * len(_STRONG_POSITIVE_RETURNS_PCT))

    batch = evaluate_candidate_batch([strong, weak], incumbent_oos_alpha=0.0, default_oos_alpha=0.0)
    survivor = next((r for r in batch.results if r.verdict.decision == "ADOPT_CANDIDATE"), None)
    assert survivor is not None, (
        f"expected the strong candidate to survive a 2-candidate batch; got "
        f"decisions: {[r.verdict.decision for r in batch.results]}"
    )
    assert any("Yekutieli" in c or "FDR" in c for c in survivor.caveats), (
        f"REGRESSION GUARD: an N>1 batch survivor must still carry FDR/Yekutieli "
        f"caveat text. Got caveats: {survivor.caveats}"
    )
