"""While the market is closed the sleeve rule row names the session its fire count belongs to.

The row prints `today 3 · lifetime 9 · realized $0.00`. While the market is closed the count is the
closed session's (the anchor of `meta.portfolio.window_anchor`), so on a Saturday page the word
"today" would state a false window for Friday's fires. The ruling: while closed the row names the
session's date in the wording of the History label ("Oct 30": the month abbreviation and the day
without a zero pad), so the row reads `Oct 30 3 · lifetime 9 · realized $0.00`. While the market is
open it stays `today 3`.

The row is read from the page `GET /` renders: the text between the rule's mode and the word
"lifetime", whitespace collapsed (the markup that carries the label is not the contract; the text
is). The count and the session day are never typed in: the day is the anchor the route's own poll
names, the count is derived from the seeded fires by the Eastern day of each, and the label is built
here from the anchor's ISO digits. Fixture provenance: the world is `_closed_session_world`
(derived from captured droplet rows), the sleeves, rules and fires are synthetic, written through
the production writers; the clock is the world's virtual host clock on the real XNYS calendar.

The poll side of the same ruling is `tests/dashboard/test_sleeve_rule_fires_label_follows_session.py`.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime

import pytest

import database
from tests.app._closed_session_world import ClosedWorld, build_history_world, utc_of
from tests.app.test_closed_market_numbers_never_move import Session, local, write_engine_cycle
from tests.app.test_health_no_cycle_since_open import world
from tests.app.test_history_exits_hold_while_closed import (
    FRIDAY,
    INSTANTS,
    MONDAY,
    SLEEVE_FIRES,
    UTC,
    anchor_of,
    et_day_of,
    fires_by,
    seed_sleeve_with_fires,
)

MONTH_ABBREVIATIONS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
CLOSED_WORDING = "the closed session's date, month abbreviation then day without a zero pad"

# A session whose day has one digit: a zero-padded label ("Nov 03") reads wrong here and nowhere in
# the Friday world.
TUESDAY_NOV_3 = Session(
    name="tuesday session",
    session_day="2026-11-03",
    previous_day="2026-11-02",
    baseline=local("2026-11-03", "16:02:00"),
    steps=(),
)
TUESDAY_FIRES = (
    "2026-11-02 15:00:00",  # Monday 10:00 ET
    "2026-11-03 14:30:00",  # Tuesday 09:30 ET
    "2026-11-03 19:45:00",  # Tuesday 14:45 ET
    "2026-11-04 11:00:00",  # Wednesday 06:00 ET, before that morning's open
)
TUESDAY_INSTANTS = {
    "Tue 16:30 ET": ("2026-11-03", "16:30:00"),
    "Wed 08:00 ET": ("2026-11-04", "08:00:00"),
}


def session_label(anchor: str) -> str:
    year, month, day = (int(part) for part in anchor.split("-"))
    assert date(year, month, day).isoformat() == anchor
    return f"{MONTH_ABBREVIATIONS[month - 1]} {day}"


def rule_rows(page: str) -> list[str]:
    """What each rule row of the rendered page prints between its mode and the word "lifetime",
    whitespace collapsed ("today 3", "Oct 30 3"). The page's inert `<template>` row is skipped."""
    rows = []
    for segment in page.split('data-testid="sleeve-rule-row"')[1:]:
        if not re.match(r'\s+data-rule-id="\d+"', segment):
            continue
        text = html.unescape(re.sub(r"<[^>]+>", " ", segment.split("lifetime", 1)[0]))
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"^.*?\((?:SHADOW|PAPER|LIVE)\)\s*", "", text)
        rows.append(re.sub(r"\s*·$", "", text))
    return rows


def seed_second_rule_without_fires() -> None:
    """A second sleeve with its own rule that never fired: every row names the day, not the first."""
    sleeve = database.create_sleeve("Growth sleeve", 2500.0, envelope_json="{}")
    database.create_sleeve_rule(sleeve, "Rotate leaders", json_doc="{}")


def fires_on(fired: list[str], day: str) -> int:
    return sum(1 for fired_at in fired if et_day_of(fired_at) == day)


def printed_rows(cw: ClosedWorld) -> list[str]:
    return rule_rows(cw.world.client.get("/").get_data(as_text=True))


@pytest.mark.parametrize("instant", sorted(INSTANTS))
def test_the_closed_page_names_the_session_its_rule_fire_count_belongs_to(
    monkeypatch, tmp_path, instant
):
    cw = world(monkeypatch, tmp_path)
    fired = fires_by(*INSTANTS[instant])
    seed_sleeve_with_fires(fired)
    seed_second_rule_without_fires()
    cw.at(*INSTANTS[instant])
    anchor = anchor_of(cw)
    count = fires_on(fired, anchor)
    assert count >= 2, "fixture sanity: the session has fires, and other days have their own"

    assert printed_rows(cw) == [f"{session_label(anchor)} {count}", f"{session_label(anchor)} 0"], (
        f"{instant}: while closed each rule row must read {CLOSED_WORDING} and its count of "
        f"session {anchor}, never the word 'today'"
    )


def test_a_closed_session_with_no_fires_still_names_its_date(monkeypatch, tmp_path):
    cw = world(monkeypatch, tmp_path)
    fired = [fired_at for fired_at in SLEEVE_FIRES if et_day_of(fired_at) != FRIDAY]
    seed_sleeve_with_fires(fired)
    cw.at(*INSTANTS["Sat 00:01 ET"])
    anchor = anchor_of(cw)
    assert anchor == FRIDAY and fires_on(fired, anchor) == 0, "fixture sanity"

    assert printed_rows(cw) == [f"{session_label(anchor)} 0"], (
        "a zero count belongs to a session too: the label names the date whatever the count is"
    )


@pytest.mark.parametrize("instant", sorted(TUESDAY_INSTANTS))
def test_the_closed_label_prints_a_one_digit_day_without_a_zero_pad(monkeypatch, tmp_path, instant):
    cw = build_history_world(
        monkeypatch, tmp_path, TUESDAY_NOV_3, first_history_day=date(2026, 10, 1)
    )
    fired = [
        fired_at
        for fired_at in TUESDAY_FIRES
        if datetime.fromisoformat(fired_at).replace(tzinfo=UTC)
        <= utc_of(*TUESDAY_INSTANTS[instant])
    ]
    seed_sleeve_with_fires(fired)
    cw.at(*TUESDAY_INSTANTS[instant])
    anchor = anchor_of(cw)
    assert anchor == TUESDAY_NOV_3.session_day, "fixture sanity: the closed page shows Nov 3"
    count = fires_on(fired, anchor)
    assert count == 2, "fixture sanity: two fires on the session, others on the days around it"

    assert printed_rows(cw) == [f"Nov 3 {count}"], (
        f"{instant}: a one-digit day prints as 'Nov 3', not 'Nov 03' and not 'today'"
    )


def test_while_the_market_is_open_the_row_still_reads_today(monkeypatch, tmp_path):
    cw = world(monkeypatch, tmp_path)
    fired = fires_by(MONDAY, "10:01:00")
    seed_sleeve_with_fires(fired)
    seed_second_rule_without_fires()
    write_engine_cycle(cw.world, local(MONDAY, "10:00:20"), 1)
    cw.at(MONDAY, "10:01:00")
    expected = fires_on(fired, MONDAY)
    assert expected == 1, "fixture sanity: one fire today, others on earlier days"

    assert printed_rows(cw) == [f"today {expected}", "today 0"], (
        "an open market's row counts today and says so"
    )
