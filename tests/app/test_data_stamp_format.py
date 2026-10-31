"""`app._format_data_stamp`: the one writer of a server freshness stamp.

Every `/api/state` stamp (the as-of, the frozen-at, the account-basis chip) is a text the
operator reads to judge how old the numbers are. The helper turns an instant into that text
and nothing else: it never reads the clock, never guesses a zone and never raises, so a
stamp can only ever name the moment of the data it was given.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import app as app_module

ET = ZoneInfo("America/New_York")


def _stamp(*args, **kwargs):
    assert hasattr(app_module, "_format_data_stamp"), "app._format_data_stamp is missing"
    return app_module._format_data_stamp(*args, **kwargs)


class TestAwareInstants:
    def test_an_eastern_instant_reads_date_and_minute(self):
        moment = datetime(2026, 10, 21, 16, 0, 42, tzinfo=ET)
        assert _stamp(moment) == "2026-10-21 16:00 ET"

    def test_seconds_are_kept_when_asked(self):
        moment = datetime(2026, 10, 21, 16, 0, 42, tzinfo=ET)
        assert _stamp(moment, seconds=True) == "2026-10-21 16:00:42 ET"

    def test_minutes_and_seconds_are_truncated_never_rounded(self):
        moment = datetime(2026, 10, 21, 16, 0, 59, 999999, tzinfo=ET)
        assert _stamp(moment) == "2026-10-21 16:00 ET"
        assert _stamp(moment, seconds=True) == "2026-10-21 16:00:59 ET"

    def test_a_utc_instant_is_shown_in_eastern_time(self):
        moment = datetime(2026, 10, 21, 20, 4, 4, tzinfo=UTC)
        assert _stamp(moment) == "2026-10-21 16:04 ET"

    def test_the_date_follows_the_eastern_clock_not_the_source_zone(self):
        # 03:30 UTC on the 22nd is 23:30 on the 21st in New York.
        moment = datetime(2026, 10, 22, 3, 30, tzinfo=UTC)
        assert _stamp(moment) == "2026-10-21 23:30 ET"

    def test_a_fixed_offset_instant_is_converted(self):
        moment = datetime(2026, 10, 21, 22, 0, tzinfo=timezone(timedelta(hours=2)))
        assert _stamp(moment) == "2026-10-21 16:00 ET"

    @pytest.mark.parametrize(
        ("utc_instant", "expected"),
        [
            # Either side of the autumn clock change (2026-11-01): 16:00 daylight, 16:00 standard.
            (datetime(2026, 10, 31, 20, 0, tzinfo=UTC), "2026-10-31 16:00 ET"),
            (datetime(2026, 11, 2, 21, 0, tzinfo=UTC), "2026-11-02 16:00 ET"),
            # The repeated hour: 01:30 daylight, then 01:30 standard.
            (datetime(2026, 11, 1, 5, 30, tzinfo=UTC), "2026-11-01 01:30 ET"),
            (datetime(2026, 11, 1, 6, 30, tzinfo=UTC), "2026-11-01 01:30 ET"),
            # Spring change (2026-03-08): 03:00 daylight follows 01:59 standard.
            (datetime(2026, 3, 8, 6, 59, tzinfo=UTC), "2026-03-08 01:59 ET"),
            (datetime(2026, 3, 8, 7, 0, tzinfo=UTC), "2026-03-08 03:00 ET"),
        ],
    )
    def test_clock_changes_do_not_shift_the_shown_time(self, utc_instant, expected):
        assert _stamp(utc_instant) == expected

    def test_previous_session_appends_the_marker(self):
        moment = datetime(2026, 10, 20, 16, 0, 42, tzinfo=ET)
        assert _stamp(moment, previous_session=True) == "2026-10-20 16:00 ET · previous session"

    def test_the_marker_follows_the_seconds_form_too(self):
        moment = datetime(2026, 10, 20, 16, 0, 42, tzinfo=ET)
        assert (
            _stamp(moment, seconds=True, previous_session=True)
            == "2026-10-20 16:00:42 ET · previous session"
        )

    def test_without_the_flag_there_is_no_marker(self):
        moment = datetime(2026, 10, 20, 16, 0, 42, tzinfo=ET)
        assert "previous" not in _stamp(moment)


class TestTimeOfDayWithNoDate:
    def test_a_bare_time_says_the_date_was_not_recorded(self):
        assert _stamp(time(16, 0, 42)) == "16:00 ET (date not recorded)"

    def test_a_bare_time_is_padded_and_never_given_a_date(self):
        assert _stamp(time(9, 5)) == "09:05 ET (date not recorded)"


class TestNothingToShow:
    @pytest.mark.parametrize(
        "value",
        [
            None,
            datetime(2026, 10, 21, 16, 0, 42),  # naive: its zone is a guess
            "2026-10-21T16:00:42-04:00",  # text is not an instant
            1_761_076_842,  # a number is not an instant
            True,
            object(),
        ],
        ids=["none", "naive datetime", "iso text", "epoch number", "bool", "object"],
    )
    def test_anything_else_reads_two_dashes(self, value):
        assert _stamp(value) == "--"

    def test_the_marker_does_not_decorate_a_missing_moment(self):
        assert _stamp(None, previous_session=True) == "--"
        assert _stamp(datetime(2026, 10, 21, 16, 0), previous_session=True) == "--"

    def test_an_instant_that_cannot_be_shown_in_eastern_time_reads_two_dashes(self):
        # Year 1 in UTC is before the first representable Eastern instant: converting raises.
        assert _stamp(datetime.min.replace(tzinfo=UTC)) == "--"

    def test_the_latest_representable_instant_still_gives_text(self):
        assert isinstance(_stamp(datetime.max.replace(tzinfo=UTC)), str)


class TestNeverReadsTheClock:
    @pytest.fixture
    def far_clock(self, monkeypatch):
        class _Far(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2031, 7, 4, 3, 21, 9, tzinfo=tz or UTC)

            @classmethod
            def utcnow(cls):
                return datetime(2031, 7, 4, 3, 21, 9)

        monkeypatch.setattr(app_module, "datetime", _Far)
        return _Far

    def test_the_text_names_only_the_given_moment(self, far_clock):
        moment = datetime(2026, 10, 21, 10, 43, 5, tzinfo=ET)
        text = _stamp(moment)
        assert text == "2026-10-21 10:43 ET"
        assert "2031" not in text and "03:21" not in text

    @pytest.mark.parametrize("value", [None, datetime(2026, 10, 21, 10, 43), "x"])
    def test_a_missing_moment_is_never_replaced_by_the_request_time(self, far_clock, value):
        text = _stamp(value)
        assert text == "--"

    def test_a_bare_time_is_not_dated_from_the_clock(self, far_clock):
        text = _stamp(time(10, 43))
        assert "2031" not in text and "07-04" not in text
