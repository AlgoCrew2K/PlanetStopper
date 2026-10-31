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


def _all_text(value):
    """Every string anywhere inside a value, dictionary keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, inner in value.items():
            yield from _all_text(key)
            yield from _all_text(inner)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            yield from _all_text(inner)


def _says_no_sibling(item):
    """Whether the words of the claim appear anywhere in the item (summary, reason or note)."""
    return any("no redundant sibling" in text for text in _all_text(item))


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
    window = pair["window"]
    assert (window["first"], window["last"], window["days"]) == (labels[50], labels[99], 50)


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


@pytest.mark.parametrize(
    ("build", "state"),
    [
        pytest.param(
            lambda: b.orthogonal_pair(29, 0.95, k=1),
            "insufficient data",
            id="too_few_days_for_the_interval",
        ),
        pytest.param(
            lambda: (b.wave_returns(60), b.wave_returns(60)),
            "undefined",
            id="a_correlation_of_exactly_one",
        ),
        pytest.param(
            lambda: b.block_pair(181, "diverging"), "not met", id="interval_below_the_threshold"
        ),
    ],
)
def test_a_structural_gate_that_did_not_run_names_the_state_of_the_uncertainty_gate_that_stopped_it(
    build, state
):
    """The text says why the gate did not run, in the words of the gate that stopped it. The
    words "uncertainty gate not met" belong to a pair whose interval was computed and fell
    short, and to no other pair. Rejects one fixed text for every reason."""
    a, c = build()
    pair = _pair(a, c)
    assert pair["uncertainty_gate"]["state"] == state  # fixture sanity
    gate = pair["structural_gate"]
    assert gate["state"] == "not evaluated"
    assert gate["text"] == f"not evaluated: uncertainty gate {state}"
    assert ("uncertainty gate not met" in gate["text"]) is (state == "not met")


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


def test_the_recommender_parity_golden_holds_no_day_of_plus_100_percent_or_more():
    """The review gives a window with no down day a drawdown of exactly zero, where the
    recommender takes the raw answer of the shared metric function, which reads such a
    series as prices once a day reaches +100 percent and reports about -90 percent. The
    difference is intended, so no window of this golden may be able to show it."""
    fixture = json.loads(_PARITY_FIXTURE.read_text(encoding="utf-8"))
    for symphony, returns in fixture["returns_pct"].items():
        assert max(returns) < 100.0, symphony


# ---------------------------------------------------------------------------
# The pair a row shows
# ---------------------------------------------------------------------------


# Aligned days after which a comparison that finds no correlated pair says something: more
# than the days the structural gate needs, which is the larger of its two floors.
ENOUGH_DAYS = NEEDED_DAYS + 19
NO_SIBLING_TEXT = "no redundant sibling"


def _quiet_roster(count=3):
    """Mutually orthogonal series, every pair compared on enough aligned days for its
    silence to count as a finding."""
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
        assert item.get("summary") == NO_SIBLING_TEXT, entry["symphony_id"]


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
    # the only peer on its basis has no composite, so the item is shown but not used
    assert item["status"] == "unavailable"
    assert "no same-basis peer has a composite" in item["reason"]
    assert "redundancy" in subject["missing_items"]
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
        # neither has a composite, so the pair is shown and the item is not used
        assert item["status"] == "unavailable", sid
        assert "this symphony has no composite" in item["reason"], sid
        assert item["retire_candidate"] is False, sid
        assert item["pair"]["sibling_id"] == sibling, sid
        assert item["pair"]["uncertainty_gate"]["state"] == "passed", sid
        assert item["pair"]["structural_gate"]["state"] == "passed", sid
        assert item["pair"]["n_obs"] == b.NOTE_RETURNS, sid
        assert item["pair"]["note"] == "sibling has no composite: basis differs", sid
        assert entry["verdict"] != "retire_candidate", sid


# ---------------------------------------------------------------------------
# A pair on too few aligned days is shown, never hidden
# ---------------------------------------------------------------------------

SHORT_THRESHOLD = 20
SHORT_OVERLAP = 24  # aligned returns of the short pair: below the 30 an interval needs


def _short_overlap_roster(*, correlated, third=None):
    """short-a has 40 points, short-b 25 (tenure 25, so eligible at threshold 20): their
    shared returns number 24, below the 30 an interpretable correlation needs.

    ``third`` adds short-c with 40 points: ``"unrelated"`` is uncorrelated with short-a
    over 39 shared returns (an evaluable pair that is no screen hit); ``"close"``
    follows short-a at about 0.93 over those 39 (an evaluable screen hit that is less
    correlated than the short pair and, on so few days, cannot reach the structural gate)."""
    days = b.last_n_nyse_days(b.AS_OF, 40)
    a_returns = b.wave_returns(39)
    b_returns = [0.9 * r + 0.01 for r in a_returns[-24:]] if correlated else b.sine_returns(24, 3)
    roster = [
        b.composer_symphony("short-a", "Short A", days, a_returns),
        b.composer_symphony("short-b", "Short B", days[-25:], b_returns),
    ]
    if third == "unrelated":
        roster.append(b.composer_symphony("short-c", "Short C", days, b.sine_returns(39, 5)))
    elif third == "close":
        noisy = [r + 0.2 * math.sin(2.9 * i + 1.0) for i, r in enumerate(a_returns)]
        roster.append(b.composer_symphony("short-c", "Short C", days, noisy))
    return roster


def _returns_of(roster, symphony_id):
    symphony = next(s for s in roster if s.symphony_id == symphony_id)
    return b.returns_from_values(symphony.composer_values)


def test_when_every_pair_has_too_few_aligned_days_and_none_reaches_the_screen_the_item_says_so():
    """A short overlap that shows no correlation cannot support 'no redundant sibling'
    either: it is too short to say. The item is unavailable, its reason names the peer and the
    aligned days it was compared on, the words of the claim appear nowhere in it, and the
    verdict lists it as missing."""
    result = b.review(
        _short_overlap_roster(correlated=False), as_of=b.AS_OF, threshold=SHORT_THRESHOLD
    )
    for sid, peer in (("short-a", "Short B"), ("short-b", "Short A")):
        entry = b.entry_of(result, sid)
        item = b.item_of(entry, "redundancy")
        assert item["status"] == "unavailable", sid
        assert f"{peer} ({SHORT_OVERLAP} aligned days" in item["reason"], sid
        assert not _says_no_sibling(item), sid
        assert item["pair"] is None, sid
        assert item["retire_candidate"] is False, sid
        assert "redundancy" in entry["missing_items"], sid


def test_a_screen_hit_on_too_few_aligned_days_is_shown_with_the_gate_reading_insufficient_data():
    """The pair reaches the screen threshold on 24 days. It is shown with each gate's own
    state, the uncertainty gate saying how many days it needs and the structural gate not
    evaluated, nobody is flagged, and the item is not used in the verdict, with a reason that
    names the peer and the days. Rejects hiding the pair, and 'no redundant sibling'."""
    roster = _short_overlap_roster(correlated=True)
    short_a = _returns_of(roster, "short-a")[-SHORT_OVERLAP:]
    oracle = b.pearson(short_a, _returns_of(roster, "short-b"))
    assert oracle >= b.SCREEN_THRESHOLD  # fixture sanity: a screen hit
    result = b.review(roster, as_of=b.AS_OF, threshold=SHORT_THRESHOLD)
    sibling_name = {"short-a": "Short A", "short-b": "Short B"}
    for sid, sibling in (("short-a", "short-b"), ("short-b", "short-a")):
        entry = b.entry_of(result, sid)
        item = b.item_of(entry, "redundancy")
        assert item["status"] == "unavailable", sid
        assert item["retire_candidate"] is False, sid
        assert not _says_no_sibling(item), sid
        # shown, but its gates were not evaluated, so it decides nothing
        assert f"{sibling_name[sibling]} ({SHORT_OVERLAP} aligned days" in item["reason"], sid
        assert "redundancy" in entry["missing_items"], sid
        pair = item["pair"]
        assert pair["sibling_id"] == sibling, sid
        assert pair["n_obs"] == SHORT_OVERLAP, sid
        assert item["window"]["days"] == SHORT_OVERLAP, sid
        assert pair["correlation"]["value"] == pytest.approx(oracle, rel=1e-9), sid
        assert pair["uncertainty_gate"]["state"] == "insufficient data", sid
        assert pair["uncertainty_gate"]["text"] == INSUFFICIENT_UNCERTAINTY, sid
        assert pair["structural_gate"]["state"] == "not evaluated", sid
        assert (
            pair["structural_gate"]["text"] == "not evaluated: uncertainty gate insufficient data"
        )


def test_an_unrelated_evaluable_peer_changes_neither_the_pair_a_short_hit_shows_nor_its_claim():
    """Adding a third symphony that is uncorrelated with both and has enough days to be
    evaluated must leave the row as it was. Rejects an item that shows the short pair
    only until another pair can be evaluated, and one that then claims no sibling."""
    alone = b.review(
        _short_overlap_roster(correlated=True), as_of=b.AS_OF, threshold=SHORT_THRESHOLD
    )
    beside = b.review(
        _short_overlap_roster(correlated=True, third="unrelated"),
        as_of=b.AS_OF,
        threshold=SHORT_THRESHOLD,
    )
    for sid in ("short-a", "short-b"):
        before = b.item_of(b.entry_of(alone, sid), "redundancy")
        after = b.item_of(b.entry_of(beside, sid), "redundancy")
        assert before["pair"] is not None, sid  # fixture sanity
        for key in ("status", "pair", "window", "retire_candidate"):
            assert after[key] == before[key], (sid, key)
        assert after.get("summary") == before.get("summary"), sid


def test_a_screen_hit_on_too_few_days_outranks_an_evaluable_hit_of_lower_correlation():
    """The row shows the highest-correlation pair at or above 0.65, whatever its day
    count. short-a correlates 1.0 with short-b over 24 days and about 0.93 with short-c
    over 39, an interval that passes; the structural gate cannot be reached on 39 days, so
    nobody is flagged. Rejects preferring a pair that can be evaluated."""
    roster = _short_overlap_roster(correlated=True, third="close")
    short_a = _returns_of(roster, "short-a")
    r_ab = b.pearson(short_a[-SHORT_OVERLAP:], _returns_of(roster, "short-b"))
    r_ac = b.pearson(short_a, _returns_of(roster, "short-c"))
    assert r_ab > r_ac >= b.SCREEN_THRESHOLD  # fixture sanity: the short pair is the stronger
    assert b.oracle_ci(r_ac, len(short_a))[0] >= b.SCREEN_THRESHOLD  # fixture sanity: evaluable

    result = b.review(roster, as_of=b.AS_OF, threshold=SHORT_THRESHOLD)
    item = b.item_of(b.entry_of(result, "short-a"), "redundancy")
    assert item["status"] == "unavailable"
    assert item["retire_candidate"] is False
    pair = item["pair"]
    assert pair["sibling_id"] == "short-b"
    assert pair["n_obs"] == SHORT_OVERLAP
    assert pair["correlation"]["value"] == pytest.approx(r_ab, rel=1e-9)
    assert pair["uncertainty_gate"]["state"] == "insufficient data"
    assert not _says_no_sibling(item)


def test_no_redundant_sibling_is_only_said_when_no_pair_reaches_the_screen():
    """Across the rosters of this section, an item that carries the words has no pair at
    or above 0.65 with any other symphony, from the oracle on their shared returns.
    Rejects stamping the words on a row whose strongest pair was set aside."""
    rosters = [
        _short_overlap_roster(correlated=True),
        _short_overlap_roster(correlated=True, third="unrelated"),
        _short_overlap_roster(correlated=True, third="close"),
        _quiet_roster(),
    ]
    said = 0
    for roster in rosters:
        result = b.review(roster, as_of=b.AS_OF, threshold=SHORT_THRESHOLD)
        for entry in result.entries:
            item = b.item_of(entry, "redundancy")
            if item.get("summary") != NO_SIBLING_TEXT:
                continue
            said += 1
            mine = _returns_of(roster, entry["symphony_id"])
            for other in roster:
                if other.symphony_id == entry["symphony_id"]:
                    continue
                theirs = _returns_of(roster, other.symphony_id)
                shared = min(len(mine), len(theirs))
                assert b.pearson(mine[-shared:], theirs[-shared:]) < b.SCREEN_THRESHOLD, (
                    entry["symphony_id"],
                    other.symphony_id,
                )
    assert said, "fixture sanity: at least one row says it"


def test_no_row_shows_its_own_symphony_as_its_sibling():
    """A pair always joins two symphonies. Across every roster that shows pairs, no row
    and no recommendation names its own symphony as the sibling."""
    rosters = [
        (b.roster_with_two_redundant_candidates()[0], 250),
        (b.roster_with_ineligible_correlated_sibling()[0], 250),
        (
            b.roster_with_a_candidate_in_two_passing_pairs(stronger_sorts_first=True),
            b.NOTE_THRESHOLD,
        ),
        (_short_overlap_roster(correlated=True, third="close"), SHORT_THRESHOLD),
    ]
    shown = 0
    for roster, threshold in rosters:
        result = b.review(roster, as_of=b.AS_OF, threshold=threshold)
        for entry in result.entries:
            pair = b.item_of(entry, "redundancy")["pair"]
            if pair is not None:
                shown += 1
                assert pair["sibling_id"] != entry["symphony_id"], entry["symphony_id"]
        for rec in result.recommendations:
            assert rec["sibling_id"] != rec["candidate_id"], rec["candidate_id"]
    assert shown, "fixture sanity: some row shows a pair"


# ---------------------------------------------------------------------------
# "No redundant sibling" is a finding only when every comparison could have found one
# ---------------------------------------------------------------------------

NO_OWN_COMPOSITE = "this symphony has no composite"
NO_PEER_COMPOSITE = "no same-basis peer has a composite"
# A comparison settles whether two symphonies are redundant from this many aligned days: the
# larger of the days the structural gate needs and the days the uncertainty gate needs.
FLOOR_DAYS = max(NEEDED_DAYS, retirement_recommender.MIN_OBS_FLOOR)


def _entry_and_item(result, sid):
    entry = b.entry_of(result, sid)
    return entry, b.item_of(entry, "redundancy")


def _both_scored(result):
    for sid in ("lead", "peer"):
        assert b.has_composite(b.entry_of(result, sid)), sid  # fixture sanity: a composite


def _assert_not_used(entry, item, *, names_with_days, absent_names=()):
    """The item is unavailable, listed missing, says why and never makes the claim. Each
    undecided peer is named with the aligned days of its comparison and a peer that was
    decided (or has no composite) is not named."""
    assert item["status"] == "unavailable"
    assert item["retire_candidate"] is False
    assert "redundancy" in entry["missing_items"]
    assert not _says_no_sibling(item)
    reason = item["reason"]
    assert isinstance(reason, str) and reason, "an unused item always says why"
    for name, days in names_with_days:
        assert f"{name} ({days} aligned days" in reason, name
    for name in absent_names:
        assert name not in reason, name


def _assert_used(entry, item):
    assert item["status"] == "available"
    assert item["reason"] is None
    assert "redundancy" not in entry["missing_items"]


@pytest.mark.parametrize("aligned", [50, 100, FLOOR_DAYS - 1], ids=lambda n: f"{n}_aligned_days")
@pytest.mark.parametrize(
    "rho", [0.64, 0.66], ids=["just_below_the_screen_line", "just_above_the_screen_line"]
)
def test_a_comparison_below_the_floor_decides_nothing_on_either_side_of_the_screen_line(
    aligned, rho
):
    """Two scored symphonies compared on fewer days than the structural gate needs. At 0.64
    the pair is not shown and nothing is found; at 0.66 the pair is a screen hit and is shown
    with whatever its gates conclude on so few days. Either way the rule could not have
    flagged them, so the item is unavailable and names the peer and the days, and the claim
    of no redundant sibling is not made on one side of the line while the other side reads no
    verdict. Rejects the claim for the 0.64 side, and a screen hit whose interval fell short
    (a gate that did decide on 50 days) counted as a finding."""
    roster, threshold = b.composite_pair(aligned, rho)
    lead_returns = b.returns_from_values(roster[0].composer_values)
    peer_returns = b.returns_from_values(roster[1].composer_values)
    assert (b.pearson(lead_returns, peer_returns) >= b.SCREEN_THRESHOLD) is (rho > 0.65)
    result = b.review(roster, as_of=b.AS_OF, threshold=threshold)
    _both_scored(result)
    for sid, peer in (("lead", "Peer"), ("peer", "Lead")):
        entry, item = _entry_and_item(result, sid)
        assert (item["pair"] is not None) is (rho > 0.65), sid
        _assert_not_used(entry, item, names_with_days=[(peer, aligned)])


@pytest.mark.parametrize(
    ("aligned", "counts"),
    [
        pytest.param(FLOOR_DAYS - 1, False, id="one_day_below_the_floor"),
        pytest.param(FLOOR_DAYS, True, id="exactly_the_floor"),
        pytest.param(FLOOR_DAYS + 1, True, id="one_day_above_the_floor"),
    ],
)
def test_a_comparison_decides_from_exactly_the_floor(aligned, counts):
    """A defined correlation of 0.64 over the floor's days is a finding that the two are not
    redundant, and the item says so; one day fewer is not. Rejects a floor that is exclusive
    and one a day too high."""
    roster, threshold = b.composite_pair(aligned, 0.64)
    result = b.review(roster, as_of=b.AS_OF, threshold=threshold)
    _both_scored(result)
    for sid, peer in (("lead", "Peer"), ("peer", "Lead")):
        entry, item = _entry_and_item(result, sid)
        assert item["pair"] is None, sid  # fixture sanity: 0.64 is below the screen
        if counts:
            _assert_used(entry, item)
            assert item.get("summary") == NO_SIBLING_TEXT, sid
        else:
            _assert_not_used(entry, item, names_with_days=[(peer, aligned)])


GATES_THAT_REACHED_A_RESULT = [
    pytest.param(
        lambda: b.block_pair(NEEDED_DAYS, "diverging"),
        ("not met", "not evaluated"),
        id="uncertainty_gate_not_met",
    ),
    pytest.param(
        lambda: b.block_pair(265, "diverging"),
        ("passed", "not met"),
        id="uncertainty_gate_passed_and_structural_gate_not_met",
    ),
]


@pytest.mark.parametrize(("build", "states"), GATES_THAT_REACHED_A_RESULT)
def test_a_screen_hit_whose_gates_both_reached_a_result_counts_and_is_shown(build, states):
    """The pair reaches the screen over at least the floor's days, and the gates conclude
    that it is not redundant (the interval falls short, or it passes and the stressed window
    does not). The existing rule acts on both results, so the item is used in the verdict,
    shows the pair with its gate states, and is not listed missing. Rejects treating every
    shown pair as unused."""
    first, second = build()
    roster = b.two_composer_symphonies(first, second)
    result = b.review(roster, as_of=b.AS_OF, threshold=b.COMPOSITE_PAIR_THRESHOLD)
    _both_scored(result)
    for sid in ("lead", "peer"):
        entry, item = _entry_and_item(result, sid)
        _assert_used(entry, item)
        assert item["retire_candidate"] is False, sid
        pair = item["pair"]
        assert pair is not None and pair["n_obs"] == len(first), sid
        assert (pair["uncertainty_gate"]["state"], pair["structural_gate"]["state"]) == states
        assert not _says_no_sibling(item), sid


def _pair_whose_stressed_window_has_no_variance():
    """190 aligned returns on which the first series falls by exactly the same half on each of
    its last ten sessions (a level that halves repeats the same return to the last digit), so
    the ten stressed days hold one value and the stressed correlation cannot be computed. The
    second series follows it closely."""
    calm_a, calm_b = b.calm_block(180)
    first = calm_a + [-50.0] * 10
    second = calm_b + [-50.0 + 0.05 * math.sin(0.9 * i) for i in range(10)]
    assert b.oracle_stressed_correlation(first, second) is None  # fixture sanity
    assert b.oracle_ci(b.pearson(first, second), 190)[0] >= b.SCREEN_THRESHOLD  # passes
    return first, second


def test_a_screen_hit_whose_stressed_window_cannot_be_computed_leaves_the_item_unused():
    """The uncertainty gate passes and the structural gate reads undefined: no result the
    existing rule acts on. The pair is shown, the item is unavailable, and its reason names
    the peer, its aligned days and the structural gate (not the uncertainty gate) as the one
    that is undefined."""
    first, second = _pair_whose_stressed_window_has_no_variance()
    result = b.review(
        b.two_composer_symphonies(first, second),
        as_of=b.AS_OF,
        threshold=b.COMPOSITE_PAIR_THRESHOLD,
    )
    _both_scored(result)
    for sid, peer in (("lead", "Peer"), ("peer", "Lead")):
        entry, item = _entry_and_item(result, sid)
        pair = item["pair"]
        assert pair["uncertainty_gate"]["state"] == "passed", sid
        assert pair["structural_gate"]["state"] == "undefined", sid
        _assert_not_used(entry, item, names_with_days=[(peer, len(first))])
        assert "structural gate undefined" in item["reason"], sid
        assert "uncertainty gate" not in item["reason"], sid
    assert result.recommendations == []


def test_an_exact_twin_leaves_the_item_unused_and_names_the_uncertainty_gate():
    """A correlation of exactly one has no interval, so the uncertainty gate reads undefined
    and the structural gate is not evaluated. The existing rule cannot conclude, so the item
    is unused whatever the days, the reason names the peer, its days and the uncertainty gate
    as undefined, and nobody is flagged."""
    returns = b.wave_returns(FLOOR_DAYS + 20, amp=0.6)
    result = b.review(
        b.two_composer_symphonies(returns, list(returns)),
        as_of=b.AS_OF,
        threshold=b.COMPOSITE_PAIR_THRESHOLD,
    )
    _both_scored(result)
    for sid, peer in (("lead", "Peer"), ("peer", "Lead")):
        entry, item = _entry_and_item(result, sid)
        pair = item["pair"]
        assert pair["correlation"]["value"] == 1.0, sid  # fixture sanity: exactly one
        assert pair["uncertainty_gate"]["state"] == "undefined", sid
        assert pair["structural_gate"]["state"] == "not evaluated", sid
        _assert_not_used(entry, item, names_with_days=[(peer, len(returns))])
        assert "uncertainty gate undefined" in item["reason"], sid
        assert "structural gate" not in item["reason"], sid
    assert result.recommendations == []


# A factor large enough that the product of the two symphonies' return variances is beyond the
# largest float, while each symphony still scores (a composite needs only finite returns).
VARIANCE_OVERFLOW_FACTOR = 1e100


def test_a_pair_whose_return_variances_overflow_has_no_correlation_and_leaves_the_item_unused():
    """On one session both values rise by a factor of 1e100 and on the next they fall back, so
    each return series holds one enormous move and the product of the two variances is beyond
    the largest float: the correlation is undefined although each symphony has a composite and
    the pair has more than the floor's days. The item is unavailable, names the peer and the
    days, is listed missing and never makes the claim, as for a pair the interval cannot
    handle. Rejects counting a comparison with no correlation as one that found no redundant
    sibling."""
    first = b.wave_returns(FLOOR_DAYS + 19, amp=0.6)
    second = b.wave_returns(FLOOR_DAYS + 19, amp=0.6, freq=0.9)
    roster = b.two_composer_symphonies(first, second)
    spike_day = sorted(roster[0].composer_values)[-10]
    for symphony in roster:
        symphony.composer_values[spike_day] *= VARIANCE_OVERFLOW_FACTOR
    spiked = [b.returns_from_values(symphony.composer_values) for symphony in roster]
    assert (
        b.exact_pearson_or_none(*spiked) is None
    )  # fixture sanity: the recommender's own function
    result = b.review(roster, as_of=b.AS_OF, threshold=b.COMPOSITE_PAIR_THRESHOLD)
    _both_scored(result)
    for sid, peer in (("lead", "Peer"), ("peer", "Lead")):
        entry, item = _entry_and_item(result, sid)
        _assert_not_used(entry, item, names_with_days=[(peer, len(first))])
    assert result.recommendations == []


def test_a_decided_pair_beside_a_peer_compared_on_too_few_days_is_not_used():
    """The lead's strongest pair is a screen hit over 265 days whose gates both reached a result,
    and the lead has a second scored peer compared on only 40. Alone, the decided pair counts;
    with the second peer the item is unused and the reason names that peer and its days, not
    the peer whose comparison was decided. Rejects deciding from the shown pair alone."""
    first, second = b.block_pair(265, "diverging")
    days = b.last_n_nyse_days(b.AS_OF, 266)

    def roster(with_short_peer):
        out = [
            b.composer_symphony("lead", "Lead", days, first),
            b.composer_symphony("peer-a", "Peer A", days, second),
        ]
        if with_short_peer:
            short = b.composer_symphony("peer-b", "Peer B", days[-41:], b.sine_returns(40, 3))
            out.append(short)
        return out

    alone = b.review(roster(False), as_of=b.AS_OF, threshold=b.COMPOSITE_PAIR_THRESHOLD)
    entry, item = _entry_and_item(alone, "lead")
    _assert_used(entry, item)  # fixture sanity: the decided pair counts on its own
    assert item["pair"]["uncertainty_gate"]["state"] == "passed"

    beside = b.review(roster(True), as_of=b.AS_OF, threshold=b.COMPOSITE_PAIR_THRESHOLD)
    assert b.has_composite(b.entry_of(beside, "peer-b"))  # fixture sanity: the peer is scored
    entry, item = _entry_and_item(beside, "lead")
    assert item["pair"]["sibling_id"] == "peer-a"  # the decided pair is still the one shown
    assert item["pair"]["structural_gate"]["state"] == "not met"
    _assert_not_used(entry, item, names_with_days=[("Peer B", 40)], absent_names=["Peer A"])


def test_a_symphony_flagged_by_the_existing_rule_counts_beside_a_peer_compared_on_too_few_days():
    """weak-x is the weaker member of pairs that pass both gates, so the rule flags it and its
    item decides the verdict whatever another scored peer's comparison was. The short peer
    does leave an unflagged symphony's item unused. Rejects reading the short peer before the
    flag."""
    roster = b.roster_with_a_candidate_in_two_passing_pairs(stronger_sorts_first=True)
    days = b.last_n_nyse_days(b.AS_OF, b.NOTE_RETURNS + 1)
    short = b.composer_symphony(
        "short-peer", "Short Peer", days[-151:], b.sine_returns(150, 4, amp=0.4)
    )
    result = b.review([*roster, short], as_of=b.AS_OF, threshold=b.NOTE_THRESHOLD)
    assert b.has_composite(b.entry_of(result, "short-peer"))  # fixture sanity: scored
    flagged = {r["candidate_id"] for r in result.recommendations}
    assert "weak-x" in flagged
    unflagged = []
    for entry in result.entries:
        sid = entry["symphony_id"]
        item = b.item_of(entry, "redundancy")
        if sid in flagged:
            assert item["retire_candidate"] is True, sid
            _assert_used(entry, item)
            assert entry["verdict"] == "retire_candidate", sid
        elif sid != "short-peer":
            unflagged.append(sid)
            assert item["status"] == "unavailable", sid
            assert "Short Peer (150 aligned days" in item["reason"], sid
    assert unflagged  # fixture sanity: the short peer does leave an unflagged item unused


def _roster_with_a_compared_peer_and_one_peer_of_each_kind_without_a_composite():
    """``lead`` and ``steady`` are compared over 200 returns at a correlation of 0.4. Beside
    them, one peer of each kind that has no composite and was compared on fewer days or not at
    all: younger than the threshold, on the fallback basis, missing a value inside the common
    window, and flat over it. The short peers move against the lead, so none of their pairs
    reaches the screen."""
    returns = ENOUGH_DAYS
    days = b.last_n_nyse_days(b.AS_OF, returns + 1)
    lead, steady = b.orthogonal_pair(returns, 0.4, k=returns // 10, scale=0.3)
    thin = b.composer_symphony("thin", "Thin Peer", days[-40:], [-r for r in lead[-39:]])
    thin.composer_values[days[-5].isoformat()] = None
    return [
        b.composer_symphony("lead", "Lead", days, lead),
        b.composer_symphony("steady", "Steady", days, steady),
        b.composer_symphony("young", "Young Peer", days[-10:], [-r for r in lead[-9:]]),
        b.shadow_symphony(
            "fallback",
            "Fallback Peer",
            days[-31].isoformat(),
            b.by_date(days[-30:], b.wave_returns(30, freq=1.1)),
        ),
        thin,
        b.composer_symphony("flat", "Flat Peer", days[-40:], [0.05] * 39),
    ]


def test_a_peer_with_no_composite_never_blocks_and_is_never_named_as_undecided():
    """Four peers of the four kinds that have no composite sit beside one scored peer
    compared on 200 days at a correlation of 0.4. None of them can produce a retire-candidate,
    so none of them leaves the finding unsupported: the item counts, makes the claim, and the
    only peer it lists is the one on another basis that no pair could compare. Rejects a rule
    that needs every peer compared."""
    result = b.review(
        _roster_with_a_compared_peer_and_one_peer_of_each_kind_without_a_composite(),
        as_of=b.AS_OF,
        threshold=b.COMPOSITE_PAIR_THRESHOLD,
    )
    kinds = {
        "young": "not yet eligible",
        "fallback": "not comparable: basis differs",
        "thin": "insufficient coverage in the common window",
        "flat": "not computable",
    }
    for sid, starts in kinds.items():  # fixture sanity: each peer lacks a composite its own way
        assert b.item_of(b.entry_of(result, sid), "composite")["reason"].startswith(starts), sid
    for sid in ("lead", "steady"):
        entry, item = _entry_and_item(result, sid)
        _assert_used(entry, item)
        assert item.get("summary") == NO_SIBLING_TEXT, sid
        assert item["pair"] is None, sid
        assert [r["display_name"] for r in item["not_compared"]] == ["Fallback Peer"], sid


@pytest.mark.parametrize("sibling", ["thin_coverage", "flat_window"])
def test_a_symphony_with_no_composite_has_an_unused_item_and_says_so(sibling):
    """The existing rule can never flag a symphony that has no composite, so no result about
    it decides anything: its item is unavailable, listed missing, and says the symphony has no
    composite. Rejects the claim of no redundant sibling for it."""
    result = b.review(b.note_family_roster(sibling), as_of=b.AS_OF, threshold=b.NOTE_THRESHOLD)
    entry, item = _entry_and_item(result, "note-sibling")
    assert not b.has_composite(entry)  # fixture sanity
    assert item["status"] == "unavailable"
    assert NO_OWN_COMPOSITE in item["reason"]
    assert "redundancy" in entry["missing_items"]
    assert not _says_no_sibling(item)


def test_a_symphony_on_the_fallback_basis_has_an_unused_item_and_says_it_has_no_composite():
    result = b.review(b.shadow_pair_roster(), as_of=b.AS_OF, threshold=b.NOTE_THRESHOLD)
    for sid in ("shadow-a", "shadow-b"):
        entry, item = _entry_and_item(result, sid)
        assert item["status"] == "unavailable", sid
        assert NO_OWN_COMPOSITE in item["reason"], sid
        assert "redundancy" in entry["missing_items"], sid
        assert not _says_no_sibling(item), sid
        assert item["retire_candidate"] is False, sid


def _subject_beside_one_peer_without_a_composite(kind):
    """``note-subject`` (scored) beside a peer without a composite of this kind, and beside an
    eligible peer on the fallback basis that no pair can compare."""
    days = b.last_n_nyse_days(b.AS_OF, b.NOTE_RETURNS + 1)
    if kind == "young":
        subject_returns, _ = b.orthogonal_pair(b.NOTE_RETURNS, 0.4, k=26)
        roster = [
            b.composer_symphony("note-subject", "Note Subject", days, subject_returns),
            b.composer_symphony("note-sibling", "Note Sibling", days[-10:], b.wave_returns(9)),
        ]
    else:
        roster = b.note_family_roster(kind)
    roster.append(
        b.shadow_symphony(
            "shadow-extra",
            "Shadow Extra",
            days[0].isoformat(),
            b.by_date(days[1:], b.wave_returns(b.NOTE_RETURNS, freq=1.1)),
        )
    )
    return roster


@pytest.mark.parametrize("kind", ["young", "tenure_unknown", "thin_coverage", "flat_window"])
def test_a_scored_symphony_whose_every_same_basis_peer_has_no_composite_has_an_unused_item(kind):
    """The peers on its basis are younger than the threshold, have no invested-since date, miss
    a value in the common window, or are flat; the other peer is on another basis. None of them
    has a composite, so the rule could not have flagged the subject with any of them: the item
    is unavailable, says that no same-basis peer has a composite, and lists the peer on the
    other basis as not compared. Rejects the claim of no redundant sibling."""
    result = b.review(
        _subject_beside_one_peer_without_a_composite(kind),
        as_of=b.AS_OF,
        threshold=b.NOTE_THRESHOLD,
    )
    entry, item = _entry_and_item(result, "note-subject")
    assert b.has_composite(entry)  # fixture sanity
    assert not b.has_composite(b.entry_of(result, "note-sibling"))  # fixture sanity
    assert item["status"] == "unavailable"
    assert NO_PEER_COMPOSITE in item["reason"]
    assert "redundancy" in entry["missing_items"]
    assert not _says_no_sibling(item)
    assert item["retire_candidate"] is False
    assert [r["display_name"] for r in item["not_compared"]] == ["Shadow Extra"]


def _lead_beside_a_steady_peer_and(second_kind, overlap):
    """``lead`` and ``steady`` are compared on 40 returns. ``second`` is compared with them on
    ``overlap`` returns: a moving series that is scored, or a flat one that has no composite."""
    days = b.last_n_nyse_days(b.AS_OF, 41)
    returns = b.sine_returns(overlap, 3, amp=0.4) if second_kind == "moving" else [0.05] * overlap
    return [
        b.composer_symphony("lead", "Lead", days, b.wave_returns(40)),
        b.composer_symphony("steady", "Steady", days, b.sine_returns(40, 2)),
        b.composer_symphony("second", "Second", days[-(overlap + 1) :], returns),
    ]


@pytest.mark.parametrize(
    ("second_kind", "overlap"),
    [
        pytest.param("moving", 27, id="a_second_peer_compared_on_27_days"),
        pytest.param("moving", 29, id="a_second_peer_compared_on_29_days"),
        pytest.param("flat", 30, id="a_flat_second_peer_on_30_days"),
        pytest.param("flat", 40, id="a_flat_second_peer_on_40_days"),
    ],
)
def test_the_words_no_redundant_sibling_appear_in_no_item_that_does_not_count(second_kind, overlap):
    """No pair of any row reaches the screen, and no comparison reaches the floor, so no row's
    silence counts. The words of the claim appear nowhere in any of the rows' items (summary,
    reason or note), and each item says why it is unused: it names the scored peers it
    compared on too few days, with their days, and never a flat peer, which has no composite.
    Rejects stamping the claim as the reason of an unavailable item."""
    roster = _lead_beside_a_steady_peer_and(second_kind, overlap)
    returns = {s.symphony_id: b.returns_from_values(s.composer_values) for s in roster}
    moving_pairs = [("lead", "steady")] + (
        [("lead", "second"), ("steady", "second")] if second_kind == "moving" else []
    )
    for first, second in moving_pairs:
        shared = min(len(returns[first]), len(returns[second]))
        correlation = b.pearson(returns[first][-shared:], returns[second][-shared:])
        assert correlation < b.SCREEN_THRESHOLD, (first, second)  # fixture sanity
    result = b.review(roster, as_of=b.AS_OF, threshold=20)
    moving = second_kind == "moving"
    assert b.has_composite(b.entry_of(result, "second")) is moving  # fixture sanity
    for entry in result.entries:
        sid = entry["symphony_id"]
        item = b.item_of(entry, "redundancy")
        assert not _says_no_sibling(item), sid
        assert item["status"] == "unavailable", sid
        assert "redundancy" in entry["missing_items"], sid
        assert item["reason"], sid
    steady_item = b.item_of(b.entry_of(result, "steady"), "redundancy")
    assert "Lead (40 aligned days" in steady_item["reason"]
    assert ("Second (" in steady_item["reason"]) is moving
    if moving:
        assert f"Second ({overlap} aligned days" in steady_item["reason"]
    else:
        assert NO_OWN_COMPOSITE in b.item_of(b.entry_of(result, "second"), "redundancy")["reason"]


def test_the_words_no_redundant_sibling_appear_in_no_row_of_a_roster_with_a_flat_symphony():
    """A flat symphony has no composite, so it is unused for that reason; the two moving
    symphonies are compared with each other on 70 days only, so theirs are unused for that."""
    days = b.last_n_nyse_days(b.AS_OF, 71)
    roster = [
        b.composer_symphony("flat", "Flat", days, [0.0] * 70),
        b.composer_symphony("wave-one", "Wave One", days, b.wave_returns(70)),
        b.composer_symphony("wave-two", "Wave Two", days, b.sine_returns(70, 3)),
    ]
    result = b.review(roster, as_of=b.AS_OF, threshold=60)
    flat_entry, flat_item = _entry_and_item(result, "flat")
    assert NO_OWN_COMPOSITE in flat_item["reason"]
    assert not _says_no_sibling(flat_item)
    for sid, peer in (("wave-one", "Wave Two"), ("wave-two", "Wave One")):
        entry, item = _entry_and_item(result, sid)
        _assert_not_used(entry, item, names_with_days=[(peer, 70)], absent_names=["Flat"])


def test_symphonies_that_were_all_compared_on_enough_days_are_not_listed_as_missing_redundancy():
    """The control for the rows above: a roster of defined, uncorrelated comparisons on the
    floor's days or more each. Rejects listing the item as missing in every case."""
    result = b.review(_quiet_roster(), as_of=b.AS_OF, threshold=20)
    for entry in result.entries:
        item = b.item_of(entry, "redundancy")
        _assert_used(entry, item)
        assert item.get("summary") == NO_SIBLING_TEXT, entry["symphony_id"]


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
    # an item that does not apply gives no evidence, like one that is unavailable
    assert "redundancy" in entry["missing_items"]


@pytest.mark.parametrize("void_tenure_days", [31, 5], ids=["eligible_peer", "young_peer"])
def test_a_symphony_beside_only_peers_with_no_history_says_no_peer_has_a_composite(
    void_tenure_days,
):
    """The peer exists, so 'no other symphonies' is false; it has no history, so it has no
    composite and nothing was compared. Eligibility of the peer does not change that. Rejects
    the not-applicable text and the claim of no redundant sibling."""
    void = b.no_history_symphony(
        "void-peer", "Void Peer", ROSTER_DAYS[-void_tenure_days].isoformat()
    )
    result = b.review([_lone_composer(), void], as_of=b.AS_OF, threshold=ROSTER_THRESHOLD)
    entry = b.entry_of(result, "lone-a")
    item = b.item_of(entry, "redundancy")
    assert item["status"] == "unavailable"
    assert "no same-basis peer has a composite" in item["reason"]
    assert "no other symphonies" not in item["reason"]
    assert not _says_no_sibling(item)
    assert item["pair"] is None
    assert item["retire_candidate"] is False
    assert "redundancy" in entry["missing_items"]


@pytest.mark.parametrize("peer_tenure_days", [30, 10], ids=["eligible_peer", "young_peer"])
def test_a_peer_with_history_on_another_basis_is_not_compared_whether_or_not_it_is_eligible(
    peer_tenure_days,
):
    """Whether the other symphony is eligible has no bearing on whether its basis differs: it
    is listed as a peer that was not compared, and with no peer left on the subject's basis
    the item says no same-basis peer has a composite. Rejects letting eligibility decide
    whether the peer is listed."""
    days = ROSTER_DAYS[-peer_tenure_days:]
    peer = b.make_symphony(
        "shadow-peer",
        "Shadow Peer",
        days[0].isoformat(),
        series_failure="unavailable",
        shadow_returns=b.by_date(days[1:], b.wave_returns(peer_tenure_days - 1, freq=1.4)),
    )
    result = b.review([_lone_composer(), peer], as_of=b.AS_OF, threshold=ROSTER_THRESHOLD)
    entry = b.entry_of(result, "lone-a")
    item = b.item_of(entry, "redundancy")
    assert item["status"] == "unavailable"
    assert "no same-basis peer has a composite" in item["reason"]
    assert not _says_no_sibling(item)
    assert _not_compared(entry) == [("Shadow Peer", BASES_DIFFER)]
    assert "redundancy" in entry["missing_items"]


# ---------------------------------------------------------------------------
# Peers that were not compared because their basis differs are listed
# ---------------------------------------------------------------------------

CROSS_BASIS_THRESHOLD = 60
CROSS_BASIS_RETURNS = 90
BASES_DIFFER = "bases differ"


def _two_composer_symphonies_and_a_shadow_clone_of_the_first():
    """Three eligible symphonies. ``Xray`` and ``Yoke`` are on the Composer basis and
    unrelated; ``Zulu Shadow`` is on the shadow basis (its Composer read failed) and repeats
    Xray's returns, so a comparison across the two bases would flag it."""
    days = b.last_n_nyse_days(b.AS_OF, CROSS_BASIS_RETURNS + 1)
    xray = b.wave_returns(CROSS_BASIS_RETURNS, freq=0.37)
    return [
        b.composer_symphony("x", "Xray", days, xray),
        b.composer_symphony("y", "Yoke", days, b.sine_returns(CROSS_BASIS_RETURNS, 5)),
        b.shadow_symphony("z", "Zulu Shadow", days[0].isoformat(), b.by_date(days[1:], xray)),
    ]


def _not_compared(entry):
    listed = b.item_of(entry, "redundancy").get("not_compared")
    assert listed is not None, "the redundancy item carries no list of peers it did not compare"
    return sorted((record["display_name"], record["reason"]) for record in listed)


def test_an_eligible_peer_on_another_basis_is_listed_as_not_compared_beside_the_ones_that_were():
    """Xray and Yoke are compared with each other and no pair reaches the screen. Zulu is
    on another basis and cannot be compared with either, so each row names it with the
    reason and never stays silent about it; Zulu's own row names both. No row lists
    itself. The list reaches the stored record. Rejects a row that says nothing of a peer it
    did not compare, which hides a clone on the other basis."""
    result = b.review(
        _two_composer_symphonies_and_a_shadow_clone_of_the_first(),
        as_of=b.AS_OF,
        threshold=CROSS_BASIS_THRESHOLD,
    )
    for sid in ("x", "y"):
        entry = b.entry_of(result, sid)
        assert entry["basis"]["kind"] == "composer", sid  # fixture sanity
        assert b.item_of(entry, "redundancy")["pair"] is None, sid  # fixture sanity
        assert _not_compared(entry) == [("Zulu Shadow", BASES_DIFFER)], sid
    zulu = b.entry_of(result, "z")
    assert zulu["basis"]["kind"] == "shadow"  # fixture sanity
    assert _not_compared(zulu) == [("Xray", BASES_DIFFER), ("Yoke", BASES_DIFFER)]
    record = json.loads(json.dumps(tr.run_record(result, b.et(2026, 12, 31, 17, 5))))
    stored = next(e for e in record["entries"] if e["symphony_id"] == "x")
    assert _not_compared(stored) == [("Zulu Shadow", BASES_DIFFER)]


def test_peers_that_are_young_or_have_no_tenure_are_listed_as_not_compared_beside_the_pairs():
    """Xray and Yoke are compared with each other, so each row has pairs. Two shadow-basis peers
    cannot be compared with either: one is younger than the threshold and one has no
    invested-since date. Both are listed on both rows, whatever their eligibility, and the
    stored record carries the same lists. Rejects a list narrowed to eligible peers, which
    hides a clone on the other basis whose tenure is short or unknown."""
    days = b.last_n_nyse_days(b.AS_OF, CROSS_BASIS_RETURNS + 1)
    xray = b.wave_returns(CROSS_BASIS_RETURNS, freq=0.37)
    young = b.shadow_symphony(
        "young-shadow", "Young Shadow", days[-20].isoformat(), b.by_date(days[-19:], xray[-19:])
    )
    unknown = b.shadow_symphony("unknown-shadow", "Unknown Shadow", None, b.by_date(days[1:], xray))
    roster = [
        b.composer_symphony("x", "Xray", days, xray),
        b.composer_symphony("y", "Yoke", days, b.sine_returns(CROSS_BASIS_RETURNS, 5)),
        young,
        unknown,
    ]
    result = b.review(roster, as_of=b.AS_OF, threshold=CROSS_BASIS_THRESHOLD)
    assert b.entry_of(result, "young-shadow")["eligible"] is False  # fixture sanity
    assert b.entry_of(result, "unknown-shadow")["tenure_trading_days"] is None  # fixture sanity
    expected = [("Unknown Shadow", BASES_DIFFER), ("Young Shadow", BASES_DIFFER)]
    record = json.loads(json.dumps(tr.run_record(result, b.et(2026, 12, 31, 17, 5))))
    for sid in ("x", "y"):
        entry = b.entry_of(result, sid)
        assert _not_compared(entry) == expected, sid
        stored = next(e for e in record["entries"] if e["symphony_id"] == sid)
        assert _not_compared(stored) == expected, sid


def test_a_roster_on_one_basis_lists_no_peer_as_not_compared():
    """The list is present and empty when every peer was compared, and for a symphony with
    no peer at all. Rejects a list that is absent when it is empty, and one that names a
    peer that was compared."""
    for entry in b.review(_quiet_roster(), as_of=b.AS_OF, threshold=20).entries:
        assert b.item_of(entry, "redundancy").get("not_compared") == [], entry["symphony_id"]
    lone = b.entry_of(
        b.review([_lone_composer()], as_of=b.AS_OF, threshold=ROSTER_THRESHOLD), "lone-a"
    )
    assert b.item_of(lone, "redundancy")["status"] == "not_applicable"  # fixture sanity
    assert b.item_of(lone, "redundancy").get("not_compared") == []
