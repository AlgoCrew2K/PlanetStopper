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

import dataclasses
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
    """The message names the shortest history that yields 10 stressed days (5% of n,
    rounded up), whatever length the pair actually has."""
    assert b.days_needed_for_structural_gate() == NEEDED_DAYS  # oracle agrees with the plan
    a, c = b.block_pair(150, "redundant")
    pair = _pair(a, c)
    assert pair["structural_gate"]["state"] == "insufficient data"
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


ENOUGH_DAYS = retirement_recommender.MIN_OBS_FLOOR + 10  # aligned days for a trusted screen
NO_SIBLING_TEXT = "no redundant sibling"


def _quiet_roster(count=3):
    """Mutually orthogonal series with enough aligned days for the screen to be trusted."""
    days = b.last_n_nyse_days(b.AS_OF, ENOUGH_DAYS + 1)
    return [
        b.composer_symphony(f"quiet-{k}", f"Quiet {k}", days, b.sine_returns(ENOUGH_DAYS, k))
        for k in range(1, count + 1)
    ]


def test_a_symphony_with_no_screen_hit_reads_no_redundant_sibling_not_unavailable():
    result = b.review(_quiet_roster(), as_of=b.AS_OF, threshold=20)
    for k in (1, 2, 3):
        item = b.item_of(b.entry_of(result, f"quiet-{k}"), "redundancy")
        assert item["status"] == "available", k
        assert item["pair"] is None, k
        assert item["retire_candidate"] is False, k


def test_a_symphony_with_no_screen_hit_carries_the_text_no_redundant_sibling_in_its_record():
    """The API returns the stored record as it is, so the words live in the record and
    not in a renderer. Rejects an item that is available with nothing to read."""
    result = b.review(_quiet_roster(), as_of=b.AS_OF, threshold=20)
    record = json.loads(json.dumps(tr.run_record(result, b.et(2026, 12, 31, 17, 5))))
    for entry in record["entries"]:
        item = entry["evidence"]["redundancy"]
        assert item["status"] == "available", entry["symphony_id"]
        assert item["pair"] is None, entry["symphony_id"]
        assert item["summary"] == NO_SIBLING_TEXT, entry["symphony_id"]


def test_the_no_sibling_text_is_not_carried_by_an_item_that_shows_a_pair_or_has_no_result():
    """Rejects stamping the text on every item."""
    roster, threshold = b.roster_with_ineligible_correlated_sibling()
    result = b.review(roster, as_of=b.AS_OF, threshold=threshold)
    shown = b.item_of(b.entry_of(result, "keep-a"), "redundancy")
    assert shown["pair"] is not None  # fixture sanity
    assert shown.get("summary") != NO_SIBLING_TEXT
    young = b.item_of(b.entry_of(result, "young-c"), "redundancy")
    assert young["status"] == "unavailable"  # fixture sanity
    assert young.get("summary") != NO_SIBLING_TEXT


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


# ---------------------------------------------------------------------------
# A candidate with several passing pairs keeps the strongest, as the recommender does
# ---------------------------------------------------------------------------


def _oracle_pair_passes_both_gates(x, y):
    n = len(x)
    r = b.pearson(x, y)
    stressed = b.oracle_stressed_correlation(x, y)
    return (
        b.oracle_ci(r, n)[0] >= b.SCREEN_THRESHOLD
        and stressed is not None
        and stressed >= b.SCREEN_THRESHOLD
    )


@pytest.mark.parametrize("stronger_sorts_first", [True, False], ids=["id_first", "id_last"])
def test_a_candidate_with_two_passing_pairs_is_shown_with_the_more_correlated_one(
    stronger_sorts_first,
):
    """weak-x is the lowest composite and both of its pairs pass both gates. The pair it
    shows, and the sibling it names, are the ones with the higher correlation, whichever
    sibling's id sorts first. Rejects choosing the weaker pair, the first pair found and
    the last pair found."""
    roster = b.roster_with_a_candidate_in_two_passing_pairs(
        stronger_sorts_first=stronger_sorts_first
    )
    returns = {s.symphony_id: b.returns_from_values(s.composer_values) for s in roster}
    r_a = b.pearson(returns["weak-x"], returns["sib-a"])
    r_b = b.pearson(returns["weak-x"], returns["sib-b"])
    stronger, weaker = ("sib-a", "sib-b") if r_a > r_b else ("sib-b", "sib-a")
    # fixture sanity: both pairs pass both gates, weak-x scores lowest, and the stronger
    # sibling's id is where the parameter says it is
    assert abs(r_a - r_b) > 0.01
    assert _oracle_pair_passes_both_gates(returns["weak-x"], returns[stronger])
    assert _oracle_pair_passes_both_gates(returns["weak-x"], returns[weaker])
    assert (stronger == "sib-a") == stronger_sorts_first
    window = -(b.NOTE_THRESHOLD - 1)
    composites = b.oracle_composites(
        {sid: b.oracle_metrics(series[window:]) for sid, series in returns.items()}
    )
    assert composites["weak-x"] < min(composites["sib-a"], composites["sib-b"])

    result = b.review(roster, as_of=b.AS_OF, threshold=b.NOTE_THRESHOLD)
    item = b.item_of(b.entry_of(result, "weak-x"), "redundancy")
    assert item["retire_candidate"] is True
    assert item["pair"]["sibling_id"] == stronger
    assert item["pair"]["correlation"]["value"] == pytest.approx(max(r_a, r_b), rel=1e-9)


def test_the_pair_a_candidate_shows_is_the_pair_the_existing_recommender_keeps(
    tmp_path, monkeypatch
):
    """The same series seeded as shadow rows for the existing recommender: for the
    candidate with two passing pairs it keeps one recommendation, and the review's row
    names the same sibling. The flagged set is the recommender's set."""
    roster = b.roster_with_a_candidate_in_two_passing_pairs(stronger_sorts_first=False)
    dates = sorted(roster[0].composer_values)
    seed_state_db(tmp_path, monkeypatch, series_by_symphony=b.shadow_rows_of(roster))
    recs = {r["candidate_id"]: r for r in retirement_recommender.build_recommendations(days=None)}
    assert "weak-x" in recs  # fixture sanity

    result = b.review(roster, as_of=b.AS_OF, threshold=len(dates))
    flagged = {
        e["symphony_id"] for e in result.entries if b.item_of(e, "redundancy")["retire_candidate"]
    }
    assert flagged == set(recs)
    pair = b.item_of(b.entry_of(result, "weak-x"), "redundancy")["pair"]
    assert pair["sibling_id"] == recs["weak-x"]["sibling_id"]
    assert pair["correlation"]["value"] == pytest.approx(recs["weak-x"]["correlation"], rel=1e-9)


# ---------------------------------------------------------------------------
# The structural gate is the recommender's gate
# ---------------------------------------------------------------------------


def test_the_structural_gate_state_follows_the_recommenders_gate_function(monkeypatch):
    """Both pairs have a defined stressed correlation. With the recommender's gate
    function inverted, the states invert with it, so the module asks it and does not
    compare the number to the threshold itself. The module keeps calling it as
    retirement_recommender.evaluate_structural_redundancy_gate.

    Rejects a second copy of the comparison."""
    redundant = b.block_pair(181, "redundant")
    diverging = b.block_pair(265, "diverging")
    assert _pair(*redundant)["structural_gate"]["state"] == "passed"  # fixture sanity
    assert _pair(*diverging)["structural_gate"]["state"] == "not met"  # fixture sanity

    real = retirement_recommender.evaluate_structural_redundancy_gate

    def inverted(*args, **kwargs):
        verdict = real(*args, **kwargs)
        return dataclasses.replace(verdict, passed=not verdict.passed)

    monkeypatch.setattr(retirement_recommender, "evaluate_structural_redundancy_gate", inverted)
    assert _pair(*redundant)["structural_gate"]["state"] == "not met"
    assert _pair(*diverging)["structural_gate"]["state"] == "passed"


# ---------------------------------------------------------------------------
# What a row says about a sibling that has no composite
# ---------------------------------------------------------------------------

NOTE_PREFIX = "sibling has no composite: "


@pytest.mark.parametrize(
    ("sibling", "reason_starts"),
    [
        ("tenure_unknown", "tenure unavailable"),
        ("thin_coverage", "insufficient coverage in the common window"),
        ("flat_window", "not computable"),
    ],
)
def test_a_sibling_without_a_composite_is_shown_with_the_reason_it_has_none(sibling, reason_starts):
    """The pair passes both gates and the subject is scored, but the sibling has no
    composite, so nobody is flagged and the note carries the sibling's own reason. A
    sibling with an unknown tenure is never described as one that will become eligible.

    Rejects dropping the pair, a generic note, and a flag on a pair with an unscored
    member."""
    result = b.review(b.note_family_roster(sibling), as_of=b.AS_OF, threshold=b.NOTE_THRESHOLD)
    subject = b.entry_of(result, "note-subject")
    other = b.entry_of(result, "note-sibling")
    reason = b.item_of(other, "composite")["reason"]
    assert reason.startswith(reason_starts)  # fixture sanity: the intended branch
    item = b.item_of(subject, "redundancy")
    assert item["status"] == "available"
    assert item["retire_candidate"] is False
    assert item["pair"]["sibling_id"] == "note-sibling"
    assert item["pair"]["uncertainty_gate"]["state"] == "passed"
    assert item["pair"]["structural_gate"]["state"] == "passed"
    assert item["pair"]["note"] == NOTE_PREFIX + reason
    assert subject["verdict"] != "retire_candidate"
    assert other["verdict"] != "retire_candidate"


def test_a_sibling_with_unknown_tenure_is_not_described_as_not_yet_eligible():
    """Its tenure may never be known, so a note that says it is not yet eligible promises
    something nobody can keep."""
    result = b.review(
        b.note_family_roster("tenure_unknown"), as_of=b.AS_OF, threshold=b.NOTE_THRESHOLD
    )
    note = b.item_of(b.entry_of(result, "note-subject"), "redundancy")["pair"]["note"]
    assert note == "sibling has no composite: tenure unavailable"
    assert "not yet eligible" not in note


def test_two_eligible_shadow_basis_symphonies_are_paired_with_the_basis_note_and_nobody_is_flagged():
    """Same basis, so the pair is computed and passes both gates; neither has a
    composite because the composite is Composer-basis only."""
    result = b.review(b.shadow_pair_roster(), as_of=b.AS_OF, threshold=b.NOTE_THRESHOLD)
    for sid, sibling in (("shadow-a", "shadow-b"), ("shadow-b", "shadow-a")):
        entry = b.entry_of(result, sid)
        assert entry["basis"]["kind"] == "shadow", sid  # fixture sanity
        assert b.item_of(entry, "composite")["reason"] == "not comparable: basis differs", sid
        item = b.item_of(entry, "redundancy")
        assert item["status"] == "available", sid
        assert item["retire_candidate"] is False, sid
        assert item["pair"]["sibling_id"] == sibling, sid
        assert item["pair"]["uncertainty_gate"]["state"] == "passed", sid
        assert item["pair"]["structural_gate"]["state"] == "passed", sid
        assert item["pair"]["n_obs"] == b.NOTE_RETURNS, sid
        assert item["pair"]["note"] == "sibling has no composite: basis differs", sid
        assert entry["verdict"] != "retire_candidate", sid


# ---------------------------------------------------------------------------
# When no pair can be evaluated, the item says so
# ---------------------------------------------------------------------------

SHORT_THRESHOLD = 20


def _short_overlap_roster(*, correlated, third=False):
    """short-a has 40 points, short-b 25 (tenure 25, so eligible at threshold 20): their
    shared returns number 24, below the 30 an interpretable correlation needs."""
    days = b.last_n_nyse_days(b.AS_OF, 40)
    a_returns = b.wave_returns(39)
    b_returns = [0.9 * r + 0.01 for r in a_returns[-24:]] if correlated else b.sine_returns(24, 3)
    roster = [
        b.composer_symphony("short-a", "Short A", days, a_returns),
        b.composer_symphony("short-b", "Short B", days[-25:], b_returns),
    ]
    if third:
        roster.append(b.composer_symphony("short-c", "Short C", days, b.sine_returns(39, 5)))
    return roster


@pytest.mark.parametrize("correlated", [True, False], ids=["correlated", "uncorrelated"])
def test_when_every_pair_has_too_few_aligned_days_the_item_says_insufficient_data(correlated):
    """Whether the short overlap looks correlated or not, it cannot support 'no
    redundant sibling' or a shown pair. The item is unavailable with the gate's own
    wording and the verdict lists it as missing."""
    result = b.review(
        _short_overlap_roster(correlated=correlated), as_of=b.AS_OF, threshold=SHORT_THRESHOLD
    )
    expected = f"insufficient data: needs {retirement_recommender.MIN_OBS_FLOOR} aligned days"
    for sid in ("short-a", "short-b"):
        entry = b.entry_of(result, sid)
        item = b.item_of(entry, "redundancy")
        assert item["status"] == "unavailable", sid
        assert item["reason"] == expected, sid
        assert item["pair"] is None, sid
        assert item["retire_candidate"] is False, sid
        assert "redundancy" in entry["missing_items"], sid


def test_one_pair_with_enough_days_keeps_the_item_available():
    """short-a also shares 39 days with short-c, so it has a pair that can be evaluated
    (no hit). short-b shares 24 days with each, so every pair of its own is too short."""
    result = b.review(
        _short_overlap_roster(correlated=True, third=True),
        as_of=b.AS_OF,
        threshold=SHORT_THRESHOLD,
    )
    a = b.entry_of(result, "short-a")
    assert b.item_of(a, "redundancy")["status"] == "available"
    assert b.item_of(a, "redundancy")["summary"] == NO_SIBLING_TEXT
    assert "redundancy" not in a["missing_items"]
    short_b = b.entry_of(result, "short-b")
    assert b.item_of(short_b, "redundancy")["status"] == "unavailable"
    assert "redundancy" in short_b["missing_items"]


# ---------------------------------------------------------------------------
# A peer with no history, a peer on another basis, and a symphony with no peer
# ---------------------------------------------------------------------------

ROSTER_DAYS = b.last_n_nyse_days(b.AS_OF, 31)
ROSTER_THRESHOLD = 30


def _lone_composer():
    return b.composer_symphony("lone-a", "Lone A", ROSTER_DAYS, b.wave_returns(30, freq=0.4))


def test_a_symphony_with_no_other_symphony_reads_not_applicable_nothing_to_compare():
    entry = b.entry_of(
        b.review([_lone_composer()], as_of=b.AS_OF, threshold=ROSTER_THRESHOLD), "lone-a"
    )
    item = b.item_of(entry, "redundancy")
    assert item["status"] == "not_applicable"
    assert item["reason"] == "no other symphonies to compare"


def test_a_peer_with_no_history_is_named_as_the_reason_there_is_nothing_to_compare():
    """The peer exists, so 'no other symphonies' is false; it has no basis, so 'bases
    differ' is false. Rejects either of those two texts."""
    void = b.no_history_symphony("void-peer", "Void Peer", ROSTER_DAYS[0].isoformat())
    result = b.review([_lone_composer(), void], as_of=b.AS_OF, threshold=ROSTER_THRESHOLD)
    entry = b.entry_of(result, "lone-a")
    item = b.item_of(entry, "redundancy")
    assert item["status"] == "unavailable"
    assert item["reason"] == "no history for any other symphony"
    assert item["pair"] is None
    assert item["retire_candidate"] is False
    assert "redundancy" in entry["missing_items"]


@pytest.mark.parametrize("peer_tenure_days", [30, 10], ids=["eligible_peer", "young_peer"])
def test_a_peer_with_history_on_another_basis_reads_bases_differ_whether_or_not_it_is_eligible(
    peer_tenure_days,
):
    """Whether the other symphony is eligible has no bearing on whether its basis
    differs. Rejects letting eligibility decide this text."""
    days = ROSTER_DAYS[-peer_tenure_days:]
    peer = b.make_symphony(
        "shadow-peer",
        "Shadow Peer",
        days[0].isoformat(),
        series_failure="unavailable",
        shadow_returns=b.by_date(days[1:], b.wave_returns(peer_tenure_days - 1, freq=1.4)),
    )
    result = b.review([_lone_composer(), peer], as_of=b.AS_OF, threshold=ROSTER_THRESHOLD)
    item = b.item_of(b.entry_of(result, "lone-a"), "redundancy")
    assert item["status"] == "unavailable"
    assert item["reason"] == "bases differ"
