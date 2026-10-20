"""Realized windows, basis selection and the labels on every figure.

Module under test: advisors.tenure_review.

Returns are percent-scale. A value series is a deposit-adjusted level keyed by
ISO date; a return is the move between consecutive kept points inside the window,
so M kept points give M - 1 returns. Dropped days are never counted as 0%.
"""

from __future__ import annotations

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
    ("not_found", "Composer has no series for this symphony"),
    ("rate_limited", "Composer rate limit"),
    ("unavailable", "Composer unavailable"),
    ("empty", "Composer series empty"),
    ("unreadable", "Composer response unreadable"),
]


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


def test_an_empty_composer_series_without_a_failure_class_also_falls_back():
    subject = _subject(composer_values={}, shadow_returns=_shadow_rows())
    entry, _ = _entry(subject, _peer())
    assert entry["basis"]["kind"] == "shadow"
    assert entry["basis"]["fallback_reason"] == "Composer series empty"


def test_a_composer_series_with_a_single_usable_point_falls_back_to_shadow_history():
    one_point = {WINDOW_DAYS[-1].isoformat(): 100.0}
    subject = _subject(composer_values=one_point, shadow_returns=_shadow_rows())
    entry, _ = _entry(subject, _peer())
    assert entry["basis"]["kind"] == "shadow"


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


def test_earliest_usable_first_date_is_not_older_than_the_tenure_start():
    """A shadow series never reaches back before the symphony's own tenure."""
    rows = b.by_date(DAYS, b.wave_returns(len(DAYS)))  # 25 rows, 5 before the tenure
    subject = _subject(series_failure="unavailable", shadow_returns=rows)
    entry, _ = _entry(subject, _peer())
    window = b.item_of(entry, "performance")["window"]
    assert date.fromisoformat(window["first"]) >= WINDOW_DAYS[0]
    assert window["days"] == THRESHOLD
