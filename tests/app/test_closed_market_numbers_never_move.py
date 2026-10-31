"""While the markets are closed no number on the dashboard moves.

The operator's rule, in his words: "IF I SEE A NUMBER UPDATE WHEN THE MARKETS ARE CLOSED" is a
defect. This test is that rule run end to end, through the real routes, against a temp database
seeded from the committed fixture records (`_refresh_timing_frozen_world`); nothing in
`analytics` or `database` is mocked.

One session is played out. The close step stores the close snapshot, the first account-totals
refresh after the capture latches the close totals, and the clock stops just after that. The full
JSON of `/api/state`, `/api/strip/30d` and `/api/hero-chart/30d` is read, with every number on
`GET /` (the rows, headline, counts and each numeric token of the visible text). Then, step by
step, everything the droplet does after the close is done:

  * the engine writes further shadow rows (16:03, 16:04, evening, 03:00, 08:30) and saves
    `bot_state` again each time, with every symphony's live value and return moved to new numbers;
  * the account-totals refresh runs after the latch, each time against a Composer body whose
    figures have moved;
  * the clock advances through the evening, past midnight and overnight to 09:29:59 ET.

After every step all four reads are taken again. Each leaf of each JSON response and each number
of the page must equal the baseline. The fields in `CLOCK_FIELDS` are the only ones allowed to
differ; each is a clock or a clock-derived label, named with the reason. The same session is then
repeated across Sunday 2026-11-01 02:00 ET, when daylight saving ends and 01:00 to 01:59 ET
happens twice.

Expected values are never typed in: the baseline is the response itself, and the checks that a
step really landed read the database and the refresh cache.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import app as app_module
import database as database_module
from tests.app._refresh_timing_card_world import exit_row_record
from tests.app._refresh_timing_frozen_world import (
    PREVIOUS_SESSION_SUFFIX,
    SYMPHONY_IDS,
    base_snapshot,
    clean_account_totals,
    clocked_world,
    composer_body,
    cycle_stamp,
    describe,
    differences,
    et,
    install_composer,
    refresh_at,
    retime_rows,
    save_state,
    set_clock,
    snapshot_variant,
    stored_snapshot,
    visible_text,
)
from tests.app._refresh_timing_state_world import (
    TRIGGERED_ID,
    World,
    rendered_page_figures,
)

UTC = ZoneInfo("UTC")
ET = ZoneInfo("America/New_York")

# Fields that legitimately differ between two reads taken at different moments after the close.
# Each is a clock reading or the label of the market state
# the clock is in. A figure never belongs here: adding a path is a decision the reviewer sees.
# Paths are prefixed by the response they are in: state, strip, chart.
CLOCK_FIELDS = {
    "state.market_state": "the label of the clock's market state (closed_frozen, then pre_market)",
    "state.meta.market_state": "the same label under meta",
    "state.meta.clock_et": "the request's wall clock in ET",
}

# The exit row of the session, written when the stop fired at 15:02 ET.
EXIT_CLOCK = "15:02:07"
CLOSE_ROWS_CLOCK = "16:00:30"
CAPTURE_AT = "16:00:42 ET"
REFRESH_STEP_VALUE = 1

NUMBER_TOKEN = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?%?")


@dataclass(frozen=True)
class Step:
    when: datetime
    kind: str  # "read", "engine" or "refresh"
    label: str


def local(day: str, clock: str, *, fold: int = 0) -> datetime:
    """An ET instant; `fold=1` selects the second occurrence of a repeated autumn hour."""
    return et(day, clock).replace(fold=fold)


@dataclass(frozen=True)
class Session:
    name: str
    session_day: str
    previous_day: str
    baseline: datetime
    steps: tuple[Step, ...]


WEDNESDAY_TO_THURSDAY = Session(
    name="wednesday session to thursday's open",
    session_day="2026-10-21",
    previous_day="2026-10-20",
    baseline=local("2026-10-21", "16:02:00"),
    steps=(
        Step(local("2026-10-21", "16:03:05"), "engine", "engine row 16:03"),
        Step(local("2026-10-21", "16:03:30"), "refresh", "account refresh 16:03"),
        Step(local("2026-10-21", "16:04:05"), "engine", "engine row 16:04"),
        Step(local("2026-10-21", "16:04:30"), "refresh", "account refresh 16:04"),
        Step(local("2026-10-21", "16:30:00"), "read", "half past four"),
        Step(local("2026-10-21", "20:00:05"), "engine", "engine row 20:00"),
        Step(local("2026-10-21", "20:00:30"), "refresh", "account refresh 20:00"),
        Step(local("2026-10-21", "23:59:59"), "read", "last second of the day"),
        Step(local("2026-10-22", "00:00:01"), "read", "first second of the next day"),
        Step(local("2026-10-22", "03:00:05"), "engine", "engine row 03:00"),
        Step(local("2026-10-22", "03:00:30"), "refresh", "account refresh 03:00"),
        Step(local("2026-10-22", "04:00:00"), "read", "pre-market hour"),
        Step(local("2026-10-22", "08:30:05"), "engine", "engine row 08:30"),
        Step(local("2026-10-22", "08:30:30"), "refresh", "account refresh 08:30"),
        Step(local("2026-10-22", "09:29:00"), "read", "09:29"),
        Step(local("2026-10-22", "09:29:59"), "read", "last second before the open"),
    ),
)

FRIDAY_ACROSS_THE_CLOCK_CHANGE = Session(
    name="friday session across the november clock change to monday's open",
    session_day="2026-10-30",
    previous_day="2026-10-29",
    baseline=local("2026-10-30", "16:02:00"),
    steps=(
        Step(local("2026-10-30", "16:03:05"), "engine", "engine row 16:03"),
        Step(local("2026-10-30", "16:03:30"), "refresh", "account refresh 16:03"),
        Step(local("2026-10-30", "16:04:05"), "engine", "engine row 16:04"),
        Step(local("2026-10-30", "16:04:30"), "refresh", "account refresh 16:04"),
        Step(local("2026-10-30", "20:00:05"), "engine", "engine row Friday evening"),
        Step(local("2026-10-30", "20:00:30"), "refresh", "account refresh Friday evening"),
        Step(local("2026-10-31", "03:00:05"), "engine", "engine row Saturday 03:00"),
        Step(local("2026-10-31", "03:00:30"), "refresh", "account refresh Saturday 03:00"),
        Step(local("2026-11-01", "01:30:05"), "engine", "engine row 01:30 daylight time"),
        Step(local("2026-11-01", "01:59:59"), "read", "01:59:59 daylight time"),
        Step(
            local("2026-11-01", "01:00:00", fold=1),
            "read",
            "01:00:00 standard time, the clock went back",
        ),
        Step(
            local("2026-11-01", "01:30:05", fold=1),
            "engine",
            "engine row 01:30 standard time, the second 01:30",
        ),
        Step(
            local("2026-11-01", "01:30:30", fold=1),
            "refresh",
            "account refresh 01:30 standard time",
        ),
        Step(local("2026-11-01", "02:00:00"), "read", "02:00:00, the instant of the change"),
        Step(local("2026-11-01", "02:00:30"), "refresh", "account refresh 02:00"),
        Step(local("2026-11-01", "03:00:05"), "engine", "engine row Sunday 03:00"),
        Step(local("2026-11-01", "12:00:00"), "read", "Sunday noon"),
        Step(local("2026-11-02", "03:00:05"), "engine", "engine row Monday 03:00"),
        Step(local("2026-11-02", "03:00:30"), "refresh", "account refresh Monday 03:00"),
        Step(local("2026-11-02", "08:30:05"), "engine", "engine row Monday 08:30"),
        Step(local("2026-11-02", "08:30:30"), "refresh", "account refresh Monday 08:30"),
        Step(local("2026-11-02", "09:29:00"), "read", "09:29 standard time"),
        Step(local("2026-11-02", "09:29:59"), "read", "last second before the open"),
    ),
)

SESSIONS = (WEDNESDAY_TO_THURSDAY, FRIDAY_ACROSS_THE_CLOCK_CHANGE)


# ---------------------------------------------------------------------------
# Building the session
# ---------------------------------------------------------------------------


def seed_exit_row_on(day: str) -> None:
    """The captured exit row of the triggered symphony, written through the real writer on `day`."""
    row = exit_row_record()
    moment = et(day, EXIT_CLOCK)
    row_id = database_module.record_exit_trigger(
        symphony_id=TRIGGERED_ID,
        account_id=row["account_id"],
        triggered_reason=row["triggered_reason"],
        at_return=row["at_return"],
        cycle_id=f"{day.replace('-', '')}_1502",
        ts_utc=moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ts_et=moment.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    assert row_id is not None, "the real exit-trigger writer refused the exit row"


def build_session(monkeypatch, tmp_path, session: Session):
    """The end of a trading day as the droplet holds it, with the close totals latched.

    Previous-day rows, the close cycle's rows before the capture, the exit row, the close
    snapshot captured at 16:00:42, live `bot_state` at the close cycle, and one account-totals
    refresh after the capture (the first read after the capture is the close's own totals).
    """
    world = clocked_world(monkeypatch, tmp_path, session.baseline)
    world.seed_rows(
        retime_rows(
            world.previous_day_rows(),
            day=session.previous_day,
            clock="15:52:00",
            cycle_id=f"{session.previous_day.replace('-', '')}_1552",
        )
    )
    world.seed_rows(
        retime_rows(
            world.cycle_rows("m_minus_1"),
            day=session.session_day,
            clock=CLOSE_ROWS_CLOCK,
            cycle_id=f"{session.session_day.replace('-', '')}_1600",
        )
    )
    seed_exit_row_on(session.session_day)
    snapshot = snapshot_variant(
        base_snapshot(world), trading_day=session.session_day, captured_at_et=CAPTURE_AT
    )
    save_state(
        world,
        "m",
        snapshot=snapshot,
        stamp=cycle_stamp(session.session_day, CLOSE_ROWS_CLOCK),
    )
    clean_account_totals(monkeypatch)
    composer = install_composer(monkeypatch, version=0)
    refresh_at(monkeypatch, local(session.session_day, "16:01:00"))
    assert composer.calls == 1, "the close totals come from one refresh after the capture"
    set_clock(monkeypatch, session.baseline)
    return world, composer


# ---------------------------------------------------------------------------
# What the droplet does after the close
# ---------------------------------------------------------------------------


def write_engine_cycle(world: World, when: datetime, number: int) -> int:
    """One engine cycle at `when`: new shadow rows (return moved on), then `bot_state` saved
    with the cycle's stamp, the close snapshot untouched and each symphony's live value and
    return moved to new numbers. Returns how many shadow rows the database holds afterwards."""
    wall = when.astimezone(ET)
    day, clock = wall.strftime("%Y-%m-%d"), wall.strftime("%H:%M:%S")
    zone_tag = when.astimezone(UTC).strftime("%Y%m%d_%H%MZ")
    rows = retime_rows(world.cycle_rows("m"), day=day, clock=clock, cycle_id=zone_tag)
    written = when + timedelta(seconds=3)
    for row in rows:
        # retime_rows reads the wall clock, which is ambiguous in the repeated hour: set the
        # instant the writer would stamp from the aware time.
        row["ts_utc"] = written.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        row["current_return"] += 0.25 * number
        row["shadow_return"] += 0.25 * number
    world.seed_rows(rows)

    live = world._records["live"]["m"]["entries"]
    overrides = {
        sym_id: {
            "current_value": live[sym_id]["current_value"] * (1.0 + 0.5 * number),
            "current_return": live[sym_id]["current_return"] + 1.5 * number,
        }
        for sym_id in SYMPHONY_IDS
    }
    stamp = when.replace(microsecond=413198).isoformat()
    save_state(world, "m", snapshot=stored_snapshot(), stamp=stamp, entry_overrides=overrides)

    saved = database_module.load_state()
    assert saved["last_successful_cycle_at"] == stamp, "the engine's state save did not land"
    assert saved[TRIGGERED_ID]["current_value"] == pytest.approx(
        overrides[TRIGGERED_ID]["current_value"]
    ), "the engine's live value did not land in bot_state"
    return len(rows)


def run_refresh(monkeypatch, composer, when: datetime) -> None:
    """The account-totals job at `when`, against a Composer body whose figures have moved."""
    composer.version += REFRESH_STEP_VALUE
    refresh_at(monkeypatch, when)
    assert app_module._account_totals_cache["portfolio_value"] == pytest.approx(
        composer_body(composer.version)["portfolio_value"]
    ), "the refresh must reach the cache for this test to mean anything"


# ---------------------------------------------------------------------------
# Reading everything an operator can see
# ---------------------------------------------------------------------------


def read_everything(world: World) -> dict:
    """The full JSON of the three routes and every number on the page."""
    page = world.page()
    return {
        "state": world.poll("/api/state"),
        "strip": world.poll("/api/strip/30d"),
        "chart": world.poll("/api/hero-chart/30d"),
        "page": {
            "figures": rendered_page_figures(page),
            "numbers": NUMBER_TOKEN.findall(visible_text(page)),
        },
    }


def moved_figures(baseline: dict, current: dict) -> dict[str, tuple]:
    """Every leaf that differs from the baseline and is not a named clock field."""
    return {
        path: pair
        for path, pair in differences(baseline, current).items()
        if path not in CLOCK_FIELDS
    }


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("session", SESSIONS, ids=[s.name for s in SESSIONS])
def test_no_number_moves_from_the_close_to_the_next_open(monkeypatch, tmp_path, session):
    world, composer = build_session(monkeypatch, tmp_path, session)
    baseline = read_everything(world)
    state = baseline["state"]

    # The baseline must be the closed day's figures, or the comparison proves nothing.
    assert state["market_state"] == "closed_frozen"
    assert state.get("trading_day") == session.session_day
    assert PREVIOUS_SESSION_SUFFIX not in str(state.get("data_as_of"))
    strip = state["portfolio_strip"]
    assert strip.get("account_value") is not None, "the close totals must be latched"
    assert strip.get("guard_alpha") is not None, "the exit row must give the headline a figure"
    assert baseline["chart"]["hist_dates"], "the hero chart must have points to compare"
    assert baseline["strip"], "the 30 day strip must answer"
    assert baseline["page"]["numbers"], "the page must show numbers"
    assert baseline["page"]["figures"]["guard_alpha"] is not None

    failures: list[str] = []
    engine_cycles = 0
    for step in session.steps:
        set_clock(monkeypatch, step.when)
        if step.kind == "engine":
            engine_cycles += 1
            write_engine_cycle(world, step.when, engine_cycles)
        elif step.kind == "refresh":
            run_refresh(monkeypatch, composer, step.when)
        current = read_everything(world)
        changed = moved_figures(baseline, current)
        if changed:
            failures.append(f"{step.label} ({step.when.isoformat()}): {describe(changed, 8)}")

    assert not failures, (
        f"{session.name}: numbers moved while the markets were closed:\n" + "\n".join(failures)
    )
    assert engine_cycles >= 4 and composer.version >= 4, "the steps must have been played"
