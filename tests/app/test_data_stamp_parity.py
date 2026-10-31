"""The two Eastern-stamp formatters give one text for one instant.

``app._format_data_stamp`` stamps the data ages the History and Performance routes serve;
``analytics.format_et_stamp`` stamps stored advisor instants. The operator reads both on one
screen, so they must never disagree about the same moment. Both already exist on this base, so these
cases pin the agreement rather than drive a change: they pass before the History work starts, and a
later edit to either formatter fails them.

Instants are built with ``ZoneInfo`` and compared formatter against formatter; no stamp text is typed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

import analytics
import app as app_module

ET = ZoneInfo("America/New_York")

INSTANTS = {
    "aware UTC": datetime(2026, 10, 22, 14, 3, 59, 999000, tzinfo=UTC),
    "aware Eastern": datetime(2026, 10, 22, 10, 3, 59, tzinfo=ET),
    "minute boundary": datetime(2026, 10, 22, 14, 4, 0, tzinfo=UTC),
    "last second of a minute": datetime(2026, 10, 22, 14, 3, 59, tzinfo=UTC),
    "before the fall-back hour": datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
    "after the fall-back hour": datetime(2026, 11, 1, 6, 30, tzinfo=UTC),
    "before the spring-forward hour": datetime(2026, 3, 8, 6, 59, tzinfo=UTC),
    "after the spring-forward hour": datetime(2026, 3, 8, 7, 0, tzinfo=UTC),
    "UTC date ahead of the Eastern date": datetime(2026, 10, 23, 0, 30, tzinfo=UTC),
}


@pytest.mark.parametrize("name", list(INSTANTS))
def test_same_instant_same_text(name):
    moment = INSTANTS[name]
    assert app_module._format_data_stamp(moment) == analytics.format_et_stamp(moment.isoformat())


def test_the_dst_pair_reads_the_zone_name_et_both_sides():
    summer = app_module._format_data_stamp(datetime(2026, 7, 1, 16, 0, tzinfo=UTC))
    winter = app_module._format_data_stamp(datetime(2026, 12, 1, 16, 0, tzinfo=UTC))
    assert summer.endswith(" ET") and winter.endswith(" ET")
    assert summer.split()[1] == "12:00" and winter.split()[1] == "11:00"
