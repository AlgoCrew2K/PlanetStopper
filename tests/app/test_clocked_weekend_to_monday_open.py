"""The two failures the operator named cannot happen: a number moving while the markets are closed,
and the refresh cycle not starting in the morning.

Real code, one virtual clock. The droplet's host clock is UTC (a production fact the
refresh-timing plans record), so the scheduler's naive `datetime.now()` reads UTC wall time while every route reads ET.
One `Host` holds the instant and drives, together:

  * the real `app.run_scheduler` registrations and `app._scheduler_tick` (the `schedule` module's
    clock is the host's naive UTC wall clock). The five daily slots and the minute account-refresh
    slot are replaced by recorders; `threaded_trigger` is wrapped by a recorder that still calls the
    real spawn decision (`_begin_engine_run` single flight, then `trigger_alpha_bot`, the real
    publisher and the real SSE fan-out);
  * the engine process, replaced at `subprocess.run` by a run that lasts a stated number of virtual
    seconds and writes what the engine writes just before it ends (below);
  * the routes' ET clock (`app.datetime`), the real XNYS calendar, the window cutoff (the real
    `analytics._window_cutoff_date`, reading the host's UTC date) and the monotonic clock the health
    verdict reads.

What the engine writes. While closed, `alpha_bot_execution.main` loads `bot_state`, persists the
Composer inception fields of each symphony through `_persist_composer_fields_to_bot_state` (not on a
weekday holiday) and saves; this test calls that function itself with figures that move on every
cycle. In the five minutes after the close the cycle also commits new shadow rows and a `bot_state`
with the cycle's stamp, and while open each cycle does (`write_engine_cycle`, adopted from
test_closed_market_numbers_never_move together with its world, baseline and comparison).

Expected values are never typed from a response: the state of the market at an instant comes from
the independent calendar table `SESSIONS_2026` below, and the baseline of a frozen stretch is the
response itself, read once after the close has settled.

These tests run against the code as it is. A failure is a finding, not a test to adjust: each
failing line names the virtual ET time and the field.
"""

from __future__ import annotations

import datetime as stdlib_datetime
import json
import queue
import re
import threading
import time
import types
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import schedule as schedule_module

import alpha_bot_execution as engine_module
import analytics as analytics_module
import app as app_module
import database as database_module
import market_calendar
from tests.app import test_closed_market_numbers_never_move as closed_market
from tests.app._refresh_timing_frozen_world import (
    SYMPHONY_IDS,
    describe,
    retime_rows,
    visible_text,
)
from tests.app._refresh_timing_state_world import rendered_page_figures
from tests.app.test_closed_market_numbers_never_move import (
    NUMBER_TOKEN,
    Session,
    build_session,
    local,
    moved_figures,
    write_engine_cycle,
)
from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

UTC = ZoneInfo("UTC")
ET = ZoneInfo("America/New_York")

# The real function, captured before any test patches it: it reads the host's UTC date.
_REAL_WINDOW_CUTOFF = analytics_module._window_cutoff_date

# ---------------------------------------------------------------------------
# The calendar, written down independently of market_calendar
# ---------------------------------------------------------------------------

# Trading days of the stretches played, as (open, close) in ET. Thursday 2026-11-26 is
# Thanksgiving (closed all day); Friday 2026-11-27 closes at 13:00.
SESSIONS_2026 = {
    "2026-10-30": ("09:30:00", "16:00:00"),
    "2026-11-02": ("09:30:00", "16:00:00"),
    "2026-11-25": ("09:30:00", "16:00:00"),
    "2026-11-27": ("09:30:00", "13:00:00"),
}
HOLIDAYS_ON_WEEKDAYS = frozenset({"2026-11-26"})

# The daily slots run at host-clock (UTC) times by design, as (hour, minute); the routes' "today"
# is the ET date.
DAILY_SLOTS = {
    "_run_trigger_retention": (2, 0),
    "_run_lens_pipeline": (3, 0),
    "_run_incubation_tick": (3, 30),
    "_run_retirement_recommender_tick": (3, 45),
    "_start_model_warmup": (4, 15),
}

ENGINE_RUN_SECONDS = 25  # an ordinary engine run, in virtual seconds
SLOW_RUN_SECONDS = 70  # a run that outlasts the next :00
REBALANCE_BLACKOUT = timedelta(minutes=7)  # the engine's pause before the close
POST_MORTEM_GRACE = timedelta(minutes=5)  # how long after the close the engine still runs
MINUTE = timedelta(minutes=1)
SECOND = timedelta(seconds=1)


def clock_text(moment: datetime) -> str:
    return moment.astimezone(ET).strftime("%H:%M:%S")


def session_hours(moment: datetime) -> tuple[str, str] | None:
    return SESSIONS_2026.get(moment.astimezone(ET).strftime("%Y-%m-%d"))


def expected_market_state(moment: datetime) -> str:
    hours = session_hours(moment)
    if hours is None:
        return "closed_frozen"
    clock = clock_text(moment)
    if clock < hours[0]:
        return "pre_market"
    return "open" if clock < hours[1] else "closed_frozen"


def expected_engine_phase(moment: datetime, *, first_cycle_done: bool) -> str:
    """closed, blackout, opening or running, by the plan: opening is the first five minutes of the
    session until the session's first cycle is in."""
    if expected_market_state(moment) != "open":
        return "closed"
    hours = session_hours(moment)
    wall = moment.astimezone(ET)
    close = datetime.fromisoformat(f"{wall:%Y-%m-%d}T{hours[1]}").replace(tzinfo=ET)
    opened = datetime.fromisoformat(f"{wall:%Y-%m-%d}T{hours[0]}").replace(tzinfo=ET)
    if wall >= close - REBALANCE_BLACKOUT:
        return "blackout"
    if wall < opened + timedelta(minutes=5) and not first_cycle_done:
        return "opening"
    return "running"


def expected_outcome(moment: datetime) -> str:
    """How `trigger_alpha_bot` should classify an engine run that began at `moment`."""
    hours = session_hours(moment)
    if hours is None:
        return "closed"
    wall = moment.astimezone(ET)
    day = f"{wall:%Y-%m-%d}"
    opened = datetime.fromisoformat(f"{day}T{hours[0]}").replace(tzinfo=ET)
    close = datetime.fromisoformat(f"{day}T{hours[1]}").replace(tzinfo=ET)
    if wall < opened or wall > close + POST_MORTEM_GRACE:
        return "closed"
    if close - REBALANCE_BLACKOUT <= wall < close:
        return "blackout"
    return "eod" if wall >= close else "cycle"


def in_post_close_window(moment: datetime) -> bool:
    hours = session_hours(moment)
    if hours is None:
        return False
    wall = moment.astimezone(ET)
    close = datetime.fromisoformat(f"{wall:%Y-%m-%d}T{hours[1]}").replace(tzinfo=ET)
    return close <= wall <= close + POST_MORTEM_GRACE


# ---------------------------------------------------------------------------
# The virtual host
# ---------------------------------------------------------------------------


class Host:
    """The droplet's clocks at one virtual instant: aware UTC, ET, naive UTC wall, monotonic."""

    def __init__(self, start_utc: datetime) -> None:
        self.utc = start_utc.astimezone(UTC)
        self._origin = self.utc
        self._mono0 = time.monotonic()

    def move(self, instant: datetime) -> None:
        self.utc = instant.astimezone(UTC)

    @property
    def et(self) -> datetime:
        return self.utc.astimezone(ET)

    def monotonic(self) -> float:
        return self._mono0 + (self.utc - self._origin).total_seconds()


def host_datetime_class(host: Host) -> type[datetime]:
    """A `datetime` whose `now()` is the host: naive means the UTC wall clock of the droplet."""

    class _Host(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return host.utc.replace(tzinfo=None)
            return host.utc.astimezone(tz)

    return _Host


def install_host(monkeypatch, host: Host) -> None:
    """Point every clock the daemon and the routes read at `host`."""
    host_datetime = host_datetime_class(host)
    monkeypatch.setattr(app_module, "datetime", host_datetime)
    monkeypatch.setattr(analytics_module, "datetime", host_datetime)
    monkeypatch.setattr(app_module, "get_market_state", market_calendar.get_market_state)
    monkeypatch.setattr(app_module, "_monotonic", host.monotonic)

    # The scheduler library reads `datetime.datetime.now()` (naive, host-local) through its own
    # module reference: hand it a copy of the module whose `datetime` is the host's.
    scheduler_clock = types.SimpleNamespace(
        **{
            name: getattr(stdlib_datetime, name)
            for name in ("time", "timedelta", "timezone", "date", "MINYEAR", "MAXYEAR")
        }
    )
    scheduler_clock.datetime = host_datetime
    monkeypatch.setattr(schedule_module, "datetime", scheduler_clock)

    def cutoff_on_the_host(window):
        # The real function does `from datetime import datetime` at each call and reads the UTC
        # date from it; give it the host's clock for the length of the call.
        real = stdlib_datetime.datetime
        stdlib_datetime.datetime = host_datetime
        try:
            return _REAL_WINDOW_CUTOFF(window)
        finally:
            stdlib_datetime.datetime = real

    monkeypatch.setattr(analytics_module, "_window_cutoff_date", cutoff_on_the_host)


# ---------------------------------------------------------------------------
# The engine process
# ---------------------------------------------------------------------------


@dataclass
class EngineRun:
    started_utc: datetime
    ends_utc: datetime
    release: threading.Event = field(default_factory=threading.Event)


class VirtualEngine:
    """`subprocess.run` for the engine: a run lasts `duration_for(start)` virtual seconds, and
    `writes(run)` is called just before it ends."""

    def __init__(self, host: Host, duration_for) -> None:
        self.host = host
        self.duration_for = duration_for
        self.writes = lambda run: None
        self.runs: list[EngineRun] = []
        self._pending: list[EngineRun] = []
        self.changed = threading.Condition()  # signalled by a run starting or a record published

    def __call__(self, cmd, **_kwargs):
        import subprocess

        with self.changed:
            start = self.host.utc
            run = EngineRun(start, start + timedelta(seconds=self.duration_for(start)))
            self.runs.append(run)
            self._pending.append(run)
            self.changed.notify_all()
        run.release.wait(timeout=30.0)
        return subprocess.CompletedProcess(cmd, 0)

    def settle(self) -> None:
        """End every run whose virtual time has come, writing what the engine writes first."""
        with self.changed:
            due = [run for run in self._pending if run.ends_utc <= self.host.utc]
            self._pending = [run for run in self._pending if run.ends_utc > self.host.utc]
        for run in due:
            self.writes(run)
            run.release.set()
            join_threads("engine-cycle")
            join_threads("cycle-refresh")


def join_threads(name: str, timeout_s: float = 10.0) -> None:
    for thread in threading.enumerate():
        if thread.name == name and thread.is_alive():
            thread.join(timeout=timeout_s)


# ---------------------------------------------------------------------------
# The daemon: the real scheduler registrations, the slots recorded
# ---------------------------------------------------------------------------


class _StopLoop(BaseException):
    """Leaves run_scheduler's endless loop once it has registered its slots and taken a pass."""


class _SleepStops:
    def sleep(self, _seconds):
        raise _StopLoop

    def __getattr__(self, name):
        return getattr(time, name)


class Daemon:
    def __init__(self, monkeypatch, host: Host, engine: VirtualEngine) -> None:
        self.host = host
        self.engine = engine
        self.monkeypatch = monkeypatch
        self.spawns: list[datetime] = []  # host instant of each threaded_trigger call
        self.refresh_slots: list[datetime] = []
        self.slot_fires: dict[str, list[datetime]] = defaultdict(list)
        self.cycles: list = []  # every CycleRecord published, with the host instant it was
        self.cycle_times: list[datetime] = []
        self.sse: queue.Queue = queue.Queue()
        self._real_trigger = app_module.threaded_trigger
        self._real_notify = app_module._notify_cycle_complete

        scheduler = schedule_module.Scheduler()
        monkeypatch.setattr(
            app_module,
            "schedule",
            types.SimpleNamespace(every=scheduler.every, run_pending=scheduler.run_pending),
        )
        monkeypatch.setattr(app_module.subprocess, "run", engine)
        for name in DAILY_SLOTS:
            monkeypatch.setattr(app_module, name, self._recorder(name))
        monkeypatch.setattr(app_module, "_start_account_refresh", self._refresh_recorder)
        monkeypatch.setattr(app_module, "threaded_trigger", self._spawn)
        monkeypatch.setattr(app_module, "_notify_cycle_complete", self._notify)
        with app_module._sse_clients_lock:
            app_module._sse_clients.append(self.sse)

    def close(self) -> None:
        with app_module._sse_clients_lock:
            if self.sse in app_module._sse_clients:
                app_module._sse_clients.remove(self.sse)

    def _recorder(self, name: str):
        def _slot():
            self.slot_fires[name].append(self.host.utc)

        return _slot

    def _refresh_recorder(self, _reason: str) -> bool:
        self.refresh_slots.append(self.host.utc)
        return True

    def _notify(self, record) -> None:
        with self.engine.changed:
            self.cycles.append(record)
            self.cycle_times.append(self.host.utc)
            self.engine.changed.notify_all()
        self._real_notify(record)

    def _spawn(self) -> None:
        self.spawns.append(self.host.utc)
        runs_before, cycles_before = len(self.engine.runs), len(self.cycles)
        self._real_trigger()
        with self.engine.changed:
            # An engine start, or a skipped minute (published at once).
            arrived = self.engine.changed.wait_for(
                lambda: len(self.engine.runs) > runs_before or len(self.cycles) > cycles_before,
                timeout=10.0,
            )
        assert arrived, f"neither an engine start nor a skip after the spawn at {self.host.utc}"

    def start(self) -> None:
        """Register the real slots and take the first pass, as `run_scheduler` does."""
        with self.monkeypatch.context() as stopped_sleep:
            stopped_sleep.setattr(app_module, "time", _SleepStops())
            with pytest.raises(_StopLoop):
                app_module.run_scheduler()

    def tick(self) -> None:
        app_module._scheduler_tick()

    def events(self) -> list[tuple[str, dict]]:
        out = []
        while True:
            try:
                name, data = self.sse.get_nowait()
            except queue.Empty:
                return out
            out.append((name, json.loads(data)))


# ---------------------------------------------------------------------------
# What the engine writes
# ---------------------------------------------------------------------------


def closed_engine_cycle(number: int, *, weekday_holiday: bool) -> None:
    """One closed-market cycle as `alpha_bot_execution.main` runs it: load, persist the Composer
    inception fields of each symphony through the engine's own function (not on a weekday
    holiday), save. Composer's figures move on every call."""
    state = database_module.load_state()
    if not weekday_holiday:
        for sym_id in SYMPHONY_IDS:
            entry = state[sym_id]
            engine_module._persist_composer_fields_to_bot_state(
                state,
                sym_id,
                {
                    "id": sym_id,
                    "name": entry.get("name", ""),
                    "simple_return": float(entry.get("simple_return") or 0.0) + 0.0007 * number,
                    "net_deposits": float(entry.get("net_deposits") or 0.0) + 1.5 * number,
                    "time_weighted_return": float(entry.get("time_weighted_return") or 0.0)
                    + 0.0009 * number,
                    "max_drawdown": float(entry.get("max_drawdown") or 0.0) - 0.0003 * number,
                },
            )
    database_module.save_state(state)


class ComposerRefresh:
    """The refresh after an engine cycle: the real `_refresh_account_totals`, Composer's figures
    moved on by one step each time. `due(instant)` says whether this cycle's refresh is played: a
    long stretch plays it only in the minutes it reads, to keep the run short, and the cache each
    read finds is the one the minute's own refresh just filled."""

    def __init__(self, composer, due) -> None:
        self.composer = composer
        self.due = due
        self.host = None
        self.runs = 0

    def body(self) -> None:
        if not self.due(self.host.utc):
            return
        self.composer.version += 1
        self.runs += 1
        app_module._refresh_account_totals()


# ---------------------------------------------------------------------------
# The rig
# ---------------------------------------------------------------------------


@dataclass
class Rig:
    host: Host
    world: object
    daemon: Daemon
    engine: VirtualEngine
    refresh: ComposerRefresh
    failures: list[str] = field(default_factory=list)
    engine_cycles: int = 0
    open_commits: list[datetime] = field(default_factory=list)
    # Which minutes read the pages (and so play the refresh and the engine's closed writes);
    # the other minutes still spawn, run and publish, and write every tenth run.
    reads_in: object = field(default=lambda _minute: True)

    def note(self, text: str) -> None:
        self.failures.append(f"{self.host.et.strftime('%a %Y-%m-%d %H:%M:%S %Z')}: {text}")

    def at(self, instant: datetime) -> None:
        self.host.move(instant)
        self.engine.settle()

    def poll(self, path: str):
        """A route's JSON, or None after noting the refusal."""
        try:
            return self.world.poll(path)
        except AssertionError as refusal:
            self.note(f"{path} refused: {str(refusal)[:160]}")
            return None


def engine_writes(rig: Rig):
    """What the engine writes at the end of a run that began in each part of the day."""

    def _writes(run: EngineRun) -> None:
        started = run.started_utc
        rig.engine_cycles += 1
        state = expected_market_state(started)
        if (
            state == "open"
            and expected_outcome(started) == "cycle"
            or in_post_close_window(started)
        ):
            when = started.astimezone(ET) + timedelta(seconds=20)
            write_engine_cycle(rig.world, when, rig.engine_cycles)
            rig.open_commits.append(when)
        elif expected_outcome(started) == "blackout":
            return  # the pause before the close writes nothing
        elif rig.reads_in(started) or rig.engine_cycles % 10 == 0:
            day = f"{started.astimezone(ET):%Y-%m-%d}"
            closed_engine_cycle(rig.engine_cycles, weekday_holiday=day in HOLIDAYS_ON_WEEKDAYS)

    return _writes


def build_rig(monkeypatch, tmp_path, session: Session, start_utc: datetime, *, duration_for=None):
    world, composer = build_session(monkeypatch, tmp_path, session)
    host = Host(start_utc)
    install_host(monkeypatch, host)
    engine = VirtualEngine(host, duration_for or (lambda _start: ENGINE_RUN_SECONDS))
    daemon = Daemon(monkeypatch, host, engine)
    refresh = ComposerRefresh(composer, due=lambda instant: rig.reads_in(instant))
    refresh.host = host
    monkeypatch.setattr(app_module, "_cycle_refresh_body", refresh.body)
    rig = Rig(host, world, daemon, engine, refresh)
    engine.writes = engine_writes(rig)
    return rig


def utc_of(day: str, clock: str) -> datetime:
    return local(day, clock).astimezone(UTC)


def seconds(start: datetime, end: datetime):
    moment = start
    while moment <= end:
        yield moment
        moment += SECOND


def minutes(start: datetime, end: datetime):
    moment = start
    while moment <= end:
        yield moment
        moment += MINUTE


# ---------------------------------------------------------------------------
# Checks shared by the stretches
# ---------------------------------------------------------------------------


def check_header(rig: Rig, *, first_cycle_done: bool = True) -> dict | None:
    header = rig.poll("/api/header-status")
    if header is None:
        return None
    now = rig.host.utc
    want_state = expected_market_state(now)
    if header["market_state"] != want_state:
        rig.note(
            f"/api/header-status market_state {header['market_state']!r}, expected {want_state!r}"
        )
    want_phase = expected_engine_phase(now, first_cycle_done=first_cycle_done)
    if header["engine_phase"] != want_phase:
        rig.note(
            f"/api/header-status engine_phase {header['engine_phase']!r}, expected {want_phase!r}"
        )
    if header["degraded_reasons"] or header["status"] != "ok":
        rig.note(
            f"/api/header-status degraded: {header['status']!r} {header['degraded_reasons']!r}"
        )
    reasons = app_module._health_degraded_reasons(app_module._monotonic())
    if reasons:
        rig.note(f"_health_degraded_reasons {reasons!r}")
    return header


# The page prints the session's hours ("09:30 ET -> 16:00 ET") from the calendar: on the day after
# Thanksgiving it reads 13:00 from midnight ET on. That is a schedule label, not a figure, so it is
# left out of the page's numbers; every other number of the page is compared.
SESSION_HOURS_LABEL = re.compile(r"09:30 ET\s*\S+\s*\d\d:\d\d ET")


def read_all(world) -> dict:
    """The full JSON of the state, the 30d strip and the 30d hero chart, and every number on the
    page, as closed_market.read_everything reads them, less the session-hours label."""
    page = world.page()
    return {
        "state": world.poll("/api/state"),
        "strip": world.poll("/api/strip/30d"),
        "chart": world.poll("/api/hero-chart/30d"),
        "page": {
            "figures": rendered_page_figures(page),
            "numbers": NUMBER_TOKEN.findall(SESSION_HOURS_LABEL.sub("", visible_text(page))),
        },
    }


def read_light(rig: Rig) -> dict | None:
    state, strip = rig.poll("/api/state"), rig.poll("/api/strip/30d")
    if state is None or strip is None:
        return None
    return {"state": state, "strip": strip}


def compare_to(rig: Rig, baseline: dict, current: dict | None, what: str) -> None:
    if current is None:
        return
    changed = moved_figures({key: baseline[key] for key in current}, current)
    if changed:
        rig.note(f"{what}: numbers moved while closed: {describe(changed, 6)}")


def check_spawns(rig: Rig, first: datetime, last: datetime) -> None:
    """The engine is spawned at :00 of every minute, around the clock, and never skipped. A
    scheduler registered at `first` fires its minute slot from the next :00 on."""
    want = list(minutes(first + MINUTE, last))
    if rig.daemon.spawns != want:
        extra = sorted(set(rig.daemon.spawns) - set(want))[:3]
        gone = sorted(set(want) - set(rig.daemon.spawns))[:3]
        rig.failures.append(
            f"spawns: {len(rig.daemon.spawns)} calls for {len(want)} minutes; "
            f"missing {[g.astimezone(ET).strftime('%a %H:%M') for g in gone]}, "
            f"unexpected {[e.astimezone(ET).strftime('%a %H:%M:%S') for e in extra]}"
        )
    skipped = [r for r in rig.daemon.cycles if r.outcome == "skipped"]
    if skipped:
        rig.failures.append(f"{len(skipped)} minutes skipped by single flight with ordinary runs")


def check_outcomes(rig: Rig) -> None:
    """Each published run is classified as the engine's own gates would, in a closed stretch none
    claims a change, and every run has an id of its own (the repeated hour of the clock change
    repeats every ET wall-clock second, never an id)."""
    ids = [record.cycle_id for record in rig.daemon.cycles]
    if len(set(ids)) != len(ids):
        rig.failures.append(f"{len(ids) - len(set(ids))} runs share a cycle id")
    for record, seen_at in zip(rig.daemon.cycles, rig.daemon.cycle_times, strict=True):
        if record.outcome == "skipped":
            continue
        started = datetime.fromisoformat(record.started_at)
        want = expected_outcome(started)
        if record.outcome != want:
            rig.failures.append(
                f"{started.astimezone(ET):%a %H:%M:%S}: run classified {record.outcome!r}, expected {want!r}"
            )
        if want == "closed" and record.changed:
            rig.failures.append(
                f"{started.astimezone(ET):%a %H:%M:%S}: a closed-market run announced a change to the pages"
            )


def check_daily_slots(rig: Rig, first: datetime, last: datetime) -> None:
    """Each daily slot fires once on each UTC calendar day, at its host-clock time."""
    for name, (hour, minute) in DAILY_SLOTS.items():
        want = []
        day = first.astimezone(UTC).date()
        while day <= last.astimezone(UTC).date():
            at = datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)
            if first <= at <= last:
                want.append(at)
            day += timedelta(days=1)
        fired = rig.daemon.slot_fires[name]
        if fired != want:
            rig.failures.append(
                f"{name}: fired at {[f.strftime('%a %H:%MZ') for f in fired]}, "
                f"expected {[w.strftime('%a %H:%MZ') for w in want]}"
            )
    minute_slots = rig.daemon.refresh_slots
    if minute_slots != list(minutes(first + MINUTE, last)):
        rig.failures.append(
            f"account-refresh slot fired {len(minute_slots)} times, not once a minute"
        )


# ---------------------------------------------------------------------------
# A closed stretch: nothing moves from the settled close to the end
# ---------------------------------------------------------------------------


def play_closed_stretch(
    rig: Rig,
    first: datetime,
    last: datetime,
    baseline_at: datetime,
    *,
    dense: tuple[tuple[datetime, datetime], ...],
    full_every: int = 180,
) -> None:
    """Drive the scheduler minute by minute from `first` to `last`.

    Per minute: the :00 pass (the spawn), the end of the run at :25 (the engine's writes, the
    publish and the refresh), a pass at :30 (the reads) and a pass at :59. Spawns are checked for
    every minute. The header is read, and the state and the 30 day strip compared with the
    baseline, every minute inside a `dense` range and every 15th minute elsewhere; the whole set
    (the page, the hero chart and the figures on it) every `full_every` minutes.
    """
    baseline_light = baseline_full = None

    def reads(minute: datetime) -> bool:
        return any(a <= minute <= b for a, b in dense) or minute.minute % 30 == 0 or minute == last

    rig.reads_in = reads
    for index, minute in enumerate(minutes(first, last)):
        rig.at(minute)
        rig.daemon.tick()
        rig.at(minute + timedelta(seconds=ENGINE_RUN_SECONDS))
        rig.daemon.tick()
        rig.at(minute + timedelta(seconds=30))
        rig.daemon.tick()
        if reads(minute):
            check_header(rig)
        if baseline_light is None and rig.host.utc >= baseline_at:
            baseline_full = read_all(rig.world)
            baseline_light = {key: baseline_full[key] for key in ("state", "strip")}
        elif baseline_light is not None and reads(minute):
            compare_to(rig, baseline_light, read_light(rig), "state and 30d strip")
        if baseline_full is not None and (index % full_every == 0 or minute == last):
            compare_to(rig, baseline_full, read_all(rig.world), "full read")
        rig.at(minute + timedelta(seconds=59))
        rig.daemon.tick()
    check_spawns(rig, first, last)
    check_outcomes(rig)
    assert baseline_light is not None, "the baseline was never read"


# ---------------------------------------------------------------------------
# Stretch A: Friday's close, the weekend and the clock change, to Monday's open
# ---------------------------------------------------------------------------

FRIDAY_SESSION = Session(
    name="friday session",
    session_day="2026-10-30",
    previous_day="2026-10-29",
    baseline=local("2026-10-30", "16:02:00"),
    steps=(),
)


def test_friday_close_to_monday_open_moves_no_number_and_spawns_every_minute(monkeypatch, tmp_path):
    first, last = utc_of("2026-10-30", "15:59:00"), utc_of("2026-11-02", "09:29:00")
    rig = build_rig(monkeypatch, tmp_path, FRIDAY_SESSION, first)
    try:
        rig.daemon.start()
        play_closed_stretch(
            rig,
            first,
            last,
            baseline_at=utc_of("2026-10-30", "16:02:00"),
            dense=(
                (utc_of("2026-10-30", "15:59:00"), utc_of("2026-10-30", "16:30:00")),
                (utc_of("2026-11-01", "00:00:00"), utc_of("2026-11-01", "03:30:00")),
                (utc_of("2026-11-02", "08:30:00"), utc_of("2026-11-02", "09:29:00")),
            ),
        )
        check_daily_slots(rig, first, last)
    finally:
        rig.daemon.close()
    assert not rig.failures, "\n".join(rig.failures[:40])


# ---------------------------------------------------------------------------
# The trailing windows while closed
# ---------------------------------------------------------------------------


def test_a_session_on_the_edge_of_the_30d_window_stays_in_it_until_the_next_session(
    monkeypatch, tmp_path
):
    """Friday's 30 day window starts on 2026-09-30. A closed dashboard shows the last session's
    windows, so that day's figures stay in them through the weekend: the window slides with the
    next session's data, not with the calendar (the host's UTC date turns over at 20:00 EDT)."""
    world, _composer = build_session(monkeypatch, tmp_path, FRIDAY_SESSION)
    world.seed_rows(
        retime_rows(
            world.previous_day_rows(), day="2026-09-30", clock="15:52:00", cycle_id="20260930_1552"
        )
    )
    host = Host(utc_of("2026-10-30", "16:30:00"))
    install_host(monkeypatch, host)
    baseline = read_all(world)
    moved = []

    for day, clock in (
        ("2026-10-30", "19:59:59"),
        ("2026-10-30", "20:00:00"),
        ("2026-10-30", "23:59:59"),
        ("2026-10-31", "00:00:01"),
        ("2026-10-31", "12:00:00"),
        ("2026-11-01", "12:00:00"),
        ("2026-11-02", "09:00:00"),
    ):
        host.move(utc_of(day, clock))
        # No scheduler runs here: keep its tick stamp current so the health verdict stays quiet.
        monkeypatch.setattr(app_module, "_scheduler_last_tick_mono", host.monotonic())
        changed = moved_figures(baseline, read_all(world))
        if changed:
            moved.append(f"{day} {clock} ET: {sorted(changed)}")
    assert not moved, "\n".join(moved)


# ---------------------------------------------------------------------------
# Stretch B: the morning, 09:00 to 10:05 ET, second by second around the open
# ---------------------------------------------------------------------------


def play_open_morning(rig: Rig, day: str) -> dict:
    """Drive the scheduler second by second from 09:00:00 to 10:05:00 ET on `day`.

    Returns what the checks after the loop need: the header and state seen each second of the
    band around the open, the light reads at 09:30:24 and 09:30:26, and the light read of every
    minute from the first open cycle on.
    """
    first, last = utc_of(day, "09:00:00"), utc_of(day, "10:05:00")
    band = (utc_of(day, "09:29:50"), utc_of(day, "09:30:40"))
    baseline = None
    seen = {"phases": [], "band": {}, "minute_reads": {}}
    for moment in seconds(first, last):
        rig.at(moment)
        rig.daemon.tick()
        in_band = band[0] <= moment <= band[1]
        if not (in_band or moment.second == 30):
            continue
        header = check_header(rig, first_cycle_done=bool(rig.open_commits))
        light = read_light(rig)
        if header is not None:
            if not seen["phases"] or seen["phases"][-1][1] != header["engine_phase"]:
                seen["phases"].append((moment, header["engine_phase"]))
        if light is None:
            continue
        if in_band:
            seen["band"][moment] = {"header": header, "light": light}
        if baseline is None:
            baseline = light
        elif moment < utc_of(day, "09:30:00"):
            compare_to(rig, baseline, light, "pre-market state and 30d strip")
        if moment.second == 30 and moment >= utc_of(day, "09:30:00"):
            seen["minute_reads"][moment] = (len(rig.open_commits), light)
    return seen


def test_monday_morning_opens_at_0930_and_the_first_cycle_moves_the_figures(monkeypatch, tmp_path):
    day = "2026-11-02"
    slow = utc_of(day, "09:45:00")
    rig = build_rig(
        monkeypatch,
        tmp_path,
        FRIDAY_SESSION,
        utc_of(day, "09:00:00"),
        duration_for=lambda start: SLOW_RUN_SECONDS if start == slow else ENGINE_RUN_SECONDS,
    )
    try:
        rig.daemon.start()
        seen = play_open_morning(rig, day)
        events = rig.daemon.events()
    finally:
        rig.daemon.close()
    verify_open_morning(rig, seen, events, day, slow)
    assert not rig.failures, "\n".join(rig.failures[:40])


def verify_open_morning(rig: Rig, seen: dict, events: list, day: str, slow: datetime) -> None:
    open_at = utc_of(day, "09:30:00")
    first, last = utc_of(day, "09:00:00"), utc_of(day, "10:05:00")
    # The market state moves at 09:30:00 ET, not an hour before or after.
    states = {t: b["light"]["state"]["market_state"] for t, b in seen["band"].items()}
    first_open = min((t for t, s in states.items() if s == "open"), default=None)
    if first_open != open_at:
        rig.failures.append(f"market_state first read 'open' at {first_open}, expected {open_at}")
    if states.get(open_at - SECOND) != "pre_market":
        rig.failures.append(f"market_state at 09:29:59 was {states.get(open_at - SECOND)!r}")
    # Closed, then opening, then live: the order the header shows.
    order = [phase for _t, phase in seen["phases"]]
    if order != ["closed", "opening", "running"]:
        rig.failures.append(f"engine phase order {order!r}, expected closed, opening, running")
    opening_at = next((t for t, phase in seen["phases"] if phase == "opening"), None)
    if opening_at != open_at:
        rig.failures.append(f"the Opening phase began at {opening_at}, expected {open_at}")

    # The engine is spawned every minute through the open; one slow run skips one minute only.
    check_spawns_with_one_slow_run(rig, first, last, slow)

    # The first committed cycle after the open changes the figures on the next /api/state.
    if not rig.open_commits:
        rig.failures.append("the engine never committed an open-market cycle")
    else:
        before, after = utc_of(day, "09:30:24"), utc_of(day, "09:30:26")
        changed = moved_figures(
            {k: seen["band"][before]["light"][k] for k in ("state", "strip")},
            seen["band"][after]["light"],
        )
        if not changed:
            rig.failures.append(
                "no number changed on /api/state after the first open cycle committed"
            )
    # Every later read that follows a new commit differs from the one before it.
    reads = [
        seen["minute_reads"][t]
        for t in sorted(seen["minute_reads"])
        if t >= utc_of(day, "09:31:00")
    ]
    for (count_before, previous), (count_after, current) in zip(reads, reads[1:], strict=False):
        if count_after > count_before and not moved_figures(previous, current):
            rig.failures.append("a committed cycle left every figure on /api/state unchanged")
            break

    check_events(rig, events, day)


def check_spawns_with_one_slow_run(
    rig: Rig, first: datetime, last: datetime, slow: datetime
) -> None:
    want = list(minutes(first + MINUTE, last))
    if rig.daemon.spawns != want:
        rig.failures.append(f"spawns: {len(rig.daemon.spawns)} calls for {len(want)} minutes")
    skipped = [
        datetime.fromisoformat(r.started_at) for r in rig.daemon.cycles if r.outcome == "skipped"
    ]
    if skipped != [slow + MINUTE]:
        rig.failures.append(
            f"skipped minutes {[s.astimezone(ET).strftime('%H:%M:%S') for s in skipped]}, "
            f"expected only {(slow + MINUTE).astimezone(ET):%H:%M:%S}"
        )
    started = [run.started_utc for run in rig.engine.runs]
    gaps = {b - a for a, b in zip(started, started[1:], strict=False)}
    if gaps - {MINUTE, 2 * MINUTE}:
        rig.failures.append(f"engine start gaps {sorted(gaps)}")
    check_outcomes(rig)


def check_events(rig: Rig, events: list[tuple[str, dict]], day: str) -> None:
    """Each finished run was announced once; the runs before the open announced no change and the
    run that began at 09:30:00 announced one."""
    published = [r for r in rig.daemon.cycles if r.outcome != "skipped"]
    skipped = [r for r in rig.daemon.cycles if r.outcome == "skipped"]
    names = {name for name, _payload in events}
    if names != {"cycle-complete"}:
        rig.failures.append(f"SSE event names {sorted(names)}")
    if len(events) != len(published) + len(skipped):
        rig.failures.append(
            f"{len(events)} SSE events for {len(published) + len(skipped)} published records"
        )
    by_start = {
        payload["started_at"]: payload
        for _name, payload in events
        if payload["outcome"] != "skipped"
    }
    open_event = by_start.get(utc_of(day, "09:30:00").astimezone(ET).isoformat())
    if open_event is None or not open_event["changed"] or open_event["outcome"] != "cycle":
        rig.failures.append(f"the 09:30:00 run's event was {open_event!r}")
    for payload in by_start.values():
        started = datetime.fromisoformat(payload["started_at"])
        if started < utc_of(day, "09:30:00") and payload["changed"]:
            rig.failures.append(f"a pre-market run at {started:%H:%M:%S} announced a change")


# ---------------------------------------------------------------------------
# Stretch D: Thanksgiving (closed on a weekday) and the 13:00 early close after it
# ---------------------------------------------------------------------------

WEDNESDAY_SESSION = Session(
    name="wednesday session before thanksgiving",
    session_day="2026-11-25",
    previous_day="2026-11-24",
    baseline=local("2026-11-25", "16:02:00"),
    steps=(),
)

EARLY_CLOSE_SESSION = Session(
    name="friday early close after thanksgiving",
    session_day="2026-11-27",
    previous_day="2026-11-25",
    baseline=local("2026-11-27", "13:02:00"),
    steps=(),
)


def test_thanksgiving_thursday_moves_no_number_and_no_morning_opens(monkeypatch, tmp_path):
    first, last = utc_of("2026-11-25", "15:59:00"), utc_of("2026-11-27", "09:29:00")
    rig = build_rig(monkeypatch, tmp_path, WEDNESDAY_SESSION, first)
    try:
        rig.daemon.start()
        play_closed_stretch(
            rig,
            first,
            last,
            baseline_at=utc_of("2026-11-25", "16:02:00"),
            dense=(
                (utc_of("2026-11-25", "15:59:00"), utc_of("2026-11-25", "16:30:00")),
                (utc_of("2026-11-26", "09:00:00"), utc_of("2026-11-26", "10:05:00")),
                (utc_of("2026-11-27", "08:30:00"), utc_of("2026-11-27", "09:29:00")),
            ),
        )
        check_daily_slots(rig, first, last)
    finally:
        rig.daemon.close()
    assert not rig.failures, "\n".join(rig.failures[:40])


def test_the_friday_after_thanksgiving_opens_at_0930_like_any_morning(monkeypatch, tmp_path):
    day = "2026-11-27"
    slow = utc_of(day, "09:45:00")
    rig = build_rig(
        monkeypatch,
        tmp_path,
        WEDNESDAY_SESSION,
        utc_of(day, "09:00:00"),
        duration_for=lambda start: SLOW_RUN_SECONDS if start == slow else ENGINE_RUN_SECONDS,
    )
    try:
        rig.daemon.start()
        seen = play_open_morning(rig, day)
        events = rig.daemon.events()
    finally:
        rig.daemon.close()
    verify_open_morning(rig, seen, events, day, slow)
    assert not rig.failures, "\n".join(rig.failures[:40])


def test_the_early_close_at_1300_freezes_every_number_from_that_minute(monkeypatch, tmp_path):
    # The close step of this session is stored at 13:00, not 16:00.
    monkeypatch.setattr(closed_market, "CAPTURE_AT", "13:00:42 ET")
    monkeypatch.setattr(closed_market, "CLOSE_ROWS_CLOCK", "13:00:30")
    monkeypatch.setattr(closed_market, "EXIT_CLOCK", "11:02:07")
    first, last = utc_of("2026-11-27", "12:40:00"), utc_of("2026-11-27", "18:00:00")
    rig = build_rig(monkeypatch, tmp_path, EARLY_CLOSE_SESSION, first)
    try:
        rig.daemon.start()
        play_closed_stretch(
            rig,
            first,
            last,
            baseline_at=utc_of("2026-11-27", "13:02:00"),
            dense=((utc_of("2026-11-27", "12:40:00"), utc_of("2026-11-27", "13:30:00")),),
        )
    finally:
        rig.daemon.close()
    assert not rig.failures, "\n".join(rig.failures[:40])


# ---------------------------------------------------------------------------
# The header label in the page's own script, and a page left open from Friday
# ---------------------------------------------------------------------------

SCRIPTS = sup.CHROME_AND_INDEX
DEFAULT_WORLD = "default"


def morning_bodies(tmp_path) -> dict:
    """The bodies the real routes answer around Monday's open, from a world that was closed since
    Friday: the Friday evening state, then 09:29:59, 09:30:05 and (after the first cycle) 09:31:10.
    The routes run under their own patches, undone before the page harness builds anything."""
    day = "2026-11-02"
    with pytest.MonkeyPatch.context() as patches:
        world, _composer = build_session(patches, tmp_path, FRIDAY_SESSION)
        host = Host(utc_of("2026-10-30", "16:30:00"))
        install_host(patches, host)
        bodies = {}

        def grab(label: str, instant: datetime) -> None:
            host.move(instant)
            # A running scheduler has just ticked: the health verdict is "ok".
            patches.setattr(app_module, "_scheduler_last_tick_mono", host.monotonic())
            bodies[label] = {
                "at": instant,
                "state": world.poll("/api/state"),
                "header": world.poll("/api/header-status"),
            }

        grab("friday", utc_of("2026-10-30", "16:30:00"))
        grab("closed", utc_of(day, "09:29:59"))
        grab("opening", utc_of(day, "09:30:05"))
        host.move(utc_of(day, "09:30:25"))
        write_engine_cycle(world, local(day, "09:30:20"), 1)
        grab("live", utc_of(day, "09:31:10"))
    return bodies


def page_run(scenario: str, instant: datetime, **bodies) -> dict:
    from tests.dashboard.test_dashboard_liveness import PRELUDE

    return sup.run(
        PRELUDE + scenario,
        world=DEFAULT_WORLD,
        scripts=SCRIPTS,
        extra_bodies=bodies,
        now_iso=sup.iso_z(int(instant.timestamp() * 1000)),
        timeout=420,
    )


def test_the_header_label_goes_closed_then_opening_then_live_at_the_monday_open(tmp_path):
    hx.get_world(DEFAULT_WORLD)  # built before this test's rows exist
    bodies = morning_bodies(tmp_path)
    labels = {}
    for name in ("closed", "opening", "live"):
        shot = bodies[name]
        labels[name] = page_run(
            r"""
        await H.boot({ extras: true, state: H.bodies.state });
        const fromState = header();
        H.sandbox.window.updateEngineStatus(H.bodies.header);
        return { fromState: fromState, fromHeader: header() };
        """,
            shot["at"],
            state=shot["state"],
            header=shot["header"],
        )
    seen = {
        name: (result["fromState"]["label"], result["fromHeader"]["label"])
        for name, result in labels.items()
    }
    assert seen == {
        "closed": ("Closed", "Closed"),
        "opening": ("Opening", "Opening"),
        "live": ("Live", "Live"),
    }, f"the header label in the page's script, from /api/state and /api/header-status: {seen}"


@pytest.mark.parametrize(
    "perf", [True, False], ids=["monotonic-clock-keeps-running", "monotonic-clock-stops"]
)
def test_a_page_left_open_from_friday_shows_mondays_figures_without_a_reload(perf, tmp_path):
    hx.get_world(DEFAULT_WORLD)
    bodies = morning_bodies(tmp_path)
    friday, live = bodies["friday"], bodies["live"]
    wake = int(live["at"].timestamp() * 1000)
    start = int(friday["at"].timestamp() * 1000)
    fixed = {
        "today": ("today_change", "dry_run"),
        "cumulative": ("cumulative_return", "dry_run"),
    }
    r = page_run(
        r"""
        const WAKE = __WAKE__;
        const monday = function () { return now() >= WAKE - 60000; };
        await H.boot({ extras: true, overrides: [
          ['/api/state', function () { return { body: monday() ? H.bodies.live : H.bodies.friday }; }],
          ['/api/events', function () { return { status: 200, body: {} }; }]] });
        const text = function () {
          return { today: H.text('[data-testid="comp-today-bot-text"]'),
                   cumulative: H.text('[data-testid="comp-cumulative-bot-text"]'),
                   label: H.text('#engine-status-label') };
        };
        const friday = text();
        H.sleepFor(WAKE - now(), { perf: __PERF__ });
        if (__PERF__) { await H.wakeUp(); } else { await H.runTimers(65000); }
        await H.completeAnimations();
        await H.runTimers(35000); await H.completeAnimations();
        return { friday: friday, monday: text(), reloads: H.reloads, navigations: H.navigations.slice() };
        """.replace("__WAKE__", str(wake)).replace("__PERF__", "true" if perf else "false"),
        friday["at"],
        friday=friday["state"],
        live=live["state"],
    )
    assert r["reloads"] == 0 and r["navigations"] == []
    for row, (field_name, leg) in fixed.items():
        strip = live["state"]["portfolio_strip"][field_name]
        want = "Bot " + sup.fmt_pct(strip[leg])
        before = "Bot " + sup.fmt_pct(friday["state"]["portfolio_strip"][field_name][leg])
        assert r["friday"][row] == before, f"{row}: the page opened with {r['friday'][row]!r}"
        assert r["monday"][row] == want, (
            f"{row}: Monday 09:31 EST the page shows {r['monday'][row]!r}, the poll carries {want!r}"
        )
    assert start < wake
