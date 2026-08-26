"""RED tests -- Retirement Recommender fail-closed / degenerate cases (AC-11).

Module under test: advisors.retirement_recommender (NEW).

Every case here must degrade to an honest empty result -- never an exception,
never a fabricated recommendation.
"""

from __future__ import annotations

import math

import pytest

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


class TestFewerThanTwoSymphonies:
    def test_zero_symphonies_yields_empty_list_not_error(self, rr, tmp_path, monkeypatch):
        db_file = seed_state_db(tmp_path, monkeypatch, series_by_symphony={})
        recs = rr.build_recommendations(db_file=db_file, days=None)
        assert recs == []

    def test_one_symphony_yields_empty_list_not_error(self, rr, tmp_path, monkeypatch):
        days = trading_days(60)
        series = {"only-sym": {d: (0.1 * math.sin(i * 0.3), 0.0) for i, d in enumerate(days)}}
        db_file = seed_state_db(tmp_path, monkeypatch, series_by_symphony=series)
        recs = rr.build_recommendations(db_file=db_file, days=None)
        assert recs == []

    def test_screen_correlated_pairs_handles_fewer_than_two_series(self, rr):
        assert rr.screen_correlated_pairs({}) == []
        assert rr.screen_correlated_pairs({"only": {"2026-01-01": 0.1}}) == []


class TestNoPairMeetsScreenThreshold:
    def test_all_uncorrelated_symphonies_yield_empty_list(self, rr, tmp_path, monkeypatch):
        n = 150
        days = trading_days(n)
        a = [0.10 * math.sin(i * 0.31) for i in range(n)]
        b = [0.10 * math.cos(i * 1.7 + 0.9) for i in range(n)]
        c = [0.10 * math.sin(i * 2.3 + 2.1) for i in range(n)]
        series = {
            "sym-a": {d: (a[i], 0.0) for i, d in enumerate(days)},
            "sym-b": {d: (b[i], 0.0) for i, d in enumerate(days)},
            "sym-c": {d: (c[i], 0.0) for i, d in enumerate(days)},
        }
        db_file = seed_state_db(tmp_path, monkeypatch, series_by_symphony=series)
        recs = rr.build_recommendations(db_file=db_file, days=None)
        assert recs == []


class TestThinHistory:
    def test_n_obs_below_min_obs_floor_yields_no_recommendation(self, rr, tmp_path, monkeypatch):
        """A pair with a real high correlation but too few overlapping days
        (below MIN_OBS_FLOOR) must fail-closed via the uncertainty gate."""
        n = 5  # deliberately thin -- below any plausible MIN_OBS_FLOOR
        days = trading_days(n)
        a = [0.1, -0.05, 0.08, -0.02, 0.03]
        b = [0.95 * v for v in a]
        series = {
            "sym-a": {d: (a[i], 0.0) for i, d in enumerate(days)},
            "sym-b": {d: (b[i], 0.0) for i, d in enumerate(days)},
        }
        db_file = seed_state_db(tmp_path, monkeypatch, series_by_symphony=series)
        recs = rr.build_recommendations(db_file=db_file, days=None)
        assert recs == []


class TestZeroVarianceCorrelation:
    def test_zero_variance_series_yields_no_crash_and_no_recommendation(
        self, rr, tmp_path, monkeypatch
    ):
        """A perfectly flat (zero-variance) return series makes Pearson r
        undefined (None) -- must never crash and must never be a screen hit."""
        n = 60
        days = trading_days(n)
        flat_a = [0.0] * n
        flat_b = [0.0] * n
        series = {
            "sym-a": {d: (flat_a[i], 0.0) for i, d in enumerate(days)},
            "sym-b": {d: (flat_b[i], 0.0) for i, d in enumerate(days)},
        }
        db_file = seed_state_db(tmp_path, monkeypatch, series_by_symphony=series)
        recs = rr.build_recommendations(db_file=db_file, days=None)
        assert recs == []


class TestCoverageMismatch:
    def test_symphony_in_screen_but_absent_from_metrics_map_handled_without_keyerror(self, rr):
        """compute_composite_scores is fed a metrics map that is MISSING a
        symphony the screen flagged -- e.g. a quantstats computation failure
        for one side of a pair. select_retirement_candidate must handle a
        missing key without raising KeyError."""
        scores = {
            "sym-a": rr.CompositeScore(
                composite=0.4, metrics=_metrics(0.1, 0.1, 0.1, -0.1, 0.1), eligible=True
            ),
            # "sym-b" deliberately absent
        }
        try:
            result = rr.select_retirement_candidate("sym-a", "sym-b", scores)
        except KeyError:
            pytest.fail(
                "select_retirement_candidate raised KeyError for a symphony "
                "missing from the scores map -- AC-11's coverage-mismatch edge "
                "case requires this to be handled without KeyError (e.g. "
                "degrade to no candidate)."
            )
        assert result is None or result == "sym-a"


class TestClusterDedup:
    def test_same_symphony_flagged_in_multiple_pairs_dedupes_on_persist(
        self, rr, tmp_path, monkeypatch
    ):
        """sym-a correlates strongly with BOTH sym-b and sym-c (a correlation
        cluster). Each pair is evaluated independently (both may appear as
        raw candidate-pairs), but at persist/render time sym-a must not be
        recommended for retirement twice under two different sibling
        pairings -- dedupe keeping the strongest-evidence pair."""
        n = 200
        days = trading_days(n)
        base = [0.10 * math.sin(i * 0.3) for i in range(n)]
        b = [0.95 * v + 0.001 * ((i % 3) - 1) for i, v in enumerate(base)]
        c = [0.93 * v + 0.001 * ((i % 5) - 2) for i, v in enumerate(base)]
        series = {
            "sym-a": {d: (base[i], 0.0) for i, d in enumerate(days)},
            "sym-b": {d: (b[i], 0.0) for i, d in enumerate(days)},
            "sym-c": {d: (c[i], 0.0) for i, d in enumerate(days)},
        }
        db_file = seed_state_db(tmp_path, monkeypatch, series_by_symphony=series)
        recs = rr.build_recommendations(db_file=db_file, days=None)

        candidate_counts: dict[str, int] = {}
        for rec in recs:
            cid = _rec_field(rec, "candidate_id")
            candidate_counts[cid] = candidate_counts.get(cid, 0) + 1
        for cid, count in candidate_counts.items():
            assert count == 1, (
                f"symphony {cid!r} appears as the candidate in {count} separate "
                "recommendations -- must be deduplicated to a single "
                "strongest-evidence recommendation per candidate."
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
