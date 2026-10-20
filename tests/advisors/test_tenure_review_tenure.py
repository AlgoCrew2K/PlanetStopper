"""Tenure in NYSE trading days, eligibility, threshold parsing and the as-of day.

Module under test: advisors.tenure_review (pure compute, no I/O).

The calendar oracle is a holiday list kept in tests/advisors/_tenure_review_builders.py,
independent of market_calendar and of the module under test. Expected numbers for the
worked roster (85 trading days, 41 remaining) are the plan's own stated values.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

import advisors.tenure_review as tr
from tests.advisors import _tenure_review_builders as b

_STATS_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "composer" / "symphony_stats_meta.json"
)

INVALID_NOTE = "configured threshold invalid; 126 in use"


# ---------------------------------------------------------------------------
# parse_threshold
# ---------------------------------------------------------------------------


def test_default_threshold_and_environment_key_names():
    assert tr.DEFAULT_MIN_TENURE_TRADING_DAYS == 126
    assert tr.TENURE_THRESHOLD_ENV_KEY == "TENURE_REVIEW_MIN_TRADING_DAYS"


def test_absent_threshold_setting_uses_the_default_and_raises_no_invalid_note():
    """Absent is not invalid: nothing was configured, so no note claims otherwise.

    Rejects an implementation that sends None down the invalid path."""
    setting = tr.parse_threshold(None)
    assert setting.value == 126
    assert setting.note is None


@pytest.mark.parametrize(
    "raw",
    [
        "abc",
        "12x",
        "126.5",
        "1e2",
        "0",
        "000",
        "-5",
        "-0",
        "",
        "   ",
        "+126",
        "12 6",
        "0x10",
        "²",  # superscript two: str.isdigit() is True but int() raises
    ],
)
def test_present_but_invalid_threshold_falls_back_to_default_and_says_so(raw):
    """One case per family member: non-numeric, decimal, scientific, zero, negative,
    empty, whitespace only, signed, embedded space, hex, a digit-like character.

    Rejects int(float(raw)) (accepts 126.5 and 1e2), abs(), max(1, value) (accepts 0
    and -5 as 1), a bare isdigit() check (crashes on the superscript), and a
    missing note."""
    setting = tr.parse_threshold(raw)
    assert setting.value == 126
    assert setting.note == INVALID_NOTE


def test_an_oversized_digit_string_does_not_crash_the_parser():
    """Python refuses int() on digit runs over 4300 characters. A setting the operator
    typed (or a corrupted environment file) must never stop a review from starting.

    Rejects a bare int() with no guard; either 126 with the note or a large positive
    value is acceptable, a raised exception is not."""
    setting = tr.parse_threshold("9" * 5000)
    assert type(setting.value) is int
    assert setting.value >= 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("126", 126), ("1", 1), (" 30 ", 30), ("\t45\n", 45), ("7", 7), ("500", 500)],
)
def test_whole_number_threshold_is_used_as_given_after_stripping(raw, expected):
    setting = tr.parse_threshold(raw)
    assert setting.value == expected
    assert type(setting.value) is int
    assert setting.note is None


# ---------------------------------------------------------------------------
# as_of_trading_day (real NYSE calendar, ET instants)
# ---------------------------------------------------------------------------

AS_OF_CASES = [
    ("sunday_before_open", b.et(2026, 10, 18, 3, 45), date(2026, 10, 16)),
    ("sunday_after_a_holiday_friday", b.et(2026, 12, 27, 3, 45), date(2026, 12, 24)),
    ("new_year_holiday_early_morning", b.et(2027, 1, 1, 3, 45), date(2026, 12, 31)),
    ("thanksgiving_evening_is_still_closed", b.et(2026, 11, 26, 17, 0), date(2026, 11, 25)),
    ("trading_day_morning_excludes_today", b.et(2026, 12, 30, 11, 0), date(2026, 12, 29)),
    ("monday_morning_after_a_holiday_friday", b.et(2026, 12, 28, 11, 0), date(2026, 12, 24)),
    ("monday_morning_regular", b.et(2026, 10, 19, 11, 0), date(2026, 10, 16)),
    ("after_the_close_includes_today", b.et(2026, 12, 31, 17, 0), date(2026, 12, 31)),
    ("one_second_before_the_regular_close", b.et(2026, 12, 30, 15, 59, 59), date(2026, 12, 29)),
    ("exactly_at_the_regular_close", b.et(2026, 12, 30, 16, 0, 0), date(2026, 12, 30)),
    ("half_day_after_the_early_close", b.et(2026, 11, 27, 13, 5), date(2026, 11, 27)),
    ("half_day_exactly_at_the_early_close", b.et(2026, 11, 27, 13, 0, 0), date(2026, 11, 27)),
    (
        "half_day_one_second_before_the_early_close",
        b.et(2026, 11, 27, 12, 59, 59),
        date(2026, 11, 25),
    ),
    ("half_day_mid_afternoon", b.et(2026, 11, 27, 15, 0), date(2026, 11, 27)),
    ("christmas_eve_half_day", b.et(2026, 12, 24, 13, 30), date(2026, 12, 24)),
]


@pytest.mark.parametrize(
    ("now_et", "expected"),
    [pytest.param(n, e, id=i) for i, n, e in AS_OF_CASES],
)
def test_as_of_day_is_the_latest_closed_nyse_session(now_et, expected):
    """Rejects: weekday-only logic (Sunday and holiday cases), counting the current
    day before its close, a hard-coded 16:00 close (half-day cases), an off-by-one at
    the exact close instant, and ignoring NYSE holidays."""
    result = tr.as_of_trading_day(now_et)
    assert result == expected
    assert type(result) is date


@pytest.mark.parametrize(
    ("instant_utc", "expected"),
    [
        (datetime(2026, 12, 31, 20, 30, tzinfo=UTC), date(2026, 12, 30)),  # 15:30 EST
        (datetime(2026, 12, 31, 21, 30, tzinfo=UTC), date(2026, 12, 31)),  # 16:30 EST
        (datetime(2026, 7, 1, 19, 30, tzinfo=UTC), date(2026, 6, 30)),  # 15:30 EDT
        (datetime(2026, 7, 1, 20, 30, tzinfo=UTC), date(2026, 7, 1)),  # 16:30 EDT
        (datetime(2027, 1, 1, 2, 0, tzinfo=UTC), date(2026, 12, 31)),  # 21:00 EST the day before
    ],
)
def test_as_of_day_reads_the_instant_in_eastern_time_whatever_zone_it_arrives_in(
    instant_utc, expected
):
    """The run's day is decided in US Eastern time, independent of the host zone.

    Rejects an implementation that reads .date() and .time() of the value as given
    (a UTC-aware instant 30 minutes before the close would then count as after it)
    and one that assumes a fixed UTC offset through daylight saving."""
    assert tr.as_of_trading_day(instant_utc) == expected


# ---------------------------------------------------------------------------
# count_trading_days (inclusive on both ends)
# ---------------------------------------------------------------------------


def test_plan_worked_example_tenure_is_85_trading_days_and_matches_the_oracle():
    start, end = date(2026, 9, 1), date(2026, 12, 31)
    expected = b.oracle_count_trading_days(start, end)
    assert expected == 85  # the plan's stated value; holiday-list oracle agrees
    assert tr.count_trading_days(start, end) == expected


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2025, 3, 3), date(2026, 12, 31)),
        (date(2026, 11, 23), date(2026, 11, 27)),  # Thanksgiving week: four sessions
        (date(2026, 12, 24), date(2026, 12, 28)),  # half day, closed Friday, weekend, Monday
        (date(2026, 1, 1), date(2026, 1, 1)),  # a holiday on its own
        (date(2026, 10, 17), date(2026, 10, 18)),  # a weekend on its own
        (date(2026, 10, 16), date(2026, 10, 19)),  # Friday through Monday
        (date(2026, 12, 31), date(2026, 12, 31)),  # one trading day
    ],
)
def test_trading_day_count_matches_an_independent_holiday_list(start, end):
    """Rejects a weekday count, a calendar-day count, and any exclusive end."""
    expected = b.oracle_count_trading_days(start, end)
    assert len(b.nyse_days(start, end)) == expected  # the oracle and the library agree
    result = tr.count_trading_days(start, end)
    assert result == expected
    assert type(result) is int


def test_trading_day_count_is_zero_when_the_end_precedes_the_start():
    assert tr.count_trading_days(date(2026, 12, 31), date(2026, 12, 30)) == 0


def test_trading_day_count_includes_both_ends_when_both_are_sessions():
    assert tr.count_trading_days(date(2026, 12, 30), date(2026, 12, 31)) == 2


# ---------------------------------------------------------------------------
# build_review: tenure and eligibility
# ---------------------------------------------------------------------------

THRESHOLD = 20  # small so the history stays small; the rule does not depend on the value


def _pair_of_eligible_symphonies(tenure_days: int):
    """Two uncorrelated Composer-basis symphonies, both with ``tenure_days`` tenure."""
    days = b.last_n_nyse_days(b.AS_OF, tenure_days)
    first = b.composer_symphony(
        "tenure-sym-1", "Tenure One", days, b.wave_returns(tenure_days - 1, freq=0.37)
    )
    second = b.composer_symphony(
        "tenure-sym-2", "Tenure Two", days, b.wave_returns(tenure_days - 1, freq=1.31, phase=0.5)
    )
    return [first, second]


def test_symphony_whose_tenure_equals_the_threshold_is_eligible_and_gets_a_verdict():
    """Rejects a strict greater-than comparison."""
    result = b.review(_pair_of_eligible_symphonies(THRESHOLD), as_of=b.AS_OF, threshold=THRESHOLD)
    entry = b.entry_of(result, "tenure-sym-1")
    assert entry["tenure_trading_days"] == THRESHOLD
    assert entry["eligible"] is True
    assert entry["eligibility_text"] == "eligible"
    assert entry["verdict"] in ("keep", "watch", "retire_candidate")


def test_symphony_one_trading_day_short_of_the_threshold_gets_no_verdict():
    """Singular wording for exactly one day remaining. Rejects a greater-or-equal
    comparison off by one and a plural-only message."""
    result = b.review(
        _pair_of_eligible_symphonies(THRESHOLD - 1), as_of=b.AS_OF, threshold=THRESHOLD
    )
    entry = b.entry_of(result, "tenure-sym-1")
    assert entry["tenure_trading_days"] == THRESHOLD - 1
    assert entry["eligible"] is False
    assert entry["days_remaining"] == 1
    assert entry["eligibility_text"] == "not yet eligible: 1 trading day remaining"
    assert entry["verdict"] is None
    assert entry["verdict_text"].startswith("no verdict")


def test_plan_worked_example_young_symphony_shows_85_days_and_41_remaining():
    young = b.make_symphony("Wr6Ty1Ui7Op3As5Df8Gh", "Slate Valley Hedge", "2026-09-01")
    result = b.review([young], as_of=b.AS_OF, threshold=126)
    entry = b.entry_of(result, "Wr6Ty1Ui7Op3As5Df8Gh")
    assert entry["display_name"] == "Slate Valley Hedge"
    assert entry["invested_since"] == "2026-09-01"
    assert entry["tenure_trading_days"] == 85
    assert entry["eligible"] is False
    assert entry["days_remaining"] == 41
    assert entry["eligibility_text"] == "not yet eligible: 41 trading days remaining"
    assert entry["verdict"] is None


def test_tenure_counts_both_the_first_and_the_as_of_day():
    one_day = b.make_symphony("sym-one-day", "One Day", b.AS_OF.isoformat())
    result = b.review([one_day], as_of=b.AS_OF, threshold=126)
    assert b.entry_of(result, "sym-one-day")["tenure_trading_days"] == 1


def test_symphony_invested_after_the_as_of_day_has_zero_tenure_not_an_unknown_one():
    """A computed 0 and an unknown tenure are different states. Rejects collapsing
    the future-date case into the missing-date case."""
    future = b.make_symphony("sym-future", "Future Start", "2027-01-04")
    result = b.review([future], as_of=b.AS_OF, threshold=126)
    entry = b.entry_of(result, "sym-future")
    assert entry["tenure_trading_days"] == 0
    assert entry["tenure_trading_days"] is not None
    assert entry["days_remaining"] == 126
    assert entry["eligible"] is False
    assert not entry["eligibility_text"].startswith("tenure unavailable")


def test_threshold_in_effect_decides_eligibility_not_the_default():
    days = b.last_n_nyse_days(b.AS_OF, 40)
    sym = b.composer_symphony("sym-forty", "Forty Days", days, b.wave_returns(39))
    at_thirty = b.entry_of(b.review([sym], as_of=b.AS_OF, threshold=30), "sym-forty")
    at_fifty = b.entry_of(b.review([sym], as_of=b.AS_OF, threshold=50), "sym-forty")
    assert at_thirty["eligible"] is True
    assert at_fifty["eligible"] is False
    assert at_fifty["days_remaining"] == 10
    assert at_fifty["eligibility_text"] == "not yet eligible: 10 trading days remaining"


def test_symphony_without_an_invested_since_date_has_unknown_tenure_not_zero():
    """Built from a captured Composer statistics record with the field removed."""
    record = dict(json.loads(_STATS_FIXTURE.read_text(encoding="utf-8"))["symphonies"][0])
    record.pop("invested_since")
    sym = b.make_symphony(record["id"], record["name"], record.get("invested_since"))
    result = b.review([sym], as_of=b.AS_OF, threshold=126)
    entry = b.entry_of(result, record["id"])
    assert entry["tenure_trading_days"] is None
    assert entry["days_remaining"] is None
    assert entry["eligible"] is False
    assert (
        entry["eligibility_text"]
        == "tenure unavailable: Composer did not report an invested-since date"
    )
    assert entry["verdict"] is None
    assert entry["verdict_text"].startswith("no verdict")


def test_renamed_symphony_keeps_its_id_and_tenure_and_shows_the_new_name():
    base = {"symphony_id": "Qz7Lm2Rt9Wx4Kp1Nv8Ys", "invested_since": "2025-03-03"}
    old = b.make_symphony(base["symphony_id"], "Copper Ridge Rotation", base["invested_since"])
    new = b.make_symphony(base["symphony_id"], "Copper Ridge Rotation v2", base["invested_since"])
    first = b.entry_of(b.review([old], as_of=b.AS_OF), base["symphony_id"])
    second = b.entry_of(b.review([new], as_of=b.AS_OF), base["symphony_id"])
    assert first["display_name"] == "Copper Ridge Rotation"
    assert second["display_name"] == "Copper Ridge Rotation v2"
    assert second["tenure_trading_days"] == first["tenure_trading_days"]
    assert second["invested_since"] == first["invested_since"]


def test_a_later_invested_since_date_shortens_the_tenure_to_the_new_start():
    sid = "Bt3Hc8Jd5Fg2Lk9Mn4Pq"
    earlier = b.entry_of(
        b.review([b.make_symphony(sid, "Harbor Lights Momentum", "2025-01-06")], as_of=b.AS_OF),
        sid,
    )
    later = b.entry_of(
        b.review([b.make_symphony(sid, "Harbor Lights Momentum", "2025-03-03")], as_of=b.AS_OF),
        sid,
    )
    assert earlier["tenure_trading_days"] == b.oracle_count_trading_days(date(2025, 1, 6), b.AS_OF)
    assert later["tenure_trading_days"] == b.oracle_count_trading_days(date(2025, 3, 3), b.AS_OF)
    assert later["tenure_trading_days"] < earlier["tenure_trading_days"]


def test_every_input_symphony_gets_exactly_one_entry_keyed_by_its_id():
    inputs = [b.make_symphony(f"sym-{i}", f"Name {i}", "2026-12-01") for i in range(4)]
    result = b.review(inputs, as_of=b.AS_OF)
    assert sorted(e["symphony_id"] for e in result.entries) == [f"sym-{i}" for i in range(4)]


def test_a_review_of_no_symphonies_has_no_entries_and_still_names_its_window():
    result = b.review([], as_of=b.AS_OF, threshold=126)
    assert list(result.entries) == []
    window = result.composite_window
    assert window["last"] == b.AS_OF.isoformat()
    assert window["days"] == 126
    assert b.oracle_count_trading_days(date.fromisoformat(window["first"]), b.AS_OF) == 126
