"""RED tests -- Retirement Recommender adversarial property invariants.

Module under test: advisors.retirement_recommender (NEW).

hypothesis (6.152.6 confirmed available in this repo -- see
tests/advisors/test_build_plan_generator_property.py) drives the pure-function
properties (select_retirement_candidate, evaluate_structural_redundancy_gate,
compute_composite_scores) where a fixture is cheap to generate. The two
orchestrator-level properties (uncorrelated-symphony-never-creates-a-rec,
determinism-under-reordering) use deterministic multi-case fixtures instead --
a real seeded state DB per hypothesis example would be prohibitively slow, and
the codebase's own precedent for expensive integration properties is
deterministic multi-case construction, not hypothesis.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.advisors._retirement_recommender_reference import seed_state_db, trading_days


@pytest.fixture(scope="module")
def rr():
    import advisors.retirement_recommender as _rr  # noqa: PLC0415

    return _rr


def _metrics(a, s, so, m, c):
    return {
        "total_return": 0.0,
        "annualized_return": a,
        "sharpe": s,
        "sortino": so,
        "max_drawdown": m,
        "calmar": c,
        "win_rate": 0.5,
        "volatility": 0.1,
    }


# ---------------------------------------------------------------------------
# Property: no recommendation ever targets an ineligible symphony as candidate
#
# PR-level /code-review Finding 1: the ORIGINAL version of this property
# drew composite_a/composite_b and eligible_a/eligible_b as fully INDEPENDENT
# hypothesis strategies -- meaning eligible=False could pair with any random
# REAL float composite, an input compute_composite_scores itself never
# produces (there, composite is None IFF eligible is False, always) and
# hypothesis's st.floats() strategy can NEVER draw None. That made the
# property structurally incapable of ever exercising
# select_retirement_candidate's `comp_a is None`/`comp_b is None` branches --
# 100% vacuous with respect to Finding 1's actual bug. Fixed: a single
# correlated strategy per side that always pairs eligible=True with a real
# float and eligible=False with composite=None, matching the real
# compute_composite_scores invariant exactly.
# ---------------------------------------------------------------------------

_composite_floats = st.floats(
    min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False
)

# (composite, eligible) pairs matching compute_composite_scores' real
# invariant: eligible entries always carry a real float, ineligible entries
# always carry composite=None. Never the inconsistent combination.
_eligible_or_ineligible_composite = st.one_of(
    _composite_floats.map(lambda c: (c, True)),
    st.just((None, False)),
)


class TestPropertyIneligibleNeverCandidate:
    @given(
        side_a=_eligible_or_ineligible_composite,
        side_b=_eligible_or_ineligible_composite,
    )
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=200)
    def test_ineligible_symphony_never_returned_as_candidate(self, rr, side_a, side_b):
        composite_a, eligible_a = side_a
        composite_b, eligible_b = side_b
        scores = {
            "sym-a": rr.CompositeScore(
                composite=composite_a,
                metrics=_metrics(0.1, 0.1, 0.1, -0.1, 0.1),
                eligible=eligible_a,
            ),
            "sym-b": rr.CompositeScore(
                composite=composite_b,
                metrics=_metrics(0.1, 0.1, 0.1, -0.1, 0.1),
                eligible=eligible_b,
            ),
        }
        candidate = rr.select_retirement_candidate("sym-a", "sym-b", scores)
        if candidate is not None:
            assert scores[candidate].eligible is True, (
                f"select_retirement_candidate returned {candidate!r} as the "
                f"candidate, but scores[{candidate!r}].eligible is False -- an "
                "ineligible symphony must never be the candidate, for ANY "
                "composite/eligibility combination."
            )

    @given(
        side_a=_eligible_or_ineligible_composite,
        side_b=_eligible_or_ineligible_composite,
    )
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=200)
    def test_any_ineligible_side_yields_no_candidate_at_all_fail_closed(self, rr, side_a, side_b):
        """PR-level /code-review Finding 1 (BLOCKING), PM ruling: fail-CLOSED
        overrides the original 'may still be the keep member' design -- if
        EITHER side is ineligible (composite is None), the pair must yield
        NO candidate at all, never a fallback nominating the eligible side."""
        composite_a, eligible_a = side_a
        composite_b, eligible_b = side_b
        scores = {
            "sym-a": rr.CompositeScore(
                composite=composite_a,
                metrics=_metrics(0.1, 0.1, 0.1, -0.1, 0.1),
                eligible=eligible_a,
            ),
            "sym-b": rr.CompositeScore(
                composite=composite_b,
                metrics=_metrics(0.1, 0.1, 0.1, -0.1, 0.1),
                eligible=eligible_b,
            ),
        }
        candidate = rr.select_retirement_candidate("sym-a", "sym-b", scores)
        if not eligible_a or not eligible_b:
            assert candidate is None, (
                f"eligible_a={eligible_a}, eligible_b={eligible_b} -- at least "
                f"one side is ineligible, so the pair must yield NO candidate "
                f"(fail-closed), got {candidate!r}."
            )


# ---------------------------------------------------------------------------
# Property: candidate selection is order-invariant (determinism)
# ---------------------------------------------------------------------------


class TestPropertyOrderInvariance:
    @given(composite_a=_composite_floats, composite_b=_composite_floats)
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=200)
    def test_selection_is_invariant_to_argument_order(self, rr, composite_a, composite_b):
        scores = {
            "sym-a": rr.CompositeScore(
                composite=composite_a, metrics=_metrics(0.1, 0.1, 0.1, -0.1, 0.1), eligible=True
            ),
            "sym-b": rr.CompositeScore(
                composite=composite_b, metrics=_metrics(0.1, 0.1, 0.1, -0.1, 0.1), eligible=True
            ),
        }
        forward = rr.select_retirement_candidate("sym-a", "sym-b", scores)
        backward = rr.select_retirement_candidate("sym-b", "sym-a", scores)
        assert forward == backward


# ---------------------------------------------------------------------------
# Property: a strictly-dominated symphony never outranks the dominant one
# (a hypothesis-randomized generalization of the fixed-number CAGR-dominance
# test in test_retirement_recommender_composite.py)
# ---------------------------------------------------------------------------


class TestPropertyStrictDominance:
    @given(
        base_cagr=st.floats(min_value=-0.5, max_value=0.5, allow_nan=False, allow_infinity=False),
        base_sharpe=st.floats(min_value=-3.0, max_value=3.0, allow_nan=False, allow_infinity=False),
        base_sortino=st.floats(
            min_value=-3.0, max_value=3.0, allow_nan=False, allow_infinity=False
        ),
        base_mdd=st.floats(min_value=-0.9, max_value=-0.01, allow_nan=False, allow_infinity=False),
        base_calmar=st.floats(min_value=-3.0, max_value=3.0, allow_nan=False, allow_infinity=False),
        gap_cagr=st.floats(min_value=0.01, max_value=0.5, allow_nan=False, allow_infinity=False),
        gap_sharpe=st.floats(min_value=0.01, max_value=2.0, allow_nan=False, allow_infinity=False),
        gap_sortino=st.floats(min_value=0.01, max_value=2.0, allow_nan=False, allow_infinity=False),
        gap_mdd=st.floats(min_value=0.001, max_value=0.5, allow_nan=False, allow_infinity=False),
        gap_calmar=st.floats(min_value=0.01, max_value=2.0, allow_nan=False, allow_infinity=False),
    )
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=100)
    def test_strictly_dominated_symphony_never_outranks_dominant(
        self,
        rr,
        base_cagr,
        base_sharpe,
        base_sortino,
        base_mdd,
        base_calmar,
        gap_cagr,
        gap_sharpe,
        gap_sortino,
        gap_mdd,
        gap_calmar,
    ):
        # 'dominant' beats 'dominated' on every one of the 5 metrics by a
        # strictly positive gap (max_drawdown: LESS negative = better, so the
        # dominant symphony's mdd = base_mdd + gap_mdd, closer to zero).
        dominated_metrics = _metrics(base_cagr, base_sharpe, base_sortino, base_mdd, base_calmar)
        dominant_metrics = _metrics(
            base_cagr + gap_cagr,
            base_sharpe + gap_sharpe,
            base_sortino + gap_sortino,
            min(base_mdd + gap_mdd, -0.0001),  # stays <= 0 per the plan's convention
            base_calmar + gap_calmar,
        )
        scores = rr.compute_composite_scores(
            {"dominated": dominated_metrics, "dominant": dominant_metrics}
        )
        assert scores["dominant"].composite > scores["dominated"].composite


# ---------------------------------------------------------------------------
# Property (pure gate): structural-redundancy gate never passes below threshold
# ---------------------------------------------------------------------------


class TestPropertyStructuralGateNeverPassesBelowThreshold:
    @given(
        delta=st.floats(min_value=0.001, max_value=1.5, allow_nan=False, allow_infinity=False),
    )
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=100)
    def test_gate_fails_for_any_stressed_corr_below_threshold(self, rr, delta):
        from advisors.correlation_diagnostic import PairResult

        stressed_corr = rr.STRESS_REDUNDANCY_THRESHOLD - delta
        pair = PairResult(
            sym_a="a",
            sym_b="b",
            n_obs=100,
            correlation=0.90,
            thin_data=False,
            window=("2026-01-01", "2026-12-31"),
        )
        verdict = rr.evaluate_structural_redundancy_gate(
            pair, stressed_corr=stressed_corr, holdings_overlap=None
        )
        assert verdict.passed is False


# ---------------------------------------------------------------------------
# Orchestrator-level property: adding an uncorrelated symphony never creates a
# recommendation involving it, and never changes the pre-existing pair's own
# outcome. Deterministic multi-case (not hypothesis) -- a real DB per example.
# ---------------------------------------------------------------------------


class TestPropertyUncorrelatedSymphonyNeverCreatesRecommendation:
    @pytest.mark.parametrize("noise_seed_phase", [0.0, 1.7, 3.14, 5.0])
    def test_adding_an_independent_third_symphony_does_not_introduce_it_as_candidate_or_sibling(
        self, rr, tmp_path, monkeypatch, noise_seed_phase
    ):
        n = 200
        days = trading_days(n)
        # Baseline pair: strongly correlated (would be flagged on its own).
        # Linear ramp, not a sine wave -- review-response cycle 3 F3 fallout:
        # see test_retirement_recommender_composite.py's _fleet_scope_series
        # docstring for the full derivation.
        base_a = [-0.10 + 0.20 * i / (n - 1) for i in range(n)]
        base_b = [0.95 * v + 0.001 * ((i % 3) - 1) for i, v in enumerate(base_a)]
        # Third symphony: independent-ish oscillation at a different, unrelated
        # frequency/phase -- uncorrelated with both a and b.
        indep_c = [0.10 * math.cos(i * 1.31 + noise_seed_phase) for i in range(n)]

        series = {
            "sym-a": {d: (base_a[i], 0.0) for i, d in enumerate(days)},
            "sym-b": {d: (base_b[i], 0.0) for i, d in enumerate(days)},
            "sym-c": {d: (indep_c[i], 0.0) for i, d in enumerate(days)},
        }
        db_file = seed_state_db(tmp_path, monkeypatch, series_by_symphony=series)
        recs = rr.build_recommendations(db_file=db_file, days=None)

        for rec in recs:
            candidate = _rec_field(rec, "candidate_id")
            sibling = _rec_field(rec, "sibling_id")
            assert "sym-c" not in (candidate, sibling), (
                f"sym-c is independent/uncorrelated with both sym-a and sym-b "
                f"(noise_seed_phase={noise_seed_phase}) yet appears in a "
                f"recommendation: {rec!r}."
            )


# ---------------------------------------------------------------------------
# Orchestrator-level property: determinism under symphony dict insertion order
# ---------------------------------------------------------------------------


class TestPropertyDeterminismUnderReordering:
    def test_build_recommendations_output_is_order_independent(self, rr, tmp_path, monkeypatch):
        """Two scratch DBs seeded with the SAME per-symphony series but built by
        inserting the symphonies (and their shadow_history rows) in reversed
        order -- the resulting recommendation set must be identical (as a set
        of (candidate_id, sibling_id) pairs), since SQL storage/iteration
        order must not leak into which pairs get recommended."""
        n = 200
        days = trading_days(n)
        # Linear ramp, not a sine wave -- review-response cycle 3 F3 fallout:
        # see test_retirement_recommender_composite.py's _fleet_scope_series
        # docstring for the full derivation.
        a = [-0.10 + 0.20 * i / (n - 1) for i in range(n)]
        b = [0.95 * v + 0.001 * ((i % 3) - 1) for i, v in enumerate(a)]
        c = [0.10 * math.cos(i * 1.31) for i in range(n)]

        forward_series = {
            "sym-a": {d: (a[i], 0.0) for i, d in enumerate(days)},
            "sym-b": {d: (b[i], 0.0) for i, d in enumerate(days)},
            "sym-c": {d: (c[i], 0.0) for i, d in enumerate(days)},
        }
        reversed_series = dict(reversed(list(forward_series.items())))

        db_file_1 = seed_state_db(
            tmp_path, monkeypatch, series_by_symphony=forward_series, filename="fwd.db"
        )
        recs_1 = rr.build_recommendations(db_file=db_file_1, days=None)
        pairs_1 = {
            frozenset({_rec_field(r, "candidate_id"), _rec_field(r, "sibling_id")}) for r in recs_1
        }

        db_file_2 = seed_state_db(
            tmp_path, monkeypatch, series_by_symphony=reversed_series, filename="rev.db"
        )
        recs_2 = rr.build_recommendations(db_file=db_file_2, days=None)
        pairs_2 = {
            frozenset({_rec_field(r, "candidate_id"), _rec_field(r, "sibling_id")}) for r in recs_2
        }

        assert pairs_1 == pairs_2, (
            f"build_recommendations produced different pair sets depending on "
            f"symphony insertion order: {pairs_1!r} vs {pairs_2!r}."
        )


def _rec_field(rec, name):
    if isinstance(rec, dict):
        if name in rec:
            return rec[name]
        raw = rec.get("raw_response")
        if isinstance(raw, dict):
            return raw.get(name)
        return None
    if hasattr(rec, name):
        return getattr(rec, name)
    raw = getattr(rec, "raw_response", None)
    if isinstance(raw, dict):
        return raw.get(name)
    return None
