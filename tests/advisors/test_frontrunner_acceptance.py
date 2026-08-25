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
                f"fixture row {row['name']!r} accepted but is not tagged 'simplification'"
            )
        else:
            assert "simplification" not in result.tags, (
                f"fixture row {row['name']!r} declined but still carries a 'simplification' tag"
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
                symphony_schema.make_condition_operand("relative-strength-index", "SPY", window=10),
                "gt",
                symphony_schema.make_constant_rhs(80),
            ),
            symphony_schema.make_binary_condition(
                symphony_schema.make_condition_operand("relative-strength-index", "QQQ", window=10),
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


# ---------------------------------------------------------------------------
# Revise 3 (PR #128 /code-review, RULING 3 / F3+F10): _count_tree_nodes must
# descend into a compound condition's clause list, not just the
# "children"-shaped structural nodes -- otherwise a 2-clause and a 12-clause
# compound condition (identical then/else allocation content) count as the
# SAME size, which is dishonest (a 12-rung OR collapse is a materially
# bigger piece of logic than a 2-rung one) and made the ">1" sanity check
# above vacuous -- any nonzero compound condition trivially clears ">1"
# regardless of clause count.
# ---------------------------------------------------------------------------


def test_count_tree_nodes_does_not_descend_compound_condition_clauses_after_ruling_3_reversion():
    """R4-2 (Revise 4): RULING 3 (Revise 3's clause-descent change to the
    SHARED, general-purpose _count_tree_nodes) is REVERTED -- a 12-clause
    compound condition and a 2-clause one, with otherwise IDENTICAL then/
    else allocation content, must now count the SAME via this shared
    counter -- restoring its ORIGINAL children-only semantics (AC-6's
    node_count_delta display metric was never meant to change; the
    clause-aware behavior Revise 3 added here corrupted that display metric
    for every symphony containing a compound condition anywhere, not just
    frontrunner cascades, and was never numerically pinned before this
    reversion). The DEDICATED clause-aware counter Revise 4 introduces
    lives elsewhere and is used ONLY for the overlay SIMPLIFY operand --
    see test_frontrunner_detector_r4_signal_logic.py's own clause-descent
    test for THAT counter's (opposite) behavior."""
    from advisors import frontrunner_builder, symphony_schema

    def _make_if_compound_with_n_clauses(n: int) -> dict:
        conditions = [
            symphony_schema.make_binary_condition(
                symphony_schema.make_condition_operand(
                    "relative-strength-index", f"TICKER_{i:02d}", window=10
                ),
                "gt",
                symphony_schema.make_constant_rhs(80),
            )
            for i in range(n)
        ]
        compound_condition = symphony_schema.make_compound_condition("any", conditions)
        return symphony_schema.make_if_compound(
            compound_condition,
            then_children=[symphony_schema.make_weight_equal([symphony_schema.make_asset("VIXY")])],
            else_children=[
                symphony_schema.make_weight_equal([symphony_schema.make_asset("CORE_ASSET_0001")])
            ],
        )

    tree_2 = _make_if_compound_with_n_clauses(2)
    tree_12 = _make_if_compound_with_n_clauses(12)

    count_2 = frontrunner_builder._count_tree_nodes(tree_2)
    count_12 = frontrunner_builder._count_tree_nodes(tree_12)

    assert count_12 == count_2, (
        f"a 12-clause compound condition counted {count_12} nodes, a "
        f"2-clause one counted {count_2} -- the SHARED _count_tree_nodes "
        f"must NOT descend into a compound condition's clause list after "
        f"R4-2's reversion (both trees have IDENTICAL then/else allocation "
        f"content, so ANY difference means the reversion didn't land, or "
        f"was only partially applied)"
    )


# ---------------------------------------------------------------------------
# R4-2 companion: a real-fixture numeric regression pin on node_count_delta
# itself (team-lead flagged this was never actually pinned before this
# reversion -- only the compound-clause-descent SYMPTOM was tested, never
# the display metric's own numeric value against a known real tree pair).
# ---------------------------------------------------------------------------


def test_node_count_delta_numeric_value_pinned_against_a_real_fixture_pair(facc):
    """A concrete, hand-derivable node_count_delta value for a real
    incumbent/candidate tree pair -- proving the display metric's ARITHMETIC
    (candidate_node_count - incumbent_node_count, via the reverted,
    children-only _count_tree_nodes) is correct, not just "some value that
    happens not to crash". Uses two small, hand-countable trees (never a
    hardcoded literal disconnected from their own structure) so the
    expected value is independently derivable by inspection."""
    from advisors import frontrunner_builder, symphony_schema

    incumbent_tree = symphony_schema.make_root(
        "Incumbent",
        "daily",
        [symphony_schema.make_asset("SPY"), symphony_schema.make_asset("AGG")],
    )
    candidate_tree = symphony_schema.make_root(
        "Candidate",
        "daily",
        [
            symphony_schema.make_asset("SPY"),
            symphony_schema.make_asset("AGG"),
            symphony_schema.make_asset("GLD"),
        ],
    )
    incumbent_count = frontrunner_builder._count_tree_nodes(incumbent_tree)
    candidate_count = frontrunner_builder._count_tree_nodes(candidate_tree)
    # Hand-derivable: candidate has exactly ONE additional asset leaf over
    # incumbent, with everything else structurally identical (same root
    # wrapper shape, same first two assets) -- the delta must be exactly 1,
    # independent of whatever the root wrapper's own internal node count is.
    assert candidate_count - incumbent_count == 1, (
        f"fixture sanity: candidate has exactly one more asset leaf than "
        f"incumbent, so _count_tree_nodes(candidate) - "
        f"_count_tree_nodes(incumbent) must be exactly 1, got "
        f"{candidate_count - incumbent_count!r} -- either the fixture is "
        f"malformed or _count_tree_nodes counts something beyond simple "
        f"per-leaf structure"
    )

    metrics = {"annualized_return": 0.10, "max_drawdown": -0.05}
    result = facc.evaluate_calmar_acceptance(
        metrics,
        metrics,
        incumbent_node_count=incumbent_count,
        candidate_node_count=candidate_count,
        overlay_node_count=None,
        replaced_cascade_node_count=None,
    )
    assert result.node_count_delta == 1, (
        f"expected node_count_delta=1 (candidate_count={candidate_count} - "
        f"incumbent_count={incumbent_count}), got {result.node_count_delta!r}"
    )


# ---------------------------------------------------------------------------
# Revise 3, RULING 2 (F2, PR #128 /code-review): SIMPLIFY additionally
# requires whole-tree no-growth (node_count_delta <= 0) -- restores the
# deleted invariant. A candidate whose delta-scoped ratio passes but whose
# WHOLE SYMPHONY actually grew (e.g. _graft_incumbent_core re-inserting the
# full core into the else branch makes the overall spliced tree bigger even
# though the signal logic shrank) must decline -- "simplification" tagged
# on a bigger tree is an absurdity the ratio alone cannot catch.
# ---------------------------------------------------------------------------


def test_simplify_declines_when_whole_tree_grew_even_though_ratio_passes(facc):
    """A candidate with a genuinely small overlay vs a large replaced
    cascade (ratio easily passes) must still DECLINE if the whole-tree node
    count GREW (candidate_node_count > incumbent_node_count) -- the
    delta-scoped ratio alone cannot see this; RULING 2 adds it as a THIRD,
    independent gate."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=100,
        candidate_node_count=150,  # whole tree GREW (+50)
        overlay_node_count=10,
        replaced_cascade_node_count=200,  # ratio 0.05 -- easily passes
    )
    assert result.accepted is False, (
        "a candidate was accepted via SIMPLIFY despite the whole symphony "
        "growing (candidate_node_count=150 > incumbent_node_count=100) -- "
        "RULING 2's no-growth gate did not fire"
    )
    assert "simplification" not in result.tags


def test_simplify_accepts_when_whole_tree_node_count_delta_is_exactly_zero(facc):
    """Boundary: node_count_delta == 0 (whole tree exactly unchanged) must
    still ACCEPT -- RULING 2's gate is <=0, not a strict <0."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=100,
        candidate_node_count=100,  # delta == 0
        overlay_node_count=10,
        replaced_cascade_node_count=200,
    )
    assert result.accepted is True
    assert "simplification" in result.tags


def test_simplify_accepts_when_whole_tree_shrank_and_ratio_passes(facc):
    """Regression pin: the ordinary, expected case -- whole tree shrank AND
    the delta-scoped ratio passes -- must accept (RULING 2's gate is
    additive, never a NEW way to block a genuinely good candidate)."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=500,
        candidate_node_count=495,  # whole tree shrank
        overlay_node_count=10,
        replaced_cascade_node_count=200,
    )
    assert result.accepted is True
    assert "simplification" in result.tags


def test_simplify_still_declines_on_growth_even_with_a_dramatic_ratio(facc):
    """Adversarial: an extremely favorable delta-scoped ratio (overlay
    massively smaller than the cascade) must NOT override the no-growth
    gate -- RULING 2 is independent of, not traded off against, the
    ratio."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=100,
        candidate_node_count=101,  # grew by just 1 node
        overlay_node_count=1,
        replaced_cascade_node_count=1000,  # ratio 0.001 -- as favorable as it gets
    )
    assert result.accepted is False
    assert "simplification" not in result.tags


# ---------------------------------------------------------------------------
# Revise 3, F9 (PR #128 /code-review): _safe_float at the SIMPLIFY gate
# boundary must accept real finite numbers only -- bool/numeric-string/inf/
# nan must DECLINE, per the existing docstring's own promise ("either
# operand is non-numeric" -> decline). A bool is technically an int subtype
# in Python (float(True) == 1.0) and would otherwise silently coerce into a
# passing ratio; a numeric string would silently coerce too; inf/nan pass a
# bare isinstance(float) check but are never genuine node counts.
# ---------------------------------------------------------------------------


def test_simplify_declines_when_overlay_node_count_is_a_bool(facc):
    """bool is an int subtype in Python -- float(True) == 1.0 would silently
    coerce and could satisfy a favorable ratio. Must decline: a boolean is
    never a genuine node count."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,
        overlay_node_count=True,  # float(True) == 1.0, would trivially pass any ratio
        replaced_cascade_node_count=200,
    )
    assert result.accepted is False
    assert "simplification" not in result.tags


def test_simplify_declines_when_replaced_cascade_node_count_is_a_bool(facc):
    """Symmetric to the overlay-side bool guard -- the cascade side must be
    equally protected."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,
        overlay_node_count=10,
        replaced_cascade_node_count=True,  # float(True) == 1.0
    )
    assert result.accepted is False
    assert "simplification" not in result.tags


def test_simplify_declines_when_overlay_node_count_is_a_numeric_string(facc):
    """A numeric string (a plausible caller bug -- e.g. a value that
    round-tripped through JSON as a string) must decline, never silently
    coerce via float("20")."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,
        overlay_node_count="20",
        replaced_cascade_node_count=200,
    )
    assert result.accepted is False
    assert "simplification" not in result.tags


def test_simplify_declines_when_overlay_node_count_is_infinite(facc):
    """float('inf') passes a naive isinstance(float) check and is not
    caught by a bare '<=0' guard, but is never a genuine node count -- must
    decline."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,
        overlay_node_count=float("inf"),
        replaced_cascade_node_count=200,
    )
    assert result.accepted is False
    assert "simplification" not in result.tags


def test_simplify_declines_when_replaced_cascade_node_count_is_infinite(facc):
    """Symmetric inf guard on the cascade side -- inf trivially satisfies
    'overlay <= cascade * 0.5' for ANY positive overlay, which would
    fabricate an acceptance for literally any candidate."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,
        overlay_node_count=10,
        replaced_cascade_node_count=float("inf"),
    )
    assert result.accepted is False
    assert "simplification" not in result.tags


def test_simplify_declines_when_overlay_node_count_is_nan(facc):
    """NaN comparisons are always False in Python (nan <= x is False, nan >
    x is False) -- must be explicitly caught and declined, not silently
    fall through a comparison chain to an ambiguous result."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,
        overlay_node_count=float("nan"),
        replaced_cascade_node_count=200,
    )
    assert result.accepted is False
    assert "simplification" not in result.tags


# ---------------------------------------------------------------------------
# Revise 3 ADDENDUM A3 (empirically verified, PR #128 review): _safe_float
# catches only (TypeError, ValueError). overlay_node_count=10**400 raises
# OverflowError (a real int too large to represent as a float) -- this
# escapes _safe_float entirely, propagates up through evaluate_calmar_
# acceptance's OWN outer except-all, and returns a BARE _rejected() with
# candidate_sharpe/candidate_volatility/incumbent_calmar/candidate_calmar/
# node_count_delta all NULLED -- even though incumbent/candidate metrics
# were fully valid and these fields were genuinely computable. This is a
# reporting-payload-loss bug, not just a wrong-accept-reject bug: a
# legitimately-computed reject silently loses its audit trail.
# ---------------------------------------------------------------------------


def test_simplify_declines_on_a_huge_int_overlay_without_losing_reporting_fields(facc):
    """overlay_node_count=10**400 must still DECLINE (never accept), but the
    reporting fields that were genuinely computable from valid incumbent/
    candidate metrics (Sharpe, volatility, both Calmar figures, and
    node_count_delta) must survive intact -- NOT fall through to the outer
    except-all's bare _rejected() (which nulls everything). Expected values
    are derived independently from the SAME fixture inputs, never
    hardcoded."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08, sharpe=1.2, volatility=0.15)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08, sharpe=0.9, volatility=0.18)
    expected_calmar = incumbent["annualized_return"] / abs(incumbent["max_drawdown"])
    expected_node_count_delta = 50 - 50

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,
        overlay_node_count=10**400,  # raises OverflowError inside float()
        replaced_cascade_node_count=200,
    )
    assert result.accepted is False, "a 10**400-node overlay must never be accepted"
    assert "simplification" not in result.tags

    assert result.candidate_sharpe == pytest.approx(0.9, abs=1e-9), (
        f"candidate_sharpe was lost (got {result.candidate_sharpe!r}) -- the "
        f"OverflowError escaped to the outer except-all's bare _rejected(), "
        f"nulling reporting fields that were genuinely computable from valid "
        f"metrics"
    )
    assert result.candidate_volatility == pytest.approx(0.18, abs=1e-9), (
        f"candidate_volatility was lost (got {result.candidate_volatility!r})"
    )
    assert result.incumbent_calmar == pytest.approx(expected_calmar, abs=1e-9), (
        f"incumbent_calmar was lost (got {result.incumbent_calmar!r})"
    )
    assert result.candidate_calmar == pytest.approx(expected_calmar, abs=1e-9), (
        f"candidate_calmar was lost (got {result.candidate_calmar!r})"
    )
    assert result.node_count_delta == expected_node_count_delta, (
        f"node_count_delta was lost (got {result.node_count_delta!r}, "
        f"expected {expected_node_count_delta!r})"
    )


# ---------------------------------------------------------------------------
# Revise 4, B2 (review round 3): _safe_node_count_float accepts non-integral
# floats today -- overlay_node_count=0.4 trivially passes the ratio check
# (0.4 <= 200*0.5) even though a real node count is never fractional. Fold
# into the same F9 hardening class: an is_integer() requirement.
# ---------------------------------------------------------------------------


def test_simplify_declines_when_overlay_node_count_is_non_integral(facc):
    """B2: a fractional node count (0.4) is never a genuine node count --
    must decline, never silently pass the ratio check on a value that
    happens to satisfy the arithmetic while being structurally nonsensical."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,
        overlay_node_count=0.4,  # 0.4 <= 200*0.5 -- would trivially pass the ratio
        replaced_cascade_node_count=200,
    )
    assert result.accepted is False
    assert "simplification" not in result.tags


def test_simplify_declines_when_replaced_cascade_node_count_is_non_integral(facc):
    """Symmetric non-integral guard on the cascade side. overlay_node_count
    is deliberately chosen (1) so that IF the fractional value were silently
    allowed through, the ratio would ACCIDENTALLY pass (1 <= 3.7*0.5=1.85) --
    proving this test genuinely exercises the is_integer() guard, not some
    unrelated ratio failure that would decline regardless (verified
    empirically: an earlier draft with overlay_node_count=10 passed
    vacuously, since 10 > 3.7*0.5 fails the ratio on its own, for reasons
    unrelated to the is_integer() guard under test)."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,
        overlay_node_count=1,
        replaced_cascade_node_count=3.7,  # non-integral -- never a genuine count
    )
    assert result.accepted is False
    assert "simplification" not in result.tags


def test_simplify_accepts_a_genuinely_whole_number_float_node_count(facc):
    """Sanity/non-regression companion to the two declines above: a float
    that IS a whole number (e.g. 20.0, the natural type a JSON round-trip
    or an average-of-identical-values computation might produce) is a
    legitimate node count and must NOT be rejected merely for being a
    float -- only genuinely FRACTIONAL values are the adversarial target."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,
        overlay_node_count=20.0,  # whole-number float -- legitimate
        replaced_cascade_node_count=200,
    )
    assert result.accepted is True, (
        f"a whole-number float (20.0) node count was rejected -- the "
        f"is_integer() guard must accept whole-number floats, only reject "
        f"genuinely fractional ones; got tags={result.tags!r}"
    )
    assert "simplification" in result.tags


# ---------------------------------------------------------------------------
# Revise 4, R4-5: RULING 2's own operands (incumbent_node_count/
# candidate_node_count, feeding node_count_delta = int(candidate_node_count)
# - int(incumbent_node_count), the FIRST statement inside the try block)
# get the SAME hardening class as the ratio operands (F9/B2) -- bool/string/
# None must decline SIMPLIFY without nulling the OTHER reporting fields
# (Sharpe/volatility/both Calmar values), never fall through to the outer
# except-all's bare _rejected() the way a bare int(None)/int("x") raise
# would today.
# ---------------------------------------------------------------------------


def test_simplify_declines_when_incumbent_node_count_is_none_without_losing_reporting_fields(
    facc,
):
    """incumbent_node_count=None -- int(None) raises TypeError today,
    escaping to the outer except-all and nulling every reporting field.
    Calmar is exactly PRESERVED (never improved) so 'performance' cannot
    mask this -- the only path to acceptance here is SIMPLIFY, which must
    decline; Sharpe/volatility/Calmar must all survive as genuinely
    computable values from valid incumbent/candidate metrics."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08, sharpe=1.2, volatility=0.15)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08, sharpe=0.9, volatility=0.18)
    expected_calmar = incumbent["annualized_return"] / abs(incumbent["max_drawdown"])

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=None,
        candidate_node_count=50,
        overlay_node_count=10,  # would otherwise satisfy the ratio (10 <= 200*0.5)
        replaced_cascade_node_count=200,
    )
    assert result.accepted is False, "SIMPLIFY must decline with an undeterminable delta"
    assert "simplification" not in result.tags
    assert result.candidate_sharpe == pytest.approx(0.9, abs=1e-9), (
        f"candidate_sharpe was lost (got {result.candidate_sharpe!r}) -- "
        f"incumbent_node_count=None must not null unrelated reporting fields"
    )
    assert result.candidate_volatility == pytest.approx(0.18, abs=1e-9)
    assert result.incumbent_calmar == pytest.approx(expected_calmar, abs=1e-9)
    assert result.candidate_calmar == pytest.approx(expected_calmar, abs=1e-9)


def test_simplify_declines_when_candidate_node_count_is_a_numeric_string_without_losing_reporting_fields(
    facc,
):
    """candidate_node_count='50' -- a plausible caller bug (e.g. a value
    that round-tripped through JSON as a string) -- int('50') actually
    succeeds in Python, so this specifically targets the malformed
    NON-numeric-string case (e.g. a corrupted/truncated value) that would
    raise ValueError and null everything today."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08, sharpe=1.2, volatility=0.15)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08, sharpe=0.9, volatility=0.18)
    expected_calmar = incumbent["annualized_return"] / abs(incumbent["max_drawdown"])

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count="not-a-number",
        overlay_node_count=10,
        replaced_cascade_node_count=200,
    )
    assert result.accepted is False
    assert "simplification" not in result.tags
    assert result.candidate_sharpe == pytest.approx(0.9, abs=1e-9), (
        f"candidate_sharpe was lost (got {result.candidate_sharpe!r}) -- a "
        f"malformed candidate_node_count must not null unrelated reporting "
        f"fields"
    )
    assert result.candidate_volatility == pytest.approx(0.18, abs=1e-9)
    assert result.incumbent_calmar == pytest.approx(expected_calmar, abs=1e-9)
    assert result.candidate_calmar == pytest.approx(expected_calmar, abs=1e-9)


def test_simplify_declines_when_incumbent_node_count_is_a_bool_without_losing_reporting_fields(
    facc,
):
    """bool is an int subtype -- int(True)==1 silently coerces rather than
    raising, so this specifically targets the SILENT-coercion risk class
    (consistent with every other operand's bool guard in this file) rather
    than an exception-escape risk: a bool sneaking through as a node count
    is a genuine caller-bug class even when it doesn't crash. candidate_
    node_count is deliberately chosen (1) so that IF the bool were silently
    coerced to 1, node_count_delta=1-1=0 would ACCIDENTALLY satisfy RULING
    2's <=0 gate -- proving this test genuinely exercises bool-rejection,
    not an unrelated delta failure (verified empirically: an earlier draft
    with candidate_node_count=50 passed vacuously, since 50-1=49 already
    fails the <=0 gate for reasons unrelated to bool-rejection)."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08, sharpe=1.2, volatility=0.15)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08, sharpe=0.9, volatility=0.18)
    expected_calmar = incumbent["annualized_return"] / abs(incumbent["max_drawdown"])

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=True,  # int(True) == 1 -- silent coercion risk
        candidate_node_count=1,
        overlay_node_count=10,
        replaced_cascade_node_count=200,
    )
    assert result.accepted is False
    assert "simplification" not in result.tags
    assert result.candidate_sharpe == pytest.approx(0.9, abs=1e-9)
    assert result.candidate_volatility == pytest.approx(0.18, abs=1e-9)
    assert result.incumbent_calmar == pytest.approx(expected_calmar, abs=1e-9)
    assert result.candidate_calmar == pytest.approx(expected_calmar, abs=1e-9)


# ---------------------------------------------------------------------------
# Revise 4, final pin: an inverted-polarity cascade (fire content genuinely
# on the is-else-condition?==True side -- the real_tree_09/n2oo class) must
# DECLINE SIMPLIFY fail-closed with a WARNING, regardless of what the
# ratio/node_count_delta gates alone would say -- the PRE-EXISTING (not this
# cycle's) _graft_incumbent_core always reads "the incumbent's real
# is-else-condition?==True child" as "the real core to preserve"; for an
# inverted-polarity cascade that child is actually the FIRE content, and the
# genuine core silently gets dropped by the graft, making node_count_delta
# go hugely negative and RULING 2's <=0 gate pass VACUOUSLY. This file does
# NOT characterize or fix the graft itself (a tracked, separate backlog
# item) -- only proves the acceptance layer fails closed on the polarity
# signal.
# ---------------------------------------------------------------------------


def test_simplify_declines_on_inverted_polarity_cascade_regardless_of_ratio_or_delta(facc, caplog):
    """A cascade whose fire content sits on the is-else-condition?==True
    side must DECLINE SIMPLIFY unconditionally -- even when the ratio and
    node_count_delta gates would OTHERWISE both pass (constructed
    deliberately here so this test isolates the polarity check itself, not
    a side effect of some other gate already failing)."""
    import logging

    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    with caplog.at_level(logging.WARNING):
        result = facc.evaluate_calmar_acceptance(
            incumbent,
            candidate,
            incumbent_node_count=200,  # candidate << incumbent -- delta gate would pass
            candidate_node_count=50,
            overlay_node_count=10,  # 10 <= 200*0.5 -- ratio gate would pass
            replaced_cascade_node_count=200,
            fire_is_else_branch=True,  # the inverted-polarity signal
        )

    assert result.accepted is False, (
        "an inverted-polarity cascade must decline SIMPLIFY even though the "
        "ratio and node_count_delta gates would both otherwise pass -- the "
        "graft's core-dropping defect on this exact polarity makes those "
        "gates meaningless here"
    )
    assert "simplification" not in result.tags
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, (
        "an inverted-polarity decline must log a WARNING (team-lead ruling) "
        "-- a silent decline here gives the operator zero signal that a "
        "genuinely-materially-simpler-looking candidate was withheld for a "
        "structural reason unrelated to its own Calmar/size profile"
    )


def test_simplify_reachable_on_normal_polarity_cascade_with_identical_otherwise_inputs(facc):
    """Direct contrast to the inverted-polarity decline above: the SAME
    otherwise-qualifying inputs, with fire_is_else_branch=False (or
    omitted, matching every pre-existing call site's legacy shape), must
    still admit via SIMPLIFY -- proving the new polarity check declines
    ONLY the inverted case, never a blanket new restriction on SIMPLIFY
    generally."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=200,
        candidate_node_count=50,
        overlay_node_count=10,
        replaced_cascade_node_count=200,
        fire_is_else_branch=False,
    )
    assert result.accepted is True
    assert "simplification" in result.tags
