"""Long-VIX co-firing: information only, never an input to the verdict.

Module under test: advisors.tenure_review (long_vix_cofiring and build_review).

Holdings arrive as {day: {symphony id: {ticker: market value}}}. A ticker may be
the bare symbol or Composer's long form (EQUITIES::VIXY//USD). Counts are counts of
DAYS, not positions. Shares are percent of the portfolio's total holding value that
day, taken across the live symphonies only.
"""

from __future__ import annotations

import copy

import pytest

import advisors.tenure_review as tr
from advisors import frontrunner_detector
from tests.advisors import _tenure_review_builders as b

LABEL = "informational: not used in the verdict"
LONG_VIX = ("VIXY", "VIXM", "UVXY", "UVIX", "VXX")
SHORT_VIX = ("SVXY", "SVIX")
DAYS = b.last_n_nyse_days(b.AS_OF, 5)
ISO = [d.isoformat() for d in DAYS]
WINDOW = {"first": ISO[0], "last": ISO[-1], "days": 5}


def _long_form(symbol):
    return f"EQUITIES::{symbol}//USD"


def _holdings(per_day, failure=None):
    """per_day: list aligned with ISO of {symphony id: {ticker: value}} (None = no data)."""
    days = {iso: held for iso, held in zip(ISO, per_day, strict=True) if held is not None}
    return tr.HoldingsInput(days=days, failure=failure)


def _cofire(holdings, live=("subj", "o1", "o2"), window=WINDOW):
    return tr.long_vix_cofiring(holdings, "subj", list(live), window)


def _figures(item):
    return {name: fig["value"] for name, fig in item["figures"].items()}


# ---------------------------------------------------------------------------
# Constant
# ---------------------------------------------------------------------------


def test_long_vix_products_are_the_vix_family_without_the_short_products():
    assert frozenset(LONG_VIX) == tr.LONG_VIX_TICKERS
    assert tr.LONG_VIX_TICKERS.isdisjoint(SHORT_VIX)
    assert frontrunner_detector.VIX_FAMILY_TICKERS - set(SHORT_VIX) == tr.LONG_VIX_TICKERS


# ---------------------------------------------------------------------------
# The counts and peak shares
# ---------------------------------------------------------------------------

# Five days, three live symphonies. Totals are 300 every day.
#   D1 subj holds VIXY 10; o2 holds UVXY 5: long-VIX 15
#   D2 only o1 holds VIXM 20: 20
#   D3 subj holds UVXY 30; o1 holds VXX 10: 40
#   D4 subj holds VIXY 50 and UVIX 10, nobody else: 60
#   D5 nobody: 0
SCENARIO = [
    {
        "subj": {"VIXY": 10.0, "SPY": 90.0},
        "o1": {"SPY": 100.0},
        "o2": {"UVXY": 5.0, "TLT": 95.0},
    },
    {"subj": {"SPY": 100.0}, "o1": {"VIXM": 20.0, "SPY": 80.0}, "o2": {"TLT": 100.0}},
    {"subj": {"UVXY": 30.0, "SPY": 70.0}, "o1": {"VXX": 10.0, "SPY": 90.0}, "o2": {"TLT": 100.0}},
    {
        "subj": {"VIXY": 50.0, "UVIX": 10.0, "SPY": 40.0},
        "o1": {"SPY": 100.0},
        "o2": {"TLT": 100.0},
    },
    {"subj": {"SPY": 100.0}, "o1": {"SPY": 100.0}, "o2": {"TLT": 100.0}},
]


def test_counts_are_days_and_shares_are_percent_of_the_days_total_value():
    """Held on D1, D3, D4 (three days; D4 has two products but is one day). Another
    symphony also held one on D1 and D3 (two days; not D2, when only the others held).
    Peak share with the subject: D4, 60 of 300 = 20 percent. Without it, the others
    only: D2, 20 of 200 = 10 percent.

    Rejects counting positions, counting days a peer held without the subject,
    sharing by ticker count, and a peak taken over the wrong portfolio."""
    item = _cofire(_holdings(SCENARIO))
    got = _figures(item)
    assert item["status"] == "available"
    assert got["held_days"] == 3
    assert got["co_held_days"] == 2
    assert got["peak_share_with"] == pytest.approx(20.0, rel=1e-9)
    assert got["peak_share_without"] == pytest.approx(10.0, rel=1e-9)
    assert item["label"] == LABEL
    assert item["window"] == {"first": ISO[0], "last": ISO[-1], "days": 5}
    for name in ("held_days", "co_held_days"):
        assert item["figures"][name]["unit"] == "days", name
    for name in ("peak_share_with", "peak_share_without"):
        assert item["figures"][name]["unit"] == "percent", name
    assert item["subject"] and item["source"]


def test_co_held_days_never_exceed_held_days():
    got = _figures(_cofire(_holdings(SCENARIO)))
    assert got["co_held_days"] <= got["held_days"]


def test_a_symphony_that_never_held_a_long_vix_product_has_counts_of_zero_not_missing():
    quiet = [
        {"subj": {"SPY": 100.0}, "o1": {"UVXY": 10.0, "SPY": 90.0}, "o2": {"TLT": 100.0}},
        {"subj": {"SPY": 100.0}, "o1": {"SPY": 100.0}, "o2": {"TLT": 100.0}},
        {"subj": {"SPY": 100.0}, "o1": {"SPY": 100.0}, "o2": {"VIXM": 40.0, "TLT": 60.0}},
        {"subj": {"SPY": 100.0}, "o1": {"SPY": 100.0}, "o2": {"TLT": 100.0}},
        {"subj": {"SPY": 100.0}, "o1": {"SPY": 100.0}, "o2": {"TLT": 100.0}},
    ]
    item = _cofire(_holdings(quiet))
    got = _figures(item)
    assert item["status"] == "available"
    assert got["held_days"] == 0 and got["held_days"] is not None
    assert got["co_held_days"] == 0 and got["co_held_days"] is not None
    assert got["peak_share_with"] == pytest.approx(40 / 300 * 100, rel=1e-9)
    assert got["peak_share_without"] == pytest.approx(40 / 200 * 100, rel=1e-9)


@pytest.mark.parametrize("symbol", LONG_VIX)
@pytest.mark.parametrize("form", ["bare", "long"])
def test_every_long_vix_product_counts_in_both_ticker_forms(symbol, form):
    ticker = symbol if form == "bare" else _long_form(symbol)
    one_day = [
        {"subj": {ticker: 10.0, "SPY": 90.0}, "o1": {"SPY": 100.0}, "o2": {"TLT": 100.0}},
        None,
        None,
        None,
        None,
    ]
    got = _figures(_cofire(_holdings(one_day)))
    assert got["held_days"] == 1
    assert got["co_held_days"] == 0
    assert got["peak_share_with"] == pytest.approx(10 / 300 * 100, rel=1e-9)


@pytest.mark.parametrize("symbol", SHORT_VIX)
@pytest.mark.parametrize("form", ["bare", "long"])
def test_short_vix_products_are_not_long_vix_in_either_form(symbol, form):
    """Rejects matching on the whole VIX family or on a VI/VX substring."""
    ticker = symbol if form == "bare" else _long_form(symbol)
    shorts = [
        {
            "subj": {ticker: 40.0, "SPY": 60.0},
            "o1": {ticker: 10.0, "SPY": 90.0},
            "o2": {"TLT": 100.0},
        }
    ] * 5
    got = _figures(_cofire(_holdings(shorts)))
    assert got["held_days"] == 0
    assert got["co_held_days"] == 0
    assert got["peak_share_with"] == 0.0
    assert got["peak_share_without"] == 0.0


def test_only_live_symphonies_make_up_the_portfolio():
    ghost = [
        {
            "subj": {"SPY": 100.0},
            "o1": {"SPY": 100.0},
            "o2": {"TLT": 100.0},
            "retired-ghost": {"VIXY": 500.0},
        }
    ] * 5
    got = _figures(_cofire(_holdings(ghost)))
    assert got["co_held_days"] == 0
    assert got["peak_share_with"] == 0.0
    assert got["peak_share_without"] == 0.0


def test_days_outside_the_symphonys_window_are_ignored():
    inside = {"subj": {"VIXY": 10.0, "SPY": 90.0}, "o1": {"SPY": 100.0}, "o2": {"TLT": 100.0}}
    before = {"subj": {"VIXY": 99.0}, "o1": {"UVXY": 99.0}, "o2": {"TLT": 1.0}}
    holdings = tr.HoldingsInput(days={"2026-12-23": before, ISO[2]: inside, "2027-01-04": before})
    item = _cofire(holdings)
    assert _figures(item)["held_days"] == 1
    assert _figures(item)["co_held_days"] == 0
    assert item["window"]["days"] == 1


# ---------------------------------------------------------------------------
# An unknown holding value is a missing day, never a zero
# ---------------------------------------------------------------------------

UNKNOWN_VALUES = [None, float("nan"), float("inf"), float("-inf"), "n/a"]
UNKNOWN_IDS = ["null", "nan", "inf", "neg_inf", "text"]
# (symphony id, ticker) cells of the first scenario day: a long-VIX holding and another
# holding, each of the subject and of two peers
UNKNOWN_CELLS = [("subj", "VIXY"), ("subj", "SPY"), ("o1", "SPY"), ("o2", "UVXY")]


@pytest.mark.parametrize("unknown", UNKNOWN_VALUES, ids=UNKNOWN_IDS)
@pytest.mark.parametrize(("symphony", "ticker"), UNKNOWN_CELLS)
def test_a_day_carrying_an_unknown_holding_value_is_treated_as_a_missing_day(
    unknown, symphony, ticker
):
    """The portfolio total of that day is unknown, so every share and count that would
    read it is left out: the item equals the item for the same holdings with that day
    absent. Rejects counting the value as zero (which shrinks the total and can mark the
    day as held), and a partial sum shown as a real percent."""
    day = copy.deepcopy(SCENARIO[0])
    day[symphony][ticker] = unknown
    item = _cofire(_holdings([day, *SCENARIO[1:]]))
    absent = _cofire(_holdings([None, *SCENARIO[1:]]))
    assert item == absent
    assert item["window"]["days"] == 4  # fixture sanity: the day was dropped, not kept


def test_a_partial_day_never_sets_a_peak_share():
    """With the peer's SPY value unknown, the day's visible total is 210 and 40 of it is
    long-VIX, which would read 19 percent. The other days peak at 20/300 with the subject
    and 10 percent without it."""
    partial = {
        "subj": {"UVXY": 30.0, "SPY": 70.0},
        "o1": {"VXX": 10.0, "SPY": None},
        "o2": {"TLT": 100.0},
    }
    got = _figures(_cofire(_holdings([partial, SCENARIO[1], None, None, SCENARIO[4]])))
    assert got["held_days"] == 0
    assert got["co_held_days"] == 0
    assert got["peak_share_with"] == pytest.approx(20 / 300 * 100, rel=1e-9)
    assert got["peak_share_without"] == pytest.approx(20 / 200 * 100, rel=1e-9)


def test_an_unknown_value_in_a_symphony_outside_the_live_set_does_not_drop_the_day():
    """A retired symphony's holdings enter no figure, so what they hold is irrelevant."""
    with_ghost = [dict(day, **{"retired-ghost": {"VIXY": None}}) for day in SCENARIO]
    assert _cofire(_holdings(with_ghost)) == _cofire(_holdings(SCENARIO))


def test_when_every_day_with_holdings_carries_an_unknown_value_the_item_is_unavailable():
    unknown_day = copy.deepcopy(SCENARIO[0])
    unknown_day["subj"]["VIXY"] = None
    item = _cofire(_holdings([unknown_day] * 5))
    assert item["status"] == "unavailable"
    for fig in item["figures"].values():
        assert fig["value"] is None


# ---------------------------------------------------------------------------
# Missing data is unavailable or reduced, never zero
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        ("refused", "Composer refused the request"),
        ("rate_limited", "Composer rate limit"),
        ("unavailable", "Composer unavailable"),
        ("unreadable", "Composer response unreadable"),
    ],
)
def test_a_holdings_read_that_failed_outright_reads_unavailable_with_its_reason(failure, reason):
    item = _cofire(tr.HoldingsInput(days={}, failure=failure))
    assert item["status"] == "unavailable"
    assert item["reason"] == reason
    for fig in item["figures"].values():
        assert fig["value"] is None


FAILURE_CLASSES = (
    "refused",
    "not_found",
    "rate_limited",
    "unavailable",
    "empty",
    "unreadable",
    "rejected",
    "no-such-failure-class",
)


def _series_reason(failure):
    """The reason the series path gives a failure class, read from a real review."""
    days = b.last_n_nyse_days(b.AS_OF, 21)
    probe = b.make_symphony(
        "reason-probe",
        "Reason Probe",
        days[0].isoformat(),
        series_failure=failure,
        shadow_returns=b.by_date(days[1:], b.wave_returns(20)),
    )
    entry = b.entry_of(b.review([probe], as_of=b.AS_OF, threshold=20), "reason-probe")
    return entry["basis"]["fallback_reason"]


@pytest.mark.parametrize("failure", FAILURE_CLASSES)
def test_a_failed_holdings_read_gives_the_reason_the_series_path_gives_the_same_class(failure):
    """One mapping from failure class to reason serves both reads, including a class the
    review does not know. Rejects a second table, or a default that names another class."""
    item = _cofire(tr.HoldingsInput(days={}, failure=failure))
    assert item["status"] == "unavailable"
    assert item["reason"] == _series_reason(failure)
    for fig in item["figures"].values():
        assert fig["value"] is None


def test_every_failure_class_gives_a_holdings_reason_of_its_own():
    reasons = [_cofire(tr.HoldingsInput(days={}, failure=f))["reason"] for f in FAILURE_CLASSES]
    assert len(set(reasons)) == len(reasons)


def test_no_holdings_input_at_all_reads_unavailable_never_zero():
    item = _cofire(None)
    assert item["status"] == "unavailable"
    for fig in item["figures"].values():
        assert fig["value"] is None


def test_holdings_that_never_mention_the_subject_read_unavailable_never_zero():
    others_only = [{"o1": {"VIXY": 10.0, "SPY": 90.0}, "o2": {"TLT": 100.0}}] * 5
    item = _cofire(_holdings(others_only))
    assert item["status"] == "unavailable"
    for fig in item["figures"].values():
        assert fig["value"] is None


def test_days_with_no_holdings_are_skipped_and_the_row_shows_the_reduced_day_count():
    gappy = [SCENARIO[0], None, SCENARIO[2], None, SCENARIO[4]]
    item = _cofire(_holdings(gappy))
    got = _figures(item)
    assert item["status"] == "available"
    assert item["window"]["days"] == 3
    assert got["held_days"] == 2  # D1 and D3
    assert got["co_held_days"] == 2
    assert got["peak_share_with"] == pytest.approx(40 / 300 * 100, rel=1e-9)  # D3


def test_with_no_other_holdings_the_portfolio_without_the_subject_is_empty_not_zero_percent():
    alone = [{"subj": {"VIXY": 10.0, "SPY": 90.0}}] * 5
    item = _cofire(_holdings(alone), live=("subj",))
    got = _figures(item)
    assert got["held_days"] == 5
    assert got["peak_share_with"] == pytest.approx(10.0, rel=1e-9)
    assert got["peak_share_without"] is None
    assert item["figures"]["peak_share_without"]["value"] is None


# ---------------------------------------------------------------------------
# Never an input to the verdict
# ---------------------------------------------------------------------------

DAYS_20 = b.last_n_nyse_days(b.AS_OF, 20)


def _review_with(per_symphony_holdings):
    roster = [
        b.composer_symphony(f"vix-{k}", f"Vix {k}", DAYS_20, b.sine_returns(19, k, drift=0.05 * k))
        for k in (1, 2, 3)
    ]
    days = {d.isoformat(): per_symphony_holdings for d in DAYS_20}
    return b.review(roster, as_of=b.AS_OF, threshold=20, holdings=tr.HoldingsInput(days=days))


def test_changing_only_the_long_vix_holdings_changes_nothing_but_the_long_vix_item():
    heavy = {
        "vix-1": {"VIXY": 90.0, "SPY": 10.0},
        "vix-2": {"UVXY": 50.0, "SPY": 50.0},
        "vix-3": {"SPY": 100.0},
    }
    none = {k: {"SPY": 100.0} for k in ("vix-1", "vix-2", "vix-3")}
    a, c = _review_with(heavy), _review_with(none)
    for sid in ("vix-1", "vix-2", "vix-3"):
        ea, ec = b.entry_of(a, sid), b.entry_of(c, sid)
        assert ea["verdict"] == ec["verdict"], sid
        assert ea["verdict_reasons"] == ec["verdict_reasons"], sid
        assert ea["missing_items"] == ec["missing_items"], sid
        for name in ("performance", "composite", "redundancy", "backtest", "marginal"):
            assert b.item_of(ea, name) == b.item_of(ec, name), (sid, name)
    held_a = b.value_of(b.entry_of(a, "vix-1"), "long_vix", "held_days")
    held_c = b.value_of(b.entry_of(c, "vix-1"), "long_vix", "held_days")
    assert held_a == 20 and held_c == 0  # the item does respond to its input


def test_a_review_without_any_holdings_input_gets_the_same_verdicts():
    heavy = {"vix-1": {"VIXY": 90.0, "SPY": 10.0}, "vix-2": {"SPY": 100.0}, "vix-3": {"SPY": 100.0}}
    with_holdings = _review_with(heavy)
    roster = [
        b.composer_symphony(f"vix-{k}", f"Vix {k}", DAYS_20, b.sine_returns(19, k, drift=0.05 * k))
        for k in (1, 2, 3)
    ]
    without = b.review(roster, as_of=b.AS_OF, threshold=20, holdings=None)
    for sid in ("vix-1", "vix-2", "vix-3"):
        assert b.entry_of(with_holdings, sid)["verdict"] == b.entry_of(without, sid)["verdict"]
        assert b.item_of(b.entry_of(without, sid), "long_vix")["status"] == "unavailable"


def test_the_subject_of_an_available_long_vix_item_names_its_symphony():
    result = _review_with({"vix-1": {"VIXY": 90.0, "SPY": 10.0}, "vix-2": {"SPY": 100.0}})
    for sid in ("vix-1", "vix-2"):
        entry = b.entry_of(result, sid)
        item = b.item_of(entry, "long_vix")
        assert item["status"] == "available", sid  # fixture sanity
        assert entry["display_name"] in item["subject"], sid
