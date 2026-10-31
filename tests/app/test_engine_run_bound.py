"""A hung engine run cannot stop the session.

Single flight holds the run slot for the whole engine process, and a run that never exits would
stop every later cycle until a person restarts the daemon: the Friday autotune wedges and
Monday's 09:30 cycle never starts, with trailing stops not evaluated. The slot stays single, with
a bound:

  * at 09:25 ET on a trading day a run that started on an earlier ET date is ended and the slot
    freed, so the open is never blocked; before that the Friday autotune is not touched, however
    long it has run, over a weekend included;
  * a run that started during the regular session and is still alive after 600 seconds is ended
    and the slot freed, unless it was explicitly forced;
  * ending means terminate, then kill ten seconds later if the process is still there; the run's
    outcome is `terminated`, it is logged, and `/health` carries a reason;
  * the next minute spawns normally;
  * the line `trigger_alpha_bot` prints before it starts the engine cannot leak the slot.

Real scheduler registrations, real `threaded_trigger` / `trigger_alpha_bot` / run slot / publisher,
on the virtual host clock of the clocked module; only the engine process is a fake
(`_engine_process_fakes`). Times are read from the fake processes (their virtual start and the
virtual and real stamps of each signal) and from the published records, never typed from a
response.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

import app as app_module
from tests.app._engine_process_fakes import (
    HANGS,
    HANGS_AND_IGNORES_TERMINATE,
    ORDINARY,
    Behaviour,
    FakeEngines,
)
from tests.app.test_clocked_weekend_to_monday_open import (
    ET,
    Daemon,
    Host,
    install_host,
    join_threads,
    utc_of,
)

MONDAY = "2026-11-02"
FRIDAY = "2026-10-30"
SESSION_BOUND_S = 600
TERMINATE_GRACE_S = 10
TERMINATED = "terminated"
REAL_WAIT_S = 25.0


@dataclass
class Stage:
    host: Host
    engines: FakeEngines
    daemon: Daemon

    def at(self, day: str, clock: str) -> datetime:
        return utc_of(day, clock)

    def tick(self, instant: datetime) -> None:
        self.host.move(instant)
        self.daemon.tick()

    def tick_range(self, first: datetime, last: datetime, every_s: int) -> None:
        moment = first
        while moment <= last:
            self.tick(moment)
            moment += timedelta(seconds=every_s)

    def advance_until(self, done, start: datetime, last: datetime) -> datetime | None:
        """One virtual second and one real pause per step, until `done()` or `last`; returns the
        virtual instant at which `done()` held, else None."""
        moment, deadline = start, time.monotonic() + REAL_WAIT_S
        while moment <= last and time.monotonic() < deadline:
            self.tick(moment)
            if done():
                return moment
            time.sleep(0.01)
            moment += timedelta(seconds=1)
        return moment if done() else None

    def records(self, outcome: str) -> list:
        return [r for r in self.daemon.cycles if r.outcome == outcome]

    def health(self) -> dict:
        return app_module.app.test_client().get("/health").get_json()


_STAGES: list[Stage] = []


def stage(monkeypatch, tmp_path, behaviour, first: tuple[str, str]) -> Stage:
    """A daemon whose scheduler has taken its first pass at `first`, engines from `behaviour`."""
    monkeypatch.setattr(app_module, "LOG_FILE", str(tmp_path / "engine.log"))
    monkeypatch.setattr(app_module, "_cycle_refresh_body", lambda: None)
    host = Host(utc_of(*first))
    install_host(monkeypatch, host)
    engines = FakeEngines(behaviour=behaviour).install(monkeypatch)
    daemon = Daemon(monkeypatch, host, engines)
    daemon.start()
    made = Stage(host, engines, daemon)
    _STAGES.append(made)
    return made


@pytest.fixture(autouse=True)
def _end_every_fake_process_and_thread():
    yield
    while _STAGES:
        made = _STAGES.pop()
        made.engines.release_all()
        made.daemon.close()
    join_threads("engine-cycle")


def wait_for(condition, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return bool(condition())


def next_minute(instant: datetime) -> datetime:
    return instant.replace(second=0, microsecond=0) + timedelta(minutes=1)


def hung_first(index: int) -> Behaviour:
    return HANGS if index == 0 else ORDINARY


def runs_started_by(st: Stage, limit: datetime) -> list:
    return [p for p in st.engines.runs if p.started_et <= limit.astimezone(ET)]


def elapsed_at(signal_stamp, process) -> float:
    return signal_stamp.virtual_mono - process.started_virtual


# ---------------------------------------------------------------------------
# A run started in the session and alive after 600 seconds
# ---------------------------------------------------------------------------


def run_a_session_hang(monkeypatch, tmp_path, behaviour, caplog):
    st = stage(monkeypatch, tmp_path, behaviour, (MONDAY, "09:59:30"))
    st.tick_range(st.at(MONDAY, "09:59:30"), st.at(MONDAY, "10:00:30"), 30)
    assert len(st.engines.runs) == 1, "fixture sanity: the 10:00 minute spawned the run"
    first_id = app_module._engine_run_state().cycle_id
    st.tick_range(st.at(MONDAY, "10:01:00"), st.at(MONDAY, "10:09:30"), 30)
    inside_bound = {
        "signals": (list(st.engines.runs[0].terminate_calls), list(st.engines.runs[0].kill_calls)),
        "skipped": len(st.records("skipped")),
        "runs": len(st.engines.runs),
    }
    with caplog.at_level(logging.INFO):
        ended_at = st.advance_until(
            lambda: bool(st.records(TERMINATED)),
            st.at(MONDAY, "10:09:30"),
            st.at(MONDAY, "10:14:00"),
        )
    return st, first_id, inside_bound, ended_at


def test_a_run_hung_in_the_session_is_ended_after_600_seconds_and_the_next_minute_spawns(
    monkeypatch, tmp_path, caplog
):
    st, first_id, inside_bound, ended_at = run_a_session_hang(
        monkeypatch, tmp_path, hung_first, caplog
    )
    first = st.engines.runs[0]
    assert inside_bound["signals"] == ([], []), "the run was signalled inside the 600 seconds"
    assert inside_bound["skipped"] == 9 and inside_bound["runs"] == 1, (
        f"inside the bound each minute is skipped: {inside_bound}"
    )
    assert ended_at is not None, (
        f"a run started at 10:00:00 was still alive {SESSION_BOUND_S} seconds later: no record "
        f"with outcome {TERMINATED!r} was published by 10:14:00 (alive: {first.alive})"
    )

    assert first.terminate_calls, "the process was never asked to terminate"
    assert not first.kill_calls, "a process that exits on terminate must not also be killed"
    seconds = elapsed_at(first.terminate_calls[0], first)
    assert SESSION_BOUND_S <= seconds <= SESSION_BOUND_S + 61, (
        f"the process was asked to terminate {seconds:.0f} s after it started"
    )
    (record,) = st.records(TERMINATED)
    assert record.cycle_id == first_id, "the record names another run than the one that was ended"

    reasons = st.health()["degraded_reasons"]
    assert any("terminat" in reason for reason in reasons), (
        f"/health lists {reasons!r} after a run was ended"
    )
    warned = [
        r for r in caplog.records if r.levelno >= logging.WARNING and "terminat" in r.getMessage()
    ]
    assert warned, "ending a run was not logged at WARNING or above"

    # The slot is free and the next minute spawns; later minutes are not skipped.
    st.tick_range(ended_at, st.at(MONDAY, "10:13:30"), 5)
    assert len(st.engines.runs) >= 2, "no run was spawned after the hung one was ended"
    second = st.engines.runs[1]
    assert second.started_et <= datetime.fromisoformat(f"{MONDAY}T10:11:59").replace(tzinfo=ET), (
        f"the next run started at {second.started_et:%H:%M:%S}, later than the minute after the end"
    )
    skipped_after = [
        r for r in st.daemon.cycles[st.daemon.cycles.index(record) + 1 :] if r.outcome == "skipped"
    ]
    assert not skipped_after, f"minutes were skipped after the hung run was ended: {skipped_after}"


def test_a_process_that_ignores_terminate_is_killed_ten_seconds_after_it(
    monkeypatch, tmp_path, caplog
):
    st, _first_id, _inside, ended_at = run_a_session_hang(
        monkeypatch,
        tmp_path,
        lambda i: HANGS_AND_IGNORES_TERMINATE if i == 0 else ORDINARY,
        caplog,
    )
    first = st.engines.runs[0]
    assert first.terminate_calls, "the process was never asked to terminate"
    assert ended_at is not None and first.kill_calls, (
        f"a process that ignores terminate was never killed (alive: {first.alive})"
    )
    terminate, kill = first.terminate_calls[0], first.kill_calls[0]
    waited = max(kill.virtual_mono - terminate.virtual_mono, kill.real_mono - terminate.real_mono)
    assert TERMINATE_GRACE_S <= waited <= TERMINATE_GRACE_S + 65, (
        f"kill came {waited:.1f} s after terminate, on whichever clock ran longer"
    )
    st.tick_range(ended_at, st.at(MONDAY, "10:13:30"), 5)
    assert len(st.engines.runs) >= 2, "no run was spawned after the process was killed"
    second = st.engines.runs[1]
    assert second.started_virtual >= kill.virtual_mono, (
        "the slot was freed, and a run spawned, before the process was dead"
    )
    (record,) = st.records(TERMINATED)
    assert record.outcome == TERMINATED


def test_a_run_that_finishes_inside_the_bound_is_never_signalled(monkeypatch, tmp_path, caplog):
    st = stage(monkeypatch, tmp_path, lambda _i: Behaviour(duration_s=590), (MONDAY, "09:59:30"))
    st.tick_range(st.at(MONDAY, "09:59:30"), st.at(MONDAY, "10:12:00"), 10)
    first = st.engines.runs[0]
    assert first.terminate_calls == [] and first.kill_calls == []
    assert wait_for(lambda: first.poll() == 0), "the run did not end by itself with status 0"
    assert wait_for(lambda: st.records("cycle")), "the run's own record is an ordinary cycle"
    assert st.records(TERMINATED) == [], "a run that ended by itself was recorded as terminated"
    assert not any("terminat" in reason for reason in st.health()["degraded_reasons"]), (
        "an ordinary slow run made /health list a termination"
    )


def test_a_forced_run_in_the_session_is_not_ended_by_the_bound(monkeypatch, tmp_path):
    st = stage(monkeypatch, tmp_path, lambda _i: HANGS, (MONDAY, "09:59:30"))
    started = datetime.fromisoformat(f"{MONDAY}T10:00:00").replace(tzinfo=ET)
    st.host.move(started)
    run = app_module._begin_engine_run(started)
    assert run is not None
    thread = threading.Thread(
        target=app_module.trigger_alpha_bot,
        kwargs={"force": True, "cycle_id": run.cycle_id, "started_at": run.started_at},
        daemon=True,
        name="engine-cycle",
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not st.engines.runs and time.monotonic() < deadline:
        time.sleep(0.01)
    assert st.engines.runs, "the forced run never started"
    forced = st.engines.runs[0]
    assert "--force" in forced.args, "fixture sanity: the process is the forced one"

    st.tick_range(st.at(MONDAY, "10:00:30"), st.at(MONDAY, "10:45:00"), 30)
    try:
        assert forced.terminate_calls == [] and forced.kill_calls == [], (
            "an explicitly forced run was signalled by the session bound"
        )
        assert forced.alive
    finally:
        forced.kill()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# The Friday autotune and Monday's open
# ---------------------------------------------------------------------------


def test_a_run_from_friday_is_left_alone_over_the_weekend_and_ended_at_0925_monday(
    monkeypatch, tmp_path
):
    """The Friday EOD run (post-mortem, then the autotune) starts at 16:00:00 and never exits."""
    st = stage(monkeypatch, tmp_path, hung_first, (FRIDAY, "15:59:30"))
    st.tick_range(st.at(FRIDAY, "15:59:30"), st.at(FRIDAY, "16:00:30"), 30)
    assert len(st.engines.runs) == 1, "fixture sanity: the 16:00 minute spawned the EOD run"
    friday_run = st.engines.runs[0]

    # Friday evening, the weekend and Monday up to 09:24:30: not touched, every minute skipped.
    st.tick_range(st.at(FRIDAY, "16:01:00"), st.at(MONDAY, "09:24:30"), 30)
    failures = []
    if friday_run.terminate_calls or friday_run.kill_calls:
        failures.append(
            "the Friday run was signalled before 09:25 Monday, "
            f"{elapsed_at(friday_run.terminate_calls[0], friday_run) / 3600:.1f} h after it began"
            if friday_run.terminate_calls
            else "the Friday run was killed before 09:25 Monday"
        )
    if len(st.engines.runs) != 1:
        failures.append(f"{len(st.engines.runs) - 1} further runs started while it was in flight")

    # 09:25 ET on a trading day: the run from an earlier date is ended.
    ended_at = st.advance_until(
        lambda: bool(st.records(TERMINATED)), st.at(MONDAY, "09:24:30"), st.at(MONDAY, "09:27:00")
    )
    if ended_at is None:
        failures.append(
            "the Friday run was still in flight at 09:27 Monday: the open would be blocked"
        )
    else:
        terminate_et = ended_at.astimezone(ET)
        if not (
            datetime.fromisoformat(f"{MONDAY}T09:25:00").replace(tzinfo=ET)
            <= terminate_et
            <= datetime.fromisoformat(f"{MONDAY}T09:26:00").replace(tzinfo=ET)
        ):
            failures.append(f"the Friday run was ended at {terminate_et:%H:%M:%S}, not at 09:25")
    assert not failures, "\n".join(failures)

    # The morning goes on as any morning: the 09:30:00 cycle spawns, nothing is skipped.
    st.tick_range(next_minute(ended_at), st.at(MONDAY, "09:31:00"), 10)
    opening = datetime.fromisoformat(f"{MONDAY}T09:30:00").replace(tzinfo=ET)
    open_run = [
        p for p in st.engines.runs if opening <= p.started_et < opening + timedelta(seconds=30)
    ]
    assert open_run, "no engine run started at 09:30:00 Monday"
    morning_skips = [
        r
        for r in st.records("skipped")
        if datetime.fromisoformat(r.started_at) >= st.at(MONDAY, "09:26:00")
    ]
    assert not morning_skips, f"minutes skipped from 09:26 Monday: {len(morning_skips)}"


def test_a_run_from_an_earlier_date_is_not_ended_on_a_day_the_market_never_opens(
    monkeypatch, tmp_path
):
    """09:25 on Saturday is not a trading morning: the rule does not apply."""
    st = stage(monkeypatch, tmp_path, hung_first, (FRIDAY, "15:59:30"))
    st.tick_range(st.at(FRIDAY, "15:59:30"), st.at(FRIDAY, "16:00:30"), 30)
    friday_run = st.engines.runs[0]
    st.tick_range(st.at("2026-10-31", "09:20:00"), st.at("2026-10-31", "09:40:00"), 30)
    assert friday_run.terminate_calls == [], "the run was ended on a Saturday morning"


# ---------------------------------------------------------------------------
# The line before the engine starts
# ---------------------------------------------------------------------------


def test_a_fault_on_the_line_printed_before_the_engine_starts_never_leaks_the_slot(
    monkeypatch, tmp_path
):
    st = stage(monkeypatch, tmp_path, lambda _i: ORDINARY, (MONDAY, "10:00:00"))
    real_print = print
    raised = []

    def print_that_fails_once(*args, **kwargs):
        if args and "Triggering Alpha Bot" in str(args[0]) and not raised:
            raised.append(True)
            raise OSError("stdout is closed")
        return real_print(*args, **kwargs)

    monkeypatch.setattr(app_module, "print", print_that_fails_once, raising=False)
    st.host.move(st.at(MONDAY, "10:00:50"))
    st.daemon._real_trigger()
    join_threads("engine-cycle")
    assert raised, "fixture sanity: the print was reached and failed"
    assert app_module._engine_run_state() is None, "the run slot is still taken"

    st.host.move(st.at(MONDAY, "10:01:50"))
    st.daemon._real_trigger()
    assert not st.records("skipped"), "the minute after the fault was skipped: the slot leaked"
    assert wait_for(lambda: st.engines.runs), "the minute after the fault did not start the engine"
