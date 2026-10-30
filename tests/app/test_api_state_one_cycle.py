"""`/api/state` never pairs two engine cycles.

The engine commits a cycle's `shadow_history` rows in its data phase and its `bot_state` at
the end, so for a few seconds every minute the newest rows belong to a cycle whose state is
not saved. A poll in that window must answer from the last completed cycle: the stored
`last_successful_cycle_at` names it, and rows stamped after it are not yet part of the
picture. A response that reads a Bot leg from the newer rows and a Held leg from the older
state shows a card whose two numbers never existed together.

The tests are differential: the same request is made with the in-flight cycle's rows absent
and present, and every figure the cycle determines must be identical. Cycle `m_minus_1` is
captured; cycle `m` is the same pair of symphonies one minute later with moved figures (the
captured cycles ran after the close and did not move), see the fixture folder's PROVENANCE.md.

Seams: the committed-read connection is `app._committed_shadow_conn` (module-level, looked
up by global name); the analytics reader patched in the failing-read test is
`analytics._load_latest_shadow_row_for_analytics`.
"""

from __future__ import annotations

import sqlite3

import pytest

import analytics as analytics_module
import app as app_module
import database as database_module
from tests.app._refresh_timing_state_world import (
    SYMPHONY_IDS,
    TRIGGERED_ID,
    UNTRIGGERED_ID,
    build_world,
    card_legs,
    cycle_projection,
    format_figure,
    hero_last_points,
    load_record,
    rendered_page_figures,
    seeded_world,
)


def _world_after_previous_cycle(monkeypatch, tmp_path, *, only_triggered_before: bool = False):
    """Yesterday's rows and cycle M-1 committed, state saved for M-1; M not yet written."""
    world = build_world(monkeypatch, tmp_path, "open")
    previous_day = world.previous_day_rows()
    world.seed_rows(
        [r for r in previous_day if not only_triggered_before or r["symphony_id"] == TRIGGERED_ID]
    )
    world.seed_cycle("m_minus_1", only=TRIGGERED_ID if only_triggered_before else None)
    world.commit_state("m_minus_1")
    return world


class TestResponseIgnoresTheCycleInFlight:
    def test_every_cycle_figure_is_unchanged_by_rows_of_an_uncommitted_cycle(
        self, monkeypatch, tmp_path
    ):
        world = _world_after_previous_cycle(monkeypatch, tmp_path)
        before = world.poll()

        world.seed_cycle("m")
        after = world.poll()

        assert cycle_projection(after) == cycle_projection(before), (
            "rows of the cycle whose state is not saved changed the response"
        )

    def test_bot_leg_comes_from_the_completed_cycle_and_held_leg_from_its_state(
        self, monkeypatch, tmp_path
    ):
        world = _world_after_previous_cycle(monkeypatch, tmp_path)
        world.seed_cycle("m")

        legs = card_legs(world.poll())[UNTRIGGERED_ID]

        completed = world.row("m_minus_1", UNTRIGGERED_ID)
        assert legs["tc_bot"] == pytest.approx(completed["shadow_return"]), (
            "the Bot leg must read the completed cycle's row, not the row of the cycle in flight"
        )
        assert legs["tc_held"] == pytest.approx(
            world.live_entry("m_minus_1", UNTRIGGERED_ID)["current_return"]
        )

    def test_hero_series_ends_on_the_completed_cycle(self, monkeypatch, tmp_path):
        world = _world_after_previous_cycle(monkeypatch, tmp_path)
        before = hero_last_points(world.poll())

        world.seed_cycle("m")
        after = hero_last_points(world.poll())

        assert after == before, "the hero series' last point moved on rows of an unsaved cycle"
        assert all(point is not None for point in after.values()), (
            "the hero series must exist for this comparison to mean anything"
        )


class TestResponseFollowsTheCycleOnceItsStateIsSaved:
    def test_legs_move_to_the_new_cycle_when_its_state_is_committed(self, monkeypatch, tmp_path):
        world = _world_after_previous_cycle(monkeypatch, tmp_path)
        world.seed_cycle("m")
        in_flight = world.poll()

        world.commit_state("m")
        committed = world.poll()

        legs = card_legs(committed)[UNTRIGGERED_ID]
        assert legs["tc_bot"] == pytest.approx(world.row("m", UNTRIGGERED_ID)["shadow_return"])
        assert legs["tc_held"] == pytest.approx(
            world.live_entry("m", UNTRIGGERED_ID)["current_return"]
        )
        assert hero_last_points(committed) != hero_last_points(in_flight), (
            "the hero series must reach the new cycle once its state is saved"
        )

    def test_a_row_stamped_in_the_same_second_as_the_saved_state_is_included(
        self, monkeypatch, tmp_path
    ):
        # `last_successful_cycle_at` carries microseconds and the rows' ts_et does not, so a
        # cutoff that compared them as strings or truncated the wrong way would hide the very
        # cycle the state names.
        world = _world_after_previous_cycle(monkeypatch, tmp_path)
        world.seed_cycle("m")
        world.commit_state("m")
        assert world.stamp("m")[:19] == world.row("m", UNTRIGGERED_ID)["ts_et"], (
            "fixture: the saved stamp and the rows are in the same second"
        )

        legs = card_legs(world.poll())[UNTRIGGERED_ID]

        assert legs["tc_bot"] == pytest.approx(world.row("m", UNTRIGGERED_ID)["shadow_return"])


class TestSymphonyWithNoRowBeforeTheCycleInFlight:
    def test_bot_leg_stays_absent_instead_of_reading_the_new_cycle(self, monkeypatch, tmp_path):
        world = _world_after_previous_cycle(monkeypatch, tmp_path, only_triggered_before=True)
        before = world.poll()
        assert card_legs(before)[UNTRIGGERED_ID]["tc_bot"] is None, (
            "fixture: no committed row exists for this symphony"
        )

        world.seed_cycle("m")
        after = world.poll()

        assert card_legs(after)[UNTRIGGERED_ID]["tc_bot"] is None, (
            "the Bot leg read a row of the uncommitted cycle for a symphony with no earlier row"
        )
        assert cycle_projection(after) == cycle_projection(before)


class TestPollAfterTheEngineExits:
    def test_values_equal_the_reference_for_the_same_committed_state(self, monkeypatch, tmp_path):
        golden = load_record("golden_ref_api_state_open.json")
        world = seeded_world(monkeypatch, tmp_path, "open")

        body = world.poll()

        assert body["symphonies"] == golden["symphonies"]
        changed = {
            key: (expected, body["portfolio_strip"].get(key))
            for key, expected in golden["portfolio_strip"].items()
            if key != "data_as_of" and body["portfolio_strip"].get(key) != expected
        }
        assert not changed, (
            f"strip figures differ from the reference (reference, served): {changed}"
        )

    def test_server_render_rows_equal_the_reference_strip(self, monkeypatch, tmp_path):
        golden = load_record("golden_ref_api_state_open.json")["portfolio_strip"]
        world = seeded_world(monkeypatch, tmp_path, "open")

        figures = rendered_page_figures(world.page())

        for row, key in (
            ("today", "today_change"),
            ("cumulative", "cumulative_return"),
            ("mdd", "max_drawdown"),
        ):
            assert figures[f"{row}_bot"] == format_figure(golden[key]["dry_run"]), row
            assert figures[f"{row}_held"] == format_figure(golden[key]["if_held"]), row


class TestFailuresNeverProduceAPartialMix:
    def test_failure_to_open_the_committed_read_connection_answers_500(self, monkeypatch, tmp_path):
        world = _world_after_previous_cycle(monkeypatch, tmp_path)

        def fail_to_open(*_args, **_kwargs):
            raise OSError("committed read connection unavailable")

        monkeypatch.setattr(app_module, "_committed_shadow_conn", fail_to_open, raising=False)

        response = world.client.get("/api/state")

        assert response.status_code == 500, (
            "a response without its committed-read connection must fail, not fall back to "
            "per-call connections that read the cycle in flight"
        )
        body = response.get_json()
        assert body["status"] == "error"
        assert "portfolio_strip" not in body and "symphonies" not in body

    def test_failure_to_read_state_answers_500(self, monkeypatch, tmp_path):
        world = _world_after_previous_cycle(monkeypatch, tmp_path)

        def unreadable():
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(database_module, "load_state", unreadable)

        response = world.client.get("/api/state")

        assert response.status_code == 500
        body = response.get_json()
        assert body["status"] == "error"
        assert "portfolio_strip" not in body and "symphonies" not in body

    def test_a_failed_read_inside_one_helper_leaves_a_dash_and_the_other_legs_on_one_cycle(
        self, monkeypatch, tmp_path
    ):
        def failed_read(*_args, **_kwargs):
            return None  # what the helper returns when its own query raises

        world = _world_after_previous_cycle(monkeypatch, tmp_path)
        monkeypatch.setattr(analytics_module, "_load_latest_shadow_row_for_analytics", failed_read)
        before = world.poll()

        world.seed_cycle("m")
        after = world.poll()

        for sym_id in SYMPHONY_IDS:
            assert card_legs(after)[sym_id]["tc_bot"] is None, f"{sym_id}: failed read is a dash"
        assert cycle_projection(after) == cycle_projection(before), (
            "with one helper's read failing, the remaining legs read the unsaved cycle"
        )


class TestRowsLandingDuringARequest:
    def test_every_response_around_the_landing_comes_from_one_cycle(self, monkeypatch, tmp_path):
        world = _world_after_previous_cycle(monkeypatch, tmp_path)
        timer_poll = world.poll()

        landed = {"done": False}
        real_today = analytics_module.get_symphony_today_change

        def land_rows_then_read(*args, **kwargs):
            result = real_today(*args, **kwargs)
            if not landed["done"]:
                landed["done"] = True
                world.seed_cycle("m")  # the engine commits its rows while this request runs
            return result

        monkeypatch.setattr(analytics_module, "get_symphony_today_change", land_rows_then_read)
        event_poll = world.poll()
        later_poll = world.poll()

        assert landed["done"], "fixture: the rows must land during the request"
        assert cycle_projection(event_poll) == cycle_projection(timer_poll)
        assert cycle_projection(later_poll) == cycle_projection(timer_poll)
