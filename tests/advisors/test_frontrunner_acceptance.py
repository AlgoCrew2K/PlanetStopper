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

    result = facc.evaluate_calmar_acceptance(incumbent, candidate, incumbent_node_count=50, candidate_node_count=50)
    assert result.accepted is True
    assert "performance" in result.tags


def test_improved_calmar_via_lower_drawdown_is_accepted(facc):
    """Candidate has the SAME CAGR but shallower drawdown -> Calmar improves
    -> accept."""
    incumbent = _metrics(annualized_return=0.10, max_drawdown=-0.20)
    candidate = _metrics(annualized_return=0.10, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(incumbent, candidate, incumbent_node_count=50, candidate_node_count=50)
    assert result.accepted is True
    assert "performance" in result.tags


def test_worse_calmar_with_no_simplification_is_rejected(facc):
    """Candidate strictly worse on both CAGR and drawdown, same node count
    (no simplification) -> reject."""
    incumbent = _metrics(annualized_return=0.12, max_drawdown=-0.10)
    candidate = _metrics(annualized_return=0.05, max_drawdown=-0.18)

    result = facc.evaluate_calmar_acceptance(incumbent, candidate, incumbent_node_count=50, candidate_node_count=50)
    assert result.accepted is False


# ---------------------------------------------------------------------------
# AC-7: preserve-Calmar-within-tolerance + simplify accept path
# ---------------------------------------------------------------------------


def test_preserved_calmar_with_material_simplification_is_accepted(facc):
    """Candidate's Calmar is essentially UNCHANGED (within tolerance) but the
    candidate tree has materially fewer nodes than the incumbent (a genuine
    any/all collapse) -> accept, tagged 'simplification'."""
    # Same Calmar exactly (16 / 0.08 == 16 / 0.08) — the cleanest possible
    # "preserved" fixture, no tolerance-boundary ambiguity.
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=500,
        candidate_node_count=40,  # materially smaller
    )
    assert result.accepted is True
    assert "simplification" in result.tags


def test_preserved_calmar_without_material_simplification_is_rejected(facc):
    """Calmar preserved but the candidate is NOT materially simpler (same
    node count, or even larger) -> reject. 'Preserve + simplify' requires
    BOTH conditions, not just the preserve half."""
    incumbent = _metrics(annualized_return=0.16, max_drawdown=-0.08)
    candidate = _metrics(annualized_return=0.16, max_drawdown=-0.08)

    result = facc.evaluate_calmar_acceptance(
        incumbent,
        candidate,
        incumbent_node_count=50,
        candidate_node_count=50,  # NOT simpler
    )
    assert result.accepted is False


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
    """A candidate that improves Calmar AND is materially simpler should
    carry BOTH tags — the two acceptance paths are not mutually exclusive."""
    incumbent = _metrics(annualized_return=0.08, max_drawdown=-0.10)
    candidate = _metrics(annualized_return=0.15, max_drawdown=-0.10)

    result = facc.evaluate_calmar_acceptance(
        incumbent, candidate, incumbent_node_count=500, candidate_node_count=40
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
