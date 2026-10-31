"""A closed session with weeks of history behind it, on a virtual clock, for the tests that read
the dashboard's routes across the hours after a close.

`build_session` (test_closed_market_numbers_never_move) gives the end of one trading day: the
previous day's rows, the close cycle's rows, the close snapshot and the latched close totals. Its
world holds two days of rows, so no window edge is ever real in it. `build_history_world` adds
what a window test needs:

  * shadow rows for every trading day from `first_history_day` up to the day before the session's
    previous day, derived from the captured previous-day rows (only the day, the clock, the cycle
    id and the two return fields change; the returns follow a fixed wave, so two windows that
    start on different days give different figures);
  * one post-mortem file per trading day in a temp directory, each holding one trigger entry with
    a recognised provenance stamp, so the dollar-saved and History routes have a window to cut;
  * the virtual host clock of the clocked module, installed on the routes' own clock, the real
    XNYS calendar and the monotonic clock, and the real `analytics._window_cutoff_date` put back
    (the shared worlds pin it to the baseline date, which would hide the very fault the window
    tests look for). The standard-library `datetime.datetime` is replaced too, so a clock read
    that happens at call time (the cutoff does `from datetime import datetime` inside the
    function) sees the host, whichever way the code under test takes its date.

Provenance of the derived rows: the previous-day rows of `tests/fixtures/refresh_timing/01-c2`
(captured from the droplet), re-stamped by `retime_rows`, with `current_return` and
`shadow_return` set from `wave`. They are synthetic in those two fields and nothing asserts a value
read from them: every comparison is between two reads of the same world, or against a date
computed from the seeded day list.
"""

from __future__ import annotations

import datetime as stdlib_datetime
import json
import math
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import analytics as analytics_module
import app as app_module
import database as database_module
import market_calendar
from tests.app._refresh_timing_frozen_world import (
    SYMPHONY_IDS,
    base_snapshot,
    retime_rows,
    snapshot_variant,
)
from tests.app.test_clocked_weekend_to_monday_open import (
    ET,
    FRIDAY_SESSION,
    UTC,
    Host,
    host_datetime_class,
    utc_of,
)
from tests.app.test_closed_market_numbers_never_move import Session, build_session

# The real function, captured before any test patches it.
REAL_WINDOW_CUTOFF = analytics_module._window_cutoff_date

# Far enough back that every trailing window, the one year window included, starts inside the
# seeded days, so a window's first day is set by its cutoff and not by where the history begins.
DEFAULT_FIRST_HISTORY_DAY = date(2025, 8, 15)

WINDOW_TOKENS = ("30d", "60d", "90d", "125d", "ytd", "1y")

__all__ = [
    "ET",
    "FRIDAY_SESSION",
    "UTC",
    "WINDOW_TOKENS",
    "ClosedWorld",
    "SqlTrace",
    "Session",
    "build_history_world",
    "install_clock",
    "store_close_snapshot",
    "utc_of",
    "wave",
]


def wave(day: date, symphony_index: int) -> float:
    """A fixed per-day return, different on every day and for each symphony."""
    return round(1.8 * math.sin(day.toordinal() * 0.9 + symphony_index * 1.7), 2)


def trading_days_between(first: date, before: date) -> list[date]:
    out, day = [], first
    while day < before:
        if market_calendar.is_trading_day(day):
            out.append(day)
        day += timedelta(days=1)
    return out


def install_clock(monkeypatch, host: Host) -> None:
    """Point every clock the routes read at `host`; the window cutoff is the real function."""
    host_datetime = host_datetime_class(host)
    monkeypatch.setattr(app_module, "datetime", host_datetime)
    monkeypatch.setattr(analytics_module, "datetime", host_datetime)
    monkeypatch.setattr(stdlib_datetime, "datetime", host_datetime)
    monkeypatch.setattr(app_module, "get_market_state", market_calendar.get_market_state)
    monkeypatch.setattr(app_module, "_monotonic", host.monotonic)
    monkeypatch.setattr(analytics_module, "_window_cutoff_date", REAL_WINDOW_CUTOFF)
    # No scheduler runs in these worlds: keep its tick stamp current so the health verdict does
    # not read "scheduler_stalled" from the length of the test process.
    monkeypatch.setattr(app_module, "_scheduler_last_tick_mono", host.monotonic())


@dataclass
class ClosedWorld:
    world: object
    composer: object
    host: Host
    history_days: list[date]
    post_mortem_dir: Path
    monkeypatch: object
    session: Session

    def at(self, day: str, clock: str) -> None:
        """Move the host to an ET instant and keep the scheduler's tick stamp current."""
        self.host.move(utc_of(day, clock))
        self.monkeypatch.setattr(app_module, "_scheduler_last_tick_mono", self.host.monotonic())

    def poll(self, path: str) -> dict:
        return self.world.poll(path)

    def raw(self, path: str) -> bytes:
        response = self.world.client.get(path)
        assert response.status_code == 200, f"{path} answered {response.status_code}"
        return response.get_data()


def write_post_mortems(directory: Path, days: list[date]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for index, day in enumerate(days):
        payload = {
            "triggers": [
                {
                    "symphony_id": SYMPHONY_IDS[index % len(SYMPHONY_IDS)],
                    "symphony_name": f"Symphony {index % len(SYMPHONY_IDS)}",
                    "symphony_value": 1000.0 + index,
                    "saved_dollars": round(10.0 + 7.0 * math.sin(index), 2),
                    "saved_pct_guard_alpha": round(1.0 + math.sin(index), 3),
                    "exit_reason": "Trailing Stop",
                    "if_held_source": "shadow_history",
                    "time_triggered": "14:20",
                }
            ]
        }
        (directory / f"post_mortem_{day.isoformat()}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )


def build_history_world(
    monkeypatch,
    tmp_path,
    session: Session = FRIDAY_SESSION,
    *,
    start: tuple[str, str] | None = None,
    first_history_day: date = DEFAULT_FIRST_HISTORY_DAY,
) -> ClosedWorld:
    """The end of `session` with history behind it, the host clock at `start` (default: 16:30 ET
    on the session day)."""
    world, composer = build_session(monkeypatch, tmp_path, session)
    previous = date.fromisoformat(session.previous_day)
    days = trading_days_between(first_history_day, previous)
    template = world.previous_day_rows()
    rows: list[dict] = []
    for day in days:
        day_text = day.isoformat()
        stamped = retime_rows(
            template, day=day_text, clock="15:52:00", cycle_id=f"{day_text.replace('-', '')}_1552"
        )
        for row in stamped:
            index = SYMPHONY_IDS.index(row["symphony_id"])
            row["current_return"] = wave(day, index)
            row["shadow_return"] = (
                row["current_return"] if index == 0 else round(row["current_return"] * 0.5, 2)
            )
        rows.extend(stamped)
    world.seed_rows(rows)

    post_mortem_dir = tmp_path / "post_mortems"
    write_post_mortems(post_mortem_dir, days + [previous, date.fromisoformat(session.session_day)])
    monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(post_mortem_dir))

    day, clock = start or (session.session_day, "16:30:00")
    host = Host(utc_of(day, clock))
    install_clock(monkeypatch, host)
    return ClosedWorld(world, composer, host, days, post_mortem_dir, monkeypatch, session)


def store_close_snapshot(world, trading_day: str, captured_at_et: str = "16:00:42 ET") -> dict:
    """The engine's close step: a snapshot of `trading_day` built from the state it last
    committed (each entry's value and return are the live ones), saved into the same state.

    Derived from the signed-off snapshot record by `snapshot_variant`; only the two entry fields
    the figures rest on are taken from the live state, so the snapshot and the state agree.
    """
    state = database_module.load_state()
    snapshot = snapshot_variant(
        base_snapshot(world), trading_day=trading_day, captured_at_et=captured_at_et
    )
    for entries in snapshot["accounts_map"].values():
        for entry in entries:
            for key in ("current_value", "current_return"):
                entry[key] = state[entry["id"]][key]
    state["last_market_close_snapshot"] = snapshot
    database_module.save_state(state)
    return snapshot


# ---------------------------------------------------------------------------
# What the database was asked
# ---------------------------------------------------------------------------


@dataclass
class SqlTrace:
    """Every statement any connection in the process runs, with the thread that ran it.

    `sqlite3.connect` is wrapped for the length of the test, so a connection opened by `app`,
    `analytics` or `database` is traced the same way. `shadow_history_selects` leaves out the
    statement that builds the committed-rows view (it names the table but reads no row).
    """

    statements: list[tuple[str, str]] = field(default_factory=list)  # (thread name, sql)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def install(self, monkeypatch) -> SqlTrace:
        real_connect = sqlite3.connect
        trace = self

        class _Traced(sqlite3.Connection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.set_trace_callback(trace._record)

        def connect(*args, **kwargs):
            kwargs.setdefault("factory", _Traced)
            return real_connect(*args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", connect)
        return self

    def _record(self, sql: str) -> None:
        with self._lock:
            self.statements.append((threading.current_thread().name, " ".join(sql.split())))

    def mark(self) -> int:
        with self._lock:
            return len(self.statements)

    def shadow_history_selects(self, since: int = 0, *, thread: str | None = None) -> list[str]:
        with self._lock:
            window = self.statements[since:]
        out = []
        for thread_name, sql in window:
            head = sql.lstrip().upper()
            if "shadow_history" not in sql.lower() or head.startswith(("CREATE", "DROP")):
                continue
            if thread is not None and thread_name != thread:
                continue
            out.append(sql)
        return out
