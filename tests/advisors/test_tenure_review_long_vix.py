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
    assert item["window"] == {
        "first": ISO[0],
        "last": ISO[-1],
        "days": 5,
        "sessions": len(SESSIONS_IN_WINDOW),
    }
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
    assert item["window"].get("sessions") == len(DAYS)  # holdings on 1 of the window's 5 sessions


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
# A day key is the date in its first ten characters
# ---------------------------------------------------------------------------

TIME_PARTS = ["T00:00:00", "T00:00:00Z", " 00:00:00", "T16:00:00-05:00"]
HEAVY_DAY = {"subj": {"VIXY": 200.0}, "o1": {"UVXY": 200.0}, "o2": {"TLT": 1.0}}


@pytest.mark.parametrize("time_part", TIME_PARTS)
def test_a_holdings_key_with_a_time_part_is_read_as_its_date(time_part):
    """Every day, including the window's last, whose stamped key sorts after the bare
    date. The item equals the item for the same holdings keyed by date. Rejects comparing
    keys as text against the window's bounds, which drops the last day."""
    stamped = tr.HoldingsInput(
        days={f"{iso}{time_part}": held for iso, held in zip(ISO, SCENARIO, strict=True)}
    )
    assert _cofire(stamped) == _cofire(_holdings(SCENARIO))
    assert _cofire(stamped)["window"]["days"] == len(ISO)  # fixture sanity: no day was lost


def test_two_keys_for_one_date_count_as_one_day_and_the_greatest_key_wins():
    """Each day in turn is given two keys, the bare date holding a heavy long-VIX day and
    the stamped key the real one; the stamped key sorts later and wins, in either
    insertion order. Rejects counting both keys as days, and first-wins or last-wins."""
    expected = _cofire(_holdings(SCENARIO))
    for position, iso in enumerate(ISO):
        rest = {k: held for k, held in zip(ISO, SCENARIO, strict=True) if k != iso}
        for first_key, second_key in ((iso, f"{iso}T16:00:00"), (f"{iso}T16:00:00", iso)):
            days = dict(rest)
            for key in (first_key, second_key):
                days[key] = HEAVY_DAY if key == iso else SCENARIO[position]
            assert _cofire(tr.HoldingsInput(days=days)) == expected, (iso, first_key)


def test_an_unknown_value_under_the_greatest_key_does_not_hide_a_known_day_of_the_same_date():
    partial = copy.deepcopy(SCENARIO[0])
    partial["o1"]["SPY"] = None
    rest = {k: held for k, held in zip(ISO[1:], SCENARIO[1:], strict=True)}
    for keys in ((ISO[0], f"{ISO[0]}T16:00:00"), (f"{ISO[0]}T16:00:00", ISO[0])):
        days = {**rest}
        for key in keys:
            days[key] = SCENARIO[0] if key == ISO[0] else partial
        assert _cofire(tr.HoldingsInput(days=days)) == _cofire(_holdings(SCENARIO)), keys


def test_a_holdings_key_that_is_not_a_date_is_ignored():
    """Nine characters, 2026-12-3, sorts between the window's bounds as text; it names no
    day. Rejects counting it, and raising on it."""
    days = dict(_holdings(SCENARIO).days)
    days[ISO[-1][:-1]] = SCENARIO[0]
    days["not-a-date"] = SCENARIO[0]
    assert _cofire(tr.HoldingsInput(days=days)) == _cofire(_holdings(SCENARIO))


# ---------------------------------------------------------------------------
# A holdings key on a day the exchange was closed is not a day
# ---------------------------------------------------------------------------

SESSIONS_IN_WINDOW = b.nyse_days(DAYS[0], DAYS[-1])
CLOSED_DAYS = [d for d in b.calendar_days(DAYS[0], DAYS[-1]) if d not in SESSIONS_IN_WINDOW]
# The five sessions straddle Christmas Day (a weekday holiday) and a weekend.
CLOSED_CASES = {
    "weekday_holiday": [d for d in CLOSED_DAYS if d.weekday() < 5],
    "saturday": [d for d in CLOSED_DAYS if d.weekday() == 5],
    "sunday": [d for d in CLOSED_DAYS if d.weekday() == 6],
    "every_closed_day": CLOSED_DAYS,
}


@pytest.mark.parametrize("stamp", ["", "T16:00:00"], ids=["bare_date_key", "stamped_key"])
@pytest.mark.parametrize("case", list(CLOSED_CASES))
def test_a_holdings_key_on_a_closed_day_is_not_a_day(case, stamp):
    """Heavy long-VIX holdings are keyed on days the exchange was closed (a weekday holiday,
    a Saturday, a Sunday), bare or stamped. The item equals the item for the sessions alone,
    and its day count is the number of exchange sessions in the window, from the calendar
    library. Rejects counting every calendar day, and a rule that drops weekends but keeps a
    weekday holiday."""
    closed = CLOSED_CASES[case]
    assert closed, "fixture sanity: the window holds this kind of closed day"
    days = dict(_holdings(SCENARIO).days)
    for day in closed:
        days[f"{day.isoformat()}{stamp}"] = HEAVY_DAY
    item = _cofire(tr.HoldingsInput(days=days))
    assert item == _cofire(_holdings(SCENARIO))
    assert item["window"]["days"] == len(SESSIONS_IN_WINDOW)
    # the sessions the span holds are the exchange's, not every calendar day of the span
    assert item["window"].get("sessions") == len(SESSIONS_IN_WINDOW)


def test_holdings_and_a_value_series_read_the_same_days_from_the_same_keys():
    """One rule reads the days of a mapping keyed by dates, for a symphony's series and for
    the holdings: stamped duplicates of a session, closed days (bare and stamped), keys
    outside the window and keys that are not dates give the same days either way. Rejects a
    second copy of the rule in the holdings reader that drifts from the first."""
    keys = [
        *ISO,
        f"{ISO[1]}T16:00:00",
        f"{ISO[3]}T09:30:00",
        *(day.isoformat() for day in CLOSED_DAYS),
        f"{CLOSED_DAYS[0].isoformat()}T16:00:00",
        "2026-12-23",
        "2027-01-04",
        ISO[-1][:-1],
        "not-a-date",
    ]
    held = {"subj": {"VIXY": 10.0, "SPY": 90.0}, "o1": {"SPY": 100.0}, "o2": {"TLT": 100.0}}
    item = _cofire(tr.HoldingsInput(days={key: held for key in keys}))
    series = tr.realized_window({key: 100.0 + i for i, key in enumerate(keys)}, DAYS[0], DAYS[-1])
    assert series["days"] == len(
        SESSIONS_IN_WINDOW
    )  # fixture sanity: the series kept sessions only
    assert _figures(item)["held_days"] == series["days"]
    assert item["window"]["days"] == series["days"]


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
    assert item["window"].get("sessions") == len(DAYS)  # holdings on 3 of the window's 5 sessions
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


def _review_over(holdings_by_day):
    roster = [
        b.composer_symphony(f"vix-{k}", f"Vix {k}", DAYS_20, b.sine_returns(19, k, drift=0.05 * k))
        for k in (1, 2, 3)
    ]
    return b.review(
        roster,
        as_of=b.AS_OF,
        threshold=20,
        holdings=tr.HoldingsInput(days=holdings_by_day),
    )


def _review_with(per_symphony_holdings):
    return _review_over({d.isoformat(): per_symphony_holdings for d in DAYS_20})


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


def test_holdings_keyed_on_every_calendar_day_give_the_review_of_holdings_keyed_on_sessions():
    """Heavy holdings sit on the weekends and the holiday inside the realized window. The
    whole review (every item of every entry, the long-VIX day counts included) equals the
    review of the same holdings keyed on the sessions alone."""
    on_sessions = {
        "vix-1": {"VIXY": 90.0, "SPY": 10.0},
        "vix-2": {"UVXY": 50.0, "SPY": 50.0},
        "vix-3": {"SPY": 100.0},
    }
    on_closed_days = {"vix-1": {"VIXY": 900.0}, "vix-2": {"SPY": 1.0}, "vix-3": {"SPY": 1.0}}
    every_day = b.calendar_days(DAYS_20[0], DAYS_20[-1])
    assert len(every_day) > len(DAYS_20)  # fixture sanity: closed days lie inside the window
    keyed_on_every_day = {
        d.isoformat(): on_sessions if d in DAYS_20 else on_closed_days for d in every_day
    }
    reviewed = _review_over(keyed_on_every_day)
    assert reviewed.entries == _review_with(on_sessions).entries
    assert b.value_of(b.entry_of(reviewed, "vix-1"), "long_vix", "held_days") == len(DAYS_20)


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


# ---------------------------------------------------------------------------
# Holdings on N of M trading days: the item states both counts
# ---------------------------------------------------------------------------


def test_a_review_states_the_days_with_holdings_and_the_sessions_the_window_holds():
    """Holdings exist for 12 of the 20 sessions of the symphony's window. The item carries
    12 as the days used and 20 as the sessions the span holds (from the calendar library), so
    a reader sees holdings on 12 of 20 trading days rather than a window 12 days long. Rejects
    a window that states only the days used, and sessions taken from the days used."""
    covered = DAYS_20[:12]
    on_covered_days = {
        d.isoformat(): {
            "vix-1": {"VIXY": 90.0, "SPY": 10.0},
            "vix-2": {"SPY": 100.0},
            "vix-3": {"SPY": 100.0},
        }
        for d in covered
    }
    window = b.item_of(b.entry_of(_review_over(on_covered_days), "vix-1"), "long_vix")["window"]
    assert window["first"] == DAYS_20[0].isoformat()
    assert window["last"] == DAYS_20[-1].isoformat()
    assert window["days"] == len(covered)
    assert window.get("sessions") == len(b.nyse_days(DAYS_20[0], DAYS_20[-1]))
    assert window["days"] < window["sessions"]  # fixture sanity: the two counts differ


# ---------------------------------------------------------------------------
# A symphony outside the run's live set has no long-VIX figure and moves nobody else's
# ---------------------------------------------------------------------------


def test_a_subject_outside_the_live_set_reads_unavailable_never_zero_held_days():
    """The holdings mention the symphony and hold a long-VIX product on every day, but it is
    not among the live symphonies of the run, so nothing of its holdings is counted: the item
    has no figure at all rather than a count of 0 that reads as never having held one."""
    item = tr.long_vix_cofiring(_holdings(SCENARIO), "subj", ["o1", "o2"], WINDOW)
    assert item["status"] == "unavailable"
    assert item["reason"]
    for fig in item["figures"].values():
        assert fig["value"] is None


def test_the_holdings_of_a_symphony_outside_the_live_set_move_no_figure_of_a_reviewed_symphony():
    """A symphony that held a long-VIX product on days a reviewed symphony did too is not in
    the run: it has no entry and no long-VIX figure, and every item of every reviewed
    symphony (co-held days and both peak shares included) equals the item for the same
    holdings without it. Rejects counting it as a live peer."""
    reviewed = {
        "vix-1": {"VIXY": 90.0, "SPY": 10.0},
        "vix-2": {"UVXY": 50.0, "SPY": 50.0},
        "vix-3": {"SPY": 100.0},
    }
    stranger = {"VIXY": 700.0, "UVXY": 300.0}
    with_stranger = _review_with({**reviewed, "stranger": stranger})
    without = _review_with(reviewed)
    assert "stranger" not in [e["symphony_id"] for e in with_stranger.entries]
    for sid in reviewed:
        item = b.item_of(b.entry_of(with_stranger, sid), "long_vix")
        assert item == b.item_of(b.entry_of(without, sid), "long_vix"), sid
    co_held = b.value_of(b.entry_of(with_stranger, "vix-1"), "long_vix", "co_held_days")
    assert co_held == len(
        DAYS_20
    )  # fixture sanity: vix-2 co-holds on every day, the stranger aside


# ---------------------------------------------------------------------------
# A key the reader ignores is never evaluated
# ---------------------------------------------------------------------------

IGNORED_KEYS = {
    "a_key_that_is_not_a_date": "notes",
    "a_date_before_the_window": "2026-12-01",
    "a_date_after_the_window": "2027-01-04",
    "a_weekend_day_inside_the_window": next(d.isoformat() for d in CLOSED_DAYS if d.weekday() >= 5),
    "the_weekday_holiday_inside_the_window": next(
        d.isoformat() for d in CLOSED_DAYS if d.weekday() < 5
    ),
}
NOT_A_DAY_OF_HOLDINGS = [None, "text", 7, [1, 2], 3.5]


@pytest.mark.parametrize("value", NOT_A_DAY_OF_HOLDINGS, ids=repr)
@pytest.mark.parametrize("key", list(IGNORED_KEYS.values()), ids=list(IGNORED_KEYS))
def test_a_value_that_is_not_a_day_of_holdings_under_an_ignored_key_changes_nothing_and_raises_nothing(
    key, value
):
    """A key that names no day of the window (not a date, outside the window, or a day the
    exchange was closed) is skipped before its value is looked at, so a value that could not be
    holdings under it is harmless. Rejects reading the value first, which raises on a value
    that is not a mapping."""
    days = dict(_holdings(SCENARIO).days)
    days[key] = value
    assert _cofire(tr.HoldingsInput(days=days)) == _cofire(_holdings(SCENARIO))
