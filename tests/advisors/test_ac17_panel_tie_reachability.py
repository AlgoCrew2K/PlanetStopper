"""RED tests — AC-17 (panel unreachability, added mid-cycle on r1-engine's
proven finding; plan @ ad9b1629): ADOPT_CANDIDATE becomes REACHABLE for
tree-structural (empty-params) candidates.

PROVEN DEFECT (independently confirmed by r1-test's AC-6 fixture-tuning AND
r1-engine's cross-engine trace): all three engines (asset_swap_engine.py,
logic_change_engine.py, strategy_builder_engine.py) construct BacktestCandidate
with candidate_params={}/incumbent_params={}/theory_prior_params={} — every
current call site, no exceptions. acceptance_gate._compute_panel_score gives
an all-empty candidate a stability sub-score of 0.5 (the neutral-prior
fallback) while backtest_gate_engine.evaluate_candidate_batch hardcodes the
INCUMBENT's stability at a CONSTANT 1.0 (`inc_stability = 1.0`, "the
incumbent is stable against itself"). With PANEL_ADOPT_MARGIN_THRESHOLD=0.0,
candidate_panel_score(0.5) < incumbent_panel_score(0.75) UNCONDITIONALLY —
ADOPT_CANDIDATE is mathematically unreachable regardless of OOS performance,
for every candidate built via any of the three engines' real construction
path. This is also the DOMINANT (not merely contributing) cause of
database.get_candidate_alert_new_valid_count() reading 0 forever — the badge
has never had a reachable survivor to count.

FIX SCOPE (per plan — acceptance_gate.py and autotuner.py UNTOUCHED): contained
to advisors/backtest_gate_engine.py's evaluate_candidate_batch — when
candidate_params AND incumbent_params are BOTH structurally empty, the
parameter panel is NOT APPLICABLE; cand_stability is set equal to
inc_stability (an exact tie) so the adoption decision rests entirely on the
OOS-superiority precondition (acceptance_gate.py:257) plus the three hard
vetoes (BHY winner / PBO / SPY baseline), never on an accidental panel-score
artifact of missing parameter data.

WHAT THIS FILE COVERS: plan sub-requirements a/c/d/e — engine-agnostic core +
all three engines' real entry points + the panel_breakdown N/A marker.
Sub-requirement (c)'s exact contract was answered by r1-engine (msg
082fd634): `acceptance_gate.py` is untouched (no-touch file) — the marker is
stamped by `backtest_gate_engine.py` via `AcceptanceVerdict._replace()`
(NamedTuple immutable-update) AFTER calling `evaluate_acceptance_gate`, when
the both-empty tie condition fired. Key `"note"`, value the literal string
`"not applicable — no parameter-vector representation"`, present ONLY on the
tie path — absent (key not in dict) for every other path (OOS-inferior,
one-side-empty caller-bug, real-params-unchanged).

Sub-requirement (b) — partial param population forbidden at the three
construction sites — is a design constraint on the FIX itself, not
independently testable beyond what (a)/(d) already pin (a fix that "solves"
this by partially populating params would fail the one-side-empty guard test
below, since real single-key params would produce a non-trivial, non-tied
stability score rather than triggering the intended tie path).

NO HARDCODED PRODUCER VALUES: gate decisions are asserted (ADOPT_CANDIDATE /
KEEP_INCUMBENT strings), never a literal panel_score float, except in the
(d) regression test, which independently re-derives the expected panel score
from the documented formula (normalised L1 distance, PANEL_WEIGHT_* named
constants) rather than calling the module's own private helper — a genuine
golden-fixture-style reference calculation, not circular self-verification.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from acceptance_gate import PANEL_ADOPT_MARGIN_THRESHOLD

# ---------------------------------------------------------------------------
# Shared date-keyed fixtures (mirrors the construction pattern already
# established in test_cull_production_wiring.py / test_r1_n1_honesty.py).
# ---------------------------------------------------------------------------

_DATES = [f"2026-{m:02d}-{d:02d}" for m in range(1, 9) for d in range(1, 11)]

# Predominantly positive, real downside variance (a strictly-nonnegative
# series degenerates the bootstrap t-stat to a conservative 0.0 fallback —
# same lesson as test_r1_n1_honesty.py's fixture-tuning notes).
_STRONG_POSITIVE_RETURNS_PCT = [1.5 if i % 10 != 9 else -0.2 for i in range(len(_DATES))]
_WEAK_NEGATIVE_RETURNS_PCT = [-1.5 if i % 10 != 9 else 0.2 for i in range(len(_DATES))]


@pytest.fixture(scope="module")
def bge():
    import advisors.backtest_gate_engine as _bge  # noqa: PLC0415

    return _bge


def _log_returns_cyclical(level: float = 0.015, dip: float = -0.002) -> dict[str, float]:
    """Date-keyed LOG returns with real downside variance (a constant series
    degenerates the bootstrap Sortino t-stat to a conservative 0.0 fallback —
    same lesson as test_r1_n1_honesty.py's fixture-tuning notes). Used for the
    per-engine e2e tests, which go through run_backtest's daily_returns (log
    scale) rather than daily_returns_pct (percent scale, used by the direct
    evaluate_candidate_batch core tests above)."""
    import math

    return {d: math.log(1.0 + (level if i % 10 != 9 else dip)) for i, d in enumerate(_DATES)}


def _empty_params_candidate(bge, candidate_id: str, returns_pct: list) -> object:
    return bge.BacktestCandidate(
        candidate_id=candidate_id,
        daily_returns_pct=returns_pct,
        candidate_params={},
        incumbent_params={},
        theory_prior_params={},
    )


# ===========================================================================
# (e) CORE gate-level proof: strong-OOS empty-params candidate reaches
# ADOPT_CANDIDATE through the REAL evaluate_candidate_batch (engine-agnostic —
# every one of the three engines' real BacktestCandidate construction sites
# is structurally identical to this fixture).
# ===========================================================================


def test_ac17_core_strong_oos_empty_params_candidate_reaches_adopt_candidate(bge):
    """MUST FAIL pre-fix: today ANY empty-params candidate is structurally
    capped at KEEP_INCUMBENT regardless of OOS strength (panel_score 0.5 <
    incumbent's hardcoded 0.75, unconditionally)."""
    cand = _empty_params_candidate(bge, "ac17-strong", list(_STRONG_POSITIVE_RETURNS_PCT))
    batch = bge.evaluate_candidate_batch([cand], incumbent_oos_alpha=0.0, default_oos_alpha=0.0)
    result = batch.results[0]
    assert result.verdict.decision == "ADOPT_CANDIDATE", (
        f"AC-17 GAP: a strong-OOS empty-params candidate did not reach "
        f"ADOPT_CANDIDATE — got {result.verdict.decision} "
        f"(panel_score={result.verdict.panel_score}, oos_alpha={result.oos_alpha}, "
        f"winner_p_adj={result.winner_p_adj}). This is the exact structural "
        f"unreachability defect AC-17 exists to fix."
    )


# ===========================================================================
# (e, "brake still brakes") — an OOS-INFERIOR empty-params candidate must
# STILL land KEEP_INCUMBENT post-fix — the fix only neutralizes the panel-
# score artifact, it must never bypass the genuine OOS-superiority
# precondition (acceptance_gate.py:257). This holds BOTH pre- and post-fix
# (a real invariant, not a RED assertion) — pinned here so a future
# implementation that neutralizes the panel by ALWAYS adopting (rather than
# tying the panel score specifically) is caught.
# ===========================================================================


def test_ac17_oos_inferior_empty_params_candidate_still_keeps_incumbent(bge):
    """Regression/invariant guard (must hold both before and after AC-17):
    an empty-params candidate that does NOT beat the OOS baselines must never
    reach ADOPT_CANDIDATE, panel-tie or not — the brake still brakes on real
    OOS grounds."""
    cand = _empty_params_candidate(bge, "ac17-weak", list(_WEAK_NEGATIVE_RETURNS_PCT))
    batch = bge.evaluate_candidate_batch([cand], incumbent_oos_alpha=0.0, default_oos_alpha=0.0)
    result = batch.results[0]
    assert result.verdict.decision != "ADOPT_CANDIDATE", (
        f"AC-17 INVARIANT VIOLATED: an OOS-inferior empty-params candidate "
        f"reached {result.verdict.decision}. A correct AC-17 fix neutralizes "
        f"the panel-score ARTIFACT for missing param data — it must never "
        f"bypass the genuine OOS-superiority precondition. "
        f"oos_alpha={result.oos_alpha}"
    )


# ===========================================================================
# (a) Trigger specificity: one-side-empty params is a caller bug and must NOT
# silently trigger the tie neutralization.
# ===========================================================================


def test_ac17_one_side_empty_params_does_not_trigger_the_tie(bge):
    """MUST hold post-fix: when only ONE of candidate_params/incumbent_params
    is empty (the other carries real, non-matching data — a caller bug per
    the plan's own framing), the tie neutralization must NOT fire. Written
    flexibly per the plan's 'guard/assert' wording (either outcome — raising,
    or falling through to the pre-existing asymmetric non-tied computation —
    satisfies 'did not silently trigger the tie'); what is NOT acceptable is
    the malformed one-side-empty input silently producing ADOPT_CANDIDATE via
    the tie path, which would mean the guard checks for `not params` (falsy)
    on EITHER side independently rather than requiring BOTH to be empty."""
    lopsided_cand = bge.BacktestCandidate(
        candidate_id="ac17-lopsided",
        daily_returns_pct=list(_STRONG_POSITIVE_RETURNS_PCT),
        candidate_params={},  # empty
        incumbent_params={"window": 20.0},  # NOT empty — the caller-bug shape
        theory_prior_params={"window": 20.0},
    )
    try:
        batch = bge.evaluate_candidate_batch(
            [lopsided_cand], incumbent_oos_alpha=0.0, default_oos_alpha=0.0
        )
    except Exception:
        # Raising on the malformed caller-bug shape satisfies the "must NOT
        # silently trigger the tie" requirement — an explicit failure is not
        # a silent one.
        return
    result = batch.results[0]
    assert result.verdict.decision != "ADOPT_CANDIDATE", (
        f"AC-17 GUARD VIOLATED: a one-side-empty-params candidate (a caller "
        f"bug per the plan) reached ADOPT_CANDIDATE — the tie neutralization "
        f"must require BOTH candidate_params AND incumbent_params to be "
        f"structurally empty, not fire on either side alone. "
        f"panel_breakdown={result.verdict.panel_breakdown}"
    )


# ===========================================================================
# (d) Regression: real-params (autotuner-style) candidates keep BYTE-IDENTICAL
# panel semantics — independently re-derived from the documented formula
# (normalised L1 distance), not by calling the module's own private helper.
# ===========================================================================


def test_ac17_real_params_candidate_panel_score_unchanged_by_the_fix(bge):
    """Golden-fixture-style regression: hand-verifiable real params (single
    shared key, simple values) where the expected stability sub-score is
    independently computed here from the DOCUMENTED formula
    (1 - normalised_L1_distance, acceptance_gate._compute_parameter_stability_score's
    own docstring) rather than by invoking that function — a fix that
    accidentally touches the real-params code path (forbidden per plan
    sub-requirement b) breaks this test."""
    from acceptance_gate import PANEL_WEIGHT_PRIOR_ANCHOR, PANEL_WEIGHT_STABILITY

    candidate_params = {"window": 16.0}
    incumbent_params = {"window": 20.0}
    theory_prior_params = {"window": 18.0}

    cand = bge.BacktestCandidate(
        candidate_id="ac17-real-params",
        daily_returns_pct=list(_STRONG_POSITIVE_RETURNS_PCT),
        candidate_params=dict(candidate_params),
        incumbent_params=dict(incumbent_params),
        theory_prior_params=dict(theory_prior_params),
    )
    batch = bge.evaluate_candidate_batch([cand], incumbent_oos_alpha=0.0, default_oos_alpha=0.0)
    result = batch.results[0]

    # Independent reference calculation (normalised L1 distance formula,
    # documented in acceptance_gate._compute_parameter_stability_score):
    #   candidate vs incumbent: |16-20|/|20| = 0.20 -> stability = 1 - 0.20 = 0.80
    expected_candidate_stability = 1.0 - abs(16.0 - 20.0) / abs(20.0)
    #   candidate vs theory-prior: |16-18|/|18| ~= 0.1111 -> 1 - 0.1111 = 0.8889
    expected_candidate_prior_anchor = 1.0 - abs(16.0 - 18.0) / abs(18.0)
    expected_candidate_panel = (
        PANEL_WEIGHT_STABILITY * expected_candidate_stability
        + PANEL_WEIGHT_PRIOR_ANCHOR * expected_candidate_prior_anchor
    )

    breakdown = result.verdict.panel_breakdown
    assert breakdown, "expected a non-empty panel_breakdown for a veto-passing candidate"
    assert breakdown["stability"]["candidate_sub_score"] == pytest.approx(
        expected_candidate_stability, abs=1e-9
    ), (
        f"AC-17 REGRESSION: real-params candidate stability sub-score changed. "
        f"Expected {expected_candidate_stability}, got "
        f"{breakdown['stability']['candidate_sub_score']}"
    )
    assert breakdown["candidate_panel_score"] == pytest.approx(
        expected_candidate_panel, abs=1e-9
    ), (
        f"AC-17 REGRESSION: real-params candidate panel_score changed. "
        f"Expected {expected_candidate_panel}, got {breakdown['candidate_panel_score']}"
    )
    # NOTE: incumbent_sub_score is 1.0 here too — but that is PRE-EXISTING,
    # UNCONDITIONAL current behavior (evaluate_candidate_batch hardcodes
    # inc_stability=1.0 for every candidate, empty-params or not — "the
    # incumbent is stable against itself"), not something AC-17 changes. Not
    # asserted here; the candidate-side values above are what AC-17's fix
    # must leave untouched for a real, non-empty candidate.


# ===========================================================================
# (e) End-to-end per-engine — the same defect fixed through all THREE real
# production entry points, per team-lead's explicit spec.
# ===========================================================================


def test_ac17_e2e_asset_swap_operator_evaluate_empty_params_candidate_adopts():
    """MUST FAIL pre-fix: propose_operator_swap's real _evaluate_single_variant
    always builds empty-params candidates — same defect, same fix target."""
    import advisors.asset_swap_engine as ase
    from advisors.composer_backtest_client import BacktestResult

    raw_tree = {"ticker": None, "children": [{"ticker": "AAA", "children": []}]}
    variant_returns_log = _log_returns_cyclical()
    baseline_returns_log = {d: 0.0 for d in _DATES}

    def _side_effect(tree, *, symphony_id="", **kwargs):
        tickers = set()
        stack = [tree]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if node.get("ticker"):
                    tickers.add(node["ticker"])
                for c in node.get("children", []) or []:
                    stack.append(c)
        if tickers == {"CAND0"}:
            return BacktestResult(
                stats={"sharpe": 2.0}, data_warnings=[], daily_returns=dict(variant_returns_log)
            )
        return BacktestResult(
            stats={"sharpe": 0.0}, data_warnings=[], daily_returns=dict(baseline_returns_log)
        )

    with (
        patch.object(ase, "_has_composer_key", return_value=True),
        patch.object(ase, "run_backtest", side_effect=_side_effect),
        patch.object(ase, "database") as mock_db,
    ):
        mock_db.insert_advisor_observation.return_value = 1
        result = ase.propose_operator_swap(
            symphony_id="ac17-swap",
            score_tree=raw_tree,
            incumbent_asset="AAA",
            candidate_asset="CAND0",
            objective=ase.SwapObjective(
                objective_type="reduce_correlation", target_pair=None, measured_value=0.0
            ),
        )

    decision = result.gate_batch.results[0].verdict.decision if result.gate_batch.results else None
    assert decision == "ADOPT_CANDIDATE", (
        f"AC-17 GAP (asset_swap_engine): a strong-OOS empty-params candidate "
        f"through the REAL propose_operator_swap did not reach ADOPT_CANDIDATE; "
        f"got {decision}."
    )


def test_ac17_e2e_logic_change_operator_evaluate_empty_params_candidate_adopts():
    """MUST FAIL pre-fix — identical gap, logic_change_engine sibling.

    R2-2 NOTE: built via REAL symphony_schema constructors ("step"-based
    Composer grammar, param_key="window-days") — the legacy ad-hoc
    {"type": "root", ...} shape is correctly rejected by AC-3's new
    validate_tree guard (not real Composer grammar), which would zero out
    the candidate before it ever reaches the gate and mask this AC-17 check.
    """
    import advisors.logic_change_engine as lce
    from advisors import symphony_schema
    from advisors.composer_backtest_client import BacktestResult

    raw_tree = symphony_schema.make_root(
        "AC17 Test", "daily", [symphony_schema.make_inverse_vol([symphony_schema.make_asset("QQQ")])]
    )
    raw_tree["children"][0]["window-days"] = 20
    variant_returns_log = _log_returns_cyclical()
    baseline_returns_log = {d: 0.0 for d in _DATES}

    def _side_effect(tree, *, symphony_id="", **kwargs):
        children = tree.get("children") or []
        window = (
            children[0].get("window-days") if children and isinstance(children[0], dict) else None
        )
        if window == 16:
            return BacktestResult(
                stats={"sharpe": 2.0}, data_warnings=[], daily_returns=dict(variant_returns_log)
            )
        return BacktestResult(
            stats={"sharpe": 0.0}, data_warnings=[], daily_returns=dict(baseline_returns_log)
        )

    tweak = lce.LogicTweak(
        node_path=["children", 0],
        param_key="window-days",
        old_value=20,
        new_value=16,
        node_description="window-days=20 -> 16 at path [children, 0]",
    )

    with (
        patch.object(lce, "_has_composer_key", return_value=True),
        patch.object(lce, "run_backtest", side_effect=_side_effect),
        patch.object(lce, "database") as mock_db,
    ):
        mock_db.insert_advisor_observation.return_value = 1
        result = lce.propose_operator_logic_change(
            symphony_id="ac17-logic",
            score_tree=raw_tree,
            tweak=tweak,
            objective=lce.LogicChangeObjective(
                objective_type="reduce_drawdown", measured_value=0.0, rationale="test"
            ),
        )

    decision = result.gate_batch.results[0].verdict.decision if result.gate_batch.results else None
    assert decision == "ADOPT_CANDIDATE", (
        f"AC-17 GAP (logic_change_engine): a strong-OOS empty-params candidate "
        f"through the REAL propose_operator_logic_change did not reach "
        f"ADOPT_CANDIDATE; got {decision}."
    )


def test_ac17_e2e_strategy_builder_batch_path_empty_params_candidate_adopts():
    """MUST FAIL pre-fix — the sbe batch path, per team-lead's explicit spec
    ("and the sbe batch path"). Mirrors test_cull_production_wiring.py's
    mocking convention (mock candidate generation + run_backtest, real gate)."""
    import advisors.strategy_builder_engine as sbe
    from advisors.composer_backtest_client import BacktestResult

    variant_returns_log = _log_returns_cyclical()
    baseline_returns_log = {d: 0.0 for d in _DATES}
    # Marker ticker distinguishes our candidate tree from the SPY-benchmark
    # tree sbe sources internally (symphony_schema.make_root("SPY Benchmark",
    # ..., [make_weight_equal([make_asset("SPY")])])) — content-based
    # dispatch (only ticker == SPY), mirroring the established pattern in
    # test_asset_swap_production_wiring.py, not a guess at the "name" key.
    tree = {"step": "root", "name": "ac17-cand", "children": [{"step": "asset", "ticker": "CAND0"}]}
    info = sbe.CandidateInfo(candidate_id="ac17-sb-cand", tree=tree, template_id="T1", params={})

    def _only_ticker(node) -> str | None:
        tickers: set[str] = set()
        stack = [node]
        while stack:
            n = stack.pop()
            if isinstance(n, dict):
                if n.get("ticker"):
                    tickers.add(n["ticker"])
                for c in n.get("children", []) or []:
                    stack.append(c)
        return next(iter(tickers)) if len(tickers) == 1 else None

    def _side_effect(tree_arg, *, symphony_id="", **kwargs):
        if _only_ticker(tree_arg) == "SPY":
            return BacktestResult(
                stats={"sharpe": 0.0}, data_warnings=[], daily_returns=dict(baseline_returns_log)
            )
        return BacktestResult(
            stats={"sharpe": 2.0}, data_warnings=[], daily_returns=dict(variant_returns_log)
        )

    with (
        patch.object(sbe, "_has_composer_key", return_value=True),
        patch.object(sbe, "_generate_candidate_trees", return_value=[info]),
        patch.object(sbe, "run_backtest", side_effect=_side_effect),
        patch.object(sbe, "database") as mock_db,
    ):
        mock_db.insert_advisor_observation.return_value = 1
        result = sbe.propose_strategies(
            objective=sbe.Objective.diversify,
            universe=["AAA", "BBB"],
            screen_config=sbe.ScreenConfig(),
            live_returns=[],
            symphony_id="ac17-sb",
        )

    reasons_and_decisions = [
        (r.rejection_reason, r.verdict.decision) for r in result.gated_batch.results
    ]
    assert any(d == "ADOPT_CANDIDATE" for _, d in reasons_and_decisions), (
        f"AC-17 GAP (strategy_builder_engine): a strong-OOS empty-params "
        f"candidate through the REAL propose_strategies batch path did not "
        f"reach ADOPT_CANDIDATE. Got: {reasons_and_decisions}"
    )


# ===========================================================================
# (e) Badge accessor: get_candidate_alert_new_valid_count() increments once a
# real ADOPT_CANDIDATE survivor is persisted — "the badge's first-ever
# reachable survivor" per team-lead's framing.
# ===========================================================================


def test_ac17_candidate_alert_badge_increments_on_a_real_empty_params_survivor(bge):
    """MUST FAIL pre-fix: since ADOPT_CANDIDATE is currently unreachable for
    any empty-params candidate, the candidate-alert badge accessor has never
    had a real row to count. This test persists a REAL advisor_observations
    row (via database.insert_advisor_observation against the test-isolated
    DB — no mocking) for one of the weekly-suggestion roles with
    verdict='ADOPT_CANDIDATE', derived from a real evaluate_candidate_batch
    call on an empty-params candidate, and confirms the badge counts it."""
    import database

    cand = _empty_params_candidate(bge, "ac17-badge-cand", list(_STRONG_POSITIVE_RETURNS_PCT))
    batch = bge.evaluate_candidate_batch([cand], incumbent_oos_alpha=0.0, default_oos_alpha=0.0)
    verdict = batch.results[0].verdict

    if verdict.decision != "ADOPT_CANDIDATE":
        pytest.fail(
            f"AC-17 GAP: cannot even construct a real ADOPT_CANDIDATE verdict "
            f"pre-fix to persist for the badge test — got {verdict.decision}. "
            f"This IS the AC-17 gap (same root cause as the core test above)."
        )

    database.insert_advisor_observation(
        advisor_role="ASSET_SWAP",
        subject_type="symphony",
        subject_id="ac17-badge-symphony",
        verdict=verdict.decision,
        raw_response={"candidate_id": "ac17-badge-cand"},
        symphony_id="ac17-badge-symphony",
    )

    count = database.get_candidate_alert_new_valid_count()
    assert count >= 1, (
        f"AC-17 GAP: a real ADOPT_CANDIDATE row was persisted for a "
        f"weekly-suggestion role but get_candidate_alert_new_valid_count() "
        f"still returned {count} — the badge accessor itself is unaffected "
        f"by this AC (D-1 read path, database.py:1631), but this proves the "
        f"end-to-end chain from a reachable survivor to the badge now works."
    )


# ===========================================================================
# (c) panel_breakdown N/A marker — contract confirmed by r1-engine (msg
# 082fd634): key "note", literal value "not applicable — no parameter-vector
# representation", present ONLY on the both-empty tie path.
# ===========================================================================

_PANEL_NA_NOTE = "not applicable — no parameter-vector representation"


def test_ac17_panel_breakdown_carries_na_note_on_empty_params_tie(bge):
    """MUST FAIL pre-fix: the key does not exist at all today (nothing stamps
    it — the tie mechanism itself doesn't exist yet)."""
    cand = _empty_params_candidate(bge, "ac17-note-cand", list(_STRONG_POSITIVE_RETURNS_PCT))
    batch = bge.evaluate_candidate_batch([cand], incumbent_oos_alpha=0.0, default_oos_alpha=0.0)
    breakdown = batch.results[0].verdict.panel_breakdown
    assert breakdown.get("note") == _PANEL_NA_NOTE, (
        f"AC-17 GAP: panel_breakdown does not carry the N/A marker on the "
        f"both-empty tie path. Got breakdown={breakdown}"
    )


def test_ac17_panel_breakdown_note_absent_for_oos_inferior_empty_params(bge):
    """The marker must be present whenever the tie condition fires (both
    params empty), REGARDLESS of the eventual decision — an OOS-inferior
    empty-params candidate still hits the tie path structurally (it clears
    every veto, just loses on the separate OOS-superiority comparison). This
    is the precise contract per r1-engine's design (present whenever the tie
    fired, not "only on ADOPT").

    Fixture note: uses the SAME strong-positive returns as the core ADOPT
    test (proven to clear BHY/PBO/SPY) but an artificially high
    incumbent_oos_alpha (1000.0) to force the OOS-inferior KEEP_INCUMBENT
    branch deterministically — an earlier draft tried weak/negative returns
    directly, which instead failed the BHY veto (panel_breakdown={} on veto
    failure, a DIFFERENT branch than intended) rather than reaching the
    OOS-inferior comparison. Forcing via the baseline is more robust than
    hand-tuning the fold-split boundary."""
    cand = _empty_params_candidate(
        bge, "ac17-note-oos-inferior", list(_STRONG_POSITIVE_RETURNS_PCT)
    )
    batch = bge.evaluate_candidate_batch([cand], incumbent_oos_alpha=1000.0, default_oos_alpha=0.0)
    result = batch.results[0]
    assert result.verdict.decision == "KEEP_INCUMBENT", (
        f"self-guard: expected KEEP_INCUMBENT via the OOS-inferior branch "
        f"(vetoes must still pass); got {result.verdict.decision}"
    )
    assert result.verdict.vetoes_passed, (
        "self-guard: this test requires the veto-passed branch (panel_breakdown "
        "non-empty) to isolate the OOS-inferior case from a veto failure"
    )
    breakdown = result.verdict.panel_breakdown
    assert breakdown.get("note") == _PANEL_NA_NOTE, (
        f"AC-17 GAP: an OOS-inferior empty-params candidate still hits the "
        f"both-empty tie condition structurally — the N/A marker must be "
        f"present regardless of the eventual KEEP_INCUMBENT decision. "
        f"Got breakdown={breakdown}"
    )


def test_ac17_panel_breakdown_note_absent_for_real_params_candidate(bge):
    """The marker must be ABSENT for a real-params candidate — the tie
    condition never fires when either param dict is non-empty, so no N/A
    annotation should ever appear on a genuinely-evaluated panel."""
    cand = bge.BacktestCandidate(
        candidate_id="ac17-note-real",
        daily_returns_pct=list(_STRONG_POSITIVE_RETURNS_PCT),
        candidate_params={"window": 16.0},
        incumbent_params={"window": 20.0},
        theory_prior_params={"window": 18.0},
    )
    batch = bge.evaluate_candidate_batch([cand], incumbent_oos_alpha=0.0, default_oos_alpha=0.0)
    breakdown = batch.results[0].verdict.panel_breakdown
    assert "note" not in breakdown, (
        f"AC-17 REGRESSION: a real-params candidate's panel_breakdown carries "
        f"the empty-params N/A marker — it must never fire outside the "
        f"both-empty tie path. Got breakdown={breakdown}"
    )
