"""After the close, every figure stays fixed on the close snapshot until the next one is stored.

The rule these tests pin, in one sentence: from 16:00 ET (the market state turns closed) the
response is a function of the stored close snapshot, the account totals latched for that
snapshot, and the shadow rows stamped at or before the snapshot's capture moment. Nothing
else moves a shown number: not the engine's rows written between 16:00 and 16:04 (the engine
keeps writing them every day), not the live `bot_state` the engine keeps saving, not the
account-totals refresh that runs twice a minute all night. The only event that moves a
figure after 16:00 is the close step storing the next snapshot.

Each test shows a figure before and after something lands that must not move it (a
differential on one database), or compares the poll with the window routes and the page.
Everything runs through the real routes and the real refresh job against a temp database
seeded from the committed fixture records (`_refresh_timing_frozen_world`); neither
`analytics` nor `database` is mocked and no expected figure is typed in.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import app as app_module
from tests.app._refresh_timing_frozen_world import (
    CAPTURE_CLOCK,
    CAPTURE_DAY,
    PREVIOUS_DAY,
    account_figures,
    base_snapshot,
    clean_account_totals,
    clocked_world,
    close_world,
    composer_body,
    count_state_reads,
    describe,
    differences,
    drift_live_state,
    engine_cycle_after_close,
    et,
    expected_account_value,
    expected_all_time_cr,
    install_composer,
    legs_of_entries,
    midday_world,
    refresh_at,
    retime_rows,
    save_state,
    seed_after_close_cycles,
    set_clock,
    snapshot_variant,
    stamp_text,
    store_snapshot,
    visible_text,
)
from tests.app._refresh_timing_state_world import (
    UNTRIGGERED_ID,
    polled_figures,
    rendered_page_figures,
)

CLOSED_INSTANTS = {
    "closed_frozen": et(CAPTURE_DAY, "18:00:00"),
    "pre_market": et("2026-10-22", "08:00:00"),
}
CLOSED_BRANCHES = tuple(CLOSED_INSTANTS)


def make_close_world(monkeypatch, tmp_path, branch):
    world, _ = close_world(monkeypatch, tmp_path, CLOSED_INSTANTS[branch])
    return world


# ---------------------------------------------------------------------------
# Engine rows written after the capture cannot move a shown number
# ---------------------------------------------------------------------------


class TestRowsWrittenAfterTheCaptureCannotMoveAFigure:
    """The droplet's engine writes shadow rows until 16:04 every day, after the close snapshot
    (captured about 16:00:42). A row stamped after the capture is not part of the close."""

    @pytest.mark.parametrize("branch", CLOSED_BRANCHES)
    def test_the_poll_is_unchanged_by_the_two_cycles_written_after_the_capture(
        self, monkeypatch, tmp_path, branch
    ):
        world = make_close_world(monkeypatch, tmp_path, branch)
        before = world.poll()

        seed_after_close_cycles(world)
        after = world.poll()

        changed = differences(before, after)
        assert not changed, (
            f"{branch}: rows written after the capture moved shown numbers: {describe(changed)}"
        )
        assert before.get("trading_day") == CAPTURE_DAY, "the poll must serve the close snapshot"

    @pytest.mark.parametrize("branch", CLOSED_BRANCHES)
    def test_the_server_render_is_unchanged_by_rows_written_after_the_capture(
        self, monkeypatch, tmp_path, branch
    ):
        world = make_close_world(monkeypatch, tmp_path, branch)
        before_page = world.page()

        seed_after_close_cycles(world)
        after_page = world.page()

        assert rendered_page_figures(before_page) == rendered_page_figures(after_page), (
            f"{branch}: the rows or headline of the page moved"
        )
        assert visible_text(before_page) == visible_text(after_page), (
            f"{branch}: a number or word on the page changed after the close"
        )

    @pytest.mark.parametrize("branch", CLOSED_BRANCHES)
    @pytest.mark.parametrize("window", ["30d", "1y", "all"])
    def test_the_window_strip_is_unchanged_by_rows_written_after_the_capture(
        self, monkeypatch, tmp_path, branch, window
    ):
        world = make_close_world(monkeypatch, tmp_path, branch)
        before = world.poll(f"/api/strip/{window}")

        seed_after_close_cycles(world)
        after = world.poll(f"/api/strip/{window}")

        changed = differences(before, after)
        assert not changed, (
            f"{branch}/{window}: rows written after the capture moved the strip: "
            f"{describe(changed)}"
        )

    @pytest.mark.parametrize("branch", CLOSED_BRANCHES)
    def test_the_hero_chart_is_unchanged_by_rows_written_after_the_capture(
        self, monkeypatch, tmp_path, branch
    ):
        world = make_close_world(monkeypatch, tmp_path, branch)
        before = world.poll("/api/hero-chart/30d")

        seed_after_close_cycles(world)
        after = world.poll("/api/hero-chart/30d")

        changed = differences(before, after)
        assert not changed, (
            f"{branch}: rows written after the capture moved the chart: {describe(changed)}"
        )
        assert before["hist_dates"], "the chart must have points for this comparison to mean much"

    @pytest.mark.parametrize(
        ("day", "clock"),
        [(CAPTURE_DAY, "20:00:05"), ("2026-10-22", "03:00:05"), ("2026-10-22", "09:29:05")],
        ids=["evening", "overnight", "last minute before the open"],
    )
    def test_cycles_the_engine_runs_later_that_night_move_no_figure(
        self, monkeypatch, tmp_path, day, clock
    ):
        world, _ = close_world(monkeypatch, tmp_path, et("2026-10-22", "09:29:50"))
        before = world.poll()
        before_strip = world.poll("/api/strip/30d")
        before_chart = world.poll("/api/hero-chart/30d")
        before_page = visible_text(world.page())

        engine_cycle_after_close(world, day, clock)

        changed = differences(before, world.poll())
        assert not changed, f"a cycle at {day} {clock} moved shown numbers: {describe(changed)}"
        assert differences(before_strip, world.poll("/api/strip/30d")) == {}
        assert differences(before_chart, world.poll("/api/hero-chart/30d")) == {}
        assert visible_text(world.page()) == before_page

    def test_a_row_stamped_in_the_capture_second_counts_and_the_next_second_does_not(
        self, monkeypatch, tmp_path
    ):
        world = make_close_world(monkeypatch, tmp_path, "closed_frozen")
        baseline = world.poll()
        moved = {row["symphony_id"]: row for row in world.cycle_rows("m")}
        in_row = retime_rows(
            [moved[UNTRIGGERED_ID]],
            day=CAPTURE_DAY,
            clock=CAPTURE_CLOCK,
            cycle_id="20261021_1600b",
        )

        world.seed_rows(in_row)
        # The closed poll is held per snapshot identity, and the row lands under the identity
        # already held; this test pins where the cut falls, so each scenario is read cold.
        app_module._CLOSED_HOLD.clear()
        with_capture_second = world.poll()

        # The row in the capture second is part of the close: the untriggered symphony's
        # latest Bot return of the day is now that row's.
        legs_before = legs_of_entries(baseline)[UNTRIGGERED_ID]["_tc"]["dry_run"]
        legs_in = legs_of_entries(with_capture_second)[UNTRIGGERED_ID]["_tc"]["dry_run"]
        assert legs_in == in_row[0]["current_return"], (
            "a row stamped exactly at the capture moment belongs to the close"
        )
        assert legs_before != legs_in, "the row must differ from the close rows to prove inclusion"

        out_row = retime_rows(
            [
                dict(
                    moved[UNTRIGGERED_ID],
                    current_return=moved[UNTRIGGERED_ID]["current_return"] + 5,
                )
            ],
            day=CAPTURE_DAY,
            clock="16:00:43",
            cycle_id="20261021_1600c",
        )
        world.seed_rows(out_row)
        app_module._CLOSED_HOLD.clear()
        with_next_second = world.poll()

        changed = differences(with_capture_second, with_next_second)
        assert not changed, (
            f"a row one second after the capture moved shown numbers: {describe(changed)}"
        )

    def test_the_closed_sessions_figures_stay_until_its_snapshot_is_stored(
        self, monkeypatch, tmp_path
    ):
        # 16:00:20 on D: the engine has written D's rows through 16:04, but D's snapshot is not
        # stored yet. The page serves D's last committed state, cut at the close (never the
        # earlier session's snapshot), unchanged, until the close step stores D's snapshot; that
        # storing is the one event that moves it.
        world = clocked_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "16:00:20"))
        world.seed_rows(world.previous_day_rows())
        earlier = snapshot_variant(
            base_snapshot(world), trading_day=PREVIOUS_DAY, captured_at_et="16:00:42 ET"
        )
        save_state(world, "m", snapshot=earlier)
        before = world.poll()
        assert before.get("trading_day") == CAPTURE_DAY, "the closed session, not the earlier one"

        close_rows = retime_rows(
            world.cycle_rows("m_minus_1"),
            day=CAPTURE_DAY,
            clock="16:00:30",
            cycle_id="20261021_1600",
        )
        world.seed_rows(close_rows)
        seed_after_close_cycles(world)
        # The held figures are keyed by the session, so these rows land under a key already held;
        # this test pins the cut at the close, so the second read is cold.
        app_module._CLOSED_HOLD.clear()
        still_held = world.poll()

        changed = differences(before, still_held)
        assert not changed, (
            "the closed session's figures moved before its snapshot was stored: "
            f"{describe(changed)}"
        )
        assert still_held.get("trading_day") == CAPTURE_DAY
        assert still_held.get("snapshot_is_previous_session") is False

        store_snapshot(
            world,
            snapshot_variant(
                base_snapshot(world), trading_day=CAPTURE_DAY, captured_at_et="16:00:42 ET"
            ),
        )
        taken_over = world.poll()

        assert taken_over.get("trading_day") == CAPTURE_DAY
        assert taken_over["portfolio_strip"]["hist_dates"][-1] == CAPTURE_DAY, (
            "once the next snapshot is stored its day's rows are the close"
        )
        assert (
            taken_over["portfolio_strip"]["hist_dates"]
            != (still_held["portfolio_strip"]["hist_dates"])
        ), "the stored snapshot is what moves the figures"

    @pytest.mark.parametrize("branch", CLOSED_BRANCHES)
    def test_live_state_saved_after_the_close_does_not_move_the_poll(
        self, monkeypatch, tmp_path, branch
    ):
        world = make_close_world(monkeypatch, tmp_path, branch)
        before = world.poll()

        drift_live_state(world)
        after = world.poll()

        changed = differences(before, after)
        assert not changed, (
            f"{branch}: the engine's later saves of live state moved shown numbers: "
            f"{describe(changed)}"
        )


# ---------------------------------------------------------------------------
# Account totals: fixed on the snapshot, latched at the first read after the capture
# ---------------------------------------------------------------------------


def latched_world(monkeypatch, tmp_path, instant, **kwargs):
    world, _ = close_world(monkeypatch, tmp_path, instant, **kwargs)
    clean_account_totals(monkeypatch)
    composer = install_composer(monkeypatch, version=0)
    return world, composer


class TestFrozenFiguresDoNotDriftWithAccountRefreshes:
    @pytest.mark.parametrize("mask_cache_before_poll", [False, True], ids=["cache warm", "masked"])
    def test_a_refresh_between_two_polls_ten_minutes_apart_changes_nothing(
        self, monkeypatch, tmp_path, mask_cache_before_poll
    ):
        world, composer = latched_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "18:00:00"))
        refresh_at(monkeypatch, et(CAPTURE_DAY, "18:00:00"))
        first = world.poll()

        composer.version = 1
        refresh_at(monkeypatch, et(CAPTURE_DAY, "18:10:00"))
        assert (
            app_module._account_totals_cache["portfolio_value"]
            == composer_body(1)["portfolio_value"]
        ), "the refresh between the polls must reach the cache for this test to mean anything"
        if mask_cache_before_poll:
            app_module._account_totals_cache.mark_stale()
        second = world.poll()

        assert account_figures(second) == account_figures(first), (
            f"account-basis figures drifted between the polls: "
            f"{describe(differences(account_figures(first), account_figures(second)))}"
        )
        assert first["portfolio_strip"]["account_value"] == pytest.approx(
            composer_body(0)["portfolio_value"]
        ), "the figures are the close's own totals (the first read after the capture)"
        assert not first["portfolio_strip"].get("account_basis_stale"), (
            "the latched close totals are current for this snapshot, not stale"
        )
        assert not second["portfolio_strip"].get("account_basis_stale")

    def test_the_all_time_account_figure_is_the_latched_one_on_every_poll(
        self, monkeypatch, tmp_path
    ):
        world, composer = latched_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "18:00:00"))
        refresh_at(monkeypatch, et(CAPTURE_DAY, "18:00:00"))
        first = world.poll()["portfolio_strip"]["account_all_time_cr"]
        composer.version = 2
        refresh_at(monkeypatch, et(CAPTURE_DAY, "18:10:00"))
        second = world.poll()["portfolio_strip"]["account_all_time_cr"]

        assert first == pytest.approx(expected_all_time_cr(0))
        assert second == first, "the Account all-time figure drifted with the evening refresh"

    def test_the_server_render_shows_the_fixed_figures_after_a_later_refresh(
        self, monkeypatch, tmp_path
    ):
        world, composer = latched_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "18:00:00"))
        refresh_at(monkeypatch, et(CAPTURE_DAY, "18:00:00"))
        polled = polled_figures(world.poll())
        composer.version = 1
        refresh_at(monkeypatch, et(CAPTURE_DAY, "18:10:00"))

        rendered = rendered_page_figures(world.page())

        keys = (
            "today_bot",
            "today_held",
            "cumulative_bot",
            "cumulative_held",
            "account_all_time_cr",
            "guard_alpha",
        )
        differing = {k: (rendered[k], polled[k]) for k in keys if rendered[k] != polled[k]}
        assert not differing, f"the page and the first poll disagree (page, poll): {differing}"
        assert rendered["account_all_time_cr"] is not None

    def test_before_any_read_after_the_capture_the_last_good_totals_carry_the_stale_chip(
        self, monkeypatch, tmp_path
    ):
        world, composer = latched_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "18:00:00"))
        refresh_at(monkeypatch, et(CAPTURE_DAY, "15:30:00"))  # the last read before the close
        set_clock(monkeypatch, et(CAPTURE_DAY, "18:00:00"))
        first = world.poll()
        composer.mode = "http_503"
        refresh_at(monkeypatch, et(CAPTURE_DAY, "18:05:00"))
        second = world.poll()
        strip = first["portfolio_strip"]

        assert strip.get("account_basis_stale") is True
        assert strip.get("account_basis_as_of") == stamp_text(
            et(CAPTURE_DAY, "15:30:00"), seconds=True
        )
        assert strip["account_value"] == pytest.approx(composer_body(0)["portfolio_value"])
        assert account_figures(second) == account_figures(first)
        assert second["portfolio_strip"].get("account_basis_as_of") == strip["account_basis_as_of"]

    def test_the_first_successful_read_after_the_capture_becomes_the_close_totals_for_good(
        self, monkeypatch, tmp_path
    ):
        world, composer = latched_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "18:00:00"))
        refresh_at(monkeypatch, et(CAPTURE_DAY, "15:30:00"))
        composer.mode = "http_503"
        refresh_at(monkeypatch, et(CAPTURE_DAY, "18:05:00"))
        set_clock(monkeypatch, et(CAPTURE_DAY, "18:05:30"))
        waiting = world.poll()

        composer.mode, composer.version = "ok", 1
        refresh_at(monkeypatch, et(CAPTURE_DAY, "18:20:00"))
        latched = world.poll()
        composer.version = 2
        refresh_at(monkeypatch, et(CAPTURE_DAY, "18:30:00"))
        later = world.poll()

        assert waiting["portfolio_strip"].get("account_basis_stale") is True
        strip = latched["portfolio_strip"]
        assert strip["account_value"] == pytest.approx(composer_body(1)["portfolio_value"]), (
            "the first successful read after the capture supplies the close totals"
        )
        assert not strip.get("account_basis_stale")
        assert account_figures(later) == account_figures(latched), (
            "a later read re-latched the close totals"
        )

    @pytest.mark.parametrize("mode", ["http_503", "timeout", "connection_error"])
    @pytest.mark.parametrize("mask_cache", [True, False], ids=["cache masked each cycle", "warm"])
    def test_composer_failing_all_evening_leaves_the_last_good_totals_fixed(
        self, monkeypatch, tmp_path, mode, mask_cache
    ):
        world, composer = latched_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "18:00:00"))
        refresh_at(monkeypatch, et(CAPTURE_DAY, "15:30:00"))
        composer.mode = mode
        seen = []
        for clock in ("18:00:00", "18:30:00", "21:00:00"):
            refresh_at(monkeypatch, et(CAPTURE_DAY, clock))
            if mask_cache:
                app_module._account_totals_cache.mark_stale()
            seen.append(world.poll())

        for body in seen[1:]:
            assert account_figures(body) == account_figures(seen[0])
        strip = seen[0]["portfolio_strip"]
        assert strip.get("account_basis_stale") is True
        assert strip["account_value"] == pytest.approx(composer_body(0)["portfolio_value"])

    def test_a_read_that_started_before_the_capture_is_not_the_close_totals(
        self, monkeypatch, tmp_path
    ):
        world, composer = latched_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "18:00:00"))
        # 16:00:30: the market is closed but the close step has not captured yet (16:00:42).
        refresh_at(monkeypatch, et(CAPTURE_DAY, "16:00:30"))
        set_clock(monkeypatch, et(CAPTURE_DAY, "18:00:00"))
        before_capture_read = world.poll()["portfolio_strip"]

        composer.version = 1
        refresh_at(monkeypatch, et(CAPTURE_DAY, "16:00:41"))
        set_clock(monkeypatch, et(CAPTURE_DAY, "18:00:10"))
        still_before = world.poll()["portfolio_strip"]

        assert before_capture_read.get("account_basis_stale") is True, (
            "a read that began before the capture is only the last-good totals"
        )
        assert still_before.get("account_basis_stale") is True

    def test_a_read_that_starts_in_the_capture_second_is_the_close_totals(
        self, monkeypatch, tmp_path
    ):
        world, composer = latched_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "18:00:00"))
        composer.version = 1
        refresh_at(monkeypatch, et(CAPTURE_DAY, CAPTURE_CLOCK))
        set_clock(monkeypatch, et(CAPTURE_DAY, "18:00:00"))
        composer.version = 2
        refresh_at(monkeypatch, et(CAPTURE_DAY, "18:05:00"))

        strip = world.poll()["portfolio_strip"]

        assert strip["account_value"] == pytest.approx(composer_body(1)["portfolio_value"]), (
            "the read that began exactly at the capture moment supplies the close totals"
        )
        assert not strip.get("account_basis_stale")

    @pytest.mark.parametrize(
        ("eve", "next_morning", "snapshot_day"),
        [
            (et(CAPTURE_DAY, "18:00:00"), et("2026-10-22", "08:00:00"), CAPTURE_DAY),
            (et("2026-10-23", "18:00:00"), et("2026-10-26", "08:00:00"), "2026-10-23"),
        ],
        ids=["overnight", "weekend"],
    )
    def test_the_next_morning_serves_the_figures_the_evening_served(
        self, monkeypatch, tmp_path, eve, next_morning, snapshot_day
    ):
        world, composer = latched_world(monkeypatch, tmp_path, eve, trading_day=snapshot_day)
        refresh_at(monkeypatch, eve)
        evening = world.poll()
        assert evening["market_state"] == "closed_frozen"

        # The scheduler keeps reading all night; each read lands in the live cache.
        for version, hours_later in ((1, 3), (2, 9)):
            composer.version = version
            refresh_at(monkeypatch, eve + timedelta(hours=hours_later))
        composer.version = 3
        refresh_at(monkeypatch, next_morning)
        morning = world.poll()

        assert morning["market_state"] == "pre_market"
        assert account_figures(morning) == account_figures(evening)
        assert evening["portfolio_strip"]["account_value"] == pytest.approx(
            composer_body(0)["portfolio_value"]
        )

    def test_a_new_snapshot_moves_the_figures_to_its_own_totals(self, monkeypatch, tmp_path):
        world, composer = latched_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "18:00:00"))
        refresh_at(monkeypatch, et(CAPTURE_DAY, "18:00:00"))
        wednesday = world.poll()
        composer.version = 1
        refresh_at(monkeypatch, et(CAPTURE_DAY, "20:00:00"))

        # Thursday's close step stores the next snapshot (captured 16:00:30).
        next_day = "2026-10-22"
        set_clock(monkeypatch, et(next_day, "16:05:00"))
        store_snapshot(
            world,
            snapshot_variant(
                base_snapshot(world), trading_day=next_day, captured_at_et="16:00:30 ET"
            ),
        )
        before_any_read = world.poll()
        composer.version = 2
        refresh_at(monkeypatch, et(next_day, "16:06:00"))
        after_read = world.poll()

        assert wednesday["portfolio_strip"]["account_value"] == pytest.approx(
            composer_body(0)["portfolio_value"]
        )
        assert before_any_read.get("trading_day") == next_day
        stale = before_any_read["portfolio_strip"]
        assert stale.get("account_basis_stale") is True, (
            "the new snapshot has no read after its capture yet: last-good with the stale chip"
        )
        assert stale["account_value"] == pytest.approx(composer_body(1)["portfolio_value"]), (
            "Wednesday's latched totals must not carry over to Thursday's snapshot"
        )
        fresh = after_read["portfolio_strip"]
        assert fresh["account_value"] == pytest.approx(composer_body(2)["portfolio_value"])
        assert not fresh.get("account_basis_stale")

    def test_after_a_restart_the_stored_latch_is_served_and_no_later_read_replaces_it(
        self, monkeypatch, tmp_path
    ):
        # The latch is stored when taken, so a restart (which empties every in-memory total)
        # finds it again: the figures before the restart, right after it and after each later
        # read are the same, and are the totals of the first read taken after the capture.
        world, composer = latched_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "18:00:00"))
        refresh_at(monkeypatch, et(CAPTURE_DAY, "18:00:00"))
        before_restart = world.poll()
        clean_account_totals(monkeypatch)
        after_restart = world.poll()
        composer.version = 3
        refresh_at(monkeypatch, et(CAPTURE_DAY, "21:00:00"))
        first = world.poll()
        composer.version = 4
        refresh_at(monkeypatch, et(CAPTURE_DAY, "21:30:00"))
        second = world.poll()

        assert before_restart["portfolio_strip"]["account_value"] == pytest.approx(
            composer_body(0)["portfolio_value"]
        ), "fixture sanity: the figures rest on the first read taken after the capture"
        assert after_restart["portfolio_strip"].get("basis") != "value_weighted", (
            "the stored totals are totals: the strip is not on the value-weighted floor"
        )
        assert account_figures(after_restart) == account_figures(before_restart)
        assert account_figures(first) == account_figures(before_restart)
        assert account_figures(second) == account_figures(before_restart)

    def test_with_no_totals_and_no_stored_latch_the_first_successful_read_latches(
        self, monkeypatch, tmp_path
    ):
        # The first start after a deploy: nothing stored and nothing in memory. The strip says it
        # is value weighted until a read lands; the first read after the capture is then latched
        # and held.
        world, composer = latched_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "18:00:00"))
        no_totals = world.poll()["portfolio_strip"]
        composer.version = 3
        refresh_at(monkeypatch, et(CAPTURE_DAY, "21:00:00"))
        first = world.poll()
        composer.version = 4
        refresh_at(monkeypatch, et(CAPTURE_DAY, "21:30:00"))
        second = world.poll()

        assert no_totals.get("basis") == "value_weighted", (
            "with no totals at all the strip says it is value weighted"
        )
        assert first["portfolio_strip"]["account_value"] == pytest.approx(
            composer_body(3)["portfolio_value"]
        )
        assert account_figures(second) == account_figures(first)

    def test_a_legacy_snapshot_latches_the_first_read_the_process_sees(self, monkeypatch, tmp_path):
        world, composer = latched_world(
            monkeypatch, tmp_path, et(CAPTURE_DAY, "18:00:00"), drop_trading_day=True
        )
        composer.version = 1
        refresh_at(monkeypatch, et(CAPTURE_DAY, "18:00:00"))
        first = world.poll()
        composer.version = 2
        refresh_at(monkeypatch, et(CAPTURE_DAY, "18:10:00"))
        second = world.poll()

        assert first["portfolio_strip"]["account_value"] == pytest.approx(
            composer_body(1)["portfolio_value"]
        )
        assert not first["portfolio_strip"].get("account_basis_stale")
        assert account_figures(second) == account_figures(first)

    def test_an_open_market_refresh_reads_no_stored_state(self, monkeypatch, tmp_path):
        world, _ = midday_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "10:51:00"))
        clean_account_totals(monkeypatch)
        install_composer(monkeypatch, version=0)
        reads = count_state_reads(monkeypatch)

        refresh_at(monkeypatch, et(CAPTURE_DAY, "10:51:00"))

        assert reads.count == 0, (
            "a refresh during the session must not read stored state: the close totals are "
            "only waited for after the close"
        )

    def test_once_the_close_totals_are_latched_later_refreshes_read_no_stored_state(
        self, monkeypatch, tmp_path
    ):
        world, composer = latched_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "18:00:00"))
        refresh_at(monkeypatch, et(CAPTURE_DAY, "18:00:00"))
        assert world.poll()["market_state"] == "closed_frozen"
        reads = count_state_reads(monkeypatch)
        composer.version = 1

        refresh_at(monkeypatch, et(CAPTURE_DAY, "18:10:00"))
        refresh_at(monkeypatch, et(CAPTURE_DAY, "18:20:00"))

        assert reads.count == 0, (
            "the refresh job keeps hitting the database every cycle after the latch holds "
            "the latest session's totals"
        )

    def test_the_open_market_still_reads_the_account_totals_on_every_request(
        self, monkeypatch, tmp_path
    ):
        world, _ = midday_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "10:51:00"))
        clean_account_totals(monkeypatch)
        composer = install_composer(monkeypatch, version=0)
        refresh_at(monkeypatch, et(CAPTURE_DAY, "10:51:00"))
        first = world.poll()["portfolio_strip"]
        composer.version = 1
        refresh_at(monkeypatch, et(CAPTURE_DAY, "10:51:30"))
        second = world.poll()["portfolio_strip"]

        assert first["account_value"] == pytest.approx(composer_body(0)["portfolio_value"])
        assert second["account_value"] == pytest.approx(composer_body(1)["portfolio_value"]), (
            "during the session the figures follow the account totals"
        )
        assert second["account_all_time_cr"] != first["account_all_time_cr"]
        assert expected_account_value(0) != expected_account_value(1)


# ---------------------------------------------------------------------------
# The window routes compute from the same snapshot
# ---------------------------------------------------------------------------


class TestWindowRoutesAgreeWithTheSnapshot:
    @pytest.mark.parametrize("branch", CLOSED_BRANCHES)
    @pytest.mark.parametrize("after_close_rows", [False, True], ids=["close rows", "plus 16:04"])
    def test_the_thirty_day_strip_equals_the_polls_guard_alpha(
        self, monkeypatch, tmp_path, branch, after_close_rows
    ):
        world = make_close_world(monkeypatch, tmp_path, branch)
        drift_live_state(world)
        if after_close_rows:
            seed_after_close_cycles(world)

        poll = world.poll()
        strip = world.poll("/api/strip/30d")

        assert poll["portfolio_strip"]["guard_alpha"] is not None
        assert strip["guard_alpha"] == poll["portfolio_strip"]["guard_alpha"], (
            f"{branch}: the 30d click moved the headline guard alpha"
        )

    @pytest.mark.parametrize("branch", CLOSED_BRANCHES)
    def test_a_window_strip_is_computed_from_the_snapshot_entries_not_live_state(
        self, monkeypatch, tmp_path, branch
    ):
        world = make_close_world(monkeypatch, tmp_path, branch)
        before = world.poll("/api/strip/1y")

        drift_live_state(world)
        after = world.poll("/api/strip/1y")

        changed = differences(before, after)
        assert not changed, (
            f"{branch}: live state, which differs from the snapshot, moved the strip: "
            f"{describe(changed)}"
        )

    def test_the_hero_chart_ends_on_the_snapshots_day_and_equals_the_polls_series(
        self, monkeypatch, tmp_path
    ):
        # 09:00 the next morning: the engine has already written rows of the new trading day
        # (a pre-open cycle at 08:00) while the stored snapshot is still the last session's.
        next_day = "2026-10-22"
        world, _ = close_world(monkeypatch, tmp_path, et(next_day, "09:00:00"))
        world.seed_rows(
            retime_rows(
                world.cycle_rows("m"), day=next_day, clock="08:00:05", cycle_id="20261022_0800"
            )
        )

        chart = world.poll("/api/hero-chart/30d")
        poll = world.poll()
        strip = poll["portfolio_strip"]

        assert poll["market_state"] == "pre_market"
        assert chart["hist_dates"][-1] == CAPTURE_DAY, (
            f"the chart reaches {chart['hist_dates'][-1]}, past the snapshot's day"
        )
        assert chart["hist_dates"] == strip["hist_dates"]
        assert chart["hist_bot"] == strip["hist_bot"]
        assert chart["hist_held"] == strip["hist_held"]

    def test_an_unknown_window_token_after_the_close_is_still_a_404(self, monkeypatch, tmp_path):
        world = make_close_world(monkeypatch, tmp_path, "closed_frozen")

        response = world.client.get("/api/strip/42d")

        assert response.status_code == 404
        assert response.get_json() == {"error": "unknown window token"}

    def test_the_open_market_strip_still_follows_live_state(self, monkeypatch, tmp_path):
        world, _ = midday_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "10:51:30"))
        before = world.poll("/api/strip/30d")
        drift_live_state(world)
        after = world.poll("/api/strip/30d")

        assert differences(before, after), (
            "during the session the window strip must keep reading the live positions"
        )
