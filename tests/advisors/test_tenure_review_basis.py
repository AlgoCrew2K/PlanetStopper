"""Realized windows, basis selection and the labels on every figure.

Module under test: advisors.tenure_review.

Returns are percent-scale. A value series is a deposit-adjusted level keyed by
ISO date; a return is the move between consecutive kept points inside the window,
so M kept points give M minus 1 returns. Dropped days are never counted as 0%.
"""

from __future__ import annotations

import itertools
import math
from datetime import date, timedelta

import pytest

import advisors.tenure_review as tr
from tests.advisors import _tenure_review_builders as b

COMPOSER_LABEL = "realized: Composer account, deposit-adjusted"
SHADOW_LABEL = "PlanetStopper shadow history from the 2026-06-22 go-live floor"

THRESHOLD = 20
DAYS = b.last_n_nyse_days(b.AS_OF, 25)
WINDOW_DAYS = DAYS[-THRESHOLD:]


# ---------------------------------------------------------------------------
# realized_window
# ---------------------------------------------------------------------------


def _window(values, start=None, end=None):
    return tr.realized_window(values, start or DAYS[0], end or DAYS[-1])


def test_returns_are_percent_moves_between_consecutive_kept_points():
    """Rejects fraction-scale returns (0.01 for 1%) and log returns."""
    values = {
        DAYS[0].isoformat(): 100.0,
        DAYS[1].isoformat(): 101.0,
        DAYS[2].isoformat(): 99.99,
        DAYS[3].isoformat(): 103.0,
    }
    win = _window(values, DAYS[0], DAYS[3])
    assert win["dates"] == [d.isoformat() for d in DAYS[:4]]
    expected = [(101.0 / 100.0 - 1) * 100, (99.99 / 101.0 - 1) * 100, (103.0 / 99.99 - 1) * 100]
    # same arithmetic as the oracle; 1e-12 relative only absorbs a reordered expression
    assert win["returns_pct"] == pytest.approx(expected, rel=1e-12)
    assert win["first"] == DAYS[0].isoformat()
    assert win["last"] == DAYS[3].isoformat()
    assert win["days"] == 4


def test_window_is_sorted_whatever_order_the_mapping_arrives_in():
    ordered = {d.isoformat(): 100.0 + i for i, d in enumerate(DAYS[:5])}
    shuffled = {k: ordered[k] for k in (sorted(ordered)[i] for i in (3, 0, 4, 2, 1))}
    win = _window(shuffled, DAYS[0], DAYS[4])
    assert win["dates"] == sorted(ordered)
    assert win["returns_pct"] == pytest.approx(b.returns_from_values(ordered), rel=1e-12)


@pytest.mark.parametrize(
    "bad",
    [None, float("nan"), float("inf"), float("-inf"), 0.0, -3.0],
    ids=["null", "nan", "inf", "neg_inf", "zero", "negative"],
)
def test_an_unusable_value_is_dropped_and_never_counted_as_a_zero_return(bad):
    """The whole family of unusable values: null, NaN, both infinities, zero and
    negative levels. The move across the dropped day is kept (100 to 110 is +10%),
    and the dropped day is not a point.

    Rejects treating the value as 0 (returns [0.0, 10.0]), carrying the previous
    level forward, erasing the move across the gap, and counting the day."""
    values = {
        DAYS[0].isoformat(): 100.0,
        DAYS[1].isoformat(): bad,
        DAYS[2].isoformat(): 110.0,
    }
    win = _window(values, DAYS[0], DAYS[2])
    assert win["dates"] == [DAYS[0].isoformat(), DAYS[2].isoformat()]
    assert win["returns_pct"] == pytest.approx([10.0], rel=1e-12)
    assert win["days"] == 2


def test_a_day_missing_from_the_series_is_handled_like_a_dropped_value():
    values = {DAYS[0].isoformat(): 100.0, DAYS[2].isoformat(): 110.0}
    win = _window(values, DAYS[0], DAYS[2])
    assert win["dates"] == [DAYS[0].isoformat(), DAYS[2].isoformat()]
    assert win["returns_pct"] == pytest.approx([10.0], rel=1e-12)
    assert win["days"] == 2


def test_points_outside_the_tenure_to_as_of_range_are_dropped_and_the_ends_are_kept():
    values = {
        DAYS[0].isoformat(): 90.0,  # before the tenure starts
        DAYS[1].isoformat(): 100.0,  # the first day of the tenure
        DAYS[2].isoformat(): 101.0,
        DAYS[3].isoformat(): 102.0,  # the as-of day
        DAYS[4].isoformat(): 500.0,  # after the as-of day: the day that has not closed
    }
    win = _window(values, DAYS[1], DAYS[3])
    assert win["dates"] == [d.isoformat() for d in DAYS[1:4]]
    assert win["first"] == DAYS[1].isoformat()
    assert win["last"] == DAYS[3].isoformat()
    assert win["returns_pct"] == pytest.approx([1.0, (102.0 / 101.0 - 1) * 100], rel=1e-12)


def test_a_series_that_begins_after_the_tenure_starts_reports_its_own_first_date():
    values = {d.isoformat(): 100.0 + i for i, d in enumerate(DAYS[3:8])}
    win = _window(values, DAYS[0], DAYS[7])
    assert win["first"] == DAYS[3].isoformat()
    assert win["first"] != DAYS[0].isoformat()
    assert win["days"] == 5


def test_a_window_with_no_points_has_zero_days_and_no_dates_not_a_fabricated_range():
    win = _window({}, DAYS[0], DAYS[-1])
    assert win["days"] == 0
    assert win["first"] is None
    assert win["last"] is None
    assert win["dates"] == []
    assert win["returns_pct"] == []


def test_a_window_with_one_point_has_one_day_and_no_returns():
    win = _window({DAYS[2].isoformat(): 100.0}, DAYS[0], DAYS[-1])
    assert win["days"] == 1
    assert win["first"] == win["last"] == DAYS[2].isoformat()
    assert win["returns_pct"] == []


def test_building_a_window_does_not_modify_the_input_mapping():
    values = {DAYS[0].isoformat(): 100.0, DAYS[1].isoformat(): None, DAYS[2].isoformat(): 110.0}
    snapshot = dict(values)
    _window(values, DAYS[0], DAYS[2])
    assert values == snapshot


# ---------------------------------------------------------------------------
# One reading of a series' date keys
# ---------------------------------------------------------------------------

TIME_PARTS = ["T00:00:00", "T00:00:00Z", " 00:00:00", "T16:00:00-05:00"]


@pytest.mark.parametrize("time_part", TIME_PARTS)
def test_a_date_key_with_a_time_part_is_read_as_its_date(time_part):
    plain = {d.isoformat(): 100.0 + i * i for i, d in enumerate(DAYS[:4])}
    stamped = {k + time_part: v for k, v in plain.items()}
    win = _window(stamped, DAYS[0], DAYS[3])
    assert win["dates"] == sorted(plain)
    assert win["days"] == 4
    assert win["returns_pct"] == pytest.approx(b.returns_from_values(plain), rel=1e-12)


def test_two_keys_for_one_date_resolve_to_the_greatest_key_whatever_the_mapping_order():
    """Rule: among the keys that name one date, the greatest key (string order) whose
    value is usable wins, so a time-stamped key beats the bare date and a later stamp
    beats an earlier one. Every insertion order of the same keys gives the same window.

    Rejects last-one-wins, first-one-wins and averaging, all of which depend on order."""
    day = DAYS[1].isoformat()
    entries = {
        DAYS[0].isoformat(): 100.0,
        day: 105.0,
        day + "T00:00:00": 110.0,
        day + "T16:00:00": 120.0,
    }
    for order in itertools.permutations(entries):
        win = _window({k: entries[k] for k in order}, DAYS[0], DAYS[1])
        assert win["dates"] == [DAYS[0].isoformat(), day], order
        assert win["returns_pct"] == pytest.approx([20.0], rel=1e-12), order


def test_an_unusable_value_under_the_greatest_key_does_not_hide_a_usable_one():
    day = DAYS[1].isoformat()
    entries = {DAYS[0].isoformat(): 100.0, day: 110.0, day + "T16:00:00": None}
    for order in itertools.permutations(entries):
        win = _window({k: entries[k] for k in order}, DAYS[0], DAYS[1])
        assert win["days"] == 2, order
        assert win["returns_pct"] == pytest.approx([10.0], rel=1e-12), order


def test_a_series_keyed_with_time_parts_is_reviewed_exactly_like_the_same_series_keyed_by_date():
    """The points the window keeps are the points the composite coverage check, the
    pairs and the marginal comparison use. Rejects a second, stricter reading of the
    keys that sees none of them."""

    def roster(key_suffix):
        out = []
        for sid, freq in (("key-a", 0.3), ("key-b", 0.9), ("key-c", 1.7)):
            symphony = b.composer_symphony(
                sid, sid.upper(), WINDOW_DAYS, b.wave_returns(THRESHOLD - 1, freq=freq)
            )
            symphony.composer_values = {
                k + key_suffix: v for k, v in symphony.composer_values.items()
            }
            out.append(symphony)
        return out

    plain = b.review(roster(""), as_of=b.AS_OF, threshold=THRESHOLD)
    stamped = b.review(roster("T00:00:00Z"), as_of=b.AS_OF, threshold=THRESHOLD)
    first = b.entry_of(plain, "key-a")
    assert b.item_of(first, "composite")["status"] == "available"  # fixture sanity
    assert b.item_of(first, "marginal")["status"] == "available"  # fixture sanity
    assert stamped.entries == plain.entries


def test_shadow_rows_resolve_duplicate_dates_by_the_same_rule_whatever_the_mapping_order():
    rows = _shadow_rows()
    winner = {f"{k}T00:00:00Z": v for k, v in rows.items()}
    loser = {k: v + 25.0 for k, v in rows.items()}  # bare dates sort before the stamped keys

    def total_return(mapping):
        entry, _ = _entry(_subject(series_failure="unavailable", shadow_returns=mapping), _peer())
        assert b.item_of(entry, "performance")["window"]["days"] == THRESHOLD
        return b.value_of(entry, "performance", "total_return")

    expected = total_return(rows)
    assert total_return({**winner, **loser}) == pytest.approx(expected, rel=1e-12)
    assert total_return({**loser, **winner}) == pytest.approx(expected, rel=1e-12)


# ---------------------------------------------------------------------------
# Only days the exchange traded are points
# ---------------------------------------------------------------------------

# (a day, whether the exchange traded that day): a weekend day, weekday closures, and the
# half days around Thanksgiving and Christmas, which are sessions and so stay points
CALENDAR_CASES = [
    pytest.param(date(2026, 12, 26), False, id="saturday"),
    pytest.param(date(2026, 12, 27), False, id="sunday"),
    pytest.param(date(2026, 12, 25), False, id="christmas_day"),
    pytest.param(date(2026, 11, 26), False, id="thanksgiving_day"),
    pytest.param(date(2026, 4, 3), False, id="good_friday"),
    pytest.param(date(2026, 11, 27), True, id="half_day_after_thanksgiving"),
    pytest.param(date(2026, 12, 24), True, id="half_day_on_christmas_eve"),
]


@pytest.mark.parametrize(("extra", "traded"), CALENDAR_CASES)
def test_a_point_on_a_day_the_exchange_was_closed_is_not_a_point(extra, traded):
    """A value dated on a weekend or a holiday is dropped: it adds no return and is not a
    day of the window, so the move across the closure runs between the sessions on either
    side of it. A half day is a session and stays.

    Rejects keeping every parseable date, and a calendar that reads a half day as closed."""
    start, end = extra - timedelta(days=8), extra + timedelta(days=8)
    sessions = list(b.nyse_days(start, end))
    assert (extra in sessions) is traded  # fixture sanity: the calendar library agrees
    kept = {d.isoformat(): 100.0 + i for i, d in enumerate(sessions)}
    values = dict(kept)
    if not traded:
        values[extra.isoformat()] = 90000.0  # a level no real move produces
    win = tr.realized_window(values, start, end)
    assert win["dates"] == [d.isoformat() for d in sessions]
    assert win["days"] == len(sessions)
    assert win["returns_pct"] == pytest.approx(b.returns_from_values(kept), rel=1e-12)


def test_a_series_that_opens_and_closes_on_closed_days_starts_and_ends_on_sessions():
    start, end = date(2026, 12, 19), date(2026, 12, 27)  # a Saturday and a Sunday
    sessions = list(b.nyse_days(start, end))
    values = {d.isoformat(): 100.0 + i for i, d in enumerate(sessions)}
    values[start.isoformat()] = 5.0
    values[end.isoformat()] = 5.0
    win = tr.realized_window(values, start, end)
    assert win["first"] == sessions[0].isoformat()
    assert win["last"] == sessions[-1].isoformat()
    assert win["days"] == len(sessions)


def _closed_days_between(days):
    traded = set(days)
    return [d for d in b.calendar_days(days[0], days[-1]) if d not in traded]


def _three_composer_symphonies(*, closed_day_level=None):
    out = []
    for k, (sid, freq) in enumerate((("open-a", 0.3), ("open-b", 0.9), ("open-c", 1.7)), start=1):
        symphony = b.composer_symphony(
            sid, sid.upper(), WINDOW_DAYS, b.wave_returns(THRESHOLD - 1, freq=freq)
        )
        if closed_day_level is not None:
            for d in _closed_days_between(WINDOW_DAYS):
                symphony.composer_values[d.isoformat()] = closed_day_level * k
        out.append(symphony)
    return out


def _three_shadow_symphonies(*, closed_day_return=None):
    out = []
    for sid, freq in (("open-a", 0.3), ("open-b", 0.9), ("open-c", 1.7)):
        rows = b.by_date(WINDOW_DAYS[1:], b.wave_returns(THRESHOLD - 1, freq=freq))
        if closed_day_return is not None:
            rows.update(
                {d.isoformat(): closed_day_return for d in _closed_days_between(WINDOW_DAYS)}
            )
        out.append(b.shadow_symphony(sid, sid.upper(), WINDOW_DAYS[0].isoformat(), rows))
    return out


def test_a_composer_series_with_points_on_closed_days_is_reviewed_like_the_same_series_without():
    """Every consumer of the kept points (the window, the composite coverage check, the
    pairs, the marginal comparison) must see only sessions. Rejects dropping closed days
    in one place and keeping them in another."""
    plain = b.review(_three_composer_symphonies(), as_of=b.AS_OF, threshold=THRESHOLD)
    extra = b.review(
        _three_composer_symphonies(closed_day_level=5000.0), as_of=b.AS_OF, threshold=THRESHOLD
    )
    first = b.entry_of(plain, "open-a")
    assert b.item_of(first, "performance")["window"]["days"] == THRESHOLD  # fixture sanity
    assert b.item_of(first, "composite")["status"] == "available"  # fixture sanity
    assert b.item_of(first, "marginal")["status"] == "available"  # fixture sanity
    assert extra.entries == plain.entries


def test_shadow_rows_on_closed_days_are_reviewed_like_the_same_rows_without():
    """A row dated on a weekend or a holiday adds no return and is not a day, on the
    shadow basis as on the Composer one."""
    plain = b.review(_three_shadow_symphonies(), as_of=b.AS_OF, threshold=THRESHOLD)
    extra = b.review(
        _three_shadow_symphonies(closed_day_return=25.0), as_of=b.AS_OF, threshold=THRESHOLD
    )
    first = b.entry_of(plain, "open-a")
    assert b.item_of(first, "performance")["status"] == "available"  # fixture sanity
    assert extra.entries == plain.entries


# ---------------------------------------------------------------------------
# Basis selection through build_review
# ---------------------------------------------------------------------------


def _peer():
    """An unrelated, eligible, Composer-basis symphony."""
    return b.composer_symphony(
        "peer-comp", "Composer Peer", WINDOW_DAYS, b.wave_returns(THRESHOLD - 1, freq=1.3)
    )


def _subject(**kwargs):
    return b.make_symphony("subject-sym", "Subject Name", WINDOW_DAYS[0].isoformat(), **kwargs)


def _shadow_rows(n=THRESHOLD, **kwargs):
    return b.by_date(WINDOW_DAYS[-n:], b.wave_returns(n, **kwargs))


def _entry(subject, peer=None):
    roster = [subject] + ([peer] if peer is not None else [])
    result = b.review(roster, as_of=b.AS_OF, threshold=THRESHOLD)
    return b.entry_of(result, subject.symphony_id), result


def test_a_usable_composer_series_gives_the_composer_basis_over_the_whole_tenure():
    composer = b.composer_symphony(
        "subject-sym", "Subject Name", WINDOW_DAYS, b.wave_returns(THRESHOLD - 1)
    )
    entry, _ = _entry(composer, _peer())
    basis = entry["basis"]
    assert basis["kind"] == "composer"
    assert basis["label"] == COMPOSER_LABEL
    assert basis["fallback_reason"] is None
    assert basis["tenure_begins"] == WINDOW_DAYS[0].isoformat()
    assert basis["series_begins"] == WINDOW_DAYS[0].isoformat()
    perf = b.item_of(entry, "performance")
    assert perf["status"] == "available"
    assert perf["label"] == COMPOSER_LABEL
    assert perf["window"]["first"] == WINDOW_DAYS[0].isoformat()
    assert perf["window"]["last"] == b.AS_OF.isoformat()
    assert perf["window"]["days"] == THRESHOLD
    assert perf["subject"] and perf["source"]


def test_a_series_that_begins_after_the_tenure_states_both_start_dates():
    late = WINDOW_DAYS[3:]
    composer = b.composer_symphony(
        "subject-sym",
        "Subject Name",
        late,
        b.wave_returns(len(late) - 1),
        invested_since=WINDOW_DAYS[0].isoformat(),
    )
    entry, _ = _entry(composer, _peer())
    assert entry["basis"]["kind"] == "composer"
    assert entry["basis"]["tenure_begins"] == WINDOW_DAYS[0].isoformat()
    assert entry["basis"]["series_begins"] == WINDOW_DAYS[3].isoformat()
    window = b.item_of(entry, "performance")["window"]
    assert window["first"] == WINDOW_DAYS[3].isoformat()
    assert window["days"] == THRESHOLD - 3


FAILURE_REASONS = [
    ("refused", "Composer refused the request"),
    # Composer answers 404 only for an unknown account, so the text states what was
    # answered and never claims the symphony is missing
    ("not_found", "Composer returned not found"),
    ("rate_limited", "Composer rate limit"),
    ("unavailable", "Composer unavailable"),
    ("empty", "Composer series empty"),
    ("unreadable", "Composer response unreadable"),
    ("rejected", "Composer rejected the request"),
]
UNRECOGNIZED_FAILURE_REASON = "Composer read failed"
SHORT_SERIES_REASON = "Composer series has fewer than 2 usable points in the tenure"


@pytest.mark.parametrize(
    ("failure", "reason"), FAILURE_REASONS, ids=[f for f, _ in FAILURE_REASONS]
)
def test_every_series_failure_class_falls_back_to_shadow_history_with_its_reason(failure, reason):
    """Every figure of a fallback symphony is labelled with the shadow basis and none
    carries the Composer label. Rejects reusing one reason for several classes and a
    Composer label on shadow-derived figures."""
    subject = _subject(series_failure=failure, shadow_returns=_shadow_rows())
    entry, _ = _entry(subject, _peer())
    basis = entry["basis"]
    assert basis["kind"] == "shadow"
    assert basis["label"] == SHADOW_LABEL
    assert basis["fallback_reason"] == reason
    assert b.item_of(entry, "performance")["label"] == SHADOW_LABEL
    for name in b.EVIDENCE_ITEMS:
        assert b.item_of(entry, name)["label"] != COMPOSER_LABEL, name


def _fallback_reason(**kwargs):
    entry, _ = _entry(_subject(shadow_returns=_shadow_rows(), **kwargs), _peer())
    assert entry["basis"]["kind"] == "shadow", "fixture sanity: the subject must fall back"
    return entry["basis"]["fallback_reason"]


@pytest.mark.parametrize("values", [{}, None], ids=["empty_mapping", "no_mapping"])
def test_an_empty_composer_series_without_a_failure_class_also_falls_back(values):
    assert _fallback_reason(composer_values=values) == "Composer series empty"


def test_an_unrecognized_failure_class_reads_a_generic_reason_never_another_classs_text():
    """A class the review does not know is reported as a failed read. Rejects falling
    back to the empty-series text (a fact nobody observed) or to any other class's text."""
    reason = _fallback_reason(series_failure="no-such-failure-class")
    assert reason == UNRECOGNIZED_FAILURE_REASON
    assert reason not in {text for _failure, text in FAILURE_REASONS}


def test_a_failure_class_decides_the_reason_even_when_a_usable_series_is_also_present():
    """Rejects trusting a series that arrived beside a failure class."""
    full = b.values_from_returns(list(WINDOW_DAYS), b.wave_returns(THRESHOLD - 1))
    reason = _fallback_reason(composer_values=full, series_failure="unavailable")
    assert reason == "Composer unavailable"


def test_a_composer_series_with_a_single_usable_point_falls_back_to_shadow_history():
    one_point = {WINDOW_DAYS[-1].isoformat(): 100.0}
    assert _fallback_reason(composer_values=one_point) == SHORT_SERIES_REASON


def test_a_composer_series_with_points_only_before_the_tenure_says_it_is_too_short_in_the_tenure():
    """Twenty usable points that all precede the invested-since date (a redeployed
    symphony). Composer did return a series, so 'empty' would be false."""
    before = b.last_n_nyse_days(date(2026, 6, 30), 21)
    old = b.values_from_returns(list(before), b.wave_returns(20))
    assert _fallback_reason(composer_values=old) == SHORT_SERIES_REASON


def test_a_composer_series_whose_values_are_all_unusable_is_not_an_empty_series():
    """The mapping has dates, none of them with a usable value."""
    unusable = {d.isoformat(): None for d in WINDOW_DAYS}
    assert _fallback_reason(composer_values=unusable) == SHORT_SERIES_REASON


def test_a_point_on_a_closed_day_does_not_count_toward_the_two_points_the_basis_needs():
    """One session and one weekend day are one usable point, so the series is too short."""
    closed = _closed_days_between(WINDOW_DAYS)[0]
    values = {WINDOW_DAYS[-1].isoformat(): 100.0, closed.isoformat(): 101.0}
    assert _fallback_reason(composer_values=values) == SHORT_SERIES_REASON


def test_two_usable_composer_points_are_enough_for_the_composer_basis():
    two_points = {WINDOW_DAYS[-2].isoformat(): 100.0, WINDOW_DAYS[-1].isoformat(): 101.0}
    subject = _subject(composer_values=two_points, shadow_returns=_shadow_rows())
    entry, _ = _entry(subject, _peer())
    assert entry["basis"]["kind"] == "composer"


def test_shadow_returns_are_percent_returns_used_as_given():
    rows = _shadow_rows(THRESHOLD - 1, freq=0.9)  # the first tenure day is the baseline
    subject = _subject(series_failure="unavailable", shadow_returns=rows)
    entry, _ = _entry(subject, _peer())
    expected_total = (math.prod(1 + v / 100 for v in rows.values()) - 1) * 100
    figure = b.figure_of(entry, "performance", "total_return")
    assert figure["unit"] == "percent"
    assert figure["value"] == pytest.approx(expected_total, rel=1e-9)  # float summation order
    assert b.item_of(entry, "performance")["window"]["days"] == THRESHOLD


def test_shadow_rows_after_the_as_of_day_and_unusable_rows_are_not_counted():
    rows = _shadow_rows()
    rows["2027-01-04"] = 5.0  # the next session: not closed when the run started
    rows[WINDOW_DAYS[2].isoformat()] = None
    rows[WINDOW_DAYS[3].isoformat()] = float("nan")
    subject = _subject(series_failure="unavailable", shadow_returns=rows)
    entry, _ = _entry(subject, _peer())
    window = b.item_of(entry, "performance")["window"]
    assert window["days"] == THRESHOLD - 2
    assert window["last"] == b.AS_OF.isoformat()


def test_a_symphony_with_no_history_at_all_reads_unavailable_no_history_everywhere():
    """No figure may be shown as 0, and the verdict is withheld."""
    subject = _subject(series_failure="not_found", shadow_returns=None)
    entry, _ = _entry(subject, _peer())
    assert entry["basis"]["kind"] == "none"
    for name in b.EVIDENCE_ITEMS:
        item = b.item_of(entry, name)
        assert item["status"] == "unavailable", name
        assert item["reason"] == "no history", name
        for fig in b.iter_figures(item):
            assert fig["value"] is None, (name, fig)
    assert entry["verdict"] is None
    assert entry["verdict_text"] == "no verdict: no evidence available"


def test_a_composer_symphony_and_a_shadow_symphony_are_never_combined_in_a_pair():
    """No pair is made of the two, and each row names the other as a peer it did not compare
    because the bases differ. Neither member can be flagged for retirement."""
    shadow_sym = _subject(series_failure="unavailable", shadow_returns=_shadow_rows())
    result = b.review([shadow_sym, _peer()], as_of=b.AS_OF, threshold=THRESHOLD)
    for sid, other in (("subject-sym", "peer-comp"), ("peer-comp", "subject-sym")):
        item = b.item_of(b.entry_of(result, sid), "redundancy")
        assert item["status"] == "unavailable", sid
        assert item["pair"] is None, sid
        assert item["retire_candidate"] is False, sid
        # the peer on the other basis is named as one that was not compared
        assert [r["display_name"] for r in item["not_compared"]] == [
            b.entry_of(result, other)["display_name"]
        ], sid
        assert {r["reason"] for r in item["not_compared"]} == {"bases differ"}, sid
        assert b.entry_of(result, sid)["verdict"] != "retire_candidate", sid


def test_a_shadow_series_never_reaches_back_before_the_tenure_starts():
    """A shadow series never reaches back before the symphony's own tenure."""
    rows = b.by_date(DAYS, b.wave_returns(len(DAYS)))  # 25 rows, 5 before the tenure
    subject = _subject(series_failure="unavailable", shadow_returns=rows)
    entry, _ = _entry(subject, _peer())
    window = b.item_of(entry, "performance")["window"]
    assert date.fromisoformat(window["first"]) >= WINDOW_DAYS[0]
    assert window["days"] == THRESHOLD


# ---------------------------------------------------------------------------
# A shadow window starts at the session before its first row
# ---------------------------------------------------------------------------

# A shadow row is the return INTO its day, measured from the previous session's close, so
# the window opens on that previous session (the baseline), the way a Composer window
# opens on its first value. N rows then span N + 1 days and N returns. When the previous
# session falls before the tenure there is no baseline inside it: the first row is the
# baseline, its own return is not counted, and N rows span N days and one return fewer than N.
SHADOW_START_THRESHOLD = 5


def _sessions_from(first: date) -> list[date]:
    return list(b.nyse_days(first, b.AS_OF))


def _compounded_percent(returns_pct) -> float:
    return (math.prod(1 + r / 100 for r in returns_pct) - 1) * 100


def _shadow_performance(rows, invested_since: date):
    subject = b.shadow_symphony("shadow-sub", "Shadow Sub", invested_since.isoformat(), rows)
    result = b.review([subject], as_of=b.AS_OF, threshold=SHADOW_START_THRESHOLD)
    entry = b.entry_of(result, "shadow-sub")
    return subject, b.item_of(entry, "performance"), entry["basis"]


@pytest.mark.parametrize(
    "first_row",
    [date(2026, 12, 22), date(2026, 12, 21), date(2026, 12, 28)],
    ids=["after_a_weekday", "after_a_weekend", "after_a_holiday_and_a_weekend"],
)
def test_a_shadow_window_starts_at_the_session_before_its_first_stored_row(first_row):
    """The session before is the calendar's, not the day before: Friday for a Monday
    row, Christmas Eve for the row after the Christmas closure. Every row's return is
    counted. Rejects a window that opens on the first row, which leaves that row's
    return with no baseline in the window, and a baseline taken from calendar days."""
    sessions = _sessions_from(first_row)
    returns = b.wave_returns(len(sessions), amp=2.0)
    subject, perf, basis = _shadow_performance(b.by_date(sessions, returns), date(2026, 12, 14))
    assert perf["status"] == "available"
    assert perf["window"] == {
        "first": b.session_before(first_row).isoformat(),
        "last": b.AS_OF.isoformat(),
        "days": len(sessions) + 1,
        "sessions": len(sessions) + 1,  # a row on every session, and the baseline before them
    }
    assert perf["figures"]["total_return"]["value"] == pytest.approx(
        _compounded_percent(returns), rel=1e-9
    )
    assert tr.qualifying_window(subject, b.AS_OF, SHADOW_START_THRESHOLD) == perf["window"]
    # the data starts on the first stored row; the baseline session before it has no row
    assert basis["series_begins"] == first_row.isoformat()
    assert basis["tenure_begins"] == date(2026, 12, 14).isoformat()


def test_the_window_opens_before_the_first_usable_row_when_the_first_stored_row_is_unusable():
    """The return of the first usable row is measured from the session before it, whether
    or not that session has a stored row, and the data starts on the first usable row.
    Rejects opening the window before the first stored key, which leaves a session with no
    return inside the window, and a series that begins on a row with no usable return."""
    sessions = _sessions_from(date(2026, 12, 21))
    returns = b.wave_returns(len(sessions), amp=2.0)
    rows = b.by_date(sessions, returns)
    rows[sessions[0].isoformat()] = None
    _subject_input, perf, basis = _shadow_performance(rows, date(2026, 12, 14))
    assert perf["window"]["first"] == sessions[0].isoformat()
    assert basis["series_begins"] == sessions[1].isoformat()
    assert perf["window"]["days"] == len(sessions)  # the usable rows, plus the baseline
    assert perf["figures"]["total_return"]["value"] == pytest.approx(
        _compounded_percent(returns[1:]), rel=1e-9
    )


@pytest.mark.parametrize(
    ("invested_since", "rows_from", "baseline_in_tenure"),
    [
        pytest.param(date(2026, 12, 22), date(2026, 12, 22), False, id="first_row_on_day_one"),
        pytest.param(date(2026, 12, 22), date(2026, 12, 18), False, id="earlier_rows_are_dropped"),
        pytest.param(date(2026, 12, 21), date(2026, 12, 22), True, id="baseline_on_day_one"),
        pytest.param(date(2026, 12, 18), date(2026, 12, 22), True, id="baseline_inside_tenure"),
    ],
)
def test_a_shadow_window_never_reaches_back_before_the_tenure_and_keeps_a_baseline_it_can(
    invested_since, rows_from, baseline_in_tenure
):
    """With the previous session inside the tenure (the first tenure day counts) it is
    the window's first day and every row's return is counted. Without one, the first row
    is the baseline and its return, which would be measured from before the tenure, is
    left out: the window never reaches back before the tenure and never counts a return
    its first day cannot account for."""
    sessions = _sessions_from(rows_from)
    returns = b.wave_returns(len(sessions), amp=2.0)
    kept = [i for i, d in enumerate(sessions) if d >= invested_since]
    returns[kept[0]] = 3.7  # a first kept return no later return can stand in for
    _subject_input, perf, basis = _shadow_performance(b.by_date(sessions, returns), invested_since)
    kept_days = [sessions[i] for i in kept]
    kept_returns = [returns[i] for i in kept]
    if baseline_in_tenure:
        assert perf["window"]["first"] == b.session_before(kept_days[0]).isoformat()
        assert perf["window"]["days"] == len(kept_days) + 1
        counted = kept_returns
    else:
        assert perf["window"]["first"] == kept_days[0].isoformat()
        assert perf["window"]["days"] == len(kept_days)
        counted = kept_returns[1:]
    assert date.fromisoformat(perf["window"]["first"]) >= invested_since
    # the first shadow return: the first row's own return when a baseline session precedes it
    # inside the tenure, otherwise the next row's, because the first row is its own baseline
    first_counted = kept_days[0] if baseline_in_tenure else kept_days[1]
    assert basis["series_begins"] == first_counted.isoformat()
    assert basis["tenure_begins"] == invested_since.isoformat()
    assert perf["figures"]["total_return"]["value"] == pytest.approx(
        _compounded_percent(counted), rel=1e-9
    )


LONG_FIRST_ROW = date(2026, 9, 8)  # the session after Labor Day, so a long pair of series


@pytest.mark.parametrize(
    ("invested_since", "baseline_in_tenure"),
    [(date(2026, 9, 8), False), (date(2026, 9, 1), True)],
    ids=["first_row_on_day_one", "baseline_inside_tenure"],
)
def test_a_shadow_pair_is_measured_on_the_returns_the_window_counts(
    invested_since, baseline_in_tenure
):
    """The aligned days of a pair are the returns each window counts: one fewer than the
    window's days. Rejects dropping the first row from the figures but keeping it in the
    pair, and the reverse. The pair is long enough to be evaluated, so it is shown
    whatever the display rule for short pairs says."""
    sessions = _sessions_from(LONG_FIRST_ROW)
    first = b.wave_returns(len(sessions), amp=1.0)
    second = [
        0.8 * r + 0.3 * x for r, x in zip(first, b.sine_returns(len(sessions), 7), strict=True)
    ]
    subjects = [
        b.shadow_symphony(sid, sid.title(), invested_since.isoformat(), b.by_date(sessions, rows))
        for sid, rows in (("pair-a", first), ("pair-b", second))
    ]
    result = b.review(subjects, as_of=b.AS_OF, threshold=SHADOW_START_THRESHOLD)
    entry = b.entry_of(result, "pair-a")
    pair = b.item_of(entry, "redundancy")["pair"]
    window_days = b.item_of(entry, "performance")["window"]["days"]
    assert pair is not None  # fixture sanity: the pair reaches the screen
    assert window_days == len(sessions) + (1 if baseline_in_tenure else 0)
    assert pair["n_obs"] == window_days - 1


# ---------------------------------------------------------------------------
# A shadow symphony whose only row is its own baseline has no first return
# ---------------------------------------------------------------------------


def test_a_shadow_symphony_with_a_single_row_on_its_first_tenure_day_has_no_first_return_date():
    """That row is the baseline and its return is not counted, so no return exists to date.
    The review answers instead of failing, the basis states no first-return date (never the
    baseline's own date) and the performance item is unavailable."""
    day = b.last_n_nyse_days(b.AS_OF, 2)[-1]
    subject = b.shadow_symphony("one-row", "One Row", day.isoformat(), {day.isoformat(): 1.5})
    entry = b.entry_of(b.review([subject], as_of=b.AS_OF, threshold=1), "one-row")
    assert entry["basis"]["kind"] == "shadow"
    assert entry["basis"]["series_begins"] is None
    assert entry["basis"]["tenure_begins"] == day.isoformat()
    assert b.item_of(entry, "performance")["status"] == "unavailable"


# ---------------------------------------------------------------------------
# Every window states the days used and the sessions its span holds
# ---------------------------------------------------------------------------

# A fixed number of interior sessions the lead has no value on, so the days its window uses
# are fewer than the sessions its span holds.
SESSIONS_WITHOUT_A_VALUE = 2
SPAN_RETURNS = 60


def _roster_with_every_item_and_two_sessions_without_a_value():
    """Two correlated Composer symphonies. The lead has no value on two interior sessions; a
    backtest over its window and holdings on every session give all six items a window."""
    roster, threshold = b.composite_pair(SPAN_RETURNS, 0.9)
    lead, peer = roster
    sessions = sorted(lead.composer_values)
    for key in sessions[10 : 10 + SESSIONS_WITHOUT_A_VALUE]:
        lead.composer_values[key] = None
    window = tr.qualifying_window(lead, b.AS_OF, threshold)
    first, last = date.fromisoformat(window["first"]), date.fromisoformat(window["last"])
    lead.backtest = b.replica_backtest(
        first, last, b.wave_returns(len(b.nyse_days(first, last)) - 1, amp=0.2)
    )
    held = {
        "lead": {"VIXY": 10.0, "SPY": 90.0},
        "peer": {"SPY": 100.0},
    }
    holdings = tr.HoldingsInput(days={key: held for key in sessions})
    return roster, threshold, holdings


def test_every_window_of_every_item_states_the_sessions_its_span_holds():
    """The sessions come from the calendar library, for the same first and last dates, and
    are never fewer than the days used. With no value missing, days used and sessions are the
    same number; with two sessions missing the days are two fewer. Rejects a window that
    states only the days it used (which reads as the window's length) and sessions counted
    from the days used."""
    roster, threshold, holdings = _roster_with_every_item_and_two_sessions_without_a_value()
    result = b.review(roster, as_of=b.AS_OF, threshold=threshold, holdings=holdings)
    for sid in ("lead", "peer"):
        entry = b.entry_of(result, sid)
        for name in b.EVIDENCE_ITEMS:
            item = b.item_of(entry, name)
            window = item["window"]
            assert window is not None, (sid, name)  # fixture sanity: every item has a window
            first, last = date.fromisoformat(window["first"]), date.fromisoformat(window["last"])
            assert window.get("sessions") == len(b.nyse_days(first, last)), (sid, name)
            assert 0 < window["days"] <= window.get("sessions", 0), (sid, name)
    peer_window = b.item_of(b.entry_of(result, "peer"), "performance")["window"]
    assert peer_window["days"] == peer_window.get("sessions")
    lead_window = b.item_of(b.entry_of(result, "lead"), "performance")["window"]
    assert lead_window["days"] == lead_window.get("sessions", 0) - SESSIONS_WITHOUT_A_VALUE
    run_window = result.composite_window
    assert run_window.get("sessions") == len(
        b.nyse_days(date.fromisoformat(run_window["first"]), date.fromisoformat(run_window["last"]))
    )
    assert run_window["days"] == run_window.get("sessions")


def test_the_stored_record_carries_the_sessions_of_every_window():
    roster, threshold, holdings = _roster_with_every_item_and_two_sessions_without_a_value()
    result = b.review(roster, as_of=b.AS_OF, threshold=threshold, holdings=holdings)
    record = tr.run_record(result, b.et(2026, 12, 31, 17, 5))
    assert record["composite_window"].get("sessions") == result.composite_window.get("sessions")
    for entry in record["entries"]:
        for name in b.EVIDENCE_ITEMS:
            assert "sessions" in entry["evidence"][name]["window"], (entry["symphony_id"], name)
