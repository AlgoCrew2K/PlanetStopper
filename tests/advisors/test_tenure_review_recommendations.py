"""The retire-candidate recommendations a review hands to the existing persistence path.

Module under test: advisors.tenure_review (build_review), with the existing
advisors.retirement_recommender as the reference for the shape and the values.

The recommendation dict has the keys retirement_recommender.build_recommendations
emits, so persist_recommendations and the explanation loop accept it unchanged, plus
the window keys a later night's reuse comparison needs. The explanation, its date and
the run id are added by the caller that writes the run, not here.

Metrics inside a recommendation are in percent (a total return of 8 means +8 percent),
the scale every figure of the review uses and the one the explanation is grounded in;
the existing recommender's own metrics are fractions.
"""

from __future__ import annotations

import copy
import json

import pytest

import advisors.tenure_review as tr
import database
from advisors import retirement_recommender
from tests.advisors import _tenure_review_builders as b
from tests.advisors._retirement_recommender_reference import seed_state_db

ADDED_KEYS = {
    "as_of_trading_day",
    "window_first",
    "window_last",
    "window_days",
    "composite_window_first",
    "composite_window_last",
    "composite_window_days",
}
ADDED_LATER_KEYS = {"review_run_id", "explanation", "explanation_written_on"}
SCALE_DEPENDENT_METRICS = ("annualized_return", "max_drawdown")
RATIO_METRICS = ("sharpe", "sortino", "calmar")
PERCENT = 100.0


def _recommender_and_review(tmp_path, monkeypatch, *, stronger_sorts_first=False):
    """The same series seen by the existing recommender (as shadow rows) and by the
    review (as Composer value series over a window spanning all of them)."""
    roster = b.roster_with_a_candidate_in_two_passing_pairs(
        stronger_sorts_first=stronger_sorts_first
    )
    seed_state_db(tmp_path, monkeypatch, series_by_symphony=b.shadow_rows_of(roster))
    legacy = {r["candidate_id"]: r for r in retirement_recommender.build_recommendations(days=None)}
    result = b.review(roster, as_of=b.AS_OF, threshold=len(roster[0].composer_values))
    return legacy, result


def test_the_review_hands_over_one_recommendation_for_each_candidate_the_recommender_names(
    tmp_path, monkeypatch
):
    legacy, result = _recommender_and_review(tmp_path, monkeypatch)
    assert "weak-x" in legacy  # fixture sanity: the candidate with two passing pairs exists
    ids = [rec["candidate_id"] for rec in result.recommendations]
    assert ids == sorted(legacy)
    flagged = [
        e["symphony_id"] for e in result.entries if b.item_of(e, "redundancy")["retire_candidate"]
    ]
    assert sorted(flagged) == ids


def test_each_recommendation_has_the_recommenders_keys_and_evidence_plus_the_window_keys(
    tmp_path, monkeypatch
):
    legacy, result = _recommender_and_review(tmp_path, monkeypatch)
    for rec in result.recommendations:
        old = legacy[rec["candidate_id"]]
        assert set(old) <= set(rec)
        assert set(rec) - set(old) == ADDED_KEYS
        assert not set(rec) & ADDED_LATER_KEYS
        for key in ("candidate_id", "sibling_id", "n_obs"):
            assert rec[key] == old[key], key
        assert rec["uncertainty_gate_passed"] is True
        assert rec["structural_redundancy_gate_passed"] is True
        # the same primitives on the same series: 1e-9 relative absorbs summation order only
        for key in (
            "correlation",
            "ci_lower",
            "ci_upper",
            "stressed_correlation",
            "candidate_composite",
            "sibling_composite",
        ):
            assert rec[key] == pytest.approx(old[key], rel=1e-9), key
        assert rec["holdings_overlap"] == old["holdings_overlap"]  # no tickers on either side


def test_recommendation_metrics_are_the_scored_metrics_in_percent(tmp_path, monkeypatch):
    legacy, result = _recommender_and_review(tmp_path, monkeypatch)
    for rec in result.recommendations:
        old = legacy[rec["candidate_id"]]
        for side in ("candidate_metrics", "sibling_metrics"):
            assert set(rec[side]) == set(old[side]), side
            for key in RATIO_METRICS:
                assert rec[side][key] == pytest.approx(old[side][key], rel=1e-9), (side, key)
            for key in SCALE_DEPENDENT_METRICS:
                # the recommender's fractions times 100, in the same percent as every figure
                assert rec[side][key] == pytest.approx(old[side][key] * PERCENT, rel=1e-9), (
                    side,
                    key,
                )


def test_the_basis_of_a_recommendation_is_the_composer_basis_label(tmp_path, monkeypatch):
    _legacy, result = _recommender_and_review(tmp_path, monkeypatch)
    assert result.recommendations  # fixture sanity
    for rec in result.recommendations:
        assert rec["basis_label"] == tr.COMPOSER_BASIS_LABEL


def test_the_window_keys_are_the_shown_pairs_overlap_the_composite_window_and_the_as_of_day(
    tmp_path, monkeypatch
):
    _legacy, result = _recommender_and_review(tmp_path, monkeypatch)
    for rec in result.recommendations:
        item = b.item_of(b.entry_of(result, rec["candidate_id"]), "redundancy")
        assert rec["as_of_trading_day"] == b.AS_OF.isoformat()
        assert rec["window_first"] == item["window"]["first"]
        assert rec["window_last"] == item["window"]["last"]
        assert rec["window_days"] == item["window"]["days"] == rec["n_obs"]
        assert rec["composite_window_first"] == result.composite_window["first"]
        assert rec["composite_window_last"] == result.composite_window["last"]
        assert rec["composite_window_days"] == result.composite_window["days"]


@pytest.mark.parametrize("stronger_sorts_first", [True, False], ids=["id_first", "id_last"])
def test_a_candidate_with_two_passing_pairs_has_one_recommendation_for_the_stronger_pair(
    stronger_sorts_first, tmp_path, monkeypatch
):
    """The recommender keeps one recommendation per candidate: the one with the higher
    correlation. Rejects keeping the weaker one, keeping both, and keeping the first found."""
    legacy, result = _recommender_and_review(
        tmp_path, monkeypatch, stronger_sorts_first=stronger_sorts_first
    )
    mine = [r for r in result.recommendations if r["candidate_id"] == "weak-x"]
    assert len(mine) == 1
    assert mine[0]["sibling_id"] == legacy["weak-x"]["sibling_id"]
    shown = b.item_of(b.entry_of(result, "weak-x"), "redundancy")["pair"]
    assert mine[0]["sibling_id"] == shown["sibling_id"]


def test_a_review_with_nobody_flagged_has_an_empty_list_of_recommendations():
    roster, threshold = b.roster_with_ineligible_correlated_sibling()
    result = b.review(roster, as_of=b.AS_OF, threshold=threshold)
    assert result.recommendations == []  # a list, never None or missing


def test_a_review_of_no_symphonies_has_an_empty_list_of_recommendations():
    assert b.review([], as_of=b.AS_OF, threshold=30).recommendations == []


def test_a_recommendation_is_strict_json_and_shares_no_state_with_the_entries(
    tmp_path, monkeypatch
):
    _legacy, result = _recommender_and_review(tmp_path, monkeypatch)
    assert result.recommendations  # fixture sanity
    for rec in result.recommendations:
        assert json.loads(json.dumps(rec, allow_nan=False)) == rec
    snapshot = copy.deepcopy(result.entries)
    for rec in result.recommendations:
        for value in rec.values():
            if isinstance(value, dict):
                value.clear()
        rec.clear()
    assert result.entries == snapshot


def test_the_existing_persist_function_stores_the_recommendations_as_they_are(
    tmp_path, monkeypatch
):
    """Rejects a shape the persistence path cannot take or alters: the stored evidence
    is the recommendation, key for key."""
    _legacy, result = _recommender_and_review(tmp_path, monkeypatch)
    recs = result.recommendations
    assert recs  # fixture sanity
    stored_count = retirement_recommender.persist_recommendations(recs)
    assert stored_count == len(recs)
    rows = database.get_advisor_observations_for_role("RETIREMENT_RECOMMENDATION")
    stored = {row["raw_response"]["candidate_id"]: row["raw_response"] for row in rows}
    assert sorted(stored) == [rec["candidate_id"] for rec in recs]
    for rec in recs:
        assert stored[rec["candidate_id"]] == rec
