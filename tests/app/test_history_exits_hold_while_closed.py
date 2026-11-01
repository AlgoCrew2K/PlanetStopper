"""While the markets are closed the exits the History tab shows, and the counts beside them, hold.

`/api/history/<days>` answers `todays_exits`, the table headed "Today's exits (N)". It read the
Eastern calendar date, so at 00:00 ET with the market closed the table and its count went to zero,
the one figure of that route that moved overnight. The ruling: from the close until the next open
the route answers the exits of the session the closed page shows, the session named by
`meta.portfolio.window_anchor` of `/api/state` (the same anchor every window counts back from).
At Fri 16:30 ET, Sat 00:01 ET, Sun 20:00 ET and Mon 09:29 ET the answer is the same, from both
sources: the session's post-mortem file, and the `exit_triggers` table when that file is not
written. While the market is open it is today's, as before.

The payload names the session in two new fields, which the History page reads:
  * `todays_exits_day`: the ISO date of the session the exits belong to;
  * `todays_exits_market_closed`: true from the close to the next open, false while open.

The sleeve panel's per-rule "today" fire count (`dashboard_panels.sleeves[].rules[].today_fires`
on `/api/state`, `data-field="rule-today"` on `/`) counted fires on the same Eastern calendar date.
It is on the closed page, so it follows the same ruling: the anchor session's fires while closed,
today's while open.

Worlds are the frozen-clock closed session of `_closed_session_world`. Expected answers are read
from the fixture the test wrote (the post-mortem file, the seeded rows) and the anchor from the
route; no figure is typed in. Fixture provenance: the post-mortem files are the world's own
(derived from the captured previous-day rows); the `exit_triggers` rows and the sleeve fires are
synthetic, written through the production writers (`database.record_exit_trigger`,
`database.insert_sleeve_rule_fire`), with the ids of the world's captured symphonies.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import database
from tests.app._closed_session_world import ClosedWorld, build_history_world, utc_of
from tests.app._refresh_timing_frozen_world import SYMPHONY_IDS
from tests.app.test_closed_market_numbers_never_move import local, write_engine_cycle
from tests.app.test_health_no_cycle_since_open import world

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
FRIDAY = "2026-10-30"
THURSDAY = "2026-10-29"
MONDAY = "2026-11-02"
HISTORY_PATH = "/api/history/30"

INSTANTS = {
    "Fri 16:30 ET": (FRIDAY, "16:30:00"),
    "Sat 00:01 ET": ("2026-10-31", "00:01:00"),
    "Sun 20:00 ET": ("2026-11-01", "20:00:00"),
    "Mon 09:29 ET": (MONDAY, "09:29:00"),
}

# One writer-shaped answer row of the table: what the page prints.
EXIT_KEYS = ("ts", "symphony_id", "reason", "detail")


def anchor_of(cw: ClosedWorld) -> str:
    """The session the closed page shows, as the page's own poll says it."""
    return cw.poll("/api/state")["meta"]["portfolio"]["window_anchor"]


def shown(exits: list[dict]) -> list[tuple]:
    return sorted((tuple(row.get(key) for key in EXIT_KEYS) for row in exits), key=repr)


def file_exits(cw: ClosedWorld, day: str) -> list[tuple]:
    """The exits the post-mortem file of `day` holds, in the shape the table prints."""
    record = json.loads((cw.post_mortem_dir / f"post_mortem_{day}.json").read_text("utf-8"))
    return sorted(
        (
            (
                t["time_triggered"],
                t["symphony_id"],
                t["exit_reason"],
                t["saved_pct_guard_alpha"],
            )
            for t in record["triggers"]
        ),
        key=repr,
    )


def seed_exit_trigger(day: str, clock: str, symphony_index: int, reason: str, at_return: float):
    """One `exit_triggers` row stamped `day` `clock` ET, through the production writer."""
    moment = datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=ET)
    database.record_exit_trigger(
        symphony_id=SYMPHONY_IDS[symphony_index],
        triggered_reason=reason,
        at_return=at_return,
        ts_et=moment.strftime("%Y-%m-%dT%H:%M:%S"),
        ts_utc=moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def stored_trigger_ids(day: str) -> list[str]:
    """The symphony ids of the `exit_triggers` rows stamped `day` ET, read from the database (the
    world holds rows of its own besides the ones a test seeds)."""
    with sqlite3.connect(database._db_file()) as conn:
        rows = conn.execute(
            "SELECT symphony_id FROM exit_triggers WHERE substr(ts_et, 1, 10) = ?", (day,)
        ).fetchall()
    return sorted(row[0] for row in rows)


@pytest.mark.parametrize("instant", sorted(INSTANTS))
def test_the_exits_of_the_closed_session_are_answered_all_the_way_to_the_next_open(
    monkeypatch, tmp_path, instant
):
    """Post-mortem source: the session's file is what the table shows at every closed instant."""
    cw = world(monkeypatch, tmp_path)
    cw.at(*INSTANTS[instant])
    anchor = anchor_of(cw)
    assert anchor == cw.session.session_day, (
        "fixture sanity: the closed page shows Friday's session"
    )
    expected = file_exits(cw, anchor)
    assert expected, "fixture sanity: the session's post-mortem file holds an exit"

    body = cw.poll(HISTORY_PATH)
    assert shown(body["todays_exits"]) == expected, (
        f"{instant}: todays_exits is not the exits of the session {anchor} the closed page shows"
    )
    assert body.get("todays_exits_day") == anchor, (
        f"{instant}: the payload does not name the session its exits belong to"
    )
    assert body.get("todays_exits_market_closed") is True, (
        f"{instant}: the payload does not say the market is closed"
    )


def test_no_figure_of_the_history_route_moves_while_the_market_is_closed(monkeypatch, tmp_path):
    cw = world(monkeypatch, tmp_path)
    bodies = {}
    for instant, clock in INSTANTS.items():
        cw.at(*clock)
        bodies[instant] = cw.poll(HISTORY_PATH)
    reference = bodies["Fri 16:30 ET"]
    moved = {
        instant: sorted(key for key in reference if reference[key] != body.get(key))
        for instant, body in bodies.items()
        if body != reference
    }
    assert not moved, f"the History answer moved with the market closed: {moved}"


@pytest.mark.parametrize("instant", sorted(INSTANTS))
def test_the_exits_of_the_exit_triggers_table_hold_when_no_post_mortem_is_written(
    monkeypatch, tmp_path, instant
):
    """exit_triggers source: the session's own rows, never the day before's, until the next open."""
    cw = world(monkeypatch, tmp_path)
    (cw.post_mortem_dir / f"post_mortem_{FRIDAY}.json").unlink()
    seed_exit_trigger(THURSDAY, "11:15:00", 0, "Take Profit", 4.0)
    seed_exit_trigger(FRIDAY, "10:05:00", 0, "Trailing Stop", 2.0)
    seed_exit_trigger(FRIDAY, "13:40:00", 1, "VWAP Breakdown", 3.0)
    cw.at(*INSTANTS["Fri 16:30 ET"])
    reference = cw.poll(HISTORY_PATH)["todays_exits"]
    session_ids = stored_trigger_ids(FRIDAY)
    assert len(session_ids) >= 2 and stored_trigger_ids(THURSDAY), (
        "fixture sanity: the session has rows, and the day before has its own"
    )
    assert sorted(r["symphony_id"] for r in reference) == session_ids, (
        "fixture sanity: at the close the table holds the session's own rows"
    )

    cw.at(*INSTANTS[instant])
    body = cw.poll(HISTORY_PATH)
    assert shown(body["todays_exits"]) == shown(reference), (
        f"{instant}: the exits table is not the closed session's own rows"
    )
    assert body.get("todays_exits_day") == anchor_of(cw) == FRIDAY
    assert body.get("todays_exits_market_closed") is True


def test_while_the_market_is_open_the_exits_are_todays_and_the_payload_says_so(
    monkeypatch, tmp_path
):
    cw = world(monkeypatch, tmp_path)
    seed_exit_trigger(FRIDAY, "13:40:00", 1, "VWAP Breakdown", 3.0)
    seed_exit_trigger(MONDAY, "09:50:00", 0, "Take Profit", 5.0)
    write_engine_cycle(cw.world, local(MONDAY, "10:00:20"), 1)
    cw.at(MONDAY, "10:01:00")

    body = cw.poll(HISTORY_PATH)
    assert stored_trigger_ids(FRIDAY), "fixture sanity: the last session has rows of its own"
    assert sorted(r["symphony_id"] for r in body["todays_exits"]) == stored_trigger_ids(MONDAY), (
        "an open market's table is today's exits only"
    )
    assert body.get("todays_exits_day") == MONDAY
    assert body.get("todays_exits_market_closed") is False


# ---------------------------------------------------------------------------
# The sleeve rule's "today" fire count: the same clock, on the same closed page
# ---------------------------------------------------------------------------
# Fires are stored in UTC; the count belongs to an Eastern calendar day, so each seeded fire's day
# is computed here from its own timestamp.
SLEEVE_FIRES = (
    "2026-10-29 15:00:00",  # Thursday 11:00 ET
    "2026-10-30 14:30:00",  # Friday 10:30 ET
    "2026-10-30 19:45:00",  # Friday 15:45 ET
    "2026-11-02 13:30:00",  # Monday 08:30 ET
)


def et_day_of(fired_at: str) -> str:
    return datetime.fromisoformat(fired_at).replace(tzinfo=UTC).astimezone(ET).date().isoformat()


def fires_by(day: str, clock: str) -> list[str]:
    """The seeded fires that had happened by the ET instant (no fire is in the clock's future)."""
    now = utc_of(day, clock)
    return [f for f in SLEEVE_FIRES if datetime.fromisoformat(f).replace(tzinfo=UTC) <= now]


def seed_sleeve_with_fires(fired: list[str]) -> int:
    sleeve = database.create_sleeve("Income sleeve", 1000.0, envelope_json="{}")
    rule = database.create_sleeve_rule(sleeve, "Trim on drawdown", json_doc="{}")
    for fired_at in fired:
        database.insert_sleeve_rule_fire(rule, sleeve, "buy", "ENTRY", "SHADOW", fired_at=fired_at)
    return rule


def polled_today_fires(cw: ClosedWorld) -> tuple[int, list[int]]:
    """(the poll's count for the one rule, the counts the server-rendered page prints)."""
    rules = cw.poll("/api/state")["dashboard_panels"]["sleeves"][0]["rules"]
    page = cw.world.client.get("/").get_data(as_text=True)
    printed = [int(n) for n in re.findall(r'data-field="rule-today">\s*(\d+)\s*<', page)]
    return rules[0]["today_fires"], printed


@pytest.mark.parametrize("instant", sorted(INSTANTS))
def test_the_rule_fire_count_of_the_closed_session_holds_until_the_next_open(
    monkeypatch, tmp_path, instant
):
    cw = world(monkeypatch, tmp_path)
    fired = fires_by(*INSTANTS[instant])
    seed_sleeve_with_fires(fired)
    cw.at(*INSTANTS[instant])
    anchor = anchor_of(cw)
    expected = sum(1 for fired_at in fired if et_day_of(fired_at) == anchor)
    assert expected >= 2, "fixture sanity: the session has fires, and other days have their own"

    polled, printed = polled_today_fires(cw)
    assert polled == expected, (
        f"{instant}: /api/state counts {polled} fires for 'today', the closed session {anchor} "
        f"had {expected}"
    )
    assert printed == [expected], (
        f"{instant}: the page prints {printed} for the rule's 'today', expected [{expected}]"
    )


def test_while_the_market_is_open_the_rule_fire_count_is_todays(monkeypatch, tmp_path):
    cw = world(monkeypatch, tmp_path)
    fired = fires_by(MONDAY, "10:01:00")
    seed_sleeve_with_fires(fired)
    write_engine_cycle(cw.world, local(MONDAY, "10:00:20"), 1)
    cw.at(MONDAY, "10:01:00")
    expected = sum(1 for fired_at in fired if et_day_of(fired_at) == MONDAY)
    assert expected == 1, "fixture sanity: one fire today, others on earlier days"

    polled, printed = polled_today_fires(cw)
    assert polled == expected
    assert printed == [expected]
