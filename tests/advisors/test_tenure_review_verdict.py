"""The verdict rule: keep, watch or retire-candidate, and when there is none.

Module under test: advisors.tenure_review (decide_verdict and build_review).

The rule, in order:
  1. retire-candidate when the redundancy item flags the symphony;
  2. otherwise watch when the backtest leads the realized total return by MORE than
     10 percentage points (strict), or when removing the symphony would raise the
     portfolio's total return and leave its max drawdown the same or shallower;
  3. otherwise keep;
  4. no verdict when redundancy, backtest and marginal are all without an available
     figure.
Long-VIX co-firing and the composite never enter the rule directly.

decide_verdict takes the six evidence items as build_review builds them and returns
verdict, verdict_text, verdict_reasons and missing_items.
"""

from __future__ import annotations

import copy

import pytest

import advisors.tenure_review as tr
from tests.advisors import _tenure_review_builders as b

TRAILS = "trails its own backtest"
DRAGS = "drags portfolio return and drawdown"


def _fig(value, unit="percent"):
    return {"value": value, "unit": unit}


def _item(status="available", reason=None, figures=None, **extra):
    base = {"status": status, "reason": reason, "figures": figures or {}}
    base.update(extra)
    return base


def _unavailable(reason="no history", **extra):
    return _item("unavailable", reason, **extra)


def redundancy_item(retire=False, status="available"):
    if status != "available":
        return _unavailable("bases differ", pair=None, retire_candidate=False)
    return _item(pair=None, retire_candidate=retire)


def backtest_item(realized=None, backtest=None):
    if realized is None:
        return _unavailable("Composer unavailable")
    return _item(
        figures={
            "realized_total_return": _fig(realized),
            "backtest_total_return": _fig(backtest),
            "total_return_gap": _fig(realized - backtest, "percentage points"),
            "realized_max_drawdown": _fig(-3.0),
            "backtest_max_drawdown": _fig(-30.0),
            "max_drawdown_gap": _fig(27.0, "percentage points"),
        }
    )


def marginal_item(with_total=None, without_total=None, with_mdd=-10.0, without_mdd=-10.0):
    if with_total is None:
        return _unavailable("bases differ")
    return _item(
        figures={
            "with_total_return": _fig(with_total),
            "without_total_return": _fig(without_total),
            "with_max_drawdown": _fig(with_mdd),
            "without_max_drawdown": _fig(without_mdd),
            "total_return_difference": _fig(without_total - with_total, "percentage points"),
            "max_drawdown_difference": _fig(without_mdd - with_mdd, "percentage points"),
        },
        excluded=[],
    )


def evidence(**overrides):
    base = {
        "performance": _item(),
        "composite": _item(),
        "redundancy": redundancy_item(),
        "backtest": backtest_item(realized=5.0, backtest=5.0),
        "marginal": marginal_item(5.0, 4.0),
        "long_vix": _item(),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Watch: trailing the backtest
# ---------------------------------------------------------------------------


def test_trailing_the_backtest_by_12_points_is_a_watch_with_that_reason():
    got = tr.decide_verdict(evidence(backtest=backtest_item(realized=5.0, backtest=17.0)))
    assert got["verdict"] == "watch"
    assert got["verdict_text"] == "watch"
    assert got["verdict_reasons"] == [TRAILS]


@pytest.mark.parametrize(
    ("backtest_total", "expected"),
    [(14.99, "keep"), (15.0, "keep"), (15.01, "watch"), (17.0, "watch")],
    ids=["9.99_points", "exactly_10_points", "10.01_points", "12_points"],
)
def test_the_backtest_margin_is_strictly_more_than_ten_points(backtest_total, expected):
    """Realized is 5.0, so the shortfall is backtest minus 5.0. Exactly 10.0 is not
    enough. Rejects a greater-or-equal comparison and any other margin."""
    got = tr.decide_verdict(evidence(backtest=backtest_item(realized=5.0, backtest=backtest_total)))
    assert got["verdict"] == expected


def test_beating_the_backtest_by_a_wide_margin_is_not_a_watch():
    """A positive gap in the other direction. Rejects an absolute-value comparison."""
    got = tr.decide_verdict(evidence(backtest=backtest_item(realized=40.0, backtest=5.0)))
    assert got["verdict"] == "keep"


def test_the_margin_is_on_total_return_not_on_drawdown():
    item = backtest_item(realized=5.0, backtest=5.0)
    item["figures"]["max_drawdown_gap"] = _fig(-90.0, "percentage points")
    got = tr.decide_verdict(evidence(backtest=item))
    assert got["verdict"] == "keep"


def test_the_named_margin_constant_is_ten_percentage_points():
    assert tr.BACKTEST_TRAIL_WATCH_PP == 10.0


# ---------------------------------------------------------------------------
# Watch: dragging the portfolio
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("with_total", "without_total", "with_mdd", "without_mdd", "expected"),
    [
        (5.0, 6.0, -10.0, -8.0, "watch"),  # return up, drawdown shallower
        (5.0, 6.0, -10.0, -10.0, "watch"),  # return up, drawdown the same
        (5.0, 6.0, -10.0, -12.0, "keep"),  # return up, drawdown deeper
        (5.0, 5.0, -10.0, -8.0, "keep"),  # return the same
        (5.0, 4.0, -10.0, -8.0, "keep"),  # return down
    ],
    ids=["shallower", "same_drawdown", "deeper_drawdown", "same_return", "lower_return"],
)
def test_removal_must_raise_the_return_without_deepening_the_drawdown(
    with_total, without_total, with_mdd, without_mdd, expected
):
    got = tr.decide_verdict(
        evidence(marginal=marginal_item(with_total, without_total, with_mdd, without_mdd))
    )
    assert got["verdict"] == expected
    if expected == "watch":
        assert got["verdict_reasons"] == [DRAGS]


def test_both_watch_conditions_give_one_watch_with_both_reasons_in_order():
    got = tr.decide_verdict(
        evidence(
            backtest=backtest_item(realized=5.0, backtest=30.0),
            marginal=marginal_item(5.0, 8.0, -10.0, -9.0),
        )
    )
    assert got["verdict"] == "watch"
    assert got["verdict_reasons"] == [TRAILS, DRAGS]


# ---------------------------------------------------------------------------
# Retire-candidate, keep, and no verdict
# ---------------------------------------------------------------------------


def test_a_flagged_symphony_is_a_retire_candidate_even_when_a_watch_condition_also_holds():
    got = tr.decide_verdict(
        evidence(
            redundancy=redundancy_item(retire=True),
            backtest=backtest_item(realized=5.0, backtest=30.0),
        )
    )
    assert got["verdict"] == "retire_candidate"
    assert got["verdict_text"] == "retire-candidate"


def test_no_condition_met_is_keep_with_no_reasons():
    got = tr.decide_verdict(evidence())
    assert got["verdict"] == "keep"
    assert got["verdict_text"] == "keep"
    assert got["verdict_reasons"] == []
    assert got["missing_items"] == []


def test_a_missing_backtest_is_listed_and_the_verdict_comes_from_the_rest():
    got = tr.decide_verdict(evidence(backtest=backtest_item()))
    assert got["verdict"] == "keep"
    assert got["missing_items"] == ["backtest"]
    dragging = tr.decide_verdict(
        evidence(backtest=backtest_item(), marginal=marginal_item(5.0, 6.0, -10.0, -8.0))
    )
    assert dragging["verdict"] == "watch"
    assert dragging["missing_items"] == ["backtest"]


def test_with_redundancy_backtest_and_marginal_all_unavailable_there_is_no_verdict():
    got = tr.decide_verdict(
        evidence(
            redundancy=redundancy_item(status="unavailable"),
            backtest=backtest_item(),
            marginal=marginal_item(),
        )
    )
    assert got["verdict"] is None
    assert got["verdict_text"] == "no verdict: no evidence available"
    assert got["verdict_reasons"] == []
    assert got["missing_items"] == ["redundancy", "backtest", "marginal"]


def test_a_not_applicable_marginal_item_is_not_evidence_either():
    not_applicable = _item("not_applicable", "no other eligible symphonies")
    got = tr.decide_verdict(
        evidence(
            redundancy=redundancy_item(status="unavailable"),
            backtest=backtest_item(),
            marginal=not_applicable,
        )
    )
    assert got["verdict"] is None


@pytest.mark.parametrize("survivor", ["redundancy", "backtest", "marginal"])
def test_one_available_deciding_item_is_enough_for_a_verdict(survivor):
    items = {
        "redundancy": redundancy_item(status="unavailable"),
        "backtest": backtest_item(),
        "marginal": marginal_item(),
    }
    items[survivor] = {
        "redundancy": redundancy_item(),
        "backtest": backtest_item(realized=5.0, backtest=5.0),
        "marginal": marginal_item(5.0, 4.0),
    }[survivor]
    got = tr.decide_verdict(evidence(**items))
    assert got["verdict"] == "keep"


def test_unavailable_items_are_listed_in_the_fixed_item_order():
    unavailable = {name: _unavailable() for name in b.EVIDENCE_ITEMS}
    unavailable["redundancy"] = redundancy_item(status="unavailable")
    got = tr.decide_verdict(unavailable)
    assert got["missing_items"] == list(b.EVIDENCE_ITEMS)
    assert got["verdict"] is None


def test_an_unavailable_performance_item_alone_does_not_remove_the_verdict():
    got = tr.decide_verdict(evidence(performance=_unavailable("not computable: too few returns")))
    assert got["verdict"] == "keep"
    assert got["missing_items"] == ["performance"]


def test_the_long_vix_item_and_the_composite_never_change_the_verdict():
    baseline = tr.decide_verdict(evidence())
    swapped = tr.decide_verdict(
        evidence(
            long_vix=_item(
                figures={"held_days": _fig(120, "days"), "co_held_days": _fig(120, "days")}
            ),
            composite=_item(figures={"composite": _fig(0.0, "score")}),
        )
    )
    assert {k: swapped[k] for k in ("verdict", "verdict_text", "verdict_reasons")} == {
        k: baseline[k] for k in ("verdict", "verdict_text", "verdict_reasons")
    }


def test_deciding_a_verdict_does_not_modify_the_evidence():
    ev = evidence(backtest=backtest_item(realized=5.0, backtest=30.0))
    snapshot = copy.deepcopy(ev)
    tr.decide_verdict(ev)
    assert ev == snapshot


# ---------------------------------------------------------------------------
# Through build_review
# ---------------------------------------------------------------------------

DAYS = b.last_n_nyse_days(b.AS_OF, 20)


def test_a_symphony_whose_removal_would_raise_the_portfolio_return_is_a_watch():
    """A falls about 0.3 percent a day and B rises about 0.3 percent a day, on
    orthogonal patterns, so neither is redundant and there is no backtest."""
    roster = [
        b.composer_symphony("drag-a", "Drag A", DAYS, b.sine_returns(19, 1, amp=0.2, drift=-0.3)),
        b.composer_symphony("drag-b", "Drag B", DAYS, b.sine_returns(19, 2, amp=0.2, drift=0.3)),
    ]
    result = b.review(roster, as_of=b.AS_OF, threshold=20)
    a = b.entry_of(result, "drag-a")
    assert a["verdict"] == "watch"
    assert a["verdict_text"] == "watch"
    assert a["verdict_reasons"] == [DRAGS]
    assert "backtest" in a["missing_items"]
    assert b.entry_of(result, "drag-b")["verdict"] == "keep"


def test_a_lower_composite_member_of_a_passing_pair_is_not_retired_when_its_sibling_is_unscored():
    """keep-a would be the weaker member of a pair that passes both gates, but its
    sibling is below the tenure threshold and has no composite. Neither is retired,
    and keep-a's verdict comes only from the watch and keep conditions."""
    roster, threshold = b.roster_with_ineligible_correlated_sibling()
    result = b.review(roster, as_of=b.AS_OF, threshold=threshold)
    keeper = b.entry_of(result, "keep-a")
    assert keeper["verdict"] in ("keep", "watch")
    assert set(keeper["verdict_reasons"]) <= {TRAILS, DRAGS}
    assert b.entry_of(result, "young-c")["verdict"] is None
    assert b.entry_of(result, "young-c")["verdict_text"].startswith("no verdict")
    assert all(e["verdict"] != "retire_candidate" for e in result.entries)


def test_a_fallback_basis_sibling_is_never_paired_and_nobody_is_retired():
    """The same series on the Composer basis and on the fallback basis are never
    paired: the pair reads bases differ and nobody is flagged. With backtest and
    marginal also unavailable, neither gets a verdict, so neither offers a decision."""
    returns = b.wave_returns(19, amp=0.6, drift=0.02)
    composer = b.composer_symphony("composer-a", "Composer A", DAYS, returns)
    fallback = b.make_symphony(
        "fallback-b",
        "Fallback B",
        DAYS[0].isoformat(),
        series_failure="unavailable",
        shadow_returns=b.by_date(DAYS[1:], [r + 0.05 for r in returns]),
    )
    result = b.review([composer, fallback], as_of=b.AS_OF, threshold=20)
    for sid in ("composer-a", "fallback-b"):
        entry = b.entry_of(result, sid)
        assert b.item_of(entry, "redundancy")["reason"] == "bases differ"
        assert b.item_of(entry, "redundancy")["retire_candidate"] is False
        # redundancy, backtest and marginal are all unavailable, so there is no verdict
        assert entry["verdict"] is None, sid
        assert entry["verdict_text"] == "no verdict: no evidence available", sid
