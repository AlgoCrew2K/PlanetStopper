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
import math
from datetime import date

import pytest

import advisors.tenure_review as tr
from advisors import retirement_recommender
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


def _gate(state):
    return {"state": state, "text": f"{state}: as the gate words it"}


# Aligned days a pair needs before its stressed window can be read at all, from the oracle
# that restates the recommender's constants.
STRUCTURAL_FLOOR = b.days_needed_for_structural_gate()


def pair_redundancy_item(uncertainty, structural, aligned_days):
    """An available redundancy item that shows a pair on this many aligned days whose two
    gates read these states, with a correlation figure that has a value."""
    pair = {
        "sibling_id": "sibling-1",
        "sibling_name": "Sibling One",
        "n_obs": aligned_days,
        "correlation": _fig(0.9, "correlation"),
        "uncertainty_gate": _gate(uncertainty),
        "structural_gate": _gate(structural),
        "note": None,
    }
    return _item(pair=pair, retire_candidate=False)


# A shown pair is evidence for a verdict when it was measured on at least the aligned days the
# structural gate needs and its correlation is one the interval can be computed for, whatever
# its gates concluded: a pair found not confidently redundant on a long history is a finding.
# A shorter pair says too little to rest a verdict on in either direction, and so does a pair
# whose correlation the interval cannot be computed for (a correlation of exactly one).
# (uncertainty state, structural state, aligned days)
PAIRS_THAT_DECIDE_NOTHING = [
    pytest.param("insufficient data", "not evaluated", 24, id="too_few_days_for_the_interval"),
    pytest.param(
        "passed",
        "insufficient data",
        STRUCTURAL_FLOOR - 1,
        id="one_day_short_of_the_stressed_window",
    ),
    pytest.param(
        "not met",
        "not evaluated",
        STRUCTURAL_FLOOR - 1,
        id="interval_not_met_one_day_short_of_the_stressed_window",
    ),
    pytest.param(
        "passed",
        "passed",
        STRUCTURAL_FLOOR - 1,
        id="both_gates_passed_on_fewer_days_than_the_real_gates_allow",
    ),
    pytest.param(
        "undefined",
        "not evaluated",
        STRUCTURAL_FLOOR + 20,
        id="a_correlation_the_interval_cannot_be_computed_for",
    ),
]
PAIRS_THAT_ARE_EVIDENCE = [
    pytest.param("passed", "passed", STRUCTURAL_FLOOR + 70, id="both_passed"),
    pytest.param("passed", "not met", STRUCTURAL_FLOOR + 70, id="stressed_window_not_met"),
    pytest.param(
        "passed", "undefined", STRUCTURAL_FLOOR + 70, id="stressed_window_cannot_be_computed"
    ),
    pytest.param(
        "not met", "not evaluated", STRUCTURAL_FLOOR + 70, id="interval_not_met_on_a_long_history"
    ),
    pytest.param(
        "not met",
        "not evaluated",
        STRUCTURAL_FLOOR,
        id="interval_not_met_on_exactly_the_days_the_stressed_window_needs",
    ),
]


@pytest.mark.parametrize(("uncertainty", "structural", "aligned_days"), PAIRS_THAT_DECIDE_NOTHING)
def test_a_shown_pair_on_too_few_days_or_with_an_undefined_correlation_is_not_evidence(
    uncertainty, structural, aligned_days
):
    """With the backtest and the marginal item also unavailable, nothing is left to rest a
    verdict on. The item is listed as missing in its place in the fixed order, and the
    item itself is left as it was, pair and all.

    Rejects counting an available redundancy item as evidence whatever its pair says,
    deciding by the gates' states alone (the interval not met on too few days reads as a
    finding there), and counting a pair whose correlation is exactly one."""
    ev = evidence(
        redundancy=pair_redundancy_item(uncertainty, structural, aligned_days),
        backtest=backtest_item(),
        marginal=marginal_item(),
    )
    snapshot = copy.deepcopy(ev)
    got = tr.decide_verdict(ev)
    assert got["verdict"] is None
    assert got["verdict_text"] == "no verdict: no evidence available"
    assert got["verdict_reasons"] == []
    assert got["missing_items"] == ["redundancy", "backtest", "marginal"]
    assert ev == snapshot


@pytest.mark.parametrize(("uncertainty", "structural", "aligned_days"), PAIRS_THAT_DECIDE_NOTHING)
@pytest.mark.parametrize(
    ("backtest_total", "verdict", "reasons"),
    [(5.0, "keep", []), (17.0, "watch", [TRAILS])],
    ids=["level_with_the_backtest", "trailing_by_12_points"],
)
def test_with_the_backtest_available_the_verdict_comes_from_it_and_the_pair_is_listed_missing(
    uncertainty, structural, aligned_days, backtest_total, verdict, reasons
):
    got = tr.decide_verdict(
        evidence(
            redundancy=pair_redundancy_item(uncertainty, structural, aligned_days),
            backtest=backtest_item(realized=5.0, backtest=backtest_total),
            marginal=marginal_item(),
        )
    )
    assert got["verdict"] == verdict
    assert got["verdict_reasons"] == reasons
    assert got["missing_items"] == ["redundancy", "marginal"]


@pytest.mark.parametrize(("uncertainty", "structural", "aligned_days"), PAIRS_THAT_DECIDE_NOTHING)
def test_with_the_marginal_item_available_the_verdict_comes_from_it_and_the_pair_is_listed_missing(
    uncertainty, structural, aligned_days
):
    got = tr.decide_verdict(
        evidence(
            redundancy=pair_redundancy_item(uncertainty, structural, aligned_days),
            backtest=backtest_item(),
            marginal=marginal_item(5.0, 6.0, -10.0, -8.0),
        )
    )
    assert got["verdict"] == "watch"
    assert got["verdict_reasons"] == [DRAGS]
    assert got["missing_items"] == ["redundancy", "backtest"]


@pytest.mark.parametrize(("uncertainty", "structural", "aligned_days"), PAIRS_THAT_ARE_EVIDENCE)
def test_a_shown_pair_on_enough_days_is_evidence_whatever_its_gates_concluded(
    uncertainty, structural, aligned_days
):
    """The pair was measured on at least the aligned days the stressed window needs, so
    that it did not flag the symphony is a finding, however the gates ended: both ran, the
    interval fell short, or the stressed window could not be computed. Rejects treating
    every shown pair as no evidence, deciding by the gates' states (a stressed window that
    cannot be computed is not a reason to ignore 250 days of correlation), and a floor one
    day too high."""
    got = tr.decide_verdict(
        evidence(
            redundancy=pair_redundancy_item(uncertainty, structural, aligned_days),
            backtest=backtest_item(),
            marginal=marginal_item(),
        )
    )
    assert got["verdict"] == "keep"
    assert got["missing_items"] == ["backtest", "marginal"]


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


# ---------------------------------------------------------------------------
# Through build_review: a shown pair on too few days decides nothing
# ---------------------------------------------------------------------------

LEAD_BESIDE_A_YOUNG_PEER_UNDECIDED = [
    # (noise on the peer, gate states of the pair the lead shows)
    pytest.param(None, ("insufficient data", "not evaluated"), id="peer_shares_24_returns"),
    pytest.param(0.2, ("passed", "insufficient data"), id="peer_shares_39_returns_closely"),
    pytest.param(0.0, ("undefined", "not evaluated"), id="peer_repeats_39_returns_exactly"),
]


def _lead_beside_a_young_peer(noise):
    """Two Composer symphonies and a threshold. ``lead`` is eligible. ``peer`` is younger
    than the threshold, so the marginal item has no other eligible symphony to compare and
    reads not applicable, and the pair they form is the only evidence there can be.

    ``noise`` None: the peer shares 24 returns with the lead and follows it exactly, too few
    for the interval. Otherwise the peer shares 39 returns and follows the lead with a sine
    of that amplitude added: enough days for the interval, far too few for the stressed
    window. Noise 0.0 repeats the lead's returns exactly, a correlation of exactly 1, for
    which the interval cannot be computed."""
    if noise is None:
        days = b.last_n_nyse_days(b.AS_OF, 40)
        lead_returns = b.wave_returns(39)
        peer_days = days[-25:]
        peer_returns = [0.9 * r + 0.01 for r in lead_returns[-24:]]
        threshold = 30
    else:
        days = b.last_n_nyse_days(b.AS_OF, 60)
        lead_returns = b.wave_returns(59)
        peer_days = days[-40:]
        peer_returns = [
            r + noise * math.sin(2.9 * i + 1.0) for i, r in enumerate(lead_returns[-39:])
        ]
        threshold = 50
    roster = [
        b.composer_symphony("lead", "Lead", days, lead_returns),
        b.composer_symphony("peer", "Peer", peer_days, peer_returns),
    ]
    return roster, threshold


@pytest.mark.parametrize(("noise", "states"), LEAD_BESIDE_A_YOUNG_PEER_UNDECIDED)
def test_a_symphony_whose_only_evidence_is_a_pair_on_too_few_days_has_no_verdict(noise, states):
    """The pair is a screen hit and is shown with each gate's own state. The backtest was
    not requested and the marginal item has no other eligible symphony, so the verdict has
    nothing to rest on and says so, and the redundancy item is listed as missing.

    Rejects a keep verdict resting on a pair measured on far fewer days than the stressed
    window needs, whatever its gates read."""
    roster, threshold = _lead_beside_a_young_peer(noise)
    lead = b.entry_of(b.review(roster, as_of=b.AS_OF, threshold=threshold), "lead")
    redundancy = b.item_of(lead, "redundancy")
    assert redundancy["status"] == "available"  # fixture sanity: the pair is shown
    assert redundancy["pair"]["sibling_id"] == "peer"
    assert redundancy["pair"]["uncertainty_gate"]["state"] == states[0]
    assert redundancy["pair"]["structural_gate"]["state"] == states[1]
    assert b.item_of(lead, "marginal")["status"] == "not_applicable"  # fixture sanity
    assert b.item_of(lead, "backtest")["status"] == "unavailable"  # fixture sanity
    assert "redundancy" in lead["missing_items"]
    assert lead["verdict"] is None
    assert lead["verdict_text"] == "no verdict: no evidence available"
    assert lead["verdict_reasons"] == []


def _lead_beside_a_correlated_young_peer(rho, aligned_days):
    """The lead and a peer one session short of the threshold, correlated at ``rho`` over
    exactly ``aligned_days`` returns, and what the review says of the lead."""
    roster, threshold = b.lead_beside_young_peer(aligned_days, rho)
    lead = b.entry_of(b.review(roster, as_of=b.AS_OF, threshold=threshold), "lead")
    pair = b.item_of(lead, "redundancy")["pair"]
    assert pair is not None  # fixture sanity: a screen hit is shown
    assert pair["n_obs"] == aligned_days  # fixture sanity
    assert pair["correlation"]["value"] == pytest.approx(rho, abs=1e-9)  # fixture sanity
    assert b.item_of(lead, "marginal")["status"] == "not_applicable"  # fixture sanity
    assert b.item_of(lead, "backtest")["status"] == "unavailable"  # fixture sanity
    return lead


@pytest.mark.parametrize("rho", [0.70, 0.90], ids=["correlated_at_0.70", "correlated_at_0.90"])
@pytest.mark.parametrize(
    "aligned_days",
    [
        retirement_recommender.MIN_OBS_FLOOR,
        100,
        STRUCTURAL_FLOOR - 1,
    ],
    ids=["the_interval_floor", "100_days", "one_day_short_of_the_stressed_window"],
)
def test_a_pair_on_fewer_days_than_the_stressed_window_needs_decides_nothing_whatever_its_correlation(
    rho, aligned_days
):
    """A pair at 0.70 and a pair at 0.90, each over the same 30 to 180 aligned days, are
    both shown and neither supports a verdict: the 0.70 pair, whose interval falls short,
    must not read as a finding that the lead has no redundant sibling while the 0.90 pair
    reads as nothing. Rejects deciding by the gates' states, which counts the weaker pair
    and ignores the stronger."""
    lead = _lead_beside_a_correlated_young_peer(rho, aligned_days)
    assert "redundancy" in lead["missing_items"]
    assert lead["verdict"] is None
    assert lead["verdict_text"] == "no verdict: no evidence available"
    assert lead["verdict_reasons"] == []


@pytest.mark.parametrize("rho", [0.70, 0.90], ids=["correlated_at_0.70", "correlated_at_0.90"])
@pytest.mark.parametrize(
    "aligned_days",
    [STRUCTURAL_FLOOR, 250],
    ids=["exactly_the_days_the_stressed_window_needs", "250_days"],
)
def test_a_pair_on_at_least_the_days_the_stressed_window_needs_is_evidence_whatever_its_gates_concluded(
    rho, aligned_days
):
    """The same two pairs on a long enough history: neither flags the lead (its peer has no
    composite) and each is a finding, so the lead reads keep with the item not listed as
    missing. Rejects a floor above the days the stressed window needs, and a rule that
    discards every shown pair."""
    lead = _lead_beside_a_correlated_young_peer(rho, aligned_days)
    assert "redundancy" not in lead["missing_items"]
    assert lead["verdict"] == "keep"
    assert lead["verdict_reasons"] == []


def test_an_exact_twin_pair_is_shown_but_decides_nothing_because_the_gate_reads_a_correlation_of_one_as_undefined():
    """Two series identical to the last digit have a correlation of exactly 1, for which
    the interval does not exist, so the recommender's gate reads it as undefined and the
    pair is not evidence however many days it covers. This is a known limitation of the
    current rule, pinned so that a change to it is deliberate: the pair is shown with the
    uncertainty gate undefined and the structural gate skipped for that reason, nobody is
    flagged, the item is listed as missing and the lead has no verdict."""
    roster, threshold = b.twin_beside_young_peer(STRUCTURAL_FLOOR + 20)
    result = b.review(roster, as_of=b.AS_OF, threshold=threshold)
    lead = b.entry_of(result, "lead")
    item = b.item_of(lead, "redundancy")
    pair = item["pair"]
    assert pair is not None and pair["n_obs"] > STRUCTURAL_FLOOR  # fixture sanity
    assert pair["correlation"]["value"] == 1.0  # fixture sanity: exactly one
    assert pair["uncertainty_gate"]["state"] == "undefined"
    assert pair["structural_gate"]["state"] == "not evaluated"
    assert pair["structural_gate"]["text"] == "not evaluated: uncertainty gate undefined"
    assert item["retire_candidate"] is False
    assert result.recommendations == []
    assert b.item_of(lead, "marginal")["status"] == "not_applicable"  # fixture sanity
    assert b.item_of(lead, "backtest")["status"] == "unavailable"  # fixture sanity
    assert "redundancy" in lead["missing_items"]
    assert lead["verdict"] is None
    assert lead["verdict_text"] == "no verdict: no evidence available"


@pytest.mark.parametrize(
    ("daily_extra_pp", "verdict", "reasons"),
    [(0.0, "keep", []), (0.5, "watch", [TRAILS])],
    ids=["backtest_level_with_the_realized_returns", "backtest_half_a_point_a_day_ahead"],
)
def test_with_the_backtest_also_available_the_verdict_comes_from_it_and_the_short_pair_is_missing(
    daily_extra_pp, verdict, reasons
):
    roster, threshold = _lead_beside_a_young_peer(None)
    lead_input = roster[0]
    window = tr.qualifying_window(lead_input, b.AS_OF, threshold)
    realized = b.returns_from_values(lead_input.composer_values)
    lead_input.backtest = b.replica_backtest(
        date.fromisoformat(window["first"]),
        date.fromisoformat(window["last"]),
        [r + daily_extra_pp for r in realized],
    )
    lead = b.entry_of(b.review(roster, as_of=b.AS_OF, threshold=threshold), "lead")
    assert b.item_of(lead, "backtest")["status"] == "available"  # fixture sanity
    assert b.item_of(lead, "redundancy")["pair"] is not None  # fixture sanity
    assert "redundancy" in lead["missing_items"]
    assert "backtest" not in lead["missing_items"]
    assert lead["verdict"] == verdict
    assert lead["verdict_reasons"] == reasons


def test_with_the_marginal_item_also_available_the_verdict_comes_from_it_and_the_pair_is_missing():
    """The same two symphonies, both eligible: the comparison portfolio exists, so there is
    a verdict, and it does not rest on the short pair."""
    roster, _threshold = _lead_beside_a_young_peer(None)
    lead = b.entry_of(b.review(roster, as_of=b.AS_OF, threshold=20), "lead")
    assert b.item_of(lead, "marginal")["status"] == "available"  # fixture sanity
    assert b.item_of(lead, "redundancy")["pair"] is not None  # fixture sanity
    assert "redundancy" in lead["missing_items"]
    assert lead["verdict"] in ("keep", "watch")
