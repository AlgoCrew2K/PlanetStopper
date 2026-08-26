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

import math

import pytest

import analytics
from tests.advisors._retirement_recommender_reference import seed_state_db, trading_days


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

    def test_ineligible_higher_composite_sibling_yields_no_candidate_fail_closed(self, rr):
        """PM ruling (PR-level /code-review Finding 1, overrides the original
        AC-11-derived expectation this test used to encode): fail-CLOSED. The
        natural KEEP member (higher composite) being ineligible must NOT let
        the eligible, lower-composite sibling become the candidate -- the
        pair yields no recommendation at all.

        Non-vacuity fix: the ORIGINAL version of this test hand-set
        composite=0.80 on the ineligible entry -- a REAL float, never None.
        Since select_retirement_candidate's ineligible branches only fire on
        an ACTUAL None, that construction silently skipped the very code path
        (retirement_recommender.py's `comp_b is None` check) this test claims
        to exercise, falling through to plain float comparison instead.
        Fixed here to composite=None, the value compute_composite_scores
        itself always produces for an ineligible entry -- matching the
        production invariant so the ineligible-branch is genuinely hit."""
        scores = {
            "eligible-low": rr.CompositeScore(
                composite=0.20, metrics=_metrics(0.04, 0.2, 0.3, -0.40, 0.1), eligible=True
            ),
            "ineligible-high": rr.CompositeScore(
                composite=None,
                metrics=_metrics(0.25, None, 2.0, -0.05, 3.5),
                eligible=False,
            ),
        }
        candidate = rr.select_retirement_candidate("eligible-low", "ineligible-high", scores)
        assert candidate is None, (
            "'ineligible-high' has a genuinely None composite -- the pair "
            "must yield NO candidate at all, fail-closed. Must NOT fall back "
            "to nominating the eligible 'eligible-low' sibling."
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


# ---------------------------------------------------------------------------
# quant-code-reviewer Finding 1 (BLOCKING): fleet-normalization population
# scope at the ORCHESTRATOR level. AC-3 says "normalized across the current
# fleet" -- the ORIGINAL RED suite never pinned whether that means the WHOLE
# live roster or just the symphonies inside a flagged pair. Min-max
# normalization over exactly 2 points (a flagged pair, the common case) is
# mathematically degenerate -- it collapses every non-tied metric to a
# winner-take-all {0.0, 1.0}, discarding all magnitude information -- so this
# is not a cosmetic scoping choice, it changes WHICH symphony gets
# recommended for retirement. Verified numerically (not asserted from
# memory) that build_recommendations's current `involved`-only scoping and a
# full-live-roster scoping produce OPPOSITE candidate picks for the identical
# pair once a 3rd, uncorrelated, extreme-performing live symphony exists.
# ---------------------------------------------------------------------------


# Hand-verified metrics (see the retirement-recommender-core review-response
# session's scratch calculation): under 2-member (pair-only) normalization,
# min-max collapses every non-tied metric to {0,1} and A wins (composite
# 0.40 > B's 0.60 is FALSE -- A=0.40, B=0.60, so B is naturally "keep" and A
# is "candidate"). Under full-roster (A,B,C) normalization, C's extreme
# values on sharpe/sortino/max_drawdown/calmar compress B's advantage on
# those 4 toward ~0 while A's CAGR win stays anchored (C's CAGR sits BETWEEN
# A and B, so it does not widen the CAGR range) -- composite_A=0.40 becomes
# HIGHER than composite_B (~0.097), flipping which symphony is the weaker
# performer entirely from the population choice alone.
_FLEET_SCOPE_METRICS = {
    "A": {
        "total_return": 0.0,
        "annualized_return": 0.12,
        "sharpe": 0.3,
        "sortino": 0.4,
        "max_drawdown": -0.20,
        "calmar": 0.6,
        "win_rate": 0.5,
        "volatility": 0.1,
    },
    "B": {
        "total_return": 0.0,
        "annualized_return": 0.10,
        "sharpe": 0.5,
        "sortino": 0.6,
        "max_drawdown": -0.10,
        "calmar": 1.0,
        "win_rate": 0.5,
        "volatility": 0.1,
    },
    "C": {
        "total_return": 0.0,
        "annualized_return": 0.11,  # between A and B -- does NOT widen the CAGR range
        "sharpe": 5.0,
        "sortino": 6.0,
        "max_drawdown": -0.01,
        "calmar": 10.0,
        "win_rate": 0.5,
        "volatility": 0.1,
    },
}


def _fleet_scope_series(n: int = 200) -> dict[str, list[float]]:
    """Real, correlation_diagnostic-verified return series: A-B correlate
    ~0.9999 (a clean screen hit), A-C and B-C correlate ~0.00003/0.00001
    (C is live but joins no pair) -- computed and confirmed via
    correlation_diagnostic._pearson_r in the review-response scratch session,
    not asserted from memory."""
    bot_a = [0.10 * math.sin(i * 0.3) for i in range(n)]
    bot_b = [0.95 * v + 0.001 * ((i % 3) - 1) for i, v in enumerate(bot_a)]
    bot_c = [0.10 * math.cos(i * 1.31) for i in range(n)]
    return {"A": bot_a, "B": bot_b, "C": bot_c}


def _tagged_quantstats_mock(series_by_tag: dict[str, list[float]]):
    """Replacement for analytics.compute_quantstats_metrics that returns
    pre-validated, hand-verified metrics for whichever of A/B/C's REAL
    constructed return series was passed in (matched by value, not by call
    order or position -- robust to any internal iteration-order change).

    This decouples the test from quantstats' own return-to-metric math (an
    already-tested, separate function/module) so it isolates PURELY the
    orchestrator's population-scope choice under review -- the actual thing
    Finding 1 is about. Raises loudly (AssertionError, not a silent
    fallback) if a series doesn't match any known tag, so a broken match
    fails immediately and legibly rather than masquerading as a false
    positive/negative on the real assertion below.
    """

    def _mock(returns_series, freq="D"):
        for tag, s in series_by_tag.items():
            if len(returns_series) == len(s) and all(
                math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
                for a, b in zip(returns_series, s, strict=True)
            ):
                return dict(_FLEET_SCOPE_METRICS[tag])
        raise AssertionError(
            f"quantstats mock: no known A/B/C series matched (len={len(returns_series)}, "
            f"first 3 values={returns_series[:3]}) -- fixture/mock mismatch, not a real result"
        )

    return _mock


class TestFleetNormalizationScopeAtOrchestratorLevel:
    def test_composite_scope_over_hand_verified_metrics_flips_the_candidate(self, rr):
        """Pure-math sanity check (no DB, no mocking): confirms the SAME
        hand-verified A/B/C metrics really do produce opposite candidate
        picks under the two scoping choices, independent of
        build_recommendations entirely -- isolates the math claim from the
        orchestrator-wiring claim tested below."""
        two_member = rr.compute_composite_scores(
            {"A": _FLEET_SCOPE_METRICS["A"], "B": _FLEET_SCOPE_METRICS["B"]}
        )
        full_roster = rr.compute_composite_scores(_FLEET_SCOPE_METRICS)

        two_member_candidate = rr.select_retirement_candidate("A", "B", two_member)
        full_roster_candidate = rr.select_retirement_candidate(
            "A", "B", {"A": full_roster["A"], "B": full_roster["B"]}
        )

        assert two_member_candidate == "A", (
            "fixture sanity: 2-member scoping must pick A as the candidate"
        )
        assert full_roster_candidate == "B", (
            "fixture sanity: full-roster scoping must pick B as the candidate"
        )
        assert two_member_candidate != full_roster_candidate, (
            "fixture sanity: the whole point of this fixture is that the two "
            "scoping choices disagree on which symphony is the candidate"
        )

    def test_build_recommendations_normalizes_against_the_full_live_roster_not_just_the_flagged_pair(
        self, rr, tmp_path, monkeypatch
    ):
        """THE orchestrator-level pin for Finding 1. Three LIVE symphonies:
        A and B are correlated (screen hit); C is live but uncorrelated with
        either (joins no pair). AC-3 says the composite is 'normalized
        across the current fleet' -- read most naturally as the WHOLE live
        roster, not just the two symphonies inside one flagged pair (a
        pair-only population isn't really a 'fleet' at all, and is
        mathematically degenerate for min-max normalization -- see the
        module docstring above).

        Per the hand-verified fixture, correct full-roster scoping must pick
        B as the candidate for the A-B pair; a build_recommendations that
        instead scores only the `involved` (pair-only) subset would pick A
        -- this assertion is the one that actually distinguishes the two
        implementations, not merely a shape/presence check.
        """
        series = _fleet_scope_series()
        days = trading_days(len(series["A"]))
        db_series = {sym: {d: (series[sym][i], 0.0) for i, d in enumerate(days)} for sym in series}
        db_file = seed_state_db(tmp_path, monkeypatch, series_by_symphony=db_series)

        monkeypatch.setattr(
            analytics, "compute_quantstats_metrics", _tagged_quantstats_mock(series)
        )

        recs = rr.build_recommendations(db_file=db_file, days=None)
        assert len(recs) >= 1, (
            "fixture sanity: the A-B pair must clear the screen and both gates "
            "(near-perfect correlation, n=200) and produce a recommendation"
        )

        ab_recs = [
            r for r in recs if {_field(r, "candidate_id"), _field(r, "sibling_id")} == {"A", "B"}
        ]
        assert len(ab_recs) == 1, f"expected exactly one A/B recommendation, got {ab_recs!r}"
        candidate_id = _field(ab_recs[0], "candidate_id")

        assert candidate_id == "B", (
            f"build_recommendations picked {candidate_id!r} as the A-B pair's "
            "retirement candidate. Per the hand-verified fixture, full-live-"
            "roster fleet normalization (correctly including live symphony C "
            "in the normalization population, per AC-3's 'current fleet') "
            "must pick 'B'. Picking 'A' means compute_composite_scores was "
            "invoked over only the flagged-pair subset ({'A','B'}) rather "
            "than the full live roster ({'A','B','C'}) -- quant-code-reviewer "
            "Finding 1: fix build_recommendations to score against "
            "symphony_ids (the full live roster with a usable return series), "
            "not the narrower `involved` set."
        )

        assert "C" not in {_field(r, "candidate_id") for r in recs} | {
            _field(r, "sibling_id") for r in recs
        }, (
            "C is uncorrelated with both A and B and must never appear in any "
            "recommendation, even though its metrics now (correctly) "
            "influence the A-B pair's normalization."
        )


def _field(rec, name):
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


# ---------------------------------------------------------------------------
# PR-level /code-review Finding 1 (BLOCKING, retirement_recommender.py:266):
# fail-open on an ineligible member -- SUPERSEDES the original ruling-5
# "may still be the keep member" design. PM RULING (overrides the plan's
# AC-11 wording): fail-CLOSED. When EITHER member of a screened pair has
# composite=None (ineligible), emit NO recommendation for that pair at all
# -- never fall back to nominating the eligible sibling. Reason: composite=
# None can mean a catastrophic/unmeasurable loss (e.g. a NaN-driving CAGR
# collapse); keeping the unscoreable member in the portfolio while retiring
# the well-characterized one is backwards for a capital decision.
#
# The ORIGINAL guarding test here was VACUOUS: it hand-constructed
# CompositeScore(composite=0.80, eligible=False) for the ineligible side --
# a REAL float, never None. Since select_retirement_candidate's ineligible-
# member branch is only reached when a composite is ACTUALLY None (`if
# comp_a is None: ...` / `elif comp_b is None: ...`), a hand-set 0.80 never
# exercises that branch at all -- the test silently fell through to plain
# float comparison instead, never proving anything about the ineligible-
# member code path it claimed to guard. This section's tests use ONLY REAL
# `compute_composite_scores`-produced None values (never a hand-set float on
# an eligible=False entry) so the ineligible-member branch is what's
# actually exercised.
# ---------------------------------------------------------------------------


class TestSelectRetirementCandidateRealNoneCompositeBoundary:
    def test_real_ineligible_none_composite_yields_no_candidate_fail_closed(self, rr):
        """The primary non-vacuous pin for Finding 1: a genuinely ineligible
        symphony (compute_composite_scores-produced composite=None) paired
        with an eligible sibling must yield NO recommendation for the pair --
        not a fallback to the eligible sibling as candidate."""
        metrics_by_symphony = {
            "data-poor": _metrics(
                None, 0.5, 0.6, -0.10, 1.0
            ),  # sharpe present but CAGR missing -> ineligible
            "data-rich": _metrics(0.10, 0.5, 0.6, -0.10, 1.0),
        }
        scores = rr.compute_composite_scores(metrics_by_symphony)
        # Fixture sanity: confirm the REAL function produces composite=None
        # for the ineligible symphony (not a hand-set float) before trusting
        # the candidate-selection assertion below.
        assert scores["data-poor"].eligible is False
        assert scores["data-poor"].composite is None
        assert scores["data-rich"].eligible is True
        assert scores["data-rich"].composite is not None

        candidate = rr.select_retirement_candidate("data-poor", "data-rich", scores)
        assert candidate is None, (
            "A real (compute_composite_scores-produced) None-composite "
            "ineligible symphony paired with an eligible sibling must yield "
            "NO candidate -- fail-CLOSED (PM ruling overriding the original "
            "AC-11 'may still be the keep member' reading). Retiring the "
            "well-characterized sibling while an unscoreable member (possibly "
            "hiding a catastrophic loss) stays in the portfolio is backwards "
            "for a capital decision."
        )

    def test_real_ineligible_none_composite_yields_no_candidate_regardless_of_position(self, rr):
        """Mirror of the above with sym_a/sym_b swapped -- directly exercises
        BOTH branches of the ineligible-member check (`comp_a is None` AND
        `comp_b is None`), since select_retirement_candidate's argument order
        determines which branch a given None hits."""
        metrics_by_symphony = {
            "data-rich": _metrics(0.10, 0.5, 0.6, -0.10, 1.0),
            "data-poor": _metrics(None, 0.5, 0.6, -0.10, 1.0),
        }
        scores = rr.compute_composite_scores(metrics_by_symphony)
        assert scores["data-poor"].composite is None
        assert scores["data-rich"].composite is not None

        candidate = rr.select_retirement_candidate("data-rich", "data-poor", scores)
        assert candidate is None, (
            "Same fail-closed outcome regardless of which position (sym_a or "
            "sym_b) the ineligible member occupies."
        )
