"""Tenure in NYSE trading days, eligibility, threshold parsing and the as-of day.

Module under test: advisors.tenure_review (pure compute, no I/O).

The calendar oracle is a holiday list kept in tests/advisors/_tenure_review_builders.py,
independent of market_calendar and of the module under test. Expected numbers for the
worked roster (85 trading days, 41 remaining) are the plan's own stated values.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

import advisors.tenure_review as tr
from advisors import composer_backtest_client as client
from tests.advisors import _tenure_review_builders as b

_REPO_ROOT = Path(tr.__file__).resolve().parents[1]

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


# ---------------------------------------------------------------------------
# Unknown tenure is never described as a tenure that will pass
# ---------------------------------------------------------------------------

UNKNOWN = "tenure unavailable"
NOT_YET = "not yet eligible"
ROSTER_DAYS = b.last_n_nyse_days(b.AS_OF, 31)
ROSTER_THRESHOLD = 30


def _mixed_tenure_roster():
    """Two eligible symphonies, one with no invested-since date (but a Composer series,
    so it still takes part in pairs as a sibling) and one that is simply young."""
    return [
        b.composer_symphony("comp-a", "Comp A", ROSTER_DAYS, b.wave_returns(30, freq=0.4)),
        b.composer_symphony("comp-c", "Comp C", ROSTER_DAYS, b.wave_returns(30, freq=1.3)),
        b.composer_symphony(
            "unk-u",
            "Unknown Tenure",
            ROSTER_DAYS,
            b.wave_returns(30, freq=2.1),
            invested_since=None,
        ),
        b.composer_symphony("young-y", "Young Why", ROSTER_DAYS[-10:], b.wave_returns(9, freq=0.8)),
    ]


def test_a_symphony_with_unknown_tenure_reads_tenure_unavailable_in_every_evidence_item():
    """The six items of a symphony whose invested-since date is missing say so. A young
    symphony keeps its own, different reason. Rejects 'not yet eligible' for the missing
    date, which promises the symphony will qualify with time."""
    result = b.review(_mixed_tenure_roster(), as_of=b.AS_OF, threshold=ROSTER_THRESHOLD)
    unknown = b.entry_of(result, "unk-u")
    young = b.entry_of(result, "young-y")
    assert unknown["tenure_trading_days"] is None  # fixture sanity
    for name in b.EVIDENCE_ITEMS:
        item = b.item_of(unknown, name)
        assert item["status"] == "unavailable", name
        assert item["reason"] == UNKNOWN, name
        assert all(fig["value"] is None for fig in item["figures"].values()), name
        assert b.item_of(young, name)["reason"] == NOT_YET, name


def test_unknown_tenure_outranks_having_no_history_as_the_reason():
    """No invested-since date and no series at all: both are true, and the entry's own
    eligibility text already says the tenure is unavailable."""
    nothing = b.no_history_symphony("unk-void", "Unknown And Void", None)
    entry = b.entry_of(b.review([nothing], as_of=b.AS_OF, threshold=ROSTER_THRESHOLD), "unk-void")
    assert entry["basis"]["kind"] == "none"
    for name in b.EVIDENCE_ITEMS:
        assert b.item_of(entry, name)["reason"] == UNKNOWN, name


def test_every_marginal_item_lists_each_excluded_symphony_with_the_reason_that_applies_to_it():
    """A symphony with an unknown tenure is excluded as 'tenure unavailable' and a young
    one as 'not yet eligible', in the item of every entry that lists them."""
    result = b.review(_mixed_tenure_roster(), as_of=b.AS_OF, threshold=ROSTER_THRESHOLD)
    reasons = {
        "Unknown Tenure": "excluded: tenure unavailable",
        "Young Why": "excluded: not yet eligible",
    }
    for sid in ("comp-a", "comp-c"):
        item = b.item_of(b.entry_of(result, sid), "marginal")
        assert item["status"] == "available", sid  # fixture sanity: the two are compared
        assert len(item["excluded"]) == len(reasons), sid
        assert {r["display_name"]: r["reason"] for r in item["excluded"]} == reasons, sid
    for entry in result.entries:
        for record in b.item_of(entry, "marginal")["excluded"]:
            assert record["reason"] == reasons[record["display_name"]], entry["symphony_id"]
            assert record["display_name"] != entry["display_name"], entry["symphony_id"]


WITHHELD_KINDS = {
    "unk-u": ("Unknown Tenure", "excluded: tenure unavailable"),
    "young-y": ("Young Why", "excluded: not yet eligible"),
    "void-v": ("Void Peer", "excluded: no history"),
}


def _roster_with_one_symphony_of_each_withheld_kind():
    """Two scored symphonies, one with no invested-since date, one younger than the
    threshold and one eligible by tenure that has no series on any basis."""
    return [
        *_mixed_tenure_roster(),
        b.no_history_symphony("void-v", "Void Peer", ROSTER_DAYS[0].isoformat()),
    ]


def test_no_entry_lists_its_own_symphony_as_excluded_from_its_own_comparison():
    """A symphony is never left out of a comparison it is the subject of. Every entry
    lists exactly the other withheld symphonies, each with the reason that applies to it,
    so a scored entry lists all three and a withheld entry the other two.

    Rejects the review-wide list handed to every entry unchanged (a withheld entry then
    names itself), and a filter that empties the list instead of removing one record."""
    result = b.review(
        _roster_with_one_symphony_of_each_withheld_kind(),
        as_of=b.AS_OF,
        threshold=ROSTER_THRESHOLD,
    )
    assert len(result.entries) == 5  # fixture sanity: all five symphonies have an entry
    for entry in result.entries:
        sid = entry["symphony_id"]
        records = b.item_of(entry, "marginal")["excluded"]
        listed = sorted((r["display_name"], r["reason"]) for r in records)
        expected = sorted(v for other, v in WITHHELD_KINDS.items() if other != sid)
        assert listed == expected, sid
        assert entry["display_name"] not in [r["display_name"] for r in records], sid


def test_two_withheld_symphonies_that_share_a_display_name_each_list_the_other():
    """The list is told apart by symphony, not by name. Rejects removing every record
    that carries the entry's own display name, which would also remove the twin."""
    roster = [
        b.composer_symphony("comp-a", "Comp A", ROSTER_DAYS, b.wave_returns(30, freq=0.4)),
        b.composer_symphony("comp-c", "Comp C", ROSTER_DAYS, b.wave_returns(30, freq=1.3)),
        b.composer_symphony("twin-young", "Twin", ROSTER_DAYS[-10:], b.wave_returns(9, freq=0.8)),
        b.composer_symphony(
            "twin-unknown", "Twin", ROSTER_DAYS, b.wave_returns(30, freq=2.1), invested_since=None
        ),
    ]
    result = b.review(roster, as_of=b.AS_OF, threshold=ROSTER_THRESHOLD)
    young_reason = {"display_name": "Twin", "reason": "excluded: not yet eligible"}
    unknown_reason = {"display_name": "Twin", "reason": "excluded: tenure unavailable"}
    assert b.item_of(b.entry_of(result, "twin-young"), "marginal")["excluded"] == [unknown_reason]
    assert b.item_of(b.entry_of(result, "twin-unknown"), "marginal")["excluded"] == [young_reason]
    for sid in ("comp-a", "comp-c"):
        listed = b.item_of(b.entry_of(result, sid), "marginal")["excluded"]
        assert sorted(listed, key=lambda r: r["reason"]) == [young_reason, unknown_reason], sid


# ---------------------------------------------------------------------------
# An invested-since date outside the calendar the module supports
# ---------------------------------------------------------------------------

# Run in a fresh interpreter with a timeout, because a walk over centuries of calendar
# days does not return in any time a test could wait for.
_CALENDAR_PROBE = """
import json
import sys
from datetime import UTC, date, datetime, timedelta

sys.path.insert(0, {root!r})
import advisors.tenure_review as tr

floor = tr.CALENDAR_FLOOR
values = [
    (floor - timedelta(days=1)).isoformat(),
    "1500-01-01",
    "0001-01-01",
    floor.isoformat(),
]
inputs = tr.ReviewInputs(
    run_id="calendar-probe",
    trigger="nightly",
    started_at=datetime(2026, 12, 31, 22, 0, tzinfo=UTC),
    as_of=date(2026, 12, 31),
    threshold=tr.ThresholdSetting(126, None),
    symphonies=[
        tr.SymphonyInput(symphony_id=f"probe-{{i}}", display_name=f"Probe {{i}}", invested_since=v)
        for i, v in enumerate(values)
    ],
)
result = tr.build_review(inputs)
keys = ("invested_since", "tenure_trading_days", "eligible", "days_remaining",
        "eligibility_text", "verdict")
print(json.dumps({{"floor": floor.isoformat(), "entries": [{{k: e[k] for k in keys}} for e in result.entries]}}))
"""


def test_an_invested_since_date_before_the_supported_calendar_reads_tenure_unavailable_quickly():
    """The floor is a named constant. A date before it (a day before, or centuries
    before) gives an unknown tenure at once and never raises; a date on the floor is
    still counted, from the independent calendar library.

    Rejects walking every day since the date, a crash on an out-of-range year, and a
    floor that excludes its own first day."""
    done = subprocess.run(
        [sys.executable, "-I", "-c", _CALENDAR_PROBE.format(root=str(_REPO_ROOT))],
        capture_output=True,
        text=True,
        timeout=240,
        cwd=_REPO_ROOT,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    probe = json.loads(done.stdout.strip().splitlines()[-1])
    before_floor, long_ago, year_one, on_floor = probe["entries"]
    for entry in (before_floor, long_ago, year_one):
        assert entry["tenure_trading_days"] is None, entry
        assert entry["eligible"] is False, entry
        assert entry["days_remaining"] is None, entry
        assert entry["eligibility_text"].startswith(UNKNOWN), entry
        assert entry["verdict"] is None, entry
    floor = date.fromisoformat(probe["floor"])
    assert on_floor["tenure_trading_days"] == len(b.nyse_days(floor, b.AS_OF))
    assert on_floor["eligible"] is True


@pytest.mark.parametrize("text", ["", "garbage", "2026-02-30", "0000-00-00"])
def test_an_invested_since_text_that_is_not_a_date_reads_tenure_unavailable(text):
    entry = b.entry_of(
        b.review([b.make_symphony("bad-date", "Bad Date", text)], as_of=b.AS_OF, threshold=126),
        "bad-date",
    )
    assert entry["tenure_trading_days"] is None
    assert entry["eligible"] is False
    assert entry["eligibility_text"].startswith(UNKNOWN)


# ---------------------------------------------------------------------------
# The qualifying window a runner asks for before it requests a backtest
# ---------------------------------------------------------------------------


def _qualifying_roster():
    return [
        b.composer_symphony(
            "win-a", "Win A", ROSTER_DAYS, b.wave_returns(30, freq=0.4), definition_available=True
        ),
        b.composer_symphony("win-c", "Win C", ROSTER_DAYS, b.wave_returns(30, freq=1.3)),
    ]


def test_the_qualifying_window_of_an_eligible_symphony_is_the_window_its_review_uses():
    """The same first and last dates the performance item reports, as a plain mapping of
    ISO dates and a day count. A backtest requested over exactly these dates is accepted
    by the review, not rejected as covering other dates."""
    roster = _qualifying_roster()
    window = tr.qualifying_window(roster[0], b.AS_OF, ROSTER_THRESHOLD)
    entry = b.entry_of(
        b.review(roster, as_of=b.AS_OF, threshold=ROSTER_THRESHOLD),
        "win-a",
    )
    assert window == b.item_of(entry, "performance")["window"]
    assert set(window) == {"first", "last", "days", "sessions"}
    assert window["sessions"] == len(
        b.nyse_days(date.fromisoformat(window["first"]), date.fromisoformat(window["last"]))
    )

    values = {d.isoformat(): 100.0 + i for i, d in enumerate(ROSTER_DAYS)}
    roster[0].backtest = client.BacktestResult(
        stats={},
        data_warnings=[],
        daily_portfolio_values=values,
        first_day=window["first"],
        last_market_day=window["last"],
    )
    item = b.item_of(
        b.entry_of(b.review(roster, as_of=b.AS_OF, threshold=ROSTER_THRESHOLD), "win-a"),
        "backtest",
    )
    assert item["status"] == "available"


def test_the_qualifying_window_starts_where_a_late_series_starts():
    late = ROSTER_DAYS[5:]
    sym = b.composer_symphony(
        "late-series",
        "Late Series",
        late,
        b.wave_returns(len(late) - 1),
        invested_since=ROSTER_DAYS[0].isoformat(),
    )
    window = tr.qualifying_window(sym, b.AS_OF, ROSTER_THRESHOLD)
    assert window["first"] == late[0].isoformat()
    assert window["last"] == b.AS_OF.isoformat()
    assert window["days"] == len(late)


def test_the_qualifying_window_of_a_shadow_basis_symphony_is_its_shadow_window():
    sym = b.make_symphony(
        "shadow-win",
        "Shadow Win",
        ROSTER_DAYS[0].isoformat(),
        series_failure="unavailable",
        shadow_returns=b.by_date(ROSTER_DAYS[1:], b.wave_returns(30)),
    )
    window = tr.qualifying_window(sym, b.AS_OF, ROSTER_THRESHOLD)
    entry = b.entry_of(b.review([sym], as_of=b.AS_OF, threshold=ROSTER_THRESHOLD), "shadow-win")
    assert window == b.item_of(entry, "performance")["window"]
    # thirty rows are thirty returns, measured from the session before the first row
    assert window["first"] == ROSTER_DAYS[0].isoformat()
    assert window["days"] == len(ROSTER_DAYS)


def _shadow_with_rows_left_out(left_out, *, stored_without_return):
    """A shadow symphony with a row on every session after the first tenure day, except that
    the rows of ``left_out`` are missing or stored with no return."""
    rows = b.by_date(ROSTER_DAYS[1:], b.wave_returns(len(ROSTER_DAYS) - 1))
    for day in left_out:
        if stored_without_return:
            rows[day.isoformat()] = None
        else:
            del rows[day.isoformat()]
    return b.shadow_symphony("holey", "Holey", ROSTER_DAYS[0].isoformat(), rows)


@pytest.mark.parametrize(
    "positions", [[7], [3, 4, 9]], ids=["one_session_without_a_row", "three_sessions_without_a_row"]
)
@pytest.mark.parametrize(
    "stored_without_return", [False, True], ids=["no_row_stored", "row_stored_with_no_return"]
)
def test_a_shadow_window_with_a_session_that_has_no_usable_row_has_no_qualifying_window(
    positions, stored_without_return
):
    """The review withholds the backtest item for such a window, so a runner that asked for the
    window and fetched a backtest over it would fetch one the review discards. The window is
    absent exactly when the review's backtest item is withheld for the hole. Rejects a window
    for a symphony whose backtest item cannot be compared."""
    symphony = _shadow_with_rows_left_out(
        [ROSTER_DAYS[i] for i in positions], stored_without_return=stored_without_return
    )
    entry = b.entry_of(b.review([symphony], as_of=b.AS_OF, threshold=ROSTER_THRESHOLD), "holey")
    assert b.item_of(entry, "performance")["status"] == "available"  # fixture sanity
    assert "has no usable row" in b.item_of(entry, "backtest")["reason"]  # fixture sanity
    assert tr.qualifying_window(symphony, b.AS_OF, ROSTER_THRESHOLD) is None


def test_a_shadow_window_with_every_session_covered_and_a_composer_window_with_a_day_missing_qualify():
    """Rejects widening the hole rule: a shadow window whose sessions all have a row, and a
    Composer window with a missing interior day (a series of levels, which a backtest over the
    window can still be compared with), both keep their window."""
    whole = _shadow_with_rows_left_out([], stored_without_return=False)
    assert tr.qualifying_window(whole, b.AS_OF, ROSTER_THRESHOLD) is not None
    composer = b.composer_symphony("c-gap", "Composer Gap", ROSTER_DAYS, b.wave_returns(30))
    composer.composer_values[ROSTER_DAYS[7].isoformat()] = None
    window = tr.qualifying_window(composer, b.AS_OF, ROSTER_THRESHOLD)
    assert window is not None
    assert window["days"] == len(ROSTER_DAYS) - 1


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(
            lambda: b.composer_symphony(
                "short", "Short", ROSTER_DAYS[-10:], b.wave_returns(9), invested_since=None
            ),
            id="tenure_unknown",
        ),
        pytest.param(
            lambda: b.composer_symphony("young", "Young", ROSTER_DAYS[-10:], b.wave_returns(9)),
            id="below_the_threshold",
        ),
        pytest.param(
            lambda: b.no_history_symphony("void", "Void", ROSTER_DAYS[0].isoformat()),
            id="eligible_but_no_history",
        ),
        pytest.param(
            lambda: b.make_symphony("future", "Future", "2027-01-04"), id="invested_after_as_of"
        ),
    ],
)
def test_there_is_no_qualifying_window_unless_the_symphony_is_eligible_and_has_history(build):
    assert tr.qualifying_window(build(), b.AS_OF, ROSTER_THRESHOLD) is None


def _two_composer_points():
    values = {ROSTER_DAYS[-2].isoformat(): 100.0, ROSTER_DAYS[-1].isoformat(): 101.0}
    return b.make_symphony(
        "c-two", "Composer Two", ROSTER_DAYS[0].isoformat(), composer_values=values
    )


def _three_composer_points():
    values = {
        d.isoformat(): v for d, v in zip(ROSTER_DAYS[-3:], (100.0, 101.0, 100.5), strict=True)
    }
    return b.make_symphony(
        "c-three", "Composer Three", ROSTER_DAYS[0].isoformat(), composer_values=values
    )


def _shadow_rows_on(symphony_id, row_days):
    rows = {d.isoformat(): 0.4 - 0.3 * i for i, d in enumerate(row_days)}
    return b.shadow_symphony(symphony_id, symphony_id.title(), ROSTER_DAYS[0].isoformat(), rows)


@pytest.mark.parametrize(
    ("build", "scored"),
    [
        pytest.param(_two_composer_points, False, id="composer_two_points_is_one_return"),
        pytest.param(_three_composer_points, True, id="composer_three_points_is_two_returns"),
        pytest.param(
            lambda: _shadow_rows_on("s-one", ROSTER_DAYS[-1:]),
            False,
            id="shadow_one_row_after_a_baseline_is_one_return",
        ),
        pytest.param(
            lambda: _shadow_rows_on("s-two", ROSTER_DAYS[-2:]),
            True,
            id="shadow_two_rows_after_a_baseline_are_two_returns",
        ),
        pytest.param(
            lambda: _shadow_rows_on("s-edge-two", [ROSTER_DAYS[0], ROSTER_DAYS[-1]]),
            False,
            id="shadow_two_rows_from_the_first_tenure_day_are_one_return",
        ),
        pytest.param(
            lambda: _shadow_rows_on("s-edge-three", ROSTER_DAYS[:3]),
            True,
            id="shadow_three_rows_from_the_first_tenure_day_are_two_returns",
        ),
    ],
)
def test_a_symphony_whose_performance_cannot_be_scored_has_no_qualifying_window(build, scored):
    """Eligible, with a series, but too short for the shared metric function: no backtest
    is worth requesting for it, and the window it would be requested over is the window of
    a performance item that exists. Rejects a window for a symphony that cannot be scored
    and a rule that differs between the two bases."""
    symphony = build()
    entry = b.entry_of(
        b.review([symphony], as_of=b.AS_OF, threshold=ROSTER_THRESHOLD), symphony.symphony_id
    )
    performance = b.item_of(entry, "performance")
    assert entry["eligible"] is True and entry["basis"]["kind"] != "none"  # fixture sanity
    assert (performance["status"] == "available") is scored  # fixture sanity
    window = tr.qualifying_window(symphony, b.AS_OF, ROSTER_THRESHOLD)
    if scored:
        assert window == performance["window"]
    else:
        assert window is None


def test_the_threshold_given_decides_whether_a_window_qualifies():
    sym = b.composer_symphony("edge", "Edge", ROSTER_DAYS, b.wave_returns(30))
    assert tr.qualifying_window(sym, b.AS_OF, len(ROSTER_DAYS)) is not None
    assert tr.qualifying_window(sym, b.AS_OF, len(ROSTER_DAYS) + 1) is None


def test_asking_for_a_qualifying_window_changes_nothing():
    sym = b.composer_symphony("still", "Still", ROSTER_DAYS, b.wave_returns(30))
    snapshot = dict(sym.composer_values)
    tr.qualifying_window(sym, b.AS_OF, ROSTER_THRESHOLD)
    assert sym.composer_values == snapshot
