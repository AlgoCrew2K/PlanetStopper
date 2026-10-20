"""Realized windows, basis selection and the labels on every figure.

Module under test: advisors.tenure_review.

Returns are percent-scale. A value series is a deposit-adjusted level keyed by
ISO date; a return is the move between consecutive kept points inside the window,
so M kept points give M minus 1 returns. Dropped days are never counted as 0%.
"""

from __future__ import annotations

import itertools
import math
from datetime import date

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


def test_two_usable_composer_points_are_enough_for_the_composer_basis():
    two_points = {WINDOW_DAYS[-2].isoformat(): 100.0, WINDOW_DAYS[-1].isoformat(): 101.0}
    subject = _subject(composer_values=two_points, shadow_returns=_shadow_rows())
    entry, _ = _entry(subject, _peer())
    assert entry["basis"]["kind"] == "composer"


def test_shadow_returns_are_percent_returns_used_as_given():
    rows = _shadow_rows(freq=0.9)
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
    """Both rows read bases differ, no correlation is computed, and neither member can
    be flagged for retirement."""
    shadow_sym = _subject(series_failure="unavailable", shadow_returns=_shadow_rows())
    result = b.review([shadow_sym, _peer()], as_of=b.AS_OF, threshold=THRESHOLD)
    for sid in ("subject-sym", "peer-comp"):
        item = b.item_of(b.entry_of(result, sid), "redundancy")
        assert item["status"] == "unavailable", sid
        assert item["reason"] == "bases differ", sid
        assert item["pair"] is None, sid
        assert item["retire_candidate"] is False, sid
        assert b.entry_of(result, sid)["verdict"] != "retire_candidate", sid


def test_a_shadow_series_never_reaches_back_before_the_tenure_starts():
    """A shadow series never reaches back before the symphony's own tenure."""
    rows = b.by_date(DAYS, b.wave_returns(len(DAYS)))  # 25 rows, 5 before the tenure
    subject = _subject(series_failure="unavailable", shadow_returns=rows)
    entry, _ = _entry(subject, _peer())
    window = b.item_of(entry, "performance")["window"]
    assert date.fromisoformat(window["first"]) >= WINDOW_DAYS[0]
    assert window["days"] == THRESHOLD
