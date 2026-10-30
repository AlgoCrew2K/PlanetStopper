"""Realized performance metrics and the composite over the common window.

Module under test: advisors.tenure_review (performance_metrics and build_review).

Scale: returns are percent; total return, annualized return and max drawdown come
back in percent (drawdown <= 0); Sharpe and Sortino are daily non-annualized
ratios; Calmar is the annualized return over the absolute drawdown. The composite
weights (0.40, 0.20, 0.15, 0.15, 0.10) and the min-max normalization are written
out again in _tenure_review_builders.py from the spec, not read from the module
under test or from the recommender.
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import advisors.tenure_review as tr
from tests.advisors import _tenure_review_builders as b

_GOLDEN = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "math"
    / "tenure_review_composite_window.json"
)

COMPOSER_LABEL = "realized: Composer account, deposit-adjusted"
METRIC_KEYS = (
    "total_return",
    "annualized_return",
    "max_drawdown",
    "sharpe",
    "sortino",
    "calmar",
)
THRESHOLD = 30
DAYS = b.last_n_nyse_days(b.AS_OF, THRESHOLD + 1)  # 31 points: tenure 31, window 30


# ---------------------------------------------------------------------------
# performance_metrics (pure)
# ---------------------------------------------------------------------------


def test_metrics_match_textbook_formulas_in_percent_scale():
    returns = b.wave_returns(120, amp=1.1, drift=0.04, freq=0.29)
    expected = b.oracle_metrics(returns)
    got = tr.performance_metrics(returns)
    assert set(got) == set(METRIC_KEYS)
    for key in METRIC_KEYS:
        # pure-Python oracle against numpy-backed code: summation order only
        assert got[key] == pytest.approx(expected[key], rel=1e-9), key


def test_percent_returns_are_not_mistaken_for_fractions():
    """252 returns of +1%: total return is (1.01^252 minus 1) times 100 percent, not 252 percent
    and not a fraction."""
    got = tr.performance_metrics([1.0] * 252)
    assert got["total_return"] == pytest.approx((1.01**252 - 1) * 100, rel=1e-9)


def test_annualized_return_equals_total_return_over_exactly_252_returns():
    """Annualization is len(returns) / 252: one year of trading days.

    Rejects a 365 day basis and a basis of len(returns) minus 1."""
    got = tr.performance_metrics(b.wave_returns(252, amp=0.8, drift=0.03))
    assert got["annualized_return"] == pytest.approx(got["total_return"], rel=1e-9)


def test_max_drawdown_is_a_negative_percent_measured_from_the_running_peak():
    got = tr.performance_metrics([10.0, -20.0, 5.0])
    assert got["max_drawdown"] == pytest.approx(-20.0, rel=1e-9)  # peak 1.1, trough 0.88


def test_a_first_day_loss_counts_as_a_drawdown_from_the_starting_level():
    got = tr.performance_metrics([-10.0, 5.0, 1.0])
    assert got["max_drawdown"] == pytest.approx(-10.0, rel=1e-9)


def test_a_series_that_never_falls_has_a_computed_zero_drawdown_and_undefined_ratios():
    """Zero is a value here; None is not. The Calmar ratio is infinite and the Sortino
    ratio has no downside, so both are not computable; the other metrics show values."""
    got = tr.performance_metrics([0.5, 1.0, 0.25, 0.75, 0.6, 0.4] * 5)
    assert got["max_drawdown"] is not None
    assert got["max_drawdown"] == pytest.approx(0.0, abs=1e-12)  # zero up to float noise
    assert got["calmar"] is None
    assert got["sortino"] is None
    for key in ("total_return", "annualized_return", "sharpe"):
        assert got[key] is not None, key


@pytest.mark.parametrize(
    "returns",
    [[], [0.5], [float("nan"), 0.5], [float("inf"), 0.5], [float("nan"), float("nan")]],
    ids=["empty", "one", "nan_and_one", "inf_and_one", "all_nan"],
)
def test_fewer_than_two_finite_returns_give_no_metric_at_all_never_zero(returns):
    got = tr.performance_metrics(returns)
    assert set(got) == set(METRIC_KEYS)
    assert all(v is None for v in got.values()), got


def test_two_finite_returns_are_enough_for_return_and_drawdown():
    got = tr.performance_metrics([1.0, -0.5])
    assert got["total_return"] == pytest.approx((1.01 * 0.995 - 1) * 100, rel=1e-9)
    assert got["max_drawdown"] == pytest.approx(-0.5, rel=1e-9)


def test_non_finite_returns_are_ignored_not_treated_as_zero():
    clean = tr.performance_metrics([1.0, 2.0, -0.5, 0.7])
    noisy = tr.performance_metrics([1.0, float("nan"), 2.0, -0.5, float("inf"), 0.7])
    assert noisy == pytest.approx(clean, rel=1e-12)


def test_a_flat_series_has_a_computed_zero_return_and_no_sharpe():
    got = tr.performance_metrics([0.0] * 20)
    assert got["total_return"] == 0.0
    assert got["max_drawdown"] == pytest.approx(0.0, abs=1e-12)
    assert got["sharpe"] is None


@settings(max_examples=60, deadline=None)
@given(
    st.lists(
        st.floats(min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False),
        min_size=2,
        max_size=60,
    )
)
def test_metric_invariants_hold_for_any_finite_percent_series(returns):
    """Drawdown never exceeds zero, total return never goes below -100 percent, no
    value is NaN or infinite, and total and annualized return share a sign."""
    got = tr.performance_metrics(returns)
    for key, value in got.items():
        assert value is None or math.isfinite(value), (key, value)
    assert got["max_drawdown"] is None or got["max_drawdown"] <= 1e-9
    assert got["total_return"] is None or got["total_return"] >= -100.0
    total, ann = got["total_return"], got["annualized_return"]
    if total is not None and ann is not None and abs(total) > 1e-9:
        assert (total > 0) == (ann > 0)


# ---------------------------------------------------------------------------
# Golden fixture: the composite is scored on the common window
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def golden():
    return json.loads(_GOLDEN.read_text(encoding="utf-8"))


def _golden_roster(golden):
    return [
        b.make_symphony(
            s["symphony_id"], s["display_name"], s["invested_since"], composer_values=s["values"]
        )
        for s in golden["symphonies"]
    ]


def _window_returns(values: dict, window_dates: list[str]):
    series = [values[d] for d in window_dates]
    return [(series[i] / series[i - 1] - 1) * 100 for i in range(1, len(series))]


def test_every_composite_is_scored_over_the_same_common_window(golden):
    """Three symphonies with different tenures and very different pre-window histories.

    Rejects scoring each symphony over its own tenure (golden-a would then be the
    weakest instead of golden-b), a window of the wrong length, and a window that
    ends before the as-of day."""
    result = b.review(
        _golden_roster(golden),
        as_of=date.fromisoformat(golden["as_of"]),
        threshold=golden["threshold"],
    )
    window = golden["common_window"]
    assert {key: result.composite_window[key] for key in window} == window
    # the common window is that many consecutive sessions, so its span holds as many sessions
    assert result.composite_window.get("sessions") == window["days"]
    window_dates = [
        d.isoformat()
        for d in b.last_n_nyse_days(date.fromisoformat(window["last"]), window["days"])
    ]
    assert window_dates[0] == window["first"]

    values = {s["symphony_id"]: s["values"] for s in golden["symphonies"]}
    metrics = {sid: b.oracle_metrics(_window_returns(v, window_dates)) for sid, v in values.items()}
    expected = b.oracle_composites(metrics)

    got = {}
    for sid in values:
        entry = b.entry_of(result, sid)
        item = b.item_of(entry, "composite")
        assert item["status"] == "available", sid
        assert item["window"] == result.composite_window, sid
        got[sid] = item["figures"]["composite"]["value"]
        # sums and compounding in a different order than numpy: 1e-9 absolute
        assert got[sid] == pytest.approx(expected[sid], abs=1e-9), sid
    assert min(got, key=got.get) == golden["expected_lowest_composite_on_common_window"]
    assert max(got, key=got.get) == golden["expected_highest_composite_on_common_window"]
    assert golden["lowest_composite_if_each_symphony_used_its_own_tenure"] != min(got, key=got.get)


def test_composite_is_a_labelled_score_between_zero_and_one(golden):
    result = b.review(
        _golden_roster(golden),
        as_of=date.fromisoformat(golden["as_of"]),
        threshold=golden["threshold"],
    )
    for s in golden["symphonies"]:
        item = b.item_of(b.entry_of(result, s["symphony_id"]), "composite")
        figure = item["figures"]["composite"]
        assert 0.0 <= figure["value"] <= 1.0
        assert figure["unit"]
        assert COMPOSER_LABEL in item["label"]
        assert item["subject"] and item["source"]


def test_the_performance_item_covers_the_whole_tenure_not_the_common_window(golden):
    """Tenure metrics and the composite deliberately use different windows."""
    result = b.review(
        _golden_roster(golden),
        as_of=date.fromisoformat(golden["as_of"]),
        threshold=golden["threshold"],
    )
    sym = next(s for s in golden["symphonies"] if s["symphony_id"] == "golden-a")
    entry = b.entry_of(result, "golden-a")
    expected = b.oracle_metrics(b.returns_from_values(sym["values"]))
    perf = b.item_of(entry, "performance")
    assert perf["window"]["days"] == len(sym["values"])
    assert perf["window"]["first"] == min(sym["values"])
    assert perf["figures"]["total_return"]["value"] == pytest.approx(
        expected["total_return"], rel=1e-9
    )
    assert perf["figures"]["max_drawdown"]["value"] == pytest.approx(
        expected["max_drawdown"], rel=1e-9
    )


def test_performance_figures_carry_percent_and_ratio_units():
    days = DAYS
    roster = [
        b.composer_symphony("unit-a", "Unit A", days, b.wave_returns(30, amp=0.8)),
        b.composer_symphony("unit-b", "Unit B", days, b.wave_returns(30, amp=0.4, freq=1.2)),
    ]
    entry = b.entry_of(b.review(roster, as_of=b.AS_OF, threshold=THRESHOLD), "unit-a")
    figs = b.item_of(entry, "performance")["figures"]
    for name in ("total_return", "annualized_return", "max_drawdown"):
        assert figs[name]["unit"] == "percent", name
    for name in ("sharpe", "sortino"):
        assert "non-annualized" in figs[name]["unit"], name
        assert "daily" in figs[name]["unit"], name
    assert figs["calmar"]["unit"] == "ratio"


# ---------------------------------------------------------------------------
# Who is scored, and what happens to the rest
# ---------------------------------------------------------------------------


def _oracle_for(symphonies, threshold=THRESHOLD):
    """Oracle composites of the given Composer-basis symphonies over their last
    threshold minus 1 returns (the common window of threshold points)."""
    metrics = {}
    for s in symphonies:
        rets = b.returns_from_values(s.composer_values)[-(threshold - 1) :]
        metrics[s.symphony_id] = b.oracle_metrics(rets)
    return b.oracle_composites(metrics)


def _composite_value(result, sid):
    return b.value_of(b.entry_of(result, sid), "composite", "composite")


def _scored_pair():
    return [
        b.composer_symphony("scored-1", "Scored One", DAYS, b.wave_returns(30, amp=0.8, drift=0.1)),
        b.composer_symphony(
            "scored-2", "Scored Two", DAYS, b.wave_returns(30, amp=0.5, drift=0.0, freq=1.3)
        ),
    ]


def test_identical_series_tie_on_every_metric_and_each_scores_the_neutral_midpoint():
    """No spread in any metric means no ranking information: each metric normalizes to
    0.5 and the weights sum to 1, so every composite is 0.5.

    Rejects 0, 1 or NaN from an unguarded zero range."""
    same = b.wave_returns(30, amp=0.7, drift=0.05)
    roster = [b.composer_symphony(f"twin-{i}", f"Twin {i}", DAYS, list(same)) for i in range(3)]
    result = b.review(roster, as_of=b.AS_OF, threshold=THRESHOLD)
    for i in range(3):
        assert _composite_value(result, f"twin-{i}") == pytest.approx(0.5, abs=1e-12)


def test_a_symphony_below_the_threshold_is_outside_the_population_and_scores_nothing():
    """The young sibling is the stronger performer, so scoring it would lower
    keep-a's composite. Neither other composite may move."""
    roster, threshold = b.roster_with_ineligible_correlated_sibling()
    result = b.review(roster, as_of=b.AS_OF, threshold=threshold)
    young = b.item_of(b.entry_of(result, "young-c"), "composite")
    assert young["status"] == "unavailable"
    assert young["reason"] == "not yet eligible"
    assert young["figures"]["composite"]["value"] is None
    expected = _oracle_for([s for s in roster if s.symphony_id != "young-c"], threshold)
    for sid in ("keep-a", "keep-b"):
        assert _composite_value(result, sid) == pytest.approx(expected[sid], abs=1e-9)


def test_a_fallback_basis_symphony_is_not_comparable_and_does_not_move_the_others():
    extreme = b.make_symphony(
        "shadow-x",
        "Shadow Extreme",
        DAYS[0].isoformat(),
        series_failure="unavailable",
        shadow_returns=b.by_date(DAYS[1:], b.wave_returns(30, amp=2.0, drift=1.5)),
    )
    pair = _scored_pair()
    result = b.review([*pair, extreme], as_of=b.AS_OF, threshold=THRESHOLD)
    item = b.item_of(b.entry_of(result, "shadow-x"), "composite")
    assert item["status"] == "unavailable"
    assert item["reason"] == "not comparable: basis differs"
    assert b.entry_of(result, "shadow-x")["verdict"] != "retire_candidate"
    expected = _oracle_for(pair)
    for s in pair:
        assert _composite_value(result, s.symphony_id) == pytest.approx(
            expected[s.symphony_id], abs=1e-9
        )


def test_a_series_that_starts_inside_the_window_is_excluded_and_says_how_much_it_covers():
    late = DAYS[5:]  # 26 points; the 30 day window is DAYS[1:], so four window days are missing
    extreme = b.composer_symphony(
        "late-x",
        "Late Extreme",
        late,
        b.wave_returns(len(late) - 1, amp=2.0, drift=1.5),
        invested_since=DAYS[0].isoformat(),
    )
    pair = _scored_pair()
    result = b.review([*pair, extreme], as_of=b.AS_OF, threshold=THRESHOLD)
    window = result.composite_window
    in_window = sum(1 for d in late if d.isoformat() >= window["first"])
    item = b.item_of(b.entry_of(result, "late-x"), "composite")
    assert item["status"] == "unavailable"
    assert item["reason"] == (
        f"insufficient coverage in the common window ({in_window} of {window['days']} days)"
    )
    assert in_window < window["days"]
    assert b.entry_of(result, "late-x")["verdict"] != "retire_candidate"
    expected = _oracle_for(pair)
    for s in pair:
        assert _composite_value(result, s.symphony_id) == pytest.approx(
            expected[s.symphony_id], abs=1e-9
        )


def test_one_missing_day_inside_the_window_excludes_the_symphony_and_is_not_a_zero_return():
    values = b.values_from_returns(DAYS, b.wave_returns(30, amp=2.0, drift=1.5))
    values[DAYS[15].isoformat()] = None
    holed = b.make_symphony("holed-x", "Holed Extreme", DAYS[0].isoformat(), composer_values=values)
    pair = _scored_pair()
    result = b.review([*pair, holed], as_of=b.AS_OF, threshold=THRESHOLD)
    window = result.composite_window
    item = b.item_of(b.entry_of(result, "holed-x"), "composite")
    assert item["status"] == "unavailable"
    assert item["reason"] == (
        f"insufficient coverage in the common window ({window['days'] - 1} of {window['days']} days)"
    )
    assert b.entry_of(result, "holed-x")["verdict"] != "retire_candidate"
    expected = _oracle_for(pair)
    for s in pair:
        assert _composite_value(result, s.symphony_id) == pytest.approx(
            expected[s.symphony_id], abs=1e-9
        )


def test_a_symphony_with_fewer_than_two_returns_in_the_window_scores_not_computable():
    """Window of two days, one return: no metric exists and none is shown as 0."""
    days = b.last_n_nyse_days(b.AS_OF, 2)
    roster = [
        b.composer_symphony("thin-1", "Thin One", days, [0.4]),
        b.composer_symphony("thin-2", "Thin Two", days, [-0.2]),
    ]
    result = b.review(roster, as_of=b.AS_OF, threshold=2)
    entry = b.entry_of(result, "thin-1")
    perf = b.item_of(entry, "performance")
    assert perf["status"] == "unavailable"
    assert perf["reason"].startswith("not computable")
    for name, fig in perf["figures"].items():
        assert fig["value"] is None, name
    comp = b.item_of(entry, "composite")
    assert comp["status"] == "unavailable"
    assert comp["reason"].startswith("not computable")
    assert comp["figures"]["composite"]["value"] is None
    assert entry["verdict"] != "retire_candidate"


def test_an_undefined_calmar_makes_the_composite_not_computable_but_leaves_other_metrics():
    rising = b.composer_symphony(
        "rising-x", "Always Rising", DAYS, [0.2 + 0.1 * ((i % 3) - 1) for i in range(30)]
    )
    pair = _scored_pair()
    result = b.review([*pair, rising], as_of=b.AS_OF, threshold=THRESHOLD)
    entry = b.entry_of(result, "rising-x")
    comp = b.item_of(entry, "composite")
    assert comp["status"] == "unavailable"
    assert comp["reason"].startswith("not computable")
    figs = b.item_of(entry, "performance")["figures"]
    assert figs["calmar"]["value"] is None
    assert figs["sortino"]["value"] is None
    assert figs["max_drawdown"]["value"] is not None
    assert figs["max_drawdown"]["value"] == pytest.approx(0.0, abs=1e-12)
    for name in ("total_return", "annualized_return", "sharpe"):
        assert figs[name]["value"] is not None, name
    expected = _oracle_for(pair)
    for s in pair:
        assert _composite_value(result, s.symphony_id) == pytest.approx(
            expected[s.symphony_id], abs=1e-9
        )


# ---------------------------------------------------------------------------
# A composite says how many symphonies it was normalized against
# ---------------------------------------------------------------------------


def _population_roster():
    """Three scored Composer symphonies, one that cannot be scored (it never falls, so
    its Calmar ratio is undefined), one below the threshold and one on the fallback
    basis. Only the first three form the population."""
    scored = [
        b.composer_symphony(f"pop-{k}", f"Pop {k}", DAYS, b.wave_returns(30, amp=0.4 + 0.2 * k))
        for k in (1, 2, 3)
    ]
    rising = b.composer_symphony(
        "pop-rising", "Pop Rising", DAYS, [0.2 + 0.1 * ((i % 3) - 1) for i in range(30)]
    )
    young = b.composer_symphony("pop-young", "Pop Young", DAYS[-10:], b.wave_returns(9, freq=1.1))
    fallback = b.make_symphony(
        "pop-fallback",
        "Pop Fallback",
        DAYS[0].isoformat(),
        series_failure="unavailable",
        shadow_returns=b.by_date(DAYS[1:], b.wave_returns(30, freq=0.7)),
    )
    return [*scored, rising, young, fallback]


def test_every_scored_composite_carries_the_size_of_the_population_it_was_normalized_over():
    """The size is the number of symphonies whose five metrics are all defined on the
    common window, counted here from the oracle. A composite normalized against one
    other symphony is a different statement from one normalized against twenty.

    Rejects counting every live symphony, the eligible ones only, or omitting it."""
    roster = _population_roster()
    composer_based = [
        s for s in roster if s.symphony_id in {"pop-1", "pop-2", "pop-3", "pop-rising"}
    ]
    metrics = {
        s.symphony_id: b.oracle_metrics(
            b.returns_from_values(s.composer_values)[-(THRESHOLD - 1) :]
        )
        for s in composer_based
    }
    scored = {
        sid
        for sid, m in metrics.items()
        if all(
            m[k] is not None
            for k in ("annualized_return", "sharpe", "sortino", "max_drawdown", "calmar")
        )
    }
    assert scored == {"pop-1", "pop-2", "pop-3"}  # fixture sanity: the rising one is out

    result = b.review(roster, as_of=b.AS_OF, threshold=THRESHOLD)
    for sid in scored:
        item = b.item_of(b.entry_of(result, sid), "composite")
        assert item["status"] == "available", sid
        assert item["population_size"] == len(scored), sid
        assert type(item["population_size"]) is int, sid


def test_a_symphony_scored_alone_says_the_population_was_one():
    """With nobody to rank against every metric sits at the midpoint; the population
    size is what tells a reader that 0.5 means no comparison was made."""
    alone = b.composer_symphony("pop-alone", "Pop Alone", DAYS, b.wave_returns(30, amp=0.6))
    item = b.item_of(
        b.entry_of(b.review([alone], as_of=b.AS_OF, threshold=THRESHOLD), "pop-alone"),
        "composite",
    )
    assert item["status"] == "available"
    assert item["figures"]["composite"]["value"] == pytest.approx(0.5, abs=1e-12)  # midpoint
    assert item["population_size"] == 1


def test_the_population_size_is_the_same_on_every_composite_of_one_review():
    result = b.review(_population_roster(), as_of=b.AS_OF, threshold=THRESHOLD)
    sizes = {
        b.item_of(e, "composite")["population_size"]
        for e in result.entries
        if b.item_of(e, "composite")["status"] == "available"
    }
    assert len(sizes) == 1


# ---------------------------------------------------------------------------
# A window with no down day has no drawdown
# ---------------------------------------------------------------------------

NO_DOWN_DAY_RETURNS = [
    pytest.param([100.0, 10.0, 9.09], id="plus_100_percent_then_rises"),
    pytest.param([100.0, 0.0, 10.0], id="a_flat_day_among_gains"),
    pytest.param([0.0, 100.0], id="flat_then_plus_100_percent"),
]


@pytest.mark.parametrize("returns", NO_DOWN_DAY_RETURNS)
def test_a_window_with_no_negative_return_has_a_max_drawdown_of_exactly_zero(returns):
    """No decline means no drawdown, by definition. The shared metric function reads a
    series with no negative return and a day of +100 percent or more as prices and answers
    about -90 percent; the module must not pass that on. Calmar divides by the drawdown,
    so it is undefined, as for any series that never falls. The total return is still
    right.

    Rejects the false drawdown, a None where zero is computed, and a finite Calmar ratio
    built on the false drawdown."""
    got = tr.performance_metrics(returns)
    assert got["max_drawdown"] == 0.0
    assert got["calmar"] is None
    assert got["total_return"] == pytest.approx(b.oracle_metrics(returns)["total_return"], rel=1e-9)


@pytest.mark.parametrize(
    "returns",
    [
        pytest.param([100.0, -0.001], id="plus_100_percent_then_a_tiny_fall"),
        pytest.param([100.0, -10.0, 5.0], id="plus_100_percent_then_a_fall"),
        pytest.param([-10.0, 100.0, 5.0], id="a_first_day_loss_then_plus_100_percent"),
    ],
)
def test_a_window_with_a_down_day_takes_its_max_drawdown_from_the_shared_function(returns):
    """One negative return, however small, is a decline: the drawdown is the shared
    function's answer (here also the textbook value).

    Rejects applying the zero rule to a window that has a down day."""
    got = tr.performance_metrics(returns)
    assert got["max_drawdown"] < 0.0
    assert got["max_drawdown"] == pytest.approx(
        b.quantstats_percent(returns)["max_drawdown"], rel=1e-9
    )
    assert got["max_drawdown"] == pytest.approx(b.oracle_metrics(returns)["max_drawdown"], rel=1e-9)
