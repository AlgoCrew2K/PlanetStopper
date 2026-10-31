"""`/api/state` and `GET /` stamp the day and moment of their data, never the request's.

Every freshness stamp reads `YYYY-MM-DD HH:MM ET`, or `--` when no moment exists. After the
close the stamp names the close snapshot's capture moment and flags a snapshot that is not
the most recent finished session; during the day it names the newest recorded observation
of the completed engine cycle, not the cycle stamp (the engine stamps that even when every
Composer fetch failed) and not the request time.

The routes run against a real temp database seeded from the committed fixture records
(`_refresh_timing_frozen_world`); the XNYS calendar decides the market state. Expected
stamps are built from the inputs each test seeds.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import analytics as analytics_module
import app as app_module
from tests.app._refresh_timing_frozen_world import (
    CAPTURE_DAY,
    NO_STAMP,
    PREVIOUS_DAY,
    PREVIOUS_SESSION_SUFFIX,
    all_stamps,
    clean_account_totals,
    clocked_world,
    cycle_stamp,
    et,
    install_composer,
    install_monotonic,
    midday_world,
    observation_moment,
    refresh_at,
    retime_rows,
    save_state,
    set_clock,
    snapshot_world,
    stamp_text,
)
from tests.app._refresh_timing_state_world import (
    load_record,
    rendered_page_figures,
    seeded_world,
)

APP_SOURCE = Path(app_module.__file__)


def close_stamp(trading_day: str, hhmm: str = "16:00", *, previous: bool = False) -> str:
    return f"{trading_day} {hhmm} ET" + (PREVIOUS_SESSION_SUFFIX if previous else "")


def assert_every_stamp(body: dict, expected: str, context: str) -> None:
    stamps = all_stamps(body)
    wrong = {name: value for name, value in stamps.items() if value != expected}
    assert not wrong, f"{context}: expected every stamp to read {expected!r}, got {wrong}"


# ---------------------------------------------------------------------------
# The frozen branch states its trading day
# ---------------------------------------------------------------------------


class TestFrozenBranchStatesItsTradingDay:
    def test_evening_of_the_session_names_the_day_and_the_capture_second(
        self, monkeypatch, tmp_path
    ):
        world = snapshot_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "18:00:00"))

        body = world.poll()
        page = rendered_page_figures(world.page())

        assert body["market_state"] == "closed_frozen"
        assert body.get("trading_day") == CAPTURE_DAY
        assert body["frozen_at"] == f"{CAPTURE_DAY} 16:00:42 ET"
        assert body.get("snapshot_is_previous_session") is False
        assert_every_stamp(body, close_stamp(CAPTURE_DAY), "evening of the session")
        assert page["data_as_of"] == close_stamp(CAPTURE_DAY), (
            "the hero's as-of text must read the same dated stamp as the poll"
        )

    def test_before_the_close_step_commits_the_session_that_just_closed_is_served(
        self, monkeypatch, tmp_path
    ):
        # 16:00:20: the session is over but the close step has not stored today's snapshot. The
        # page serves the last committed state of the session that closed, never the earlier
        # snapshot, and the ordinary gap is not yet a missing snapshot.
        world = snapshot_world(
            monkeypatch, tmp_path, et(CAPTURE_DAY, "16:00:20"), trading_day=PREVIOUS_DAY
        )

        body = world.poll()

        assert body["market_state"] == "closed_frozen"
        assert body.get("trading_day") == CAPTURE_DAY
        assert body.get("snapshot_is_previous_session") is False
        assert_every_stamp(
            body, close_stamp(CAPTURE_DAY), "session that just closed, snapshot not yet stored"
        )
        assert "close_snapshot_missing" not in body["degraded_reasons"]

    def test_the_last_finished_session_carries_no_marker_on_monday_morning(
        self, monkeypatch, tmp_path
    ):
        world = snapshot_world(
            monkeypatch, tmp_path, et("2026-10-26", "08:00:00"), trading_day="2026-10-23"
        )

        body = world.poll()

        assert body["market_state"] == "pre_market"
        assert body.get("trading_day") == "2026-10-23"
        assert body.get("snapshot_is_previous_session") is False
        assert_every_stamp(body, close_stamp("2026-10-23"), "Monday pre-market, Friday close")

    def test_a_missed_session_is_flagged_even_on_a_weekday_morning(self, monkeypatch, tmp_path):
        # Tuesday morning holding Friday's snapshot: Monday's session finished and was never stored.
        world = snapshot_world(
            monkeypatch, tmp_path, et("2026-10-27", "08:00:00"), trading_day="2026-10-23"
        )

        body = world.poll()

        assert body.get("snapshot_is_previous_session") is True
        assert_every_stamp(
            body, close_stamp("2026-10-23", previous=True), "Tuesday morning, Friday close"
        )

    @pytest.mark.parametrize(
        ("now_day", "clock", "snapshot_day"),
        [
            ("2026-11-26", "18:00:00", "2026-11-25"),  # Thanksgiving evening
            ("2027-01-18", "12:00:00", "2027-01-15"),  # Monday holiday midday
        ],
        ids=["thanksgiving evening", "monday holiday"],
    )
    def test_a_holiday_does_not_make_the_session_before_it_stale(
        self, monkeypatch, tmp_path, now_day, clock, snapshot_day
    ):
        world = snapshot_world(monkeypatch, tmp_path, et(now_day, clock), trading_day=snapshot_day)

        body = world.poll()

        assert body["market_state"] == "closed_frozen"
        assert body.get("trading_day") == snapshot_day
        assert body.get("snapshot_is_previous_session") is False
        assert_every_stamp(body, close_stamp(snapshot_day), f"{now_day} holiday")

    def test_a_snapshot_without_a_trading_day_says_the_date_was_not_recorded(
        self, monkeypatch, tmp_path
    ):
        world = snapshot_world(
            monkeypatch, tmp_path, et(CAPTURE_DAY, "18:00:00"), drop_trading_day=True
        )

        body = world.poll()
        page = rendered_page_figures(world.page())

        assert "trading_day" in body and body["trading_day"] is None
        assert body.get("snapshot_is_previous_session") is False
        assert_every_stamp(body, "16:00 ET (date not recorded)", "legacy snapshot")
        assert body["frozen_at"] == "16:00:42 ET", (
            "a legacy snapshot has no date to add; its stored capture time stays as stored"
        )
        assert page["data_as_of"] == "16:00 ET (date not recorded)"

    @pytest.mark.parametrize(
        ("day", "clock"),
        [(CAPTURE_DAY, "18:00:00"), ("2026-10-22", "08:00:00")],
        ids=["closed_frozen", "pre_market"],
    )
    def test_no_snapshot_yet_returns_the_waiting_response_with_its_notice(
        self, monkeypatch, tmp_path, day, clock
    ):
        world = clocked_world(monkeypatch, tmp_path, et(day, clock))

        body = world.poll()

        assert body["status"] == "waiting"
        assert body.get("frozen_at") is None
        assert "No closing snapshot yet" in body.get("notice", "")
        assert body["portfolio_strip"]["data_as_of"] == NO_STAMP, (
            "with no state and no observation the stamp must read '--', not the request time"
        )

    @pytest.mark.parametrize("clock", ["17:00:00", "19:30:00", "23:30:00"])
    def test_a_missing_session_snapshot_serves_the_closed_session_and_says_so_on_every_poll(
        self, monkeypatch, tmp_path, clock
    ):
        # The daemon was down across today's close, so only yesterday's snapshot is stored. The
        # figures are the closed session's last committed state, held all evening, and the poll
        # states that the snapshot is missing.
        world = snapshot_world(
            monkeypatch, tmp_path, et(CAPTURE_DAY, clock), trading_day=PREVIOUS_DAY
        )

        body = world.poll()

        assert body.get("trading_day") == CAPTURE_DAY, clock
        assert body.get("snapshot_is_previous_session") is False, clock
        assert_every_stamp(body, close_stamp(CAPTURE_DAY), clock)
        assert "close_snapshot_missing" in body["degraded_reasons"], clock
        assert (
            "close_snapshot_missing" in world.client.get("/health").get_json()["degraded_reasons"]
        ), clock

    def test_half_day_before_the_close_step_commits_flags_the_earlier_snapshot(
        self, monkeypatch, tmp_path
    ):
        # 2026-11-27 closes at 13:00; 13:00:20 is after the close and before the close step.
        world = snapshot_world(
            monkeypatch, tmp_path, et("2026-11-27", "13:00:20"), trading_day="2026-11-25"
        )

        body = world.poll()

        assert body["market_state"] == "closed_frozen"
        assert body.get("snapshot_is_previous_session") is True
        assert_every_stamp(
            body, close_stamp("2026-11-25", previous=True), "half day, snapshot not yet stored"
        )

    def test_half_day_with_its_own_snapshot_carries_no_marker(self, monkeypatch, tmp_path):
        world = snapshot_world(
            monkeypatch,
            tmp_path,
            et("2026-11-27", "13:30:00"),
            trading_day="2026-11-27",
            captured_at_et="13:00:42 ET",
        )

        body = world.poll()

        assert body.get("snapshot_is_previous_session") is False
        assert_every_stamp(body, close_stamp("2026-11-27", "13:00"), "half day close")

    def test_polls_ten_minutes_apart_on_one_snapshot_carry_identical_date_fields(
        self, monkeypatch, tmp_path
    ):
        world = snapshot_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "18:00:00"))
        first = world.poll()
        set_clock(monkeypatch, et(CAPTURE_DAY, "18:10:00"))
        second = world.poll()

        fields = ("trading_day", "frozen_at", "data_as_of", "snapshot_is_previous_session")
        assert {k: first.get(k) for k in fields} == {k: second.get(k) for k in fields}
        assert all_stamps(first) == all_stamps(second)
        assert first["trading_day"] == CAPTURE_DAY, "the fields compared must exist"

    @pytest.mark.parametrize("branch", ["closed_frozen", "pre_market"])
    def test_only_the_date_fields_and_the_marker_are_added_to_the_frozen_response(
        self, monkeypatch, tmp_path, branch
    ):
        golden = load_record(f"golden_ref_api_state_{branch}.json")
        world = seeded_world(monkeypatch, tmp_path, branch)

        body = world.poll()

        added = set(body) - {"html"} - set(golden)
        # engine_phase, symphonies, degraded_reasons and dashboard_panels are later, separately
        # specified additive fields: the engine's phase label, the frozen per-symphony cards, the
        # daemon's degraded-state reasons and the Sleeves, CVaR and locked-variables panels.
        expected_added = {
            "trading_day",
            "snapshot_is_previous_session",
            "engine_phase",
            "symphonies",
            "degraded_reasons",
            "dashboard_panels",
        }
        assert added == expected_added, (
            f"{branch}: fields added to the frozen response: {sorted(added)}"
        )
        for key in ("state", "bot_state", "accounts_map", "shadow_divergence", "live_mode"):
            assert body[key] == golden[key], f"{branch}: {key} changed besides the date fields"
        served = body["portfolio_strip"]
        changed = {
            k: (expected, served.get(k))
            for k, expected in golden["portfolio_strip"].items()
            if k != "data_as_of" and served.get(k) != expected
        }
        assert not changed, f"{branch}: strip figures changed (reference, served): {changed}"


# ---------------------------------------------------------------------------
# The frozen stamp is the snapshot's moment
# ---------------------------------------------------------------------------


class TestFrozenStampIsTheSnapshotsMoment:
    def test_nine_pm_still_names_the_capture_not_the_request(self, monkeypatch, tmp_path):
        world = snapshot_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "21:00:00"))

        body = world.poll()

        assert_every_stamp(body, close_stamp(CAPTURE_DAY), "21:00 request")

    def test_seven_am_next_morning_names_the_close_of_the_day_before(self, monkeypatch, tmp_path):
        world = snapshot_world(monkeypatch, tmp_path, et("2026-10-22", "07:00:00"))

        body = world.poll()

        assert body["market_state"] == "pre_market"
        assert body.get("snapshot_is_previous_session") is False
        assert_every_stamp(body, close_stamp(CAPTURE_DAY), "07:00 next morning")


# ---------------------------------------------------------------------------
# The live stamp is the newest observation of the completed cycle
# ---------------------------------------------------------------------------


class TestLiveStampNamesTheNewestObservation:
    def test_stamp_is_the_date_and_minute_of_the_newest_recorded_row(self, monkeypatch, tmp_path):
        world, seeded = midday_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "10:51:30"))
        expected = stamp_text(observation_moment(seeded["m"][0]))

        body = world.poll()
        page = rendered_page_figures(world.page())

        assert body["market_state"] == "open"
        assert_every_stamp(body, expected, "newest row of the completed cycle")
        assert page["data_as_of"] == expected
        assert body["last_successful_cycle_at"] == cycle_stamp(CAPTURE_DAY, "10:43:05"), (
            "the header's liveness check still reads the engine's cycle stamp"
        )

    @pytest.mark.parametrize(
        "state_stamp",
        [None, cycle_stamp(CAPTURE_DAY, "10:50:00")],
        ids=["stamp is the newer cycle's", "stamp is minutes later"],
    )
    def test_cycles_that_recorded_nothing_do_not_advance_the_stamp(
        self, monkeypatch, tmp_path, state_stamp
    ):
        kwargs = {} if state_stamp is None else {"state_stamp": state_stamp}
        world, seeded = midday_world(
            monkeypatch,
            tmp_path,
            et(CAPTURE_DAY, "10:51:30"),
            cycles=("m_minus_1",),
            **kwargs,
        )
        expected = stamp_text(observation_moment(seeded["m_minus_1"][0]))

        body = world.poll()

        assert_every_stamp(body, expected, "the engine stamped later cycles that fetched nothing")

    def test_with_no_observation_and_no_cycle_time_every_stamp_reads_two_dashes(
        self, monkeypatch, tmp_path
    ):
        world = clocked_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "10:51:30"))
        save_state(world, "m", stamp=None)

        body = world.poll()
        page = rendered_page_figures(world.page())

        assert body["market_state"] == "open"
        assert_every_stamp(body, NO_STAMP, "nothing recorded yet")
        assert page["data_as_of"] == NO_STAMP, "the hero reads 'data as of --'"

    def test_an_unparseable_cycle_time_falls_back_to_the_newest_observation(
        self, monkeypatch, tmp_path
    ):
        world, seeded = midday_world(
            monkeypatch,
            tmp_path,
            et(CAPTURE_DAY, "10:51:30"),
            state_stamp="not-a-timestamp",
        )
        expected = stamp_text(observation_moment(seeded["m"][0]))

        body = world.poll()

        assert body["market_state"] == "open"
        assert_every_stamp(body, expected, "unparseable cycle stamp, rows present")

    def test_an_unparseable_cycle_time_with_no_observation_reads_two_dashes(
        self, monkeypatch, tmp_path
    ):
        world = clocked_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "10:51:30"))
        save_state(world, "m", stamp="not-a-timestamp")

        body = world.poll()

        assert_every_stamp(body, NO_STAMP, "unparseable cycle stamp, no rows")

    def test_a_failing_portfolio_computation_reads_two_dashes_not_the_request_time(
        self, monkeypatch, tmp_path
    ):
        world, _ = midday_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "10:51:30"))

        def boom(*_args, **_kwargs):
            raise RuntimeError("analytics unavailable")

        monkeypatch.setattr(analytics_module, "get_portfolio_today_change", boom)

        body = world.poll()

        assert body["market_state"] == "open"
        assert_every_stamp(body, NO_STAMP, "computation raised")

    def test_before_the_first_cycle_of_the_day_the_stamp_names_yesterdays_last_row(
        self, monkeypatch, tmp_path
    ):
        # 09:30:05: the overnight cycles stamped the state, but the newest recorded row is
        # yesterday's 15:52.
        world, seeded = midday_world(
            monkeypatch,
            tmp_path,
            et(CAPTURE_DAY, "09:30:05"),
            cycles=(),
            previous_day_clock="15:52:03",
            state_stamp=cycle_stamp(CAPTURE_DAY, "09:29:02"),
        )
        expected = stamp_text(observation_moment(seeded["previous_day"][0]))
        assert expected.startswith(PREVIOUS_DAY), "the expected stamp names the earlier day"

        body = world.poll()

        assert body["market_state"] == "open"
        assert_every_stamp(body, expected, "before the first cycle of the day")

    def test_an_observation_just_before_midnight_keeps_its_own_date_after_midnight(
        self, monkeypatch, tmp_path
    ):
        next_day = "2026-10-22"
        world = clocked_world(monkeypatch, tmp_path, et(next_day, "00:01:00"))
        late = retime_rows(
            world.previous_day_rows(), day=CAPTURE_DAY, clock="23:59:30", cycle_id="20261021_2359"
        )
        world.seed_rows(late)
        save_state(world, "m", stamp=cycle_stamp(next_day, "00:00:02"))
        expected = stamp_text(observation_moment(late[0]))
        assert expected.startswith(CAPTURE_DAY)

        body = world.poll()

        assert body["market_state"] == "pre_market"
        assert_every_stamp(body, expected, "00:01 request, 23:59 observation")

    def test_requests_in_one_minute_and_beyond_with_no_new_row_carry_identical_stamps(
        self, monkeypatch, tmp_path
    ):
        world, seeded = midday_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "10:51:05"))
        expected = stamp_text(observation_moment(seeded["m"][0]))
        seen = []
        for clock in ("10:51:05", "10:51:50", "10:55:00"):
            set_clock(monkeypatch, et(CAPTURE_DAY, clock))
            seen.append(all_stamps(world.poll()))

        for stamps in seen:
            assert set(stamps.values()) == {expected}, stamps

    def test_a_cycle_whose_rows_are_committed_but_whose_state_is_not_is_not_stamped(
        self, monkeypatch, tmp_path
    ):
        # Minute M's rows are in (10:44:05) and its state is not: the response shows M-1's
        # values, so its stamp names M-1's newest row (10:43:05).
        world, seeded = midday_world(
            monkeypatch,
            tmp_path,
            et(CAPTURE_DAY, "10:44:30"),
            newest_cycle_clock="10:44:05",
            state_label="m_minus_1",
        )
        expected = stamp_text(observation_moment(seeded["m_minus_1"][0]))
        assert expected.endswith("10:43 ET")
        assert stamp_text(observation_moment(seeded["m"][0])).endswith("10:44 ET")

        body = world.poll()

        assert_every_stamp(body, expected, "mid-cycle")


# ---------------------------------------------------------------------------
# The account-basis stamp keeps its own form but never the request clock
# ---------------------------------------------------------------------------


class TestAccountBasisStamp:
    def test_the_account_basis_as_of_keeps_its_seconds_form(self, monkeypatch, tmp_path):
        world, _ = midday_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "10:51:30"))
        clean_account_totals(monkeypatch)
        monotonic = install_monotonic(monkeypatch)
        install_composer(monkeypatch, version=0)
        refresh_at(monkeypatch, et(CAPTURE_DAY, "10:41:07"))
        set_clock(monkeypatch, et(CAPTURE_DAY, "10:51:30"))
        monotonic.now += 600.0
        app_module._account_totals_cache.mark_stale()

        strip = world.poll()["portfolio_strip"]

        assert strip.get("account_basis_stale") is True
        assert strip.get("account_basis_as_of") == stamp_text(
            et(CAPTURE_DAY, "10:41:07"), seconds=True
        )

    @pytest.mark.parametrize("branch", ["open", "closed_frozen"])
    def test_a_stale_chip_with_no_recorded_read_time_never_borrows_the_request_time(
        self, monkeypatch, tmp_path, branch
    ):
        if branch == "open":
            world, _ = midday_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "10:51:30"))
        else:
            world = snapshot_world(monkeypatch, tmp_path, et(CAPTURE_DAY, "18:00:00"))
        clean_account_totals(monkeypatch)
        # Last-good totals exist (restored by hand) but no read time was ever recorded.
        monkeypatch.setattr(
            app_module,
            "_account_totals_last_good",
            {"portfolio_value": 12000.0, "portfolio_cr": 50.0, "portfolio_tc": -0.5},
        )

        strip = world.poll()["portfolio_strip"]

        assert strip.get("account_basis_stale") is True, branch
        assert strip.get("account_basis_as_of") == NO_STAMP, (
            f"{branch}: with no recorded read time the chip's as-of must read '--'"
        )


# ---------------------------------------------------------------------------
# Source sweep: no stamp is written outside the one helper
# ---------------------------------------------------------------------------


class TestNoStampIsWrittenOutsideTheHelper:
    @staticmethod
    def _tree() -> ast.AST:
        return ast.parse(APP_SOURCE.read_text(encoding="utf-8"))

    def test_the_helper_exists(self):
        assert hasattr(app_module, "_format_data_stamp"), "app._format_data_stamp is missing"

    def test_no_route_builds_a_time_only_stamp_of_its_own(self):
        tree = self._tree()
        helper = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "_format_data_stamp"
            ),
            None,
        )
        inside = set() if helper is None else {id(n) for n in ast.walk(helper)}
        # The header's "closes at 16:00 ET" label is a schedule time, not a freshness stamp.
        for assign in ast.walk(tree):
            if isinstance(assign, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "session_close_display"
                for target in assign.targets
            ):
                inside |= {id(n) for n in ast.walk(assign)}
        hits = [
            (node.lineno, node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "%H:%M ET" in node.value
            and id(node) not in inside
        ]
        assert not hits, (
            "time-only '%H:%M ET' stamp formats remain outside _format_data_stamp "
            f"(line, text): {hits}"
        )

    def test_the_account_basis_stamp_has_no_request_clock_fallback(self):
        # The account-basis form is a recorded read time, written once when a read succeeds.
        # A fallback such as `x or datetime.now(...).strftime(_ACCOUNT_BASIS_TS_FMT)` would
        # present the request time as the time of a read.
        lines = APP_SOURCE.read_text(encoding="utf-8").splitlines()
        fallbacks = []
        for node in ast.walk(self._tree()):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "strftime"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "_ACCOUNT_BASIS_TS_FMT"
            ):
                # A call's line number is where its whole expression starts, so a fallback
                # (`x or datetime.now(...)`) shows its `or` on that line.
                line = lines[node.lineno - 1]
                if " or " in line or " else " in line:
                    fallbacks.append((node.lineno, line.strip()))
        assert not fallbacks, f"account-basis stamp falls back to the request clock: {fallbacks}"
