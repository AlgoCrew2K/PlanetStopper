"""Marginal contribution: the comparison portfolio with and without a symphony.

Module under test: advisors.tenure_review (marginal_contribution and build_review).

The comparison portfolio holds the eligible Composer-basis symphonies that have a composite.
Its value on a day is the sum of their deposit-adjusted values, so dollar scale matters (an
equal-weight average of returns is a different, wrong answer). The "without" portfolio is the
sum of the other members' values and reallocates nothing. Only days on which every member has
a usable value count, and a comparison needs at least the uncertainty floor's days. Differences
are without minus with, in percentage points, so a positive total-return difference means
removing the symphony would have raised the portfolio's return. Max drawdown is <= 0, so a
positive drawdown difference means a shallower drawdown without it.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

import advisors.tenure_review as tr
from advisors import retirement_recommender
from tests.advisors import _tenure_review_builders as b

LABEL = "counterfactual: portfolio without this symphony"
# A comparison needs this many days on which every member of the portfolio has a value.
FLOOR = retirement_recommender.MIN_OBS_FLOOR
DAYS = b.last_n_nyse_days(b.AS_OF, 14)


def _excluded(display_name, label):
    """The record an item lists for a live symphony left out of its comparison portfolio."""
    return {"display_name": display_name, "reason": f"excluded: {label}"}


def _peer(base, returns, dates=DAYS, basis="composer"):
    return {"basis": basis, "values": b.values_from_returns(list(dates), returns, base=base)}


def _oracle(eligible, subject, *, dates_filter=None):
    """With and without totals and drawdowns from raw dollar sums on shared days."""

    def usable(v):
        return isinstance(v, (int, float)) and math.isfinite(v) and v > 0

    shared = None
    for entry in eligible.values():
        days = {d for d, v in entry["values"].items() if usable(v)}
        shared = days if shared is None else shared & days
    days = sorted(shared)

    def stats(ids):
        level = [sum(eligible[i]["values"][d] for i in ids) for d in days]
        total = (level[-1] / level[0] - 1) * 100
        peak, mdd = level[0], 0.0
        for v in level:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1)
        return total, mdd * 100

    with_total, with_mdd = stats(list(eligible))
    without_total, without_mdd = stats([i for i in eligible if i != subject])
    return {
        "days": days,
        "with_total_return": with_total,
        "with_max_drawdown": with_mdd,
        "without_total_return": without_total,
        "without_max_drawdown": without_mdd,
        "total_return_difference": without_total - with_total,
        "max_drawdown_difference": without_mdd - with_mdd,
    }


def _figures(item):
    return {name: fig["value"] for name, fig in item["figures"].items()}


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------


def test_a_hand_worked_example_gives_the_expected_totals_drawdowns_and_signs():
    """A = 100, 110, 99 and B = 100, 100, 100 on three days.

    With: 200, 210, 199, so total -0.5 percent, drawdown 199/210 minus 1 = -5.238095 percent.
    Without A (B alone): flat, total 0 and drawdown 0. Removing A raises the return by
    0.5 points and makes the drawdown 5.238095 points shallower."""
    days = DAYS[:3]
    eligible = {
        "sym-a": {
            "basis": "composer",
            "values": dict(zip((d.isoformat() for d in days), (100.0, 110.0, 99.0))),
        },
        "sym-b": {
            "basis": "composer",
            "values": dict(zip((d.isoformat() for d in days), (100.0, 100.0, 100.0))),
        },
    }
    item = tr.marginal_contribution("sym-a", eligible)
    got = _figures(item)
    assert item["status"] == "available"
    assert got["with_total_return"] == pytest.approx(-0.5, rel=1e-9)
    assert got["with_max_drawdown"] == pytest.approx((199 / 210 - 1) * 100, rel=1e-9)
    assert got["without_total_return"] == pytest.approx(0.0, abs=1e-12)
    assert got["without_max_drawdown"] == pytest.approx(0.0, abs=1e-12)
    assert got["total_return_difference"] == pytest.approx(0.5, rel=1e-9)
    assert got["max_drawdown_difference"] == pytest.approx((1 - 199 / 210) * 100, rel=1e-9)


def test_figures_match_a_dollar_sum_oracle_not_an_average_of_returns():
    """The two symphonies differ ten-fold in dollar scale and in return profile, so an
    equal-weight average of returns gives a visibly different answer."""
    eligible = {
        "sym-a": _peer(
            1000.0, [1.4, -2.1, 0.6, 1.9, -1.2, 0.8, -0.4, 1.1, -1.7, 0.9, 0.3, -0.8, 1.2]
        ),
        "sym-b": _peer(
            10000.0, [0.1, 0.2, -0.1, 0.15, 0.05, -0.05, 0.1, 0.0, 0.2, -0.1, 0.05, 0.1, 0.0]
        ),
    }
    expected = _oracle(eligible, "sym-a")
    item = tr.marginal_contribution("sym-a", eligible)
    got = _figures(item)
    for name in (
        "with_total_return",
        "with_max_drawdown",
        "without_total_return",
        "without_max_drawdown",
        "total_return_difference",
        "max_drawdown_difference",
    ):
        # float sums in a different order than the module: 1e-9 relative
        assert got[name] == pytest.approx(expected[name], rel=1e-9, abs=1e-12), name
    # fixture sanity: the equal-weight shortcut would not agree
    equal_weight_with = (
        (
            math.prod(
                1 + r / 100
                for r in [1.4, -2.1, 0.6, 1.9, -1.2, 0.8, -0.4, 1.1, -1.7, 0.9, 0.3, -0.8, 1.2]
            )
            - 1
        )
        * 100
        + (
            math.prod(
                1 + r / 100
                for r in [0.1, 0.2, -0.1, 0.15, 0.05, -0.05, 0.1, 0.0, 0.2, -0.1, 0.05, 0.1, 0.0]
            )
            - 1
        )
        * 100
    ) / 2
    assert abs(equal_weight_with - expected["with_total_return"]) > 1e-3


def test_item_is_labelled_counterfactual_and_names_its_window_units_and_basis():
    eligible = {
        "sym-a": _peer(1000.0, b.wave_returns(13)),
        "sym-b": _peer(2000.0, b.wave_returns(13, freq=1.1)),
    }
    item = tr.marginal_contribution("sym-a", eligible)
    assert item["label"] == LABEL
    assert item["window"] == {
        "first": DAYS[0].isoformat(),
        "last": DAYS[-1].isoformat(),
        "days": len(DAYS),
        "sessions": len(b.nyse_days(DAYS[0], DAYS[-1])),
    }
    for name in (
        "with_total_return",
        "with_max_drawdown",
        "without_total_return",
        "without_max_drawdown",
    ):
        assert item["figures"][name]["unit"] == "percent", name
    for name in ("total_return_difference", "max_drawdown_difference"):
        assert item["figures"][name]["unit"] == "percentage points", name
    assert item["subject"] and item["source"]
    assert item["excluded"] == []


def test_the_window_starts_on_the_latest_first_date_among_the_eligible_symphonies():
    eligible = {
        "sym-a": _peer(1000.0, b.wave_returns(13)),
        "sym-b": _peer(2000.0, b.wave_returns(10, freq=1.1), dates=DAYS[3:]),
    }
    item = tr.marginal_contribution("sym-a", eligible)
    assert item["window"]["first"] == DAYS[3].isoformat()
    assert item["window"]["last"] == DAYS[-1].isoformat()
    assert item["window"]["days"] == len(DAYS) - 3
    expected = _oracle(eligible, "sym-a")
    assert item["figures"]["with_total_return"]["value"] == pytest.approx(
        expected["with_total_return"], rel=1e-9
    )


def test_days_on_which_a_peer_has_no_usable_value_are_left_out_of_both_legs():
    """Missing, null, NaN and non-positive values all remove the day for everyone."""
    peer = _peer(2000.0, b.wave_returns(13, freq=1.1))
    del peer["values"][DAYS[4].isoformat()]
    peer["values"][DAYS[6].isoformat()] = None
    peer["values"][DAYS[8].isoformat()] = float("nan")
    eligible = {"sym-a": _peer(1000.0, b.wave_returns(13)), "sym-b": peer}
    expected = _oracle(eligible, "sym-a")
    assert len(expected["days"]) == len(DAYS) - 3  # fixture sanity
    item = tr.marginal_contribution("sym-a", eligible)
    assert item["window"]["days"] == len(DAYS) - 3
    for name in ("with_total_return", "without_total_return", "with_max_drawdown"):
        assert item["figures"][name]["value"] == pytest.approx(expected[name], rel=1e-9, abs=1e-12)


def test_the_inputs_are_not_modified():
    eligible = {
        "sym-a": _peer(1000.0, b.wave_returns(13)),
        "sym-b": _peer(2000.0, b.wave_returns(13, freq=1.1)),
    }
    snapshot = {k: dict(v["values"]) for k, v in eligible.items()}
    tr.marginal_contribution("sym-a", eligible)
    assert {k: v["values"] for k, v in eligible.items()} == snapshot


# ---------------------------------------------------------------------------
# Not applicable, excluded and mixed bases
# ---------------------------------------------------------------------------


def test_the_only_eligible_symphony_reads_not_applicable_with_no_figures():
    item = tr.marginal_contribution("sym-a", {"sym-a": _peer(1000.0, b.wave_returns(13))})
    assert item["status"] == "not_applicable"
    assert item["reason"] == "no other eligible symphonies"
    for fig in item["figures"].values():
        assert fig["value"] is None


def test_young_symphonies_are_listed_as_excluded_and_never_make_the_item_applicable():
    item = tr.marginal_contribution(
        "sym-a", {"sym-a": _peer(1000.0, b.wave_returns(13))}, excluded=["Slate Valley Hedge"]
    )
    assert item["status"] == "not_applicable"
    assert item["reason"] == "no other eligible symphonies"
    assert item["excluded"] == [
        {"display_name": "Slate Valley Hedge", "reason": "excluded: not yet eligible"}
    ]


def test_excluded_symphonies_are_listed_beside_a_real_comparison():
    eligible = {
        "sym-a": _peer(1000.0, b.wave_returns(13)),
        "sym-b": _peer(2000.0, b.wave_returns(13, freq=1.1)),
    }
    item = tr.marginal_contribution("sym-a", eligible, excluded=["Slate Valley Hedge"])
    assert item["status"] == "available"
    assert [e["display_name"] for e in item["excluded"]] == ["Slate Valley Hedge"]


def test_an_all_shadow_history_roster_has_no_position_values_to_compare():
    eligible = {
        "sym-a": {"basis": "shadow", "values": None},
        "sym-b": {"basis": "shadow", "values": None},
    }
    item = tr.marginal_contribution("sym-a", eligible)
    assert item["status"] == "unavailable"
    assert item["reason"] == "no position values on the shadow-history basis"
    for fig in item["figures"].values():
        assert fig["value"] is None


# ---------------------------------------------------------------------------
# Through build_review: the worked roster
# ---------------------------------------------------------------------------

COPPER = ("Qz7Lm2Rt9Wx4Kp1Nv8Ys", "Copper Ridge Rotation", date(2025, 3, 3))
HARBOR = ("Bt3Hc8Jd5Fg2Lk9Mn4Pq", "Harbor Lights Momentum", date(2025, 1, 6))
SLATE = ("Wr6Ty1Ui7Op3As5Df8Gh", "Slate Valley Hedge", date(2026, 9, 1))


def _worked_symphony(spec, *, drift, freq, amp=0.8):
    sid, name, start = spec
    days = list(b.nyse_days(start, b.AS_OF))
    return b.composer_symphony(
        sid, name, days, b.wave_returns(len(days) - 1, amp=amp, drift=drift, freq=freq)
    )


def _worked_roster():
    return [
        _worked_symphony(COPPER, drift=0.03, freq=0.31),
        _worked_symphony(HARBOR, drift=0.05, freq=0.83),
        _worked_symphony(SLATE, drift=2.0, freq=1.9, amp=3.0),  # extreme: any leakage shows
    ]


def test_the_comparison_runs_from_the_later_eligible_start_and_excludes_the_young_symphony():
    result = b.review(_worked_roster(), as_of=b.AS_OF, threshold=126)
    item = b.item_of(b.entry_of(result, COPPER[0]), "marginal")
    assert item["status"] == "available"
    assert item["window"]["first"] == "2025-03-03"
    assert item["window"]["last"] == "2026-12-31"
    assert item["window"]["days"] == b.oracle_count_trading_days(COPPER[2], b.AS_OF)
    assert item["excluded"] == [
        {"display_name": "Slate Valley Hedge", "reason": "excluded: not yet eligible"}
    ]
    assert item["label"] == LABEL


def test_the_young_symphony_contributes_to_neither_leg():
    roster = _worked_roster()
    with_young = b.review(roster, as_of=b.AS_OF, threshold=126)
    without_young = b.review(roster[:2], as_of=b.AS_OF, threshold=126)
    for sid in (COPPER[0], HARBOR[0]):
        a = b.item_of(b.entry_of(with_young, sid), "marginal")
        c = b.item_of(b.entry_of(without_young, sid), "marginal")
        assert a["figures"] == c["figures"], sid
        assert a["window"] == c["window"], sid
    young = b.entry_of(with_young, SLATE[0])
    assert b.item_of(young, "marginal")["status"] == "unavailable"
    assert b.item_of(young, "marginal")["reason"] == "not yet eligible"


def test_the_only_eligible_symphony_with_only_young_peers_is_not_applicable():
    days = b.last_n_nyse_days(b.AS_OF, 31)
    young_days = b.last_n_nyse_days(b.AS_OF, 10)
    roster = [
        b.composer_symphony("lone-eligible", "Lone Eligible", days, b.wave_returns(30)),
        b.composer_symphony("young-peer", "Young Peer", young_days, b.wave_returns(9, freq=1.4)),
    ]
    result = b.review(roster, as_of=b.AS_OF, threshold=30)
    item = b.item_of(b.entry_of(result, "lone-eligible"), "marginal")
    assert item["status"] == "not_applicable"
    assert item["reason"] == "no other eligible symphonies"
    assert [e["display_name"] for e in item["excluded"]] == ["Young Peer"]


def test_a_composer_symphony_whose_only_eligible_peer_is_on_the_fallback_basis_is_not_applicable():
    """The comparison portfolio exists only on the Composer basis, so with every other eligible
    symphony on the fallback basis there is nothing to compare: the item says there is no other
    eligible symphony on this basis, shows no figure and lists the peer as left out for its
    basis. Rejects reading the peer's different basis as a failure of the subject's own item."""
    days = b.last_n_nyse_days(b.AS_OF, 31)
    fallback = b.shadow_symphony(
        "fallback-sym", "Fallback Sym", days[0].isoformat(), b.by_date(days[1:], b.wave_returns(30))
    )
    composer = b.composer_symphony(
        "composer-sym", "Composer Sym", days, b.wave_returns(30, freq=1.4)
    )
    result = b.review([composer, fallback], as_of=b.AS_OF, threshold=30)
    item = b.item_of(b.entry_of(result, "composer-sym"), "marginal")
    assert item["status"] == "not_applicable"
    assert item["reason"] == "no other eligible symphonies on this basis"
    assert item["excluded"] == [_excluded("Fallback Sym", "different basis")]
    for fig in item["figures"].values():
        assert fig["value"] is None


# ---------------------------------------------------------------------------
# Both legs come from the shared metric function
# ---------------------------------------------------------------------------

# Symphony A falls 30 percent on the first day and later quadruples in a day (+400
# percent); B drifts. The summed portfolio therefore opens with a loss and contains a
# day of more than +100 percent, the two shapes a second implementation tends to get
# wrong. Both series also have down days, so the metric function reads them as returns.
A_LEVELS = [100.0, 70.0, 80.0, 400.0, 380.0]
B_LEVELS = [100.0, 101.0, 99.0, 102.0, 103.0]


def _shaped_eligible():
    iso = [d.isoformat() for d in DAYS[:5]]
    return {
        "sym-a": {"basis": "composer", "values": dict(zip(iso, A_LEVELS, strict=True))},
        "sym-b": {"basis": "composer", "values": dict(zip(iso, B_LEVELS, strict=True))},
    }, iso


def _pct_returns(levels):
    return [(later / earlier - 1.0) * 100.0 for earlier, later in zip(levels, levels[1:])]


def test_the_with_and_without_legs_are_the_shared_metric_function_over_the_same_percent_returns(
    monkeypatch,
):
    """Expected values come from the metric function itself on percent returns derived
    from the level sums. A recorder proves both legs were computed by that function and
    not by a second implementation of total return and drawdown.

    Rejects a hand-written drawdown loop, fraction-scale returns and an average of
    returns instead of a sum of levels."""
    eligible, _iso = _shaped_eligible()
    with_returns = _pct_returns([a + c for a, c in zip(A_LEVELS, B_LEVELS, strict=True)])
    without_returns = _pct_returns(B_LEVELS)
    assert with_returns[0] < 0 < max(with_returns) - 100.0  # fixture sanity: the shapes exist
    expected_with = b.quantstats_percent(with_returns)
    expected_without = b.quantstats_percent(without_returns)

    calls = b.spy_on_quantstats_metrics(monkeypatch)
    got = _figures(tr.marginal_contribution("sym-a", eligible))

    assert b.was_called_with(calls, with_returns)
    assert b.was_called_with(calls, without_returns)
    # the same function on the same inputs: 1e-9 relative absorbs only float summation order
    assert got["with_total_return"] == pytest.approx(expected_with["total_return"], rel=1e-9)
    assert got["with_max_drawdown"] == pytest.approx(expected_with["max_drawdown"], rel=1e-9)
    assert got["without_total_return"] == pytest.approx(expected_without["total_return"], rel=1e-9)
    assert got["without_max_drawdown"] == pytest.approx(expected_without["max_drawdown"], rel=1e-9)
    assert got["total_return_difference"] == pytest.approx(
        expected_without["total_return"] - expected_with["total_return"], rel=1e-9
    )
    assert got["max_drawdown_difference"] == pytest.approx(
        expected_without["max_drawdown"] - expected_with["max_drawdown"], rel=1e-9
    )


@pytest.mark.parametrize("shared", [0, 1, 2])
def test_too_few_shared_days_to_score_reads_unavailable_and_names_the_minimum(shared):
    """The shared metric function scores a portfolio from two returns, which is three
    shared days. Fewer is unavailable with a reason that says how many are needed, read
    from the function. Rejects an available item with empty legs."""
    eligible, iso = _shaped_eligible()
    a_days = iso[:2] if shared == 0 else iso[:shared]
    b_days = iso[2:4] if shared == 0 else iso[:shared]
    thin = {
        "sym-a": {
            "basis": "composer",
            "values": {d: eligible["sym-a"]["values"][d] for d in a_days},
        },
        "sym-b": {
            "basis": "composer",
            "values": {d: eligible["sym-b"]["values"][d] for d in b_days},
        },
    }
    item = tr.marginal_contribution("sym-a", thin)
    assert item["status"] == "unavailable"
    assert item["reason"] == (
        f"shared days too short to score: needs at least {b.MIN_VALUES_TO_SCORE} days"
    )
    assert all(fig["value"] is None for fig in item["figures"].values())


def test_a_portfolio_with_no_down_day_has_a_drawdown_of_exactly_zero_with_and_without():
    """A quadruples in a day and B creeps up, so the portfolio with A rises 100 percent in
    a day and never falls, the shape the shared function misreads as prices (about -90
    percent). Rejects the false drawdown. The totals still match the dollar-sum oracle."""
    iso = [d.isoformat() for d in DAYS[:3]]
    eligible = {
        "sym-a": {
            "basis": "composer",
            "values": dict(zip(iso, (100.0, 300.0, 330.0), strict=True)),
        },
        "sym-b": {
            "basis": "composer",
            "values": dict(zip(iso, (100.0, 100.5, 101.0), strict=True)),
        },
    }
    expected = _oracle(eligible, "sym-a")
    assert expected["with_max_drawdown"] == 0.0 and expected["without_max_drawdown"] == 0.0
    got = _figures(tr.marginal_contribution("sym-a", eligible))
    assert got["with_max_drawdown"] == 0.0
    assert got["without_max_drawdown"] == 0.0
    assert got["max_drawdown_difference"] == 0.0
    # sums and compounding in another order than the module: 1e-9 relative
    assert got["with_total_return"] == pytest.approx(expected["with_total_return"], rel=1e-9)
    assert got["without_total_return"] == pytest.approx(expected["without_total_return"], rel=1e-9)


# ---------------------------------------------------------------------------
# A peer with no history is left out and listed, never read as a different basis
# ---------------------------------------------------------------------------

ROSTER_DAYS = b.last_n_nyse_days(b.AS_OF, 31)
ROSTER_THRESHOLD = 30
VOID = ("void-peer", "Void Peer")
EXCLUDED_NO_HISTORY = {"display_name": VOID[1], "reason": "excluded: no history"}


def _void_peer():
    """Eligible by tenure (31 sessions, threshold 30) with no series and no shadow rows."""
    return b.no_history_symphony(VOID[0], VOID[1], ROSTER_DAYS[0].isoformat())


def _composer(sid, name, freq):
    return b.composer_symphony(sid, name, ROSTER_DAYS, b.wave_returns(30, freq=freq))


def test_an_eligible_peer_with_no_history_is_excluded_and_the_others_are_still_compared():
    """A and C are compared exactly as they would be without the peer, and the peer is
    listed with its own reason. Rejects reading its basis as different from theirs."""
    with_void = b.review(
        [_composer("comp-a", "Comp A", 0.4), _composer("comp-c", "Comp C", 1.3), _void_peer()],
        as_of=b.AS_OF,
        threshold=ROSTER_THRESHOLD,
    )
    without = b.review(
        [_composer("comp-a", "Comp A", 0.4), _composer("comp-c", "Comp C", 1.3)],
        as_of=b.AS_OF,
        threshold=ROSTER_THRESHOLD,
    )
    item = b.item_of(b.entry_of(with_void, "comp-a"), "marginal")
    baseline = b.item_of(b.entry_of(without, "comp-a"), "marginal")
    assert baseline["status"] == "available"  # fixture sanity
    assert item["status"] == "available"
    assert item["reason"] is None
    assert item["figures"] == baseline["figures"]
    assert item["window"] == baseline["window"]
    assert item["excluded"] == [EXCLUDED_NO_HISTORY]


def test_the_only_comparable_symphony_beside_a_peer_with_no_history_is_not_applicable():
    result = b.review(
        [_composer("comp-a", "Comp A", 0.4), _void_peer()],
        as_of=b.AS_OF,
        threshold=ROSTER_THRESHOLD,
    )
    item = b.item_of(b.entry_of(result, "comp-a"), "marginal")
    assert item["status"] == "not_applicable"
    assert item["reason"] == "no other eligible symphonies"
    assert item["excluded"] == [EXCLUDED_NO_HISTORY]
    for fig in item["figures"].values():
        assert fig["value"] is None


# ---------------------------------------------------------------------------
# An eligible symphony on the fallback basis is left out and listed, never a reason to
# blank the other symphonies' comparison
# ---------------------------------------------------------------------------


def _fallback_peer(sid="fallback-sym", name="Fallback Sym", freq=1.4):
    """Eligible by tenure on the fallback basis: its Composer read failed, so it has only
    shadow rows, which hold percent returns and no position values."""
    return b.shadow_symphony(
        sid,
        name,
        ROSTER_DAYS[0].isoformat(),
        b.by_date(ROSTER_DAYS[1:], b.wave_returns(30, freq=freq)),
    )


def _marginal_of(result, sid):
    return b.item_of(b.entry_of(result, sid), "marginal")


def test_a_fallback_basis_peer_beside_a_composer_and_a_void_peer_still_leaves_nothing_to_compare():
    """A fallback-basis peer and a peer with no history are both left out, each with its own
    label, and the lone Composer symphony reads that it has no other eligible symphony on its
    basis. Rejects reading the fallback peer as a Composer peer, and letting the void peer's
    label stand in for the fallback peer's."""
    result = b.review(
        [_composer("comp-a", "Comp A", 0.4), _fallback_peer(), _void_peer()],
        as_of=b.AS_OF,
        threshold=ROSTER_THRESHOLD,
    )
    item = _marginal_of(result, "comp-a")
    assert item["status"] == "not_applicable"
    assert item["reason"] == "no other eligible symphonies on this basis"
    assert sorted(item["excluded"], key=lambda r: r["display_name"]) == [
        _excluded("Fallback Sym", "different basis"),
        EXCLUDED_NO_HISTORY,
    ]
    for fig in item["figures"].values():
        assert fig["value"] is None


def test_a_fallback_basis_peer_neither_blanks_nor_moves_the_comparison_of_two_composer_symphonies():
    """Two Composer symphonies are compared exactly as they are without a third eligible
    symphony on the fallback basis, which is listed as left out for its basis. The comparison
    window does not move and no figure combines the two bases. Rejects an item that reads
    unavailable because one peer's basis differs."""
    composers = [_composer("comp-a", "Comp A", 0.4), _composer("comp-c", "Comp C", 1.3)]
    beside = b.review([*composers, _fallback_peer()], as_of=b.AS_OF, threshold=ROSTER_THRESHOLD)
    alone = b.review(composers, as_of=b.AS_OF, threshold=ROSTER_THRESHOLD)
    for sid in ("comp-a", "comp-c"):
        item, baseline = _marginal_of(beside, sid), _marginal_of(alone, sid)
        assert baseline["status"] == "available"  # fixture sanity
        assert item["status"] == "available", sid
        assert item["reason"] is None, sid
        assert item["figures"] == baseline["figures"], sid
        assert item["window"] == baseline["window"], sid
        assert item["excluded"] == [_excluded("Fallback Sym", "different basis")], sid


@pytest.mark.parametrize(
    "others",
    [
        pytest.param([], id="with_no_other_eligible_symphony"),
        pytest.param(["fallback"], id="with_another_eligible_symphony_on_the_fallback_basis"),
        pytest.param(["composer"], id="with_an_eligible_symphony_on_the_composer_basis"),
    ],
)
def test_a_fallback_basis_symphony_has_no_position_values_whatever_its_peers(others):
    """Shadow history stores percent returns only, so its own item has nothing to sum: it
    reads unavailable for that reason and shows no with or without figure, with or without
    peers, and a Composer peer does not change that. Rejects an item that falls back to
    'not applicable' for a lone symphony or computes a portfolio from returns."""
    roster = [_fallback_peer("fallback-subject", "Fallback Subject")]
    if "fallback" in others:
        roster.append(_fallback_peer("fallback-other", "Fallback Other", freq=0.9))
    if "composer" in others:
        roster.append(_composer("comp-a", "Comp A", 0.4))
    item = _marginal_of(
        b.review(roster, as_of=b.AS_OF, threshold=ROSTER_THRESHOLD), "fallback-subject"
    )
    assert item["status"] == "unavailable"
    assert item["reason"] == "no position values on the shadow-history basis"
    for fig in item["figures"].values():
        assert fig["value"] is None


def test_every_composer_symphony_lists_each_fallback_basis_symphony_as_left_out_for_its_basis():
    roster = [
        _composer("comp-a", "Comp A", 0.4),
        _composer("comp-c", "Comp C", 1.3),
        _fallback_peer("fallback-one", "Fallback One"),
        _fallback_peer("fallback-two", "Fallback Two", freq=0.9),
    ]
    result = b.review(roster, as_of=b.AS_OF, threshold=ROSTER_THRESHOLD)
    for sid in ("comp-a", "comp-c"):
        assert sorted(_marginal_of(result, sid)["excluded"], key=lambda r: r["display_name"]) == [
            _excluded("Fallback One", "different basis"),
            _excluded("Fallback Two", "different basis"),
        ], sid


YOUNG_PEER = ("young-peer", "Young Peer")
UNKNOWN_PEER = ("unknown-peer", "Unknown Peer")
THIN_PEER = ("thin-peer", "Thin Peer")
GAP_PEER = ("gap-peer", "Gap Peer")
FLAT_PEER = ("flat-peer", "Flat Peer")
THIN_COVERAGE_LABEL = "insufficient coverage in the common window"
NO_COMPOSITE_LABEL = "composite not computable"


def _thin_peer(points=3):
    """Eligible by tenure (31 sessions, threshold 30) with a value on only its last ``points``
    sessions, so it covers none of the common composite window but those."""
    return b.thin_symphony(THIN_PEER[0], THIN_PEER[1], ROSTER_DAYS, points)


def _gap_peer():
    """Eligible by tenure with a value on every session but one inside the composite window."""
    symphony = b.composer_symphony(
        GAP_PEER[0], GAP_PEER[1], ROSTER_DAYS, b.wave_returns(30, freq=0.7)
    )
    symphony.composer_values[ROSTER_DAYS[15].isoformat()] = None
    return symphony


def _flat_peer():
    """Eligible by tenure with a value on every session that never changes, so its Sharpe
    ratio is undefined."""
    return b.flat_symphony(FLAT_PEER[0], FLAT_PEER[1], ROSTER_DAYS)


def _every_kind_of_symphony_left_out():
    """One live symphony of each kind the comparison portfolio leaves out: younger than the
    threshold, with no invested-since date, eligible with no history, eligible on the
    fallback basis, and eligible on the Composer basis with no composite (too few points in
    the common window, and a series that never moves)."""
    return [
        b.composer_symphony(
            YOUNG_PEER[0], YOUNG_PEER[1], ROSTER_DAYS[-10:], b.wave_returns(9, freq=0.8)
        ),
        b.composer_symphony(
            UNKNOWN_PEER[0],
            UNKNOWN_PEER[1],
            ROSTER_DAYS,
            b.wave_returns(30, freq=2.1),
            invested_since=None,
        ),
        _void_peer(),
        _fallback_peer(),
        _thin_peer(),
        _flat_peer(),
    ]


def test_each_kind_of_symphony_left_out_of_the_comparison_carries_its_own_label_and_moves_nothing():
    """The six kinds are listed in the same item, each with the label that says why it is
    left out, and the comparison of the two Composer symphonies (figures and window) equals
    the comparison without any of them. Rejects one label for the family, a label that names
    a kind the symphony is not, a window moved by a symphony that is left out, and a
    symphony with no composite taking part in the portfolio."""
    composers = [_composer("comp-a", "Comp A", 0.4), _composer("comp-c", "Comp C", 1.3)]
    beside = b.review(
        [*composers, *_every_kind_of_symphony_left_out()], as_of=b.AS_OF, threshold=ROSTER_THRESHOLD
    )
    alone = b.review(composers, as_of=b.AS_OF, threshold=ROSTER_THRESHOLD)
    expected = sorted(
        [
            _excluded(YOUNG_PEER[1], "not yet eligible"),
            _excluded(UNKNOWN_PEER[1], "tenure unavailable"),
            EXCLUDED_NO_HISTORY,
            _excluded("Fallback Sym", "different basis"),
            _excluded(THIN_PEER[1], THIN_COVERAGE_LABEL),
            _excluded(FLAT_PEER[1], NO_COMPOSITE_LABEL),
        ],
        key=lambda r: r["display_name"],
    )
    for sid in ("comp-a", "comp-c"):
        item, baseline = _marginal_of(beside, sid), _marginal_of(alone, sid)
        assert baseline["status"] == "available"  # fixture sanity
        assert sorted(item["excluded"], key=lambda r: r["display_name"]) == expected, sid
        assert item["status"] == "available", sid
        assert item["figures"] == baseline["figures"], sid
        assert item["window"] == baseline["window"], sid


# ---------------------------------------------------------------------------
# An eligible Composer symphony with no composite is not a member of the portfolio
# ---------------------------------------------------------------------------

NO_COMPOSITE_KINDS = [
    pytest.param(_thin_peer, THIN_COVERAGE_LABEL, id="three_points_only"),
    pytest.param(_gap_peer, THIN_COVERAGE_LABEL, id="a_value_missing_inside_the_window"),
    pytest.param(_flat_peer, NO_COMPOSITE_LABEL, id="a_series_that_never_moves"),
]


@pytest.mark.parametrize(("build", "label"), NO_COMPOSITE_KINDS)
def test_a_symphony_with_no_composite_has_an_unavailable_item_and_shortens_no_other_comparison(
    build, label
):
    """Its own item reads unavailable with the reason it has no composite and shows no figure,
    and is listed missing. On the two members' rows it is listed with the matching label, and
    their figures and windows are those of the review without it. Rejects a symphony with a
    handful of points shrinking every other symphony's comparison to those few days."""
    composers = [_composer("comp-a", "Comp A", 0.4), _composer("comp-c", "Comp C", 1.3)]
    peer = build()
    beside = b.review([*composers, peer], as_of=b.AS_OF, threshold=ROSTER_THRESHOLD)
    alone = b.review(composers, as_of=b.AS_OF, threshold=ROSTER_THRESHOLD)
    entry = b.entry_of(beside, peer.symphony_id)
    assert entry["eligible"] is True and entry["basis"]["kind"] == "composer"  # fixture sanity
    assert not b.has_composite(entry)  # fixture sanity
    own = b.item_of(entry, "marginal")
    assert own["status"] == "unavailable"
    assert own["reason"] == label
    for fig in own["figures"].values():
        assert fig["value"] is None
    assert "marginal" in entry["missing_items"]
    for sid in ("comp-a", "comp-c"):
        item, baseline = _marginal_of(beside, sid), _marginal_of(alone, sid)
        assert baseline["status"] == "available"  # fixture sanity
        assert _excluded(peer.display_name, label) in item["excluded"], sid
        assert item["figures"] == baseline["figures"], sid
        assert item["window"] == baseline["window"], sid


@pytest.mark.parametrize("beside", [None, "fallback"], ids=["alone", "beside_a_fallback_peer"])
@pytest.mark.parametrize(("build", "label"), NO_COMPOSITE_KINDS)
def test_a_sole_eligible_composer_symphony_with_no_composite_reads_its_own_unavailable_text(
    build, label, beside
):
    """The symphony's own composite is decided before any text about its peers: alone, or
    beside a peer on the fallback basis, it reads unavailable for the reason it has no
    composite, never one of the not-applicable texts about other symphonies. Rejects reading
    the peers before the symphony itself."""
    roster = [build()]
    if beside == "fallback":
        roster.append(_fallback_peer())
    result = b.review(roster, as_of=b.AS_OF, threshold=ROSTER_THRESHOLD)
    item = _marginal_of(result, roster[0].symphony_id)
    assert item["status"] == "unavailable"
    assert item["reason"] == label
    for fig in item["figures"].values():
        assert fig["value"] is None


@pytest.mark.parametrize("fallback_peer", [False, True], ids=["alone", "beside_a_fallback_peer"])
def test_a_symphony_whose_only_eligible_composer_peers_have_no_composite_is_not_applicable(
    fallback_peer,
):
    """Every other eligible Composer symphony lacks a composite (one with too few points, one
    that never moves), so no comparison portfolio exists. The item says there is no other
    eligible symphony with a composite on this basis, shows no figure, lists both peers with
    their labels and is listed missing. Rejects the wording about eligible symphonies on
    another basis, which would be false here, and a comparison made over a symphony with no
    composite."""
    roster = [_composer("comp-a", "Comp A", 0.4), _thin_peer(), _flat_peer()]
    expected = [
        _excluded(THIN_PEER[1], THIN_COVERAGE_LABEL),
        _excluded(FLAT_PEER[1], NO_COMPOSITE_LABEL),
    ]
    if fallback_peer:
        roster.append(_fallback_peer())
        expected.append(_excluded("Fallback Sym", "different basis"))
    result = b.review(roster, as_of=b.AS_OF, threshold=ROSTER_THRESHOLD)
    entry = b.entry_of(result, "comp-a")
    assert b.has_composite(entry)  # fixture sanity
    item = b.item_of(entry, "marginal")
    assert item["status"] == "not_applicable"
    assert item["reason"] == "no other eligible symphony with a composite on this basis"
    for fig in item["figures"].values():
        assert fig["value"] is None
    assert sorted(item["excluded"], key=lambda r: r["display_name"]) == sorted(
        expected, key=lambda r: r["display_name"]
    )
    assert "marginal" in entry["missing_items"]


def _outlier_and_a_short_symphony(points):
    """Two long Composer symphonies and a third that is eligible by tenure with only
    ``points`` values. ``x`` falls on each of its last sessions and the third rises on each of
    its, so over those few days alone removing ``x`` raises the portfolio's return and
    shallows its drawdown; over the whole window ``x`` is the stronger of the two long ones
    and removing it does the opposite. Returns the roster with the third, the long pair
    alone and the threshold."""
    sessions = 100
    days = b.last_n_nyse_days(b.AS_OF, sessions + 1)
    falling_tail = b.wave_returns(sessions, freq=0.31, drift=0.3)
    falling_tail = falling_tail[: -(points - 1)] + [-2.0] * (points - 1)
    long_pair = [
        b.composer_symphony("x", "X long", days, falling_tail),
        b.composer_symphony(
            "x2", "X2 long", days, b.wave_returns(sessions, amp=0.3, drift=0.05, freq=1.1)
        ),
    ]
    short = b.make_symphony(
        "y",
        "Y short",
        days[0].isoformat(),
        composer_values=b.values_from_returns(days[-points:], [1.0] * (points - 1)),
    )
    return [*long_pair, short], long_pair, sessions + 1


@pytest.mark.parametrize("points", [3, 4, 10])
def test_a_symphony_with_a_handful_of_points_neither_shrinks_a_window_nor_drives_a_watch(points):
    """Over the whole window the long symphony is not a drag (the review without the short one
    keeps it); over the last few days alone it would be. With the short symphony beside them
    the long pair's comparison, window and verdict are exactly those of the review without
    it. Rejects a watch verdict that rests on the few days one short symphony shares, and a
    window of that many days."""
    with_short, without_short, threshold = _outlier_and_a_short_symphony(points)
    beside = b.review(with_short, as_of=b.AS_OF, threshold=threshold)
    alone = b.review(without_short, as_of=b.AS_OF, threshold=threshold)
    baseline = b.entry_of(alone, "x")
    assert b.item_of(baseline, "marginal")["status"] == "available"  # fixture sanity
    assert baseline["verdict"] == "keep"  # fixture sanity: no drag over the whole window
    for sid in ("x", "x2"):
        item = _marginal_of(beside, sid)
        reference = _marginal_of(alone, sid)
        assert item["status"] == "available", sid
        assert item["window"] == reference["window"], sid
        assert item["window"]["days"] == threshold, sid
        assert item["figures"] == reference["figures"], sid
        assert _excluded("Y short", THIN_COVERAGE_LABEL) in item["excluded"], sid
    entry = b.entry_of(beside, "x")
    assert entry["verdict"] == baseline["verdict"]
    assert entry["verdict_reasons"] == baseline["verdict_reasons"] == []
    short = b.entry_of(beside, "y")
    assert b.item_of(short, "marginal")["reason"] == THIN_COVERAGE_LABEL
    assert "marginal" in short["missing_items"]


# ---------------------------------------------------------------------------
# A member that lacks a value before the common window stays a member
# ---------------------------------------------------------------------------


def test_a_member_missing_a_value_before_the_common_window_reduces_the_shared_days_only():
    """Both symphonies have a composite (each covers every session of the common composite
    window). One has no value on one session before that window, so the comparison runs over
    the days on which both have a value, one fewer than either series holds, and neither is
    left out. Rejects dropping a member for a gap that lies outside the common window and a
    day count taken from one series."""
    days = b.last_n_nyse_days(b.AS_OF, 41)  # threshold 30: the common window is the last 30
    first = b.composer_symphony("early-a", "Early A", days, b.wave_returns(40, freq=0.4))
    second = b.composer_symphony("early-c", "Early C", days, b.wave_returns(40, freq=1.3))
    missing = days[3]
    assert missing < days[-ROSTER_THRESHOLD]  # fixture sanity: before the common window
    second.composer_values[missing.isoformat()] = None
    result = b.review([first, second], as_of=b.AS_OF, threshold=ROSTER_THRESHOLD)
    shared = len(days) - 1
    eligible = {
        sym.symphony_id: {"basis": "composer", "values": sym.composer_values}
        for sym in (first, second)
    }
    expected = _oracle(eligible, "early-a")
    assert len(expected["days"]) == shared  # fixture sanity
    for sid in ("early-a", "early-c"):
        assert b.has_composite(b.entry_of(result, sid)), sid  # fixture sanity
        item = _marginal_of(result, sid)
        assert item["status"] == "available", sid
        assert item["excluded"] == [], sid
        assert item["window"]["days"] == shared, sid
        assert item["window"]["first"] == days[0].isoformat(), sid
    item = _marginal_of(result, "early-a")
    assert item["figures"]["with_total_return"]["value"] == pytest.approx(
        expected["with_total_return"], rel=1e-9
    )
    assert item["figures"]["without_total_return"]["value"] == pytest.approx(
        expected["without_total_return"], rel=1e-9
    )


# ---------------------------------------------------------------------------
# The comparison needs the uncertainty floor's days
# ---------------------------------------------------------------------------


def _drag_and_lift(points):
    """Two Composer symphonies that are eligible at a threshold equal to ``points`` and hold
    exactly ``points`` values, so the comparison window holds ``points`` shared days. ``drag``
    falls and ``lift`` rises, so over those days removing ``drag`` raises the portfolio's
    return and shallows its drawdown. Each has a day of loss, because a window with none has
    no Sortino ratio and so no composite. Returns (roster, threshold)."""
    days = b.last_n_nyse_days(b.AS_OF, points)
    returns = points - 1
    roster = [
        b.composer_symphony("drag", "Drag", days, b.sine_returns(returns, 1, amp=0.2, drift=-0.3)),
        b.composer_symphony("lift", "Lift", days, b.sine_returns(returns, 2, amp=0.5, drift=0.3)),
    ]
    return roster, points


# the floor as it is, and a smaller one that a typed 30 would not follow
FLOORS_IN_FORCE = [FLOOR, FLOOR - 8]


@pytest.mark.parametrize("floor", FLOORS_IN_FORCE, ids=lambda n: f"floor_{n}")
def test_a_comparison_over_fewer_shared_days_than_the_floor_is_unavailable_and_decides_nothing(
    floor, monkeypatch
):
    """With one shared day fewer than the floor the item reads unavailable, names the day
    count and the floor, shows no figure and is listed missing, and the drag it would have
    shown (the fixture is one) drives no verdict: with the redundancy and backtest items also
    giving no evidence the row has no verdict. The floor is the recommender's, read when the
    review runs. Rejects a comparison with no floor, and a floor typed into the module."""
    monkeypatch.setattr(retirement_recommender, "MIN_OBS_FLOOR", floor)
    roster, threshold = _drag_and_lift(floor - 1)
    result = b.review(roster, as_of=b.AS_OF, threshold=threshold)
    for sid in ("drag", "lift"):
        entry = b.entry_of(result, sid)
        assert b.has_composite(entry), sid  # fixture sanity
        item = b.item_of(entry, "marginal")
        assert item["status"] == "unavailable", sid
        assert f"{floor - 1} shared days" in item["reason"], sid
        assert str(floor) in item["reason"], sid
        for fig in item["figures"].values():
            assert fig["value"] is None, sid
        assert "marginal" in entry["missing_items"], sid
        assert entry["verdict"] is None, sid
        assert entry["verdict_text"] == "no verdict: no evidence available", sid
        assert entry["verdict_reasons"] == [], sid


@pytest.mark.parametrize("floor", FLOORS_IN_FORCE, ids=lambda n: f"floor_{n}")
def test_a_comparison_over_exactly_the_floors_days_is_evidence(floor, monkeypatch):
    """The same two symphonies over exactly the floor's days: the figures and both differences
    are shown, the item is not listed missing, and the drag decides the verdict. Rejects an
    exclusive floor."""
    monkeypatch.setattr(retirement_recommender, "MIN_OBS_FLOOR", floor)
    roster, threshold = _drag_and_lift(floor)
    result = b.review(roster, as_of=b.AS_OF, threshold=threshold)
    drag = b.entry_of(result, "drag")
    item = b.item_of(drag, "marginal")
    assert item["status"] == "available"
    assert item["window"]["days"] == floor
    for name in (
        "with_total_return",
        "without_total_return",
        "total_return_difference",
        "max_drawdown_difference",
    ):
        assert item["figures"][name]["value"] is not None, name
    assert "marginal" not in drag["missing_items"]
    assert drag["verdict"] == "watch"
    assert drag["verdict_reasons"] == ["drags portfolio return and drawdown"]
    assert b.entry_of(result, "lift")["verdict"] == "keep"


# ---------------------------------------------------------------------------
# "Without" is the sum of the other members, so a far larger subject cannot cancel it
# ---------------------------------------------------------------------------

SUBJECT_BASES = [
    pytest.param(1e18, id="a_subject_ten_quadrillion_times_the_peer"),
    pytest.param(1e150, id="a_subject_of_1e150"),
]


@pytest.mark.parametrize("subject_base", SUBJECT_BASES)
def test_the_without_leg_is_the_sum_of_the_other_members_whatever_the_subjects_size(subject_base):
    """The subject's values are about 1e16 or more times the peer's. Taking the subject off the
    combined value cancels the peer to nothing (or to float noise) and then divides by zero;
    summing the other members leaves the peer's own value series. The without leg then equals
    the peer's total return and drawdown from the shared metric function. Rejects a
    subtraction."""
    eligible = {
        "big": _peer(subject_base, b.wave_returns(13)),
        "small": _peer(100.0, b.wave_returns(13, freq=1.1)),
    }
    peer_returns = b.returns_from_values(eligible["small"]["values"])
    expected = b.quantstats_percent(peer_returns)
    try:
        item = tr.marginal_contribution("big", eligible)
    except Exception as exc:
        pytest.fail(f"the comparison raised {type(exc).__name__}")
    got = _figures(item)
    assert item["status"] == "available"
    # the same function over the same returns: 1e-9 relative absorbs float summation order
    assert got["without_total_return"] == pytest.approx(expected["total_return"], rel=1e-9)
    assert got["without_max_drawdown"] == pytest.approx(expected["max_drawdown"], rel=1e-9)
    oracle = _oracle(eligible, "big")
    assert got["with_total_return"] == pytest.approx(oracle["with_total_return"], rel=1e-9)


def test_a_review_with_one_enormous_value_on_one_day_still_completes_its_comparison():
    """One symphony's value is 1e100 times itself on one session and returns to its level on
    the next, beside an ordinary peer. The review completes; the enormous symphony's without
    leg is the peer's own total return. Rejects a subtraction that cancels the peer to zero and
    raises."""
    first = b.composer_symphony("spike", "Spike", ROSTER_DAYS, b.wave_returns(30, freq=0.4))
    peer = _composer("calm", "Calm", 1.3)
    first.composer_values[ROSTER_DAYS[15].isoformat()] *= 1e100
    try:
        result = b.review([first, peer], as_of=b.AS_OF, threshold=ROSTER_THRESHOLD)
    except Exception as exc:
        pytest.fail(f"the review raised {type(exc).__name__}")
    item = _marginal_of(result, "spike")
    assert item["status"] == "available"
    expected = b.quantstats_percent(b.returns_from_values(peer.composer_values))
    got = _figures(item)
    # the same function over the same returns: 1e-9 relative absorbs float summation order
    assert got["without_total_return"] == pytest.approx(expected["total_return"], rel=1e-9)
    assert got["without_max_drawdown"] == pytest.approx(expected["max_drawdown"], rel=1e-9)
