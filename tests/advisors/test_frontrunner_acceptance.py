"""RED tests — advisors/frontrunner_acceptance.py (NEW — does not exist yet).

Module under test: advisors.frontrunner_acceptance. The ImportError on the
fixture below is the first RED signal.

CONTRACT SOURCE (feature-plans/frontrunner-builder.md AC-7):
  Acceptance = candidate IMPROVES Calmar (CAGR / max-drawdown) vs. the
  incumbent on out-of-sample folds (profit up and/or drawdown down, net
  Calmar up) AND doesn't worsen max drawdown past a floor; OR preserves
  Calmar within tolerance while MATERIALLY SIMPLIFYING (node/depth
  reduction). Sharpe/vol reported, never gating. Tagged 'performance'
  and/or 'simplification'.

  quantstats' max_drawdown (analytics.compute_quantstats_metrics) is <= 0
  (a negative fraction, e.g. -0.08 = 8% drawdown) — Calmar = CAGR / |MDD|.
  This module must NEVER trust an incoming pre-computed Calmar figure
  blindly (mirrors the project-wide 'never trust incoming oos_metrics'
  posture in build_plan_generator/strategy_builder_engine) — it derives
  Calmar itself from CAGR + MDD inputs so the math is auditable and
  consistent regardless of caller-supplied metrics dict shape.

ADVERSARIAL FOCUS (assert derived values via pytest.approx with an explicit
tolerance + comment — never a bare hardcoded float; every expected number
below is computed FROM the fixture inputs at test time, not typed in as a
magic literal):
  - Calmar math: CAGR/MaxDD arithmetic matches a hand-computed expectation
    derived from the SAME inputs the function receives
  - improve-Calmar accept path (profit up and/or drawdown down -> net
    Calmar up)
  - preserve-Calmar-within-tolerance + simplify accept path (node/depth
    reduction)
  - drawdown-floor guard: even an improved-Calmar candidate is rejected if
    its own max drawdown breaches an absolute floor
  - Sharpe/vol reported but NEVER gating (a candidate with terrible Sharpe
    but genuinely better Calmar still accepts; a candidate with great
    Sharpe but worse Calmar and no simplification still rejects)
  - tagging: 'performance' / 'simplification' / both, reflecting which
    path(s) admitted the candidate
"""

from __future__ import annotations

import math

import pytest

# ---------------------------------------------------------------------------
# Module-under-test import guard — RED until advisors/frontrunner_acceptance.py exists.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def facc():
    """Import and return the frontrunner_acceptance module (RED until it exists)."""
    import advisors.frontrunner_acceptance as _facc  # noqa: PLC0415

    return _facc


# ---------------------------------------------------------------------------
# Metrics-dict fixture builder — mirrors the shape of
# analytics.compute_quantstats_metrics's output (the real, live producer of
# these fields elsewhere in the codebase), never a hand-rolled ad-hoc shape.
# max_drawdown is <= 0 per that function's own contract.
# ---------------------------------------------------------------------------


def _metrics(
    *,
    annualized_return: float,
    max_drawdown: float,
    sharpe: float = 0.0,
    volatility: float = 0.0,
) -> dict:
    assert max_drawdown <= 0, "max_drawdown fixture inputs must be <= 0 (quantstats convention)"
    return {
        "annualized_return": annualized_return,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "volatility": volatility,
    }


# ---------------------------------------------------------------------------
# Calmar math — derived, never hardcoded beyond the fixture's own arithmetic.
# ---------------------------------------------------------------------------


def test_calmar_ratio_matches_cagr_over_absolute_max_drawdown(facc):
    """Calmar = CAGR / |MaxDD|, computed from the SAME inputs passed in —
    tolerance is tight (1e-9) because this is exact floating-point division,
    not a statistically-estimated quantity."""
    cagr = 0.14
    mdd = -0.07
    expected_calmar = cagr / abs(mdd)  # derived at test time, not a literal

    result = facc.compute_calmar(cagr, mdd)
    assert result == pytest.approx(expected_calmar, abs=1e-9)


def test_calmar_ratio_is_none_when_max_drawdown_is_zero(facc):
    """Division by zero (a candidate with literally zero drawdown, e.g. an
    all-cash placeholder) must degrade to None, never raise ZeroDivisionError
    or return inf."""
    result = facc.compute_calmar(0.05, 0.0)
    assert result is None


@pytest.mark.parametrize(
    ("cagr", "mdd"),
    [(0.20, -0.05), (0.02, -0.30), (-0.10, -0.15)],
)
def test_calmar_ratio_property_matches_hand_derivation_across_inputs(facc, cagr, mdd):
    """Property test across several (cagr, mdd) pairs — each expected value
    is derived from the SAME formula at test time (never hardcoded), so this
    is really asserting compute_calmar has no hidden sign flip / scale bug
    across a range of realistic magnitudes."""
    expected = cagr / abs(mdd)
    result = facc.compute_calmar(cagr, mdd)
    assert result == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# AC-7: improve-Calmar accept path
# ---------------------------------------------------------------------------


def test_improved_calmar_via_higher_profit_is_accepted(facc):
    """Candidate has higher CAGR at the SAME drawdown as the incumbent ->
    Calmar strictly improves -> accept."""
    incumbent = _metrics(annualized_return=0.08, max_drawdown=-0.10)
    candidate = _metrics(annualized_return=0.15, max_drawdown=-0.10)

    incumbent_calmar = incumbent["annualized_return"] / abs(incumbent["max_drawdown"])
    candidate_calmar = candidate["annualized_return"] / abs(candidate["max_drawdown"])
    assert candidate_calmar > incumbent_calmar  # sanity: fixture actually improves

    result = facc.evaluate_calmar_acceptance(
        incumbent, candidate, incumbent_node_count=50, candidate_node_count=50
    )
    assert result.accepted is True
    assert "performance" in result.tags


def test_improved_calmar_via_lower_drawdown_is_accepted(facc):
    """Candidate has the SAME CAGR but shallower drawdown -> Calmar improves
    -> accept."""
    incumbent = _metrics(annualized_return=0.10, max_drawdown=-0.20)
    candidate = _metrics(annualized_return=0.10, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent, candidate, incumbent_node_count=50, candidate_node_count=50
    )
    assert result.accepted is True
    assert "performance" in result.tags


def test_worse_calmar_with_no_simplification_is_rejected(facc):
    """Candidate strictly worse on both CAGR and drawdown, same node count
    (no simplification) -> reject."""
    incumbent = _metrics(annualized_return=0.12, max_drawdown=-0.10)
    candidate = _metrics(annualized_return=0.05, max_drawdown=-0.18)

    result = facc.evaluate_calmar_acceptance(
        incumbent, candidate, incumbent_node_count=50, candidate_node_count=50
    )
    assert result.accepted is False


# ---------------------------------------------------------------------------
# AC-7: preserve-Calmar-within-tolerance + simplify accept path
# ---------------------------------------------------------------------------


def test_preserved_calmar_with_material_simplification_is_accepted(facc):
    """Candidate's Calmar is essentially UNCHANGED (within tolerance) and the
    OVERLAY (the small generated node) is materially fewer nodes than the
    REPLACED CASCADE (the incumbent subtree it swaps out) -> accept, tagged
    'simplification'.

    DE-FR-SIMPLIFY-001 (AC-1/AC-3): the whole-tree incumbent_node_count/
    candidate_node_count are deliberately left at a near-100%-of-incumbent
    ratio (500 vs 495, ~99%) — a spliced candidate is ALWAYS ~98-100% of the
    incumbent's whole-tree size (the defect this fix closes), so if this test
    passed via the whole-tree operands it would be proving the OLD broken
    behavior, not the fix. Only the delta-scoped overlay_node_count (20) vs
    replaced_cascade_node_count (200) — the real objects the builder has in
    scope — can make this fixture reach the SIMPLIFY tag."""
    # Same Calmar exactly (16 / 0.08 == 16 / 0.08) — the cleanest possible
    # "preserved" fixture, no tolerance-boundary ambiguity.
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=500,
        candidate_node_count=495,  # whole-tree: ~99% of incumbent — NOT simpler
        overlay_node_count=20,
        replaced_cascade_node_count=200,  # delta-scoped: 10% — genuinely simpler
    )
    assert result.accepted is True
    assert "simplification" in result.tags


def test_preserved_calmar_without_material_simplification_is_rejected(facc):
    """Calmar preserved but the OVERLAY is NOT materially simpler than the
    REPLACED CASCADE it swaps out (same delta-scoped count, or even larger)
    -> reject. 'Preserve + simplify' requires BOTH conditions, not just the
    preserve half."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,  # whole-tree: NOT simpler either
        overlay_node_count=50,
        replaced_cascade_node_count=50,  # delta-scoped: NOT simpler
    )
    assert result.accepted is False


def test_simplify_clause_ignores_whole_tree_counts_when_delta_scoped_operands_given(
    facc,
):
    """DE-FR-SIMPLIFY-001 AC-1 adversarial pin: the whole-tree counts must
    NEVER be what decides the SIMPLIFY tag once the delta-scoped operands are
    supplied — even when the whole-tree ratio would (under the OLD, broken
    math) have looked like a huge simplification. Fixture is deliberately
    FLIPPED: incumbent_node_count=500/candidate_node_count=40 (whole-tree
    ratio 0.08 — the old clause would have accepted on this alone) paired
    with overlay_node_count=180/replaced_cascade_node_count=200 (delta-scoped
    ratio 0.90 — genuinely NOT materially simpler). The correct, fixed
    behavior is REJECT — proving the clause reads the new operands, not the
    whole-tree ones, regardless of which one is more flattering."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=500,
        candidate_node_count=40,  # whole-tree ratio 0.08 -- would old-accept
        overlay_node_count=180,
        replaced_cascade_node_count=200,  # delta-scoped ratio 0.90 -- correct-reject
    )
    assert result.accepted is False, (
        "the SIMPLIFY clause admitted a candidate on the flattering WHOLE-TREE "
        "ratio (0.08) while the real delta-scoped ratio (0.90) says it is NOT "
        "materially simpler -- the clause is still reading the wrong operands"
    )
    assert "simplification" not in result.tags


def test_node_count_delta_is_reported_on_the_result(facc):
    """The result must carry the raw node-count delta so the Advisor-tab card
    can render it (AC-8: 'incumbent-vs-candidate ... node-count deltas')."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent, candidate, incumbent_node_count=500, candidate_node_count=40
    )
    # Derived from the SAME inputs passed in — never a hardcoded delta.
    expected_delta = 40 - 500
    assert result.node_count_delta == expected_delta


# ---------------------------------------------------------------------------
# AC-7: drawdown-floor guard
# ---------------------------------------------------------------------------


def test_improved_calmar_is_still_rejected_past_the_drawdown_floor(facc):
    """Even a Calmar-improving candidate must be rejected if its OWN absolute
    max drawdown breaches a floor (AC-7: 'doesn't worsen max drawdown past a
    floor') — a candidate can't buy an improved ratio via extreme leverage
    that also happens to post a high raw CAGR at a catastrophic drawdown."""
    incumbent = _metrics(annualized_return=0.05, max_drawdown=-0.10)
    # Candidate's Calmar is numerically HIGHER (0.90/0.60 = 1.5 > 0.05/0.10 = 0.5)
    # but its raw drawdown (-0.60 = 60%) breaches any sane floor.
    candidate = _metrics(annualized_return=0.90, max_drawdown=-0.60)

    candidate_calmar = candidate["annualized_return"] / abs(candidate["max_drawdown"])
    incumbent_calmar = incumbent["annualized_return"] / abs(incumbent["max_drawdown"])
    assert candidate_calmar > incumbent_calmar  # sanity: Calmar genuinely improved

    result = facc.evaluate_calmar_acceptance(
        incumbent, candidate, incumbent_node_count=50, candidate_node_count=50
    )
    assert result.accepted is False, (
        "a candidate with a 60% max drawdown was accepted despite an improved "
        "Calmar ratio — the drawdown floor guard did not fire"
    )


# ---------------------------------------------------------------------------
# AC-7: Sharpe/vol reported, never gating
# ---------------------------------------------------------------------------


def test_terrible_sharpe_does_not_block_an_otherwise_accepted_candidate(facc):
    """A candidate with a genuinely improved Calmar but a terrible (very
    negative) Sharpe must still be ACCEPTED — Sharpe is reported, never
    gating, per AC-7."""
    incumbent = _metrics(annualized_return=0.08, max_drawdown=-0.10, sharpe=1.2)
    candidate = _metrics(annualized_return=0.15, max_drawdown=-0.10, sharpe=-3.5)

    result = facc.evaluate_calmar_acceptance(
        incumbent, candidate, incumbent_node_count=50, candidate_node_count=50
    )
    assert result.accepted is True


def test_great_sharpe_does_not_rescue_an_otherwise_rejected_candidate(facc):
    """The mirror-image case: a candidate with an excellent Sharpe but a
    genuinely WORSE Calmar and no simplification must still be REJECTED —
    Sharpe cannot override the Calmar/simplification acceptance gate."""
    incumbent = _metrics(annualized_return=0.12, max_drawdown=-0.10, sharpe=0.5)
    candidate = _metrics(annualized_return=0.05, max_drawdown=-0.18, sharpe=4.0)

    result = facc.evaluate_calmar_acceptance(
        incumbent, candidate, incumbent_node_count=50, candidate_node_count=50
    )
    assert result.accepted is False


def test_sharpe_and_volatility_are_present_on_the_result_for_reporting(facc):
    """Sharpe/vol must be carried through onto the result (reported, not
    gating) so the Advisor-tab card can display them alongside Calmar."""
    incumbent = _metrics(annualized_return=0.08, max_drawdown=-0.10, sharpe=1.2, volatility=0.15)
    candidate = _metrics(annualized_return=0.15, max_drawdown=-0.10, sharpe=0.9, volatility=0.18)

    result = facc.evaluate_calmar_acceptance(
        incumbent, candidate, incumbent_node_count=50, candidate_node_count=50
    )
    # Whatever the exact result shape, the candidate's own reported sharpe/vol
    # must be discoverable (not silently dropped) — never hardcode which
    # exact attribute name beyond what the module documents; check presence
    # via a reasonably-named field.
    assert getattr(result, "candidate_sharpe", None) == pytest.approx(0.9, abs=1e-9)
    assert getattr(result, "candidate_volatility", None) == pytest.approx(0.18, abs=1e-9)


# ---------------------------------------------------------------------------
# AC-7: tagging — 'performance' and/or 'simplification'
# ---------------------------------------------------------------------------


def test_candidate_that_both_improves_and_simplifies_is_tagged_both(facc):
    """A candidate that improves Calmar AND is materially simpler (by the
    delta-scoped overlay-vs-replaced-cascade operands) should carry BOTH tags
    — the two acceptance paths are not mutually exclusive."""
    incumbent = _metrics(annualized_return=0.08, max_drawdown=-0.10)
    candidate = _metrics(annualized_return=0.15, max_drawdown=-0.10)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=500,
        candidate_node_count=495,  # whole-tree: ~99% -- irrelevant to this test
        overlay_node_count=20,
        replaced_cascade_node_count=200,  # delta-scoped: genuinely simpler
    )
    assert result.accepted is True
    assert "performance" in result.tags
    assert "simplification" in result.tags


def test_rejected_candidate_carries_no_acceptance_tags(facc):
    """A rejected candidate must not carry 'performance'/'simplification'
    tags (those describe WHY something was accepted, not applicable to a
    reject)."""
    incumbent = _metrics(annualized_return=0.12, max_drawdown=-0.10)
    candidate = _metrics(annualized_return=0.05, max_drawdown=-0.18)

    result = facc.evaluate_calmar_acceptance(
        incumbent, candidate, incumbent_node_count=50, candidate_node_count=50
    )
    assert result.accepted is False
    assert result.tags == set() or result.tags == []


# ---------------------------------------------------------------------------
# Edge cases: missing / None metrics fail closed (never fabricate accept)
# ---------------------------------------------------------------------------


def test_missing_incumbent_metric_fails_closed_to_reject(facc):
    """A None/missing annualized_return or max_drawdown on either side must
    degrade to a REJECT (fail-closed), never crash and never accept on
    incomplete data — mirrors the project-wide 'None metric -> screen fails
    closed' convention (strategy_builder_engine._passes_screens)."""
    incumbent = _metrics(annualized_return=0.08, max_drawdown=-0.10)
    incumbent["annualized_return"] = None
    candidate = _metrics(annualized_return=0.15, max_drawdown=-0.10)

    result = facc.evaluate_calmar_acceptance(
        incumbent, candidate, incumbent_node_count=50, candidate_node_count=50
    )
    assert result.accepted is False


def test_never_raises_on_malformed_metrics_dict(facc):
    """D-1-style never-raises contract: a malformed/empty metrics dict must
    degrade to a rejected result, never propagate an exception up to the
    caller (the builder orchestration loop must not crash mid-batch)."""
    result = facc.evaluate_calmar_acceptance(
        {}, {}, incumbent_node_count=50, candidate_node_count=50
    )
    assert result.accepted is False


# ---------------------------------------------------------------------------
# DE-FR-SIMPLIFY-001 AC-4: IMPROVE path is byte-unchanged by the new kwargs.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra_kwargs",
    [
        pytest.param({}, id="new_kwargs_omitted"),
        pytest.param(
            {"overlay_node_count": 400, "replaced_cascade_node_count": 10},
            id="new_kwargs_present_but_overlay_bigger_than_cascade",
        ),
    ],
)
def test_improve_only_candidate_is_tagged_performance_regardless_of_new_kwarg_presence(
    facc, extra_kwargs
):
    """AC-4: a candidate whose Calmar genuinely improves (and whose whole-tree
    node counts do NOT indicate simplification) must be tagged {'performance'}
    only, and this must be IDENTICAL whether the new overlay_node_count/
    replaced_cascade_node_count kwargs are omitted entirely or supplied with
    a value that itself could never admit SIMPLIFY (overlay bigger than the
    cascade it replaces) — the IMPROVE branch structurally never reads these
    operands, so its outcome is byte-unchanged either way."""
    incumbent = _metrics(annualized_return=0.08, max_drawdown=-0.10)
    candidate = _metrics(annualized_return=0.15, max_drawdown=-0.10)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,  # equal -- no whole-tree simplification signal
        **extra_kwargs,
    )
    assert result.accepted is True
    assert result.tags == {"performance"}


def test_legacy_callsite_without_new_kwargs_never_admits_via_simplify(facc):
    """AC-5/Edge-Cases fail-closed pin: a caller using the OLD 4-argument
    invocation shape (incumbent_metrics, candidate_metrics,
    incumbent_node_count, candidate_node_count — no overlay_node_count/
    replaced_cascade_node_count at all) must NEVER admit via the SIMPLIFY
    path, even when the whole-tree node counts alone look like a dramatic
    reduction (500 -> 40, the shape the OLD broken clause would have
    accepted on). The fail-closed default means an un-migrated caller
    degrades to 'simplification structurally unreachable', not 'silently
    keeps the old bug's behavior' -- either way this fixture must REJECT."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)  # Calmar preserved

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=500,
        candidate_node_count=40,
        # overlay_node_count / replaced_cascade_node_count deliberately OMITTED
    )
    assert result.accepted is False, (
        "a legacy call site omitting the new overlay/replaced-cascade kwargs "
        "was admitted via SIMPLIFY -- the fail-closed default did not fire"
    )
    assert "simplification" not in result.tags


# ---------------------------------------------------------------------------
# DE-FR-SIMPLIFY-001 AC-5: SIMPLIFY-clause truth table -- boundary + guards.
# ---------------------------------------------------------------------------


def test_simplify_declines_when_replaced_cascade_node_count_is_zero(facc):
    """A replaced-cascade count of exactly 0 must decline (no
    ZeroDivisionError, never a fabricated acceptance) -- mirrors the existing
    'incumbent_node_count > 0' guard shape already present for the whole-tree
    ratio."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,
        overlay_node_count=1,
        replaced_cascade_node_count=0,
    )
    assert result.accepted is False
    assert "simplification" not in result.tags


def test_simplify_declines_when_overlay_node_count_is_zero(facc):
    """team-lead RED addition (post-plan-approval, reviewing fps-impl's
    implementation plan): overlay_node_count=0 with a valid, positive
    replaced_cascade_node_count must DECLINE -- even though the naive ratio
    math trivially passes (0 <= cascade*0.5 is always true for any
    positive cascade count, and 0 also passes a bare non-negative/
    <=cascade guard). A genuine generated overlay can never be a literal
    zero-node tree; 0 can ONLY arise from a counting-failure silently
    degrading to a falsy default, and treating that as 'the smallest
    possible simplification' would fabricate an acceptance from a defect,
    not a real candidate. 0 must be handled exactly like None/absent."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,
        overlay_node_count=0,
        replaced_cascade_node_count=200,
    )
    assert result.accepted is False, (
        "overlay_node_count=0 was accepted via SIMPLIFY -- a zero-node "
        "overlay is not a legitimate 'maximally simple' candidate, it is "
        "indistinguishable from a counting failure and must fail closed "
        "exactly like None/absent"
    )
    assert "simplification" not in result.tags


def test_simplify_declines_when_replaced_cascade_node_count_is_none(facc):
    """replaced_cascade_node_count=None (explicit) must decline -- same
    fail-closed contract as the kwarg being omitted entirely."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,
        overlay_node_count=20,
        replaced_cascade_node_count=None,
    )
    assert result.accepted is False
    assert "simplification" not in result.tags


def test_simplify_declines_when_overlay_node_count_is_none(facc):
    """overlay_node_count=None (cascade side supplied, overlay side is not)
    must decline -- both operands are required, not just one."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,
        overlay_node_count=None,
        replaced_cascade_node_count=200,
    )
    assert result.accepted is False
    assert "simplification" not in result.tags


def test_simplify_declines_when_overlay_node_count_exceeds_replaced_cascade_node_count(
    facc,
):
    """A candidate overlay literally BIGGER than the cascade it replaces
    cannot be 'materially simplifying' under any reading -- must decline."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,
        overlay_node_count=250,
        replaced_cascade_node_count=200,
    )
    assert result.accepted is False
    assert "simplification" not in result.tags


def test_simplify_declines_and_never_raises_on_negative_overlay_node_count(facc):
    """D-1 house style (Edge Cases: 'zero-node/malformed subtree inputs ->
    decline, never raise'): a negative overlay_node_count is malformed input
    -- must degrade to a reject, never raise, never fabricate an accept."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,
        overlay_node_count=-5,
        replaced_cascade_node_count=200,
    )
    assert result.accepted is False
    assert "simplification" not in result.tags


def test_simplify_declines_and_never_raises_on_non_numeric_overlay_node_count(facc):
    """D-1: a non-numeric overlay_node_count (a caller bug passing a dict/str
    instead of an int) must degrade to a reject, never raise -- the whole
    evaluate_calmar_acceptance contract is never-raises regardless of which
    operand is malformed."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,
        overlay_node_count="not-a-number",
        replaced_cascade_node_count=200,
    )
    assert result.accepted is False


def test_simplify_ratio_boundary_matches_golden_fixture(facc):
    """Golden-fixture truth table (project rule: every math-adjacent change
    gets a golden-fixture test) -- tests/fixtures/math/
    frontrunner_simplify_ratio_boundary.json pins the ratio boundary (at/
    above/below MATERIAL_SIMPLIFICATION_MAX_RATIO=0.50) against REAL calls
    into evaluate_calmar_acceptance, including a deliberately near-100%
    whole-tree node-count pair on every row (proving the old whole-tree
    comparison could never have produced these accept rows)."""
    import json
    import pathlib

    fixture_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "tests"
        / "fixtures"
        / "math"
        / "frontrunner_simplify_ratio_boundary.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    incumbent = _metrics(
        annualized_return=fixture["annualized_return"],
        max_drawdown=fixture["max_drawdown"],
    )
    candidate = _metrics(
        annualized_return=fixture["annualized_return"],
        max_drawdown=fixture["max_drawdown"],
    )

    for row in fixture["rows"]:
        result = facc.evaluate_calmar_acceptance(
            incumbent,
            candidate,
            incumbent_node_count=fixture["whole_tree_incumbent_node_count"],
            candidate_node_count=fixture["whole_tree_candidate_node_count"],
            overlay_node_count=row["overlay_node_count"],
            replaced_cascade_node_count=row["replaced_cascade_node_count"],
        )
        assert result.accepted is row["expected_accepted"], (
            f"fixture row {row['name']!r} "
            f"(overlay={row['overlay_node_count']}, "
            f"cascade={row['replaced_cascade_node_count']}) expected "
            f"accepted={row['expected_accepted']}, got {result.accepted} "
            f"(tags={result.tags!r})"
        )
        if row["expected_accepted"]:
            assert "simplification" in result.tags, (
                f"fixture row {row['name']!r} accepted but is not tagged "
                f"'simplification'"
            )
        else:
            assert "simplification" not in result.tags, (
                f"fixture row {row['name']!r} declined but still carries "
                f"a 'simplification' tag"
            )


# ---------------------------------------------------------------------------
# team-lead plan-approval addition: at least one case must use an
# if_compound-shaped overlay as the counted overlay operand (Edge Cases:
# "compound/if_compound overlays -> counted like any node subtree, no
# special-casing").
# ---------------------------------------------------------------------------


def test_simplify_accepts_a_real_if_compound_overlay_counted_via_the_real_counter(facc):
    """A genuine if_compound-shaped overlay (the SAME compound-condition
    shape the Fable generator can actually produce, via
    symphony_schema.make_if_compound/make_compound_condition/
    make_binary_condition) must be counted like any other node subtree -- no
    special-casing that under/over-counts a compound condition block. Builds
    a REAL compound tree, counts it via the REAL
    frontrunner_builder._count_tree_nodes (the single shared counter, AC-2),
    and feeds that REAL count into evaluate_calmar_acceptance as
    overlay_node_count -- never a hand-typed literal standing in for what the
    counter would produce."""
    from advisors import frontrunner_builder, symphony_schema

    # A 2-leaf ANY compound condition (mirrors a genuine Fable-generated
    # if_compound overlay shape) driving a small then/else allocation --
    # small in absolute terms but its own node count is DERIVED below, never
    # hardcoded.
    compound_condition = symphony_schema.make_compound_condition(
        "any",
        [
            symphony_schema.make_binary_condition(
                symphony_schema.make_condition_operand(
                    "relative-strength-index", "SPY", window=10
                ),
                "gt",
                symphony_schema.make_constant_rhs(80),
            ),
            symphony_schema.make_binary_condition(
                symphony_schema.make_condition_operand(
                    "relative-strength-index", "QQQ", window=10
                ),
                "gt",
                symphony_schema.make_constant_rhs(80),
            ),
        ],
    )
    overlay_tree = symphony_schema.make_if_compound(
        compound_condition,
        then_children=[symphony_schema.make_weight_equal([symphony_schema.make_asset("VIXY")])],
        else_children=[
            symphony_schema.make_weight_equal([symphony_schema.make_asset("CORE_ASSET_0001")])
        ],
    )
    # Derived from the REAL counter, not a hand-typed literal.
    overlay_node_count = frontrunner_builder._count_tree_nodes(overlay_tree)
    assert overlay_node_count > 1, (
        "sanity: a compound if-node with then/else children must count as "
        "more than a single node, or this fixture proves nothing"
    )

    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=500,
        candidate_node_count=495,  # whole-tree: irrelevant, ~99%
        overlay_node_count=overlay_node_count,
        # A large replaced cascade so the REAL compound-tree count (however
        # many nodes it actually is) still clears the 0.50 ratio comfortably
        # -- the point is that the compound tree's REAL count flows through,
        # not that this specific boundary is being probed here.
        replaced_cascade_node_count=overlay_node_count * 10,
    )
    assert result.accepted is True, (
        f"a real if_compound overlay (counted at {overlay_node_count} nodes "
        f"via the shared _count_tree_nodes counter) replacing a cascade 10x "
        f"its size was not accepted via SIMPLIFY -- compound overlays must "
        f"count like any other node subtree"
    )
    assert "simplification" in result.tags
