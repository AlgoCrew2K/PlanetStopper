"""RED tests -- Retirement Recommender composite scoring + candidate selection
(AC-3, AC-4, AC-11's None-metric-ineligibility clause).

Module under test: advisors.retirement_recommender (NEW).

Contract pinned in .claude/tdd-handoff.md "retirement_recommender.py -- composite":
- compute_composite_scores(metrics_by_symphony) -> dict[str, CompositeScore].
  Input values are compute_quantstats_metrics-SHAPED dicts (8 keys); only 5 are
  used for the composite: annualized_return (CAGR), sharpe, sortino,
  max_drawdown, calmar -- reusing compute_quantstats_metrics' own key names
  verbatim (no renaming/translation layer -- fewer places for a mapping bug).
- CompositeScore is a dataclass: `.composite: float | None`,
  `.metrics: dict[str, float | None]` (the 5 keys above),
  `.eligible: bool` (False iff ANY of the 5 is None).
- Named weights W_CAGR / W_SHARPE / W_SORTINO / W_MAXDD / W_CALMAR; CAGR
  strictly dominant (W_CAGR > each of the other four).
- select_retirement_candidate(sym_a, sym_b, scores) -> str | None -- test-writer
  addendum to the plan's Architecture list (needed for deterministic,
  weight-independent unit testing of the AC-4 tie-break rule; NOT invented
  production behavior -- it factors out logic build_recommendations must have
  somewhere per AC-4's own text). Returns the retirement CANDIDATE's
  symphony_id: the LOWER-composite member; ties broken by lower
  metrics['annualized_return'] first, then lexically smaller symphony_id.
  An ineligible symphony (CompositeScore.eligible is False) is NEVER returned
  as the candidate; if BOTH are ineligible, returns None (no valid pair).
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def rr():
    import advisors.retirement_recommender as _rr  # noqa: PLC0415

    return _rr


def _metrics(annualized_return, sharpe, sortino, max_drawdown, calmar):
    """Full compute_quantstats_metrics-shaped dict -- includes the 3 unused
    keys (total_return/win_rate/volatility) to prove the composite function
    correctly ignores them rather than accidentally depending on their
    presence/absence."""
    return {
        "total_return": 0.0,
        "annualized_return": annualized_return,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "calmar": calmar,
        "win_rate": 0.5,
        "volatility": 0.10,
    }


# ---------------------------------------------------------------------------
# AC-3: named weights, CAGR-dominant
# ---------------------------------------------------------------------------


class TestCompositeWeights:
    def test_cagr_weight_strictly_exceeds_each_other_weight(self, rr):
        for other_name in ("W_SHARPE", "W_SORTINO", "W_MAXDD", "W_CALMAR"):
            other = getattr(rr, other_name)
            assert other < rr.W_CAGR, (
                f"W_CAGR ({rr.W_CAGR}) must be strictly greater than {other_name} "
                f"({other}) -- AC-3 requires CAGR to be weighted strictly higher "
                "than each of the other four metrics."
            )

    def test_all_weights_are_positive(self, rr):
        for name in ("W_CAGR", "W_SHARPE", "W_SORTINO", "W_MAXDD", "W_CALMAR"):
            assert getattr(rr, name) > 0, f"{name} must be positive"


# ---------------------------------------------------------------------------
# AC-3: CAGR-dominance invariant (implementation-agnostic: strictly-worse-on-
# all-5 always ranks lower, regardless of the exact weight values)
# ---------------------------------------------------------------------------


class TestCompositeCagrDominance:
    def test_symphony_strictly_worse_on_all_five_metrics_always_ranks_lower(self, rr):
        metrics_by_symphony = {
            "strong": _metrics(
                annualized_return=0.25, sharpe=1.5, sortino=2.0, max_drawdown=-0.08, calmar=3.0
            ),
            "weak": _metrics(
                annualized_return=0.05, sharpe=0.3, sortino=0.4, max_drawdown=-0.35, calmar=0.2
            ),
        }
        scores = rr.compute_composite_scores(metrics_by_symphony)
        assert scores["strong"].composite > scores["weak"].composite, (
            "A symphony strictly worse on all 5 metrics (lower CAGR, lower "
            "Sharpe, lower Sortino, deeper drawdown, lower Calmar) must always "
            "rank lower in composite, regardless of weight magnitudes."
        )

    def test_max_drawdown_orientation_deeper_drawdown_scores_worse(self, rr):
        """Isolates MDD: everything else identical, only drawdown differs."""
        metrics_by_symphony = {
            "shallow_dd": _metrics(
                annualized_return=0.15, sharpe=1.0, sortino=1.2, max_drawdown=-0.05, calmar=1.5
            ),
            "deep_dd": _metrics(
                annualized_return=0.15, sharpe=1.0, sortino=1.2, max_drawdown=-0.40, calmar=1.5
            ),
        }
        scores = rr.compute_composite_scores(metrics_by_symphony)
        assert scores["shallow_dd"].composite > scores["deep_dd"].composite, (
            "A shallower (less negative) max_drawdown must score better than a "
            "deeper one, holding all other metrics equal -- max_drawdown uses "
            "the <=0 convention (more negative = worse) and must be oriented so "
            "higher (closer to zero) = better."
        )


# ---------------------------------------------------------------------------
# AC-3: fleet-normalization -- composite is relative to the CURRENT fleet, not
# an absolute function of one symphony's metrics alone.
# ---------------------------------------------------------------------------


class TestCompositeFleetNormalization:
    def test_same_absolute_metrics_score_differently_under_a_different_fleet(self, rr):
        target_metrics = _metrics(
            annualized_return=0.12, sharpe=0.8, sortino=1.0, max_drawdown=-0.15, calmar=0.9
        )

        weak_fleet = {
            "target": target_metrics,
            "peer": _metrics(
                annualized_return=0.02, sharpe=0.1, sortino=0.1, max_drawdown=-0.50, calmar=0.05
            ),
        }
        strong_fleet = {
            "target": target_metrics,
            "peer": _metrics(
                annualized_return=0.40, sharpe=2.5, sortino=3.0, max_drawdown=-0.03, calmar=5.0
            ),
        }

        weak_fleet_scores = rr.compute_composite_scores(weak_fleet)
        strong_fleet_scores = rr.compute_composite_scores(strong_fleet)

        assert weak_fleet_scores["target"].composite != strong_fleet_scores["target"].composite, (
            "The SAME absolute metrics for 'target' produced the SAME composite "
            "score under two different fleet contexts -- this means the "
            "composite is NOT fleet-normalized (AC-3 requires 'normalized "
            "across the fleet')."
        )
        assert weak_fleet_scores["target"].composite > strong_fleet_scores["target"].composite, (
            "'target' is relatively strong vs a weak fleet peer and relatively "
            "weak vs a strong fleet peer -- fleet-relative normalization must "
            "reflect that ordering."
        )


# ---------------------------------------------------------------------------
# AC-11: a None metric makes a symphony ineligible as a candidate
# ---------------------------------------------------------------------------


class TestCompositeEligibility:
    def test_any_none_metric_marks_symphony_ineligible(self, rr):
        metrics_by_symphony = {
            "clean": _metrics(
                annualized_return=0.10, sharpe=0.8, sortino=0.9, max_drawdown=-0.10, calmar=1.0
            ),
            "thin": _metrics(
                annualized_return=0.10, sharpe=None, sortino=0.9, max_drawdown=-0.10, calmar=1.0
            ),
        }
        scores = rr.compute_composite_scores(metrics_by_symphony)
        assert scores["clean"].eligible is True
        assert scores["thin"].eligible is False, (
            "A symphony with ANY None among the 5 metrics (here: sharpe=None) "
            "must be marked ineligible (AC-11)."
        )

    @pytest.mark.parametrize(
        "none_field",
        ["annualized_return", "sharpe", "sortino", "max_drawdown", "calmar"],
    )
    def test_each_of_the_five_metrics_independently_triggers_ineligibility(self, rr, none_field):
        values = {
            "annualized_return": 0.10,
            "sharpe": 0.8,
            "sortino": 0.9,
            "max_drawdown": -0.10,
            "calmar": 1.0,
        }
        values[none_field] = None
        metrics_by_symphony = {"sym": _metrics(**values)}
        scores = rr.compute_composite_scores(metrics_by_symphony)
        assert scores["sym"].eligible is False


# ---------------------------------------------------------------------------
# AC-4: candidate selection -- lower composite, then lower CAGR, then lexical
# symphony_id; an ineligible symphony is never the candidate.
# ---------------------------------------------------------------------------


class TestSelectRetirementCandidate:
    def test_lower_composite_is_the_candidate(self, rr):
        scores = {
            "sym-a": rr.CompositeScore(
                composite=0.30, metrics=_metrics(0.05, 0.3, 0.4, -0.30, 0.2), eligible=True
            ),
            "sym-b": rr.CompositeScore(
                composite=0.70, metrics=_metrics(0.20, 1.2, 1.5, -0.08, 2.0), eligible=True
            ),
        }
        candidate = rr.select_retirement_candidate("sym-a", "sym-b", scores)
        assert candidate == "sym-a"

    def test_tied_composite_breaks_on_lower_cagr(self, rr):
        scores = {
            "sym-a": rr.CompositeScore(
                composite=0.50, metrics=_metrics(0.10, 0.8, 0.9, -0.10, 1.0), eligible=True
            ),
            "sym-b": rr.CompositeScore(
                composite=0.50, metrics=_metrics(0.15, 0.8, 0.9, -0.10, 1.0), eligible=True
            ),
        }
        candidate = rr.select_retirement_candidate("sym-a", "sym-b", scores)
        assert candidate == "sym-a", (
            "Composite tied (0.50 == 0.50); the AC-4 tiebreak is lower CAGR "
            "first -- sym-a's annualized_return=0.10 < sym-b's 0.15."
        )

    def test_tied_composite_and_cagr_breaks_on_lexical_symphony_id(self, rr):
        scores = {
            "zzz-sym": rr.CompositeScore(
                composite=0.50, metrics=_metrics(0.10, 0.8, 0.9, -0.10, 1.0), eligible=True
            ),
            "aaa-sym": rr.CompositeScore(
                composite=0.50, metrics=_metrics(0.10, 0.8, 0.9, -0.10, 1.0), eligible=True
            ),
        }
        candidate = rr.select_retirement_candidate("zzz-sym", "aaa-sym", scores)
        assert candidate == "aaa-sym", (
            "Composite AND CAGR both tied -- the final tiebreak is lexically "
            "smaller symphony_id ('aaa-sym' < 'zzz-sym')."
        )

    def test_determinism_regardless_of_argument_order(self, rr):
        scores = {
            "zzz-sym": rr.CompositeScore(
                composite=0.50, metrics=_metrics(0.10, 0.8, 0.9, -0.10, 1.0), eligible=True
            ),
            "aaa-sym": rr.CompositeScore(
                composite=0.50, metrics=_metrics(0.10, 0.8, 0.9, -0.10, 1.0), eligible=True
            ),
        }
        cand_1 = rr.select_retirement_candidate("zzz-sym", "aaa-sym", scores)
        cand_2 = rr.select_retirement_candidate("aaa-sym", "zzz-sym", scores)
        assert cand_1 == cand_2 == "aaa-sym"

    def test_ineligible_lower_composite_yields_no_candidate_not_the_eligible_sibling(self, rr):
        """AC-11: an ineligible symphony must never BE the candidate. That does
        NOT mean its eligible, higher-composite (stronger-performing) sibling
        becomes the candidate instead -- retiring the stronger performer makes
        no sense. When the natural (lower-composite) candidate is ineligible,
        the correct fail-closed outcome is NO candidate for this pair at all."""
        scores = {
            "ineligible-low": rr.CompositeScore(
                composite=0.10,
                metrics=_metrics(0.02, None, 0.1, -0.50, 0.05),
                eligible=False,
            ),
            "eligible-high": rr.CompositeScore(
                composite=0.90, metrics=_metrics(0.30, 2.0, 2.5, -0.05, 4.0), eligible=True
            ),
        }
        candidate = rr.select_retirement_candidate("ineligible-low", "eligible-high", scores)
        assert candidate != "ineligible-low", (
            "An ineligible symphony must never be returned as the candidate."
        )
        assert candidate is None, (
            "The natural (lower-composite) candidate 'ineligible-low' is "
            "ineligible -- fail-closed means no candidate emerges for this "
            "pair, not a fallback to retiring the stronger 'eligible-high' "
            "sibling instead."
        )

    def test_ineligible_higher_composite_sibling_does_not_block_the_eligible_candidate(self, rr):
        """The mirror case: the natural KEEP member (higher composite) is
        ineligible, but the natural CANDIDATE (lower composite) is eligible.
        AC-11: an ineligible symphony 'may still be the keep member of a
        pair' -- so this must NOT block the eligible, lower-composite sibling
        from being correctly selected as the candidate."""
        scores = {
            "eligible-low": rr.CompositeScore(
                composite=0.20, metrics=_metrics(0.04, 0.2, 0.3, -0.40, 0.1), eligible=True
            ),
            "ineligible-high": rr.CompositeScore(
                composite=0.80,
                metrics=_metrics(0.25, None, 2.0, -0.05, 3.5),
                eligible=False,
            ),
        }
        candidate = rr.select_retirement_candidate("eligible-low", "ineligible-high", scores)
        assert candidate == "eligible-low", (
            "'eligible-high' being ineligible must not prevent the eligible, "
            "lower-composite 'eligible-low' from being selected as the "
            "candidate -- an ineligible symphony may still serve as the keep "
            "member (AC-11)."
        )

    def test_both_ineligible_yields_no_candidate(self, rr):
        scores = {
            "sym-a": rr.CompositeScore(
                composite=None, metrics=_metrics(None, 0.1, 0.1, -0.5, 0.05), eligible=False
            ),
            "sym-b": rr.CompositeScore(
                composite=None, metrics=_metrics(0.02, None, 0.1, -0.5, 0.05), eligible=False
            ),
        }
        candidate = rr.select_retirement_candidate("sym-a", "sym-b", scores)
        assert candidate is None, (
            "Both symphonies in the pair are ineligible -- there is no valid "
            "candidate; select_retirement_candidate must return None rather "
            "than arbitrarily picking one."
        )
