"""Marginal contribution: the comparison portfolio with and without a symphony.

Module under test: advisors.tenure_review (marginal_contribution and build_review).

The comparison portfolio holds the ELIGIBLE symphonies only. Its value on a day is
the sum of their deposit-adjusted values, so dollar scale matters (an equal-weight
average of returns is a different, wrong answer). The "without" portfolio drops the
subject's value and reallocates nothing. Only days on which every eligible
symphony has a usable value count. Differences are without minus with, in
percentage points, so a positive total-return difference means removing the symphony
would have raised the portfolio's return. Max drawdown is <= 0, so a positive
drawdown difference means a shallower drawdown without it.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

import advisors.tenure_review as tr
from tests.advisors import _tenure_review_builders as b

LABEL = "counterfactual: portfolio without this symphony"
DAYS = b.last_n_nyse_days(b.AS_OF, 14)


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

    With: 200, 210, 199 -> total -0.5 percent, drawdown 199/210 - 1 = -5.238095 percent.
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


def test_a_composer_symphony_with_a_fallback_basis_peer_reads_bases_differ():
    eligible = {
        "sym-a": _peer(1000.0, b.wave_returns(13)),
        "sym-b": {"basis": "shadow", "values": None},
    }
    item = tr.marginal_contribution("sym-a", eligible)
    assert item["status"] == "unavailable"
    assert item["reason"] == "bases differ"
    for fig in item["figures"].values():
        assert fig["value"] is None


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


@pytest.fixture(scope="module")
def worked_roster():
    return [
        _worked_symphony(COPPER, drift=0.03, freq=0.31),
        _worked_symphony(HARBOR, drift=0.05, freq=0.83),
        _worked_symphony(SLATE, drift=2.0, freq=1.9, amp=3.0),  # extreme: any leakage shows
    ]


def test_the_comparison_runs_from_the_later_eligible_start_and_excludes_the_young_symphony(
    worked_roster,
):
    result = b.review(worked_roster, as_of=b.AS_OF, threshold=126)
    item = b.item_of(b.entry_of(result, COPPER[0]), "marginal")
    assert item["status"] == "available"
    assert item["window"]["first"] == "2025-03-03"
    assert item["window"]["last"] == "2026-12-31"
    assert item["window"]["days"] == b.oracle_count_trading_days(COPPER[2], b.AS_OF)
    assert item["excluded"] == [
        {"display_name": "Slate Valley Hedge", "reason": "excluded: not yet eligible"}
    ]
    assert item["label"] == LABEL


def test_the_young_symphony_contributes_to_neither_leg(worked_roster):
    with_young = b.review(worked_roster, as_of=b.AS_OF, threshold=126)
    without_young = b.review(worked_roster[:2], as_of=b.AS_OF, threshold=126)
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


def test_a_composer_symphony_beside_a_fallback_symphony_reads_bases_differ_in_a_review():
    days = b.last_n_nyse_days(b.AS_OF, 31)
    fallback = b.make_symphony(
        "fallback-sym",
        "Fallback Sym",
        days[0].isoformat(),
        series_failure="unavailable",
        shadow_returns=b.by_date(days[1:], b.wave_returns(30, freq=1.4)),
    )
    composer = b.composer_symphony("composer-sym", "Composer Sym", days, b.wave_returns(30))
    result = b.review([composer, fallback], as_of=b.AS_OF, threshold=30)
    item = b.item_of(b.entry_of(result, "composer-sym"), "marginal")
    assert item["status"] == "unavailable"
    assert item["reason"] == "bases differ"
