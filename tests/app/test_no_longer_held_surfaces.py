"""A symphony no longer held is left out of every surface, on the open and the closed-market branch.

The world is the refresh-timing world (two captured symphonies, three sessions of rows after this file
adds two older ones). A third state entry is added: a copy of a captured live entry under a new id and
name, with rows on two sessions older than the others' two most recent. Its returns are synthetic and
labelled so (tests/fixtures/no_longer_held/PROVENANCE.md): large and opposite in sign to the others'
so a leak into any portfolio figure moves it.

Every comparison is "the same read with the entry present equals the read without it": the cards, the
section counts, the strip's guard effect, the hero series, the Settings roster and the Retirement roster.
Nothing here types in a number from a run.
"""

from __future__ import annotations

import copy
import os
import re
import sqlite3

import pytest

import database as database_module
from tests.app._refresh_timing_frozen_world import retime_rows
from tests.app._refresh_timing_state_world import (
    TRIGGERED_ID,
    UNTRIGGERED_ID,
    seeded_world,
)

STALE_ID = "rvwNoLongerHeld00001"
STALE_NAME = "Review Gone Symphony"
OLDER_SESSIONS = ("2026-10-19", "2026-10-16")  # older than the world's 10-20 and 10-21
STALE_SESSIONS = (
    "2026-10-16",
    "2026-10-15",
)  # both older than the two most recent (10-21, 10-20 or 10-20, 10-19)

BRANCHES = ["open", "closed_frozen"]


def _seed_older_sessions(world) -> None:
    """Two more sessions of rows for the two real symphonies, before the world's own."""
    for day in OLDER_SESSIONS:
        world.seed_rows(
            retime_rows(
                world.previous_day_rows(),
                day=day,
                clock="15:52:00",
                cycle_id=f"{day.replace('-', '')}_1552",
            )
        )


def _add_stale_entry(world) -> None:
    """The stale symphony in the live state and in the close snapshot, with its old rows."""
    state = database_module.load_state()
    template = copy.deepcopy(world.live_entry("m", UNTRIGGERED_ID))
    template.update({"name": STALE_NAME, "current_value": 4000.0, "triggered": False})
    state[STALE_ID] = template
    snapshot = state.get("last_market_close_snapshot")
    if snapshot:
        entry = copy.deepcopy(world.snapshot_entries[UNTRIGGERED_ID])
        entry.update({"id": STALE_ID, "name": STALE_NAME, "current_value": 4000.0})
        next(iter(snapshot["accounts_map"].values())).append(entry)
    database_module.save_state(state)
    base_rows = [dict(r) for r in world.previous_day_rows() if r["symphony_id"] == UNTRIGGERED_ID]
    for index, day in enumerate(STALE_SESSIONS):
        rows = retime_rows(
            base_rows, day=day, clock="15:52:00", cycle_id=f"{day.replace('-', '')}_1552"
        )
        for row in rows:
            row["symphony_id"] = STALE_ID
            # Synthetic returns, labelled: large, and opposite to the real symphonies' on the same day.
            row["current_return"] = 9.0 + index
            row["shadow_return"] = -9.0 - index
        world.seed_rows(rows)


def _read(world) -> dict:
    page = world.page()
    state = world.poll("/api/state")
    return {
        "page": page,
        "card_ids": [card["id"] for card in state["symphonies"]],
        "strip": world.poll("/api/strip/30d"),
        "chart": world.poll("/api/hero-chart/all"),
        "state_strip": state["portfolio_strip"],
        "performance": world.poll("/api/performance?scope=aggregate&days=60"),
    }


def _counts(page: str) -> tuple[str, str]:
    def count(testid: str) -> str:
        match = re.search(rf'data-testid="{testid}"[^>]*>\s*(\d+)\s*<', page)
        assert match, f"{testid} not found on the page"
        return match.group(1)

    return count("active-section-count"), count("standby-section-count")


@pytest.fixture(params=BRANCHES)
def pair(request, monkeypatch, tmp_path):
    """(world, the read taken before the entry exists, the read taken after it was added)."""
    world = seeded_world(monkeypatch, tmp_path, request.param)
    _seed_older_sessions(world)
    before = _read(world)
    _add_stale_entry(world)
    after = _read(world)
    return world, before, after


def test_the_entry_has_no_card_and_the_counts_do_not_change(pair):
    world, before, after = pair
    assert STALE_ID not in after["card_ids"], "a symphony no longer held still has a card"
    assert after["card_ids"] == before["card_ids"]
    assert f'data-sym-id="{STALE_ID}"' not in after["page"]
    assert STALE_NAME not in after["page"], "its name is still on the dashboard"
    assert _counts(after["page"]) == _counts(before["page"]), "a section count includes it"


def test_the_headline_and_the_hero_series_ignore_it(pair):
    world, before, after = pair
    assert after["strip"]["guard_alpha"] == before["strip"]["guard_alpha"], (
        "the guard effect moved when a symphony no longer held was added to the state"
    )
    assert after["chart"]["hist_bot"] == before["chart"]["hist_bot"]
    assert after["chart"]["hist_held"] == before["chart"]["hist_held"]
    assert after["chart"]["hist_dates"] == before["chart"]["hist_dates"]


def test_the_performance_series_ignore_it(pair):
    world, before, after = pair
    for key in ("dates", "live_returns", "shadow_returns"):
        assert after["performance"][key] == before["performance"][key], (
            f"/api/performance {key} changed when a symphony no longer held was added"
        )


def test_the_poll_strip_ignores_it(pair):
    world, before, after = pair
    for key in ("guard_alpha", "cumulative_return", "today_change", "max_drawdown"):
        assert after["state_strip"].get(key) == before["state_strip"].get(key), (
            f"portfolio_strip.{key} changed when a symphony no longer held was added"
        )


def test_the_settings_roster_lists_it_apart_and_dimmed_data_is_served(pair):
    world, before, after = pair
    body = world.poll("/api/settings")
    held_names = [s["name"] for s in body["symphonies_list"]]
    assert STALE_NAME not in held_names, "the active roster still lists it"
    assert len(held_names) == len(before["card_ids"])
    gone = body["symphonies_no_longer_held"]
    assert [s["name"] for s in gone] == [STALE_NAME], (
        "Settings must list the entry under symphonies_no_longer_held so it can be shown dimmed"
    )
    assert gone[0]["id"] == database_module.normalize_name(STALE_NAME), (
        "the entry has the same id shape as symphonies_list"
    )


def test_nothing_is_deleted_from_the_database(pair):
    world, before, after = pair
    assert STALE_ID in database_module.load_state(), "the state entry was removed"
    conn = sqlite3.connect(os.environ["DB_PATH"])
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM shadow_history WHERE symphony_id = ?", (STALE_ID,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == len(STALE_SESSIONS), "its rows were deleted"


def test_the_advisor_symphony_list_does_not_offer_it(pair):
    """The Advisor's select (and the Performance picker) are filled from this route."""
    world, before, after = pair
    names = [s["name"] for s in world.poll("/api/performance/symphonies")["symphonies"]]
    assert STALE_NAME not in names, "the symphony list still offers a symphony no longer held"
    assert len(names) == len(before["card_ids"])
    response = world.client.get("/ai-advisor")
    assert response.status_code == 200
    assert STALE_NAME not in response.get_data(as_text=True)


def test_the_retirement_roster_excludes_it(pair, monkeypatch):
    from advisors import retirement_recommender

    world, before, after = pair
    _recs, summary = retirement_recommender.build_recommendations_with_summary(
        db_file=os.environ["DB_PATH"], source="shadow"
    )
    state = database_module.load_state()
    named = [k for k, v in state.items() if isinstance(v, dict) and "name" in v]
    assert STALE_ID in named
    assert summary["roster_size"] == len(named) - 1, (
        "the Retirement roster counts a symphony that is no longer held"
    )


def test_the_entry_returns_by_itself_when_rows_resume(monkeypatch, tmp_path):
    world = seeded_world(monkeypatch, tmp_path, "open")
    _seed_older_sessions(world)
    _add_stale_entry(world)
    assert STALE_ID not in [c["id"] for c in world.poll("/api/state")["symphonies"]]
    resumed = retime_rows(
        [r for r in world.previous_day_rows() if r["symphony_id"] == UNTRIGGERED_ID],
        day="2026-10-20",
        clock="15:52:00",
        cycle_id="20261020_1552",
    )
    for row in resumed:
        row["symphony_id"] = STALE_ID
        row["ts_et"] = row["ts_et"].replace("15:52:00", "15:53:00")
    world.seed_rows(resumed)
    assert STALE_ID in [c["id"] for c in world.poll("/api/state")["symphonies"]], (
        "rows resumed on the previous session and the entry did not come back"
    )


def test_the_dashboard_route_reads_the_one_predicate(monkeypatch, tmp_path):
    """No page has its own rule: the route's answer follows the shared function's answer."""
    import analytics

    world = seeded_world(monkeypatch, tmp_path, "open")
    _seed_older_sessions(world)
    _add_stale_entry(world)
    calls = []
    real = analytics.no_longer_held_symphony_ids

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(analytics, "no_longer_held_symphony_ids", spy)
    world.poll("/api/state")
    world.page()
    world.poll("/api/settings")
    assert calls, "no route asked analytics.no_longer_held_symphony_ids"
    monkeypatch.setattr(analytics, "no_longer_held_symphony_ids", lambda *a, **k: frozenset())
    assert STALE_ID in [c["id"] for c in world.poll("/api/state")["symphonies"]], (
        "with the shared predicate answering 'none', the entry must show: a page that decides "
        "for itself would not follow it"
    )
