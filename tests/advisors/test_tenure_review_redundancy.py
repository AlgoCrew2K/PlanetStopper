"""Redundancy evidence: the correlation screen, both gates and the pair a row shows.

Module under test: advisors.tenure_review (evaluate_redundancy and build_review).

Oracles come from tests/advisors/_tenure_review_builders.py (Pearson, the Fisher
interval via the repository's reference helper, the stressed window written from
the spec with integer arithmetic) and from the existing recommender, which is the
parity reference and is not modified.

Every gate state is one of: passed, not met, insufficient data, undefined,
not evaluated.
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pytest

import advisors.tenure_review as tr
from advisors import retirement_recommender
from tests.advisors import _tenure_review_builders as b
from tests.advisors._retirement_recommender_reference import seed_state_db, trading_days

_PARITY_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "math"
    / "tenure_review_redundancy_parity.json"
)

NEEDED_DAYS = 181  # the plan's value: smallest n with ceil(5% of n) >= 10
INSUFFICIENT_STRESS = f"insufficient data: needs {NEEDED_DAYS} aligned days"
INSUFFICIENT_UNCERTAINTY = "insufficient data: needs 30 aligned days"


def _series(*columns):
    """Date-keyed series sharing one run of consecutive date labels."""
    labels = trading_days(len(columns[0]))
    return [dict(zip(labels, col, strict=True)) for col in columns]


def _pair(a, b_, tickers=None):
    sa, sb = _series(a, b_)
    out = tr.evaluate_redundancy({"sym-a": sa, "sym-b": sb}, tickers)
    assert len(out) == 1
    return out[0]


def test_oracle_agrees_that_181_is_the_first_length_with_enough_stressed_days():
    assert b.days_needed_for_structural_gate() == NEEDED_DAYS


# ---------------------------------------------------------------------------
# Shape of the result
# ---------------------------------------------------------------------------


def test_one_entry_per_unordered_pair_with_the_lexically_smaller_id_first():
    x = b.sine_returns(100, 1)
    y = b.sine_returns(100, 2)
    z = b.sine_returns(100, 3)
    sx, sy, sz = _series(x, y, z)
    pairs = tr.evaluate_redundancy({"c": sz, "a": sx, "b": sy})
    assert [(p["sym_a"], p["sym_b"]) for p in pairs] == [("a", "b"), ("a", "c"), ("b", "c")]
    for p in pairs:
        assert type(p["n_obs"]) is int
        assert type(p["screen_hit"]) is bool
        assert p["n_obs"] == 100


def test_fewer_than_two_series_give_no_pairs():
    (sx,) = _series(b.sine_returns(50, 1))
    assert tr.evaluate_redundancy({"only": sx}) == []
    assert tr.evaluate_redundancy({}) == []


def test_series_that_overlap_only_partly_are_compared_on_their_shared_days():
    labels = trading_days(150)
    x = b.sine_returns(100, 1)
    y = b.orthogonal_pair(100, 0.9, k=1)[1]
    series_x = dict(zip(labels[:100], x, strict=True))
    series_y = dict(zip(labels[50:150], y, strict=True))
    (pair,) = tr.evaluate_redundancy({"sym-a": series_x, "sym-b": series_y})
    assert pair["n_obs"] == 50
    assert pair["window"] == {"first": labels[50], "last": labels[99], "days": 50}


def test_a_day_with_a_non_finite_value_in_either_series_is_not_counted():
    x = b.sine_returns(60, 1)
    y = b.orthogonal_pair(60, 0.9, k=1)[1]
    x[5] = float("nan")
    y[9] = float("inf")
    pair = _pair(x, y)
    assert pair["n_obs"] == 58


def test_evaluating_twice_gives_the_same_result_and_leaves_the_input_alone():
    x, y = b.orthogonal_pair(80, 0.9, k=1)
    sx, sy = _series(x, y)
    inputs = {"sym-a": sx, "sym-b": sy}
    snapshot = {k: dict(v) for k, v in inputs.items()}
    first = tr.evaluate_redundancy(inputs)
    second = tr.evaluate_redundancy(inputs)
    assert first == second
    assert inputs == snapshot


# ---------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------


def test_a_correlation_of_exactly_0_65_is_a_screen_hit_and_its_gates_are_evaluated():
    """The comparison is greater-or-equal. The fixture's Pearson r is the float 0.65
    exactly; the precondition below proves it with the primitive the recommender uses.

    Rejects a strict greater-than screen and a gate that is skipped at the boundary."""
    p, q = b.exact_threshold_pair()
    assert b.exact_pearson_or_none(p, q) == 0.65
    pair = _pair(p, q)
    assert pair["correlation"] == pytest.approx(0.65, abs=0, rel=1e-12)
    assert pair["screen_hit"] is True
    assert pair["uncertainty_gate"]["state"] != "not evaluated"


@pytest.mark.parametrize(("rho", "hit"), [(0.6499, False), (0.6501, True)])
def test_the_screen_threshold_sits_between_0_6499_and_0_6501(rho, hit):
    """Brackets the threshold on both sides. Rejects 0.7, 0.6 and a rounded compare."""
    x, y = b.orthogonal_pair(200, rho, k=3)
    assert _pair(x, y)["screen_hit"] is hit


def test_uncorrelated_series_are_not_screen_hits():
    sx, sy, sz = _series(b.sine_returns(100, 1), b.sine_returns(100, 2), b.sine_returns(100, 3))
    pairs = tr.evaluate_redundancy({"a": sx, "b": sy, "c": sz})
    assert [p["screen_hit"] for p in pairs] == [False, False, False]


def test_a_zero_variance_series_has_an_undefined_correlation_never_zero_and_no_hit():
    constant = [0.1] * 60
    moving = b.sine_returns(60, 1)
    pair = _pair(constant, moving)
    assert pair["correlation"] is None
    assert pair["screen_hit"] is False
    assert pair["n_obs"] == 60


def test_two_zero_variance_series_are_also_undefined():
    pair = _pair([0.1] * 60, [0.2] * 60)
    assert pair["correlation"] is None
    assert pair["screen_hit"] is False


# ---------------------------------------------------------------------------
# Uncertainty gate
# ---------------------------------------------------------------------------


def test_a_pair_with_29_aligned_days_reads_insufficient_data_for_the_uncertainty_gate():
    x, y = b.orthogonal_pair(29, 0.95, k=1)
    pair = _pair(x, y)
    assert pair["screen_hit"] is True
    assert pair["n_obs"] == 29
    assert pair["uncertainty_gate"]["state"] == "insufficient data"
    assert pair["uncertainty_gate"]["text"] == INSUFFICIENT_UNCERTAINTY
    assert pair["structural_gate"]["state"] == "not evaluated"


def test_a_pair_with_exactly_30_aligned_days_is_old_enough_for_the_uncertainty_gate():
    """Rejects a floor applied with less-or-equal."""
    x, y = b.orthogonal_pair(30, 0.95, k=1)
    r = b.exact_pearson_or_none(x, y)
    assert b.oracle_ci(r, 30)[0] >= b.SCREEN_THRESHOLD  # fixture sanity
    pair = _pair(x, y)
    assert pair["uncertainty_gate"]["state"] == "passed"


def test_the_uncertainty_interval_matches_the_textbook_fisher_interval():
    a, c = b.block_pair(181, "redundant")
    pair = _pair(a, c)
    r = b.pearson(a, c)
    lower, upper = b.oracle_ci(r, 181)
    # the oracle and the module sum in different orders; 1e-9 absorbs that only
    assert pair["correlation"] == pytest.approx(r, rel=1e-9)
    assert pair["ci_lower"] == pytest.approx(lower, rel=1e-9)
    assert pair["ci_upper"] == pytest.approx(upper, rel=1e-9)
    assert pair["uncertainty_gate"]["state"] == "passed"


def test_a_pair_whose_interval_lower_bound_is_below_0_65_does_not_meet_the_uncertainty_gate():
    a, c = b.block_pair(181, "diverging")
    r = b.pearson(a, c)
    assert r >= b.SCREEN_THRESHOLD > b.oracle_ci(r, 181)[0]  # fixture sanity
    pair = _pair(a, c)
    assert pair["screen_hit"] is True
    assert pair["uncertainty_gate"]["state"] == "not met"


def test_a_perfectly_correlated_pair_is_undefined_for_the_interval_not_passed_or_not_met():
    """The Fisher transform does not exist at |r| = 1."""
    x = b.wave_returns(60)
    pair = _pair(x, list(x))
    assert pair["screen_hit"] is True
    assert pair["uncertainty_gate"]["state"] == "undefined"
    assert pair["structural_gate"]["state"] == "not evaluated"


# ---------------------------------------------------------------------------
# Structural redundancy gate
# ---------------------------------------------------------------------------


def test_a_pair_that_fails_the_uncertainty_gate_leaves_the_structural_gate_not_evaluated():
    a, c = b.block_pair(181, "diverging")
    pair = _pair(a, c)
    assert pair["uncertainty_gate"]["state"] == "not met"
    assert pair["structural_gate"]["state"] == "not evaluated"
    assert pair["structural_gate"]["text"] == "not evaluated: uncertainty gate not met"


def test_a_pair_with_180_aligned_days_reads_insufficient_data_not_not_met():
    """The uncertainty gate passes; there are too few days for a stressed window of 10.

    Rejects reporting this as not met, a hard-coded day count other than the one
    derived from the two constants, and a float ceiling that rounds 9.0 up."""
    a, c = b.block_pair(180, "redundant")
    pair = _pair(a, c)
    assert pair["uncertainty_gate"]["state"] == "passed"
    assert pair["structural_gate"]["state"] == "insufficient data"
    assert pair["structural_gate"]["text"] == INSUFFICIENT_STRESS
    assert pair["stressed_correlation"] is None


def test_a_pair_with_181_aligned_days_and_a_stressed_correlation_above_0_65_passes():
    a, c = b.block_pair(181, "redundant")
    expected = b.oracle_stressed_correlation(a, c)
    assert expected is not None and expected >= b.SCREEN_THRESHOLD  # fixture sanity
    pair = _pair(a, c)
    assert pair["structural_gate"]["state"] == "passed"
    assert pair["stressed_correlation"] == pytest.approx(expected, rel=1e-9)


def test_a_calm_only_pair_with_a_diverging_stress_window_does_not_meet_the_structural_gate():
    """Highly correlated overall, redundant only in calm markets: the crash
    diversification case the structural gate exists to protect."""
    a, c = b.block_pair(265, "diverging")
    r = b.pearson(a, c)
    assert b.oracle_ci(r, 265)[0] >= b.SCREEN_THRESHOLD  # fixture sanity: uncertainty passes
    stressed = b.oracle_stressed_correlation(a, c)
    assert stressed is not None and stressed < b.SCREEN_THRESHOLD  # fixture sanity
    pair = _pair(a, c)
    assert pair["uncertainty_gate"]["state"] == "passed"
    assert pair["structural_gate"]["state"] == "not met"
    assert pair["stressed_correlation"] == pytest.approx(stressed, rel=1e-9)


def test_a_zero_variance_stressed_window_reads_undefined_not_zero_and_not_insufficient():
    """190 aligned days: enough for 10 stressed days, but one series is constant on
    all of them. Rejects merging the two causes of a missing stressed correlation."""
    calm_a, calm_b = b.calm_block(180)
    a = calm_a + [-1.0] * 10
    c = calm_b + [-1.0 + 0.05 * math.sin(0.9 * i) for i in range(10)]
    assert b.oracle_stressed_correlation(a, c) is None  # fixture sanity
    assert b.oracle_ci(b.pearson(a, c), 190)[0] >= b.SCREEN_THRESHOLD  # uncertainty passes
    pair = _pair(a, c)
    assert pair["structural_gate"]["state"] == "undefined"
    assert (
        pair["structural_gate"]["text"]
        == "undefined: stressed-window correlation cannot be computed"
    )
    assert pair["stressed_correlation"] is None


def test_the_day_count_in_the_insufficient_message_is_derived_from_the_constants():
    a, c = b.block_pair(150, "redundant")
    pair = _pair(a, c)
    assert pair["structural_gate"]["text"] == (
        f"insufficient data: needs {b.days_needed_for_structural_gate()} aligned days"
    )


# ---------------------------------------------------------------------------
# Holdings overlap is corroborating evidence only
# ---------------------------------------------------------------------------


def test_holdings_overlap_is_the_jaccard_ratio_of_the_two_ticker_sets():
    a, c = b.block_pair(181, "redundant")
    pair = _pair(a, c, {"sym-a": ["SPY", "QQQ", "TLT"], "sym-b": ["SPY", "QQQ", "GLD", "IEF"]})
    assert pair["holdings_overlap"] == pytest.approx(2 / 5, rel=1e-12)


@pytest.mark.parametrize(
    "tickers",
    [
        {"sym-a": [], "sym-b": ["SPY"]},
        {"sym-a": ["SPY"], "sym-b": None},
        {"sym-a": ["SPY"]},
        None,
    ],
    ids=["one_side_empty", "one_side_none", "one_side_missing", "no_holdings_at_all"],
)
def test_unavailable_holdings_give_no_overlap_never_zero_and_leave_the_gates_alone(tickers):
    a, c = b.block_pair(181, "redundant")
    with_holdings = _pair(a, c, {"sym-a": ["SPY", "QQQ"], "sym-b": ["SPY", "TLT"]})
    without = _pair(a, c, tickers)
    assert without["holdings_overlap"] is None
    assert without["uncertainty_gate"] == with_holdings["uncertainty_gate"]
    assert without["structural_gate"] == with_holdings["structural_gate"]
    assert without["screen_hit"] == with_holdings["screen_hit"]


def test_zero_overlap_between_two_non_empty_holdings_is_a_real_zero():
    a, c = b.block_pair(181, "redundant")
    pair = _pair(a, c, {"sym-a": ["SPY"], "sym-b": ["TLT"]})
    assert pair["holdings_overlap"] == 0.0
    assert pair["holdings_overlap"] is not None


# ---------------------------------------------------------------------------
# Parity with the existing recommender on the same series (golden fixture)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def parity_fixture():
    return json.loads(_PARITY_FIXTURE.read_text(encoding="utf-8"))


def _returns_by_symphony(fixture):
    dates = fixture["dates"][1:]
    return {s: dict(zip(dates, v, strict=True)) for s, v in fixture["returns_pct"].items()}


def test_every_pair_state_matches_the_golden_fixture_derived_from_independent_oracles(
    parity_fixture,
):
    pairs = tr.evaluate_redundancy(_returns_by_symphony(parity_fixture))
    assert len(pairs) == 15
    for pair in pairs:
        expected = parity_fixture["expected_pairs"][f"{pair['sym_a']}|{pair['sym_b']}"]
        assert pair["screen_hit"] is expected["screen_hit"], pair
        if expected["screen_hit"]:
            assert pair["uncertainty_gate"]["state"] == expected["uncertainty"], pair
            assert pair["structural_gate"]["state"] == expected["structural"], pair


def test_the_pairs_that_pass_both_gates_are_exactly_the_pairs_the_recommender_recommends(
    parity_fixture, tmp_path, monkeypatch
):
    """The screen, both gates and the stressed window must give the existing
    recommender's answer on one series set, with every numeric field equal.

    Rejects a re-implemented gate with a drifted constant, formula or tie-break."""
    dates = parity_fixture["dates"][1:]
    rows = {
        s: {d: (r, 0.0) for d, r in zip(dates, v, strict=True)}
        for s, v in parity_fixture["returns_pct"].items()
    }
    seed_state_db(tmp_path, monkeypatch, series_by_symphony=rows)
    recs = retirement_recommender.build_recommendations(days=None)
    assert len(recs) == 1  # fixture sanity: exactly one redundant pair

    pairs = tr.evaluate_redundancy(_returns_by_symphony(parity_fixture))
    passing = {
        frozenset((p["sym_a"], p["sym_b"]))
        for p in pairs
        if p["uncertainty_gate"]["state"] == "passed" and p["structural_gate"]["state"] == "passed"
    }
    assert passing == {frozenset((r["candidate_id"], r["sibling_id"])) for r in recs}

    rec = recs[0]
    pair = next(
        p for p in pairs if {p["sym_a"], p["sym_b"]} == {rec["candidate_id"], rec["sibling_id"]}
    )
    # identical primitives, so the match is tight; 1e-12 absorbs only summation order
    assert pair["correlation"] == pytest.approx(rec["correlation"], rel=1e-12)
    assert pair["ci_lower"] == pytest.approx(rec["ci_lower"], rel=1e-12)
    assert pair["ci_upper"] == pytest.approx(rec["ci_upper"], rel=1e-12)
    assert pair["stressed_correlation"] == pytest.approx(rec["stressed_correlation"], rel=1e-12)
    assert pair["n_obs"] == rec["n_obs"]
    assert pair["holdings_overlap"] == rec["holdings_overlap"]


def test_the_review_flags_the_same_candidate_and_composites_as_the_existing_recommender(
    parity_fixture, tmp_path, monkeypatch
):
    """The same returns, given as Composer value series over a window that spans all
    of them, must name the recommender's candidate and score it identically."""
    dates = [date.fromisoformat(d) for d in parity_fixture["dates"]]
    returns = parity_fixture["returns_pct"]
    rows = {
        s: {d.isoformat(): (r, 0.0) for d, r in zip(dates[1:], v, strict=True)}
        for s, v in returns.items()
    }
    seed_state_db(tmp_path, monkeypatch, series_by_symphony=rows)
    (rec,) = retirement_recommender.build_recommendations(days=None)

    roster = [b.composer_symphony(s, s.title(), dates, v) for s, v in returns.items()]
    result = b.review(roster, as_of=b.AS_OF, threshold=len(dates))

    flagged = [e for e in result.entries if b.item_of(e, "redundancy")["retire_candidate"]]
    assert [e["symphony_id"] for e in flagged] == [rec["candidate_id"]]
    entry = flagged[0]
    assert entry["verdict"] == "retire_candidate"
    pair = b.item_of(entry, "redundancy")["pair"]
    assert pair["sibling_id"] == rec["sibling_id"]
    assert pair["n_obs"] == rec["n_obs"]
    assert pair["correlation"]["value"] == pytest.approx(rec["correlation"], rel=1e-9)
    assert b.value_of(entry, "composite", "composite") == pytest.approx(
        rec["candidate_composite"], abs=1e-9
    )
    sibling = b.entry_of(result, rec["sibling_id"])
    assert b.value_of(sibling, "composite", "composite") == pytest.approx(
        rec["sibling_composite"], abs=1e-9
    )


# ---------------------------------------------------------------------------
# The pair a row shows
# ---------------------------------------------------------------------------


def test_a_symphony_with_no_screen_hit_reads_no_redundant_sibling_not_unavailable():
    days = b.last_n_nyse_days(b.AS_OF, 20)
    roster = [
        b.composer_symphony(f"quiet-{k}", f"Quiet {k}", days, b.sine_returns(19, k))
        for k in (1, 2, 3)
    ]
    result = b.review(roster, as_of=b.AS_OF, threshold=20)
    for k in (1, 2, 3):
        item = b.item_of(b.entry_of(result, f"quiet-{k}"), "redundancy")
        assert item["status"] == "available", k
        assert item["pair"] is None, k
        assert item["retire_candidate"] is False, k


def test_a_correlated_sibling_that_is_not_yet_eligible_is_shown_but_never_flags_anyone():
    """keep-a and the young sibling are almost the same series and pass both gates, and
    the sibling is the stronger performer, so keep-a WOULD be flagged if the sibling
    were scored. The sibling has no composite, so nobody is flagged."""
    roster, threshold = b.roster_with_ineligible_correlated_sibling()
    result = b.review(roster, as_of=b.AS_OF, threshold=threshold)
    keeper = b.entry_of(result, "keep-a")
    young = b.entry_of(result, "young-c")
    item = b.item_of(keeper, "redundancy")
    assert item["status"] == "available"
    assert item["retire_candidate"] is False
    pair = item["pair"]
    assert pair["sibling_id"] == "young-c"
    assert pair["sibling_name"] == "Charlie Newcomer"
    assert pair["uncertainty_gate"]["state"] == "passed"
    assert pair["structural_gate"]["state"] == "passed"
    assert pair["note"] == "sibling has no composite: not yet eligible"
    assert pair["n_obs"] == 199
    assert keeper["verdict"] != "retire_candidate"
    assert young["eligible"] is False
    assert young["verdict"] is None
    assert not any(b.item_of(e, "redundancy")["retire_candidate"] for e in result.entries)


def test_a_candidate_is_shown_with_the_lower_correlation_pair_that_passed_both_gates():
    """tail-x correlates most with tail-y1 but their stressed windows diverge; the pair
    with tail-y2 is slightly less correlated and passes both gates, with tail-x the
    weaker performer. The row shows the pair that actually flagged it."""
    roster, threshold = b.roster_with_two_redundant_candidates()
    returns = {s.symphony_id: b.returns_from_values(s.composer_values) for s in roster}
    r_y1 = b.pearson(returns["tail-x"], returns["tail-y1"])
    r_y2 = b.pearson(returns["tail-x"], returns["tail-y2"])
    assert (
        r_y1 > r_y2 >= b.SCREEN_THRESHOLD
    )  # fixture sanity: the flagging pair is not the strongest

    result = b.review(roster, as_of=b.AS_OF, threshold=threshold)
    x = b.entry_of(result, "tail-x")
    item = b.item_of(x, "redundancy")
    assert item["retire_candidate"] is True
    assert x["verdict"] == "retire_candidate"
    pair = item["pair"]
    assert pair["sibling_id"] == "tail-y2"
    assert pair["uncertainty_gate"]["state"] == "passed"
    assert pair["structural_gate"]["state"] == "passed"
    assert pair["correlation"]["value"] == pytest.approx(r_y2, rel=1e-9)
    assert pair["n_obs"] == 265
    assert item["window"]["days"] == 265


def test_a_symphony_that_is_not_flagged_shows_its_strongest_hit_even_when_a_gate_fails():
    roster, threshold = b.roster_with_two_redundant_candidates()
    result = b.review(roster, as_of=b.AS_OF, threshold=threshold)
    y1 = b.item_of(b.entry_of(result, "tail-y1"), "redundancy")
    assert y1["retire_candidate"] is False
    assert y1["pair"]["sibling_id"] == "tail-x"
    assert y1["pair"]["structural_gate"]["state"] == "not met"
    y2 = b.item_of(b.entry_of(result, "tail-y2"), "redundancy")
    assert y2["retire_candidate"] is False
    assert y2["pair"]["sibling_id"] == "tail-x"
    assert y2["pair"]["structural_gate"]["state"] == "passed"
