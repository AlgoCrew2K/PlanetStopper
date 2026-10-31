"""While the market is closed a poll costs a lookup, not a recomputation.

`/api/state`, `GET /` and `/api/strip/<window>` recompute every figure of the close snapshot on each
request: dozens of `shadow_history` reads through the committed-rows view of a table with hundreds
of thousands of rows, 13 to 14 seconds on the production snapshot, and a poll is abandoned at 20.
The figures of a closed market cannot change, so the result is held:

  * per snapshot identity and window anchor, for the poll, the page, each window's strip and each
    window's hero chart;
  * a second poll runs no `shadow_history` query and returns the same bytes;
  * the held result is replaced when the snapshot changes, bounded in size, and safe to build from
    several request threads at once;
  * the first poll after a new snapshot, and the first after a daemon start, is answered from a
    result a background thread built beforehand;
  * while the market is open a request never runs the same per-symphony query twice.

What is pinned is query counts and bytes, never wall time: a database statement is counted by the
trace on every connection the process opens (`SqlTrace`), and bytes are compared at a frozen host
clock. The warm-up hooks the tests drive are: the daemon start is
`run_scheduler()` taking its first pass, and a new snapshot is announced by the publication of the
cycle record of the run that stored it (outcome "eod", changed) followed by a scheduler pass.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from datetime import date

import pytest

import app as app_module
from tests.app._closed_session_world import (
    ClosedWorld,
    SqlTrace,
    build_history_world,
    store_close_snapshot,
    trading_days_between,
)
from tests.app._refresh_timing_frozen_world import differences
from tests.app.test_clocked_weekend_to_monday_open import _SleepStops, _StopLoop
from tests.app.test_closed_market_numbers_never_move import local, write_engine_cycle

FRIDAY = "2026-10-30"
MONDAY = "2026-11-02"
CLOSED_ROUTES = ("/api/state", "/api/strip/30d", "/api/hero-chart/30d", "/")
JSON_ROUTES = CLOSED_ROUTES[:3]
CLOSED_INSTANTS = {
    "closed_frozen": ("2026-10-31", "02:00:00"),
    "pre_market": (MONDAY, "08:00:00"),
}
# Paths of one /api/state body that are a clock reading or the label of the market state.
CLOCK_FIELDS = frozenset({"market_state", "meta.market_state", "meta.clock_et"})
WARM_WAIT_S = 12.0
SETTLE_S = 1.0


def world(monkeypatch, tmp_path) -> tuple[ClosedWorld, SqlTrace]:
    cw = build_history_world(monkeypatch, tmp_path, first_history_day=date(2026, 9, 1))
    return cw, SqlTrace().install(monkeypatch)


def request_thread() -> str:
    """The thread the test client runs a request on: the test's own."""
    return threading.current_thread().name


def request_queries(trace: SqlTrace, mark: int) -> list[str]:
    return trace.shadow_history_selects(mark, thread=request_thread())


def moved(first: dict, second: dict) -> dict:
    return {
        path: pair for path, pair in differences(first, second).items() if path not in CLOCK_FIELDS
    }


@pytest.mark.parametrize("path", CLOSED_ROUTES)
@pytest.mark.parametrize("market_state", sorted(CLOSED_INSTANTS))
def test_a_second_closed_poll_reads_no_shadow_rows_and_answers_the_same_bytes(
    monkeypatch, tmp_path, path, market_state
):
    cw, trace = world(monkeypatch, tmp_path)
    cw.at(*CLOSED_INSTANTS[market_state])
    first = cw.raw(path)
    mark = trace.mark()
    second, third = cw.raw(path), cw.raw(path)

    queries = request_queries(trace, mark)
    assert not queries, (
        f"{path} ({market_state}): the second and third polls of an unchanged close ran "
        f"{len(queries)} shadow_history statements, for example {queries[0][:160]!r}"
    )
    assert first == second == third, f"{path}: the held answer differs from the computed one"


def test_the_trace_sees_the_queries_of_a_poll_that_is_computed(monkeypatch, tmp_path):
    """Guards the counts above: an open-market poll is always computed, and the trace sees it."""
    cw, trace = world(monkeypatch, tmp_path)
    write_engine_cycle(cw.world, local(MONDAY, "10:00:20"), 1)
    cw.at(MONDAY, "10:01:00")
    mark = trace.mark()
    cw.raw("/api/state")
    assert request_queries(trace, mark), "the trace recorded no shadow_history statement"


def test_the_held_poll_moves_no_figure_as_the_closed_evening_passes(monkeypatch, tmp_path):
    cw, trace = world(monkeypatch, tmp_path)
    cw.at(FRIDAY, "16:30:00")
    baseline = cw.poll("/api/state")
    mark = trace.mark()
    failures = []
    # Before 20:00 ET, so that the window anchor is not what is under test here.
    for day, clock in ((FRIDAY, "16:31:30"), (FRIDAY, "18:00:00"), (FRIDAY, "19:59:00")):
        cw.at(day, clock)
        changed = moved(baseline, cw.poll("/api/state"))
        if changed:
            failures.append(f"{day} {clock}: {sorted(changed)[:4]}")
    assert not failures, "\n".join(failures)
    assert not request_queries(trace, mark), "a poll later in the evening read shadow rows"


def test_each_window_is_held_under_its_own_key(monkeypatch, tmp_path):
    cw, trace = world(monkeypatch, tmp_path)
    cw.at("2026-10-31", "02:00:00")
    thirty, sixty = cw.raw("/api/strip/30d"), cw.raw("/api/strip/60d")
    assert thirty != sixty, "fixture sanity: the two windows differ"
    for path in ("/api/hero-chart/30d", "/api/hero-chart/60d"):
        cw.raw(path)
    mark = trace.mark()
    again = [cw.raw(p) for p in ("/api/strip/30d", "/api/strip/60d")]
    again += [cw.raw(p) for p in ("/api/hero-chart/60d", "/api/hero-chart/30d")]
    assert not request_queries(trace, mark), "a held window ran a query"
    assert again[:2] == [thirty, sixty], "a window answered with another window's held result"
    assert again[2] != again[3], "the two hero windows share one held result"


def test_a_new_snapshot_replaces_the_held_result(monkeypatch, tmp_path):
    cw, trace = world(monkeypatch, tmp_path)
    cw.at(FRIDAY, "16:30:00")
    before = {path: cw.raw(path) for path in JSON_ROUTES}
    assert all(cw.raw(path) == body for path, body in before.items()), "fixture sanity"

    # Monday's session commits a cycle and closes; the engine stores its snapshot.
    write_engine_cycle(cw.world, local(MONDAY, "15:52:10"), 1)
    store_close_snapshot(cw.world, MONDAY)
    cw.at(MONDAY, "16:30:00")
    state = cw.poll("/api/state")
    assert state["trading_day"] == MONDAY, "the poll still serves the replaced snapshot"
    stale = [path for path in JSON_ROUTES if cw.raw(path) == before[path]]
    assert not stale, f"a new snapshot left these answering the old bytes: {stale}"

    mark = trace.mark()
    after = {path: cw.raw(path) for path in JSON_ROUTES}
    assert not request_queries(trace, mark), "the replacement was not itself held"
    assert after == {path: cw.raw(path) for path in JSON_ROUTES}


def test_the_hold_is_bounded_and_forgets_snapshots_long_gone(monkeypatch, tmp_path):
    """Forty successive sessions are polled, one snapshot each. Reading the first again recomputes
    it: a hold that keeps every result it was ever given is not bounded."""
    cw, trace = world(monkeypatch, tmp_path)
    sessions = [d.isoformat() for d in trading_days_between(date(2026, 9, 1), date(2026, 10, 29))]
    sessions = sessions[-40:]
    assert len(sessions) == 40
    for day in sessions:
        store_close_snapshot(cw.world, day)
        cw.at(day, "16:30:00")
        cw.raw("/api/state")

    store_close_snapshot(cw.world, sessions[0])
    cw.at(sessions[0], "16:30:00")
    mark = trace.mark()
    cw.raw("/api/state")
    assert request_queries(trace, mark), (
        f"the snapshot of {sessions[0]}, forty sessions back, was still held"
    )


def test_requests_that_arrive_together_get_one_consistent_answer(monkeypatch, tmp_path):
    cw, trace = world(monkeypatch, tmp_path)
    cw.at(FRIDAY, "16:30:00")
    cw.raw("/api/state")
    write_engine_cycle(cw.world, local(MONDAY, "15:52:10"), 1)
    store_close_snapshot(cw.world, MONDAY)
    cw.at(MONDAY, "16:30:00")  # the held result is now stale; eight requests arrive at once

    barrier = threading.Barrier(8)
    answers: list[tuple[int, bytes]] = []
    errors: list[BaseException] = []

    def poll():
        try:
            client = app_module.app.test_client()
            barrier.wait(timeout=10)
            response = client.get("/api/state")
            answers.append((response.status_code, response.get_data()))
        except BaseException as exc:  # noqa: BLE001 - reported below, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=poll, name=f"poll-{n}") for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, f"a request thread raised: {errors[:2]!r}"
    assert [status for status, _ in answers] == [200] * 8
    assert len({body for _, body in answers}) == 1, "concurrent requests answered different bodies"

    mark = trace.mark()
    cw.raw("/api/state")
    assert not trace.shadow_history_selects(mark), "the concurrent build left nothing held"


# ---------------------------------------------------------------------------
# The first poll does not pay: a background thread has built the result
# ---------------------------------------------------------------------------


def wait_for_background_build(trace: SqlTrace, mark: int) -> list[str]:
    """The shadow_history statements run off the request thread since `mark`, once they stop."""
    deadline = time.monotonic() + WARM_WAIT_S

    def off_request() -> list[str]:
        return [
            sql
            for thread, sql in list(trace.statements[mark:])
            if thread != request_thread()
            and "shadow_history" in sql.lower()
            and not sql.upper().startswith(("CREATE", "DROP"))
        ]

    while time.monotonic() < deadline and not off_request():
        time.sleep(0.05)
    settled = len(off_request())
    while time.monotonic() < deadline:
        time.sleep(SETTLE_S)
        if len(off_request()) == settled:
            break
        settled = len(off_request())
    return off_request()


def first_polls_run_nothing(cw: ClosedWorld, trace: SqlTrace, what: str) -> None:
    mark = trace.mark()
    for path in JSON_ROUTES:
        cw.raw(path)
    queries = request_queries(trace, mark)
    assert not queries, (
        f"{what}: the first poll of {len(JSON_ROUTES)} routes ran {len(queries)} shadow_history "
        f"statements on the request thread, for example {queries[0][:140]!r}"
    )


def isolate_scheduler(monkeypatch) -> None:
    """A scheduler of this test's own, so `_scheduler_tick` runs no other test's slots."""
    import schedule as schedule_module

    scheduler = schedule_module.Scheduler()
    monkeypatch.setattr(
        app_module,
        "schedule",
        type("S", (), {"every": scheduler.every, "run_pending": scheduler.run_pending})(),
    )


def test_a_new_snapshot_is_built_in_the_background_before_the_first_poll(monkeypatch, tmp_path):
    cw, trace = world(monkeypatch, tmp_path)
    isolate_scheduler(monkeypatch)
    cw.at(FRIDAY, "16:30:00")
    for path in JSON_ROUTES:
        cw.raw(path)
    write_engine_cycle(cw.world, local(MONDAY, "15:52:10"), 1)
    cw.at(MONDAY, "16:00:50")
    mark = trace.mark()
    store_close_snapshot(cw.world, MONDAY)
    record = app_module.CycleRecord(
        cycle_id=f"{MONDAY}T16:00:00-05:00#1",
        started_at=local(MONDAY, "16:00:00").isoformat(),
        completed_at=local(MONDAY, "16:00:50").isoformat(),
        outcome="eod",
        changed=True,
        exit_code=0,
        running_cycle_id=None,
        running_since=None,
    )
    app_module._publish_cycle(record)
    app_module._scheduler_tick()

    built = wait_for_background_build(trace, mark)
    assert built, "no thread read shadow rows after the close step stored a new snapshot"
    first_polls_run_nothing(cw, trace, "after a new snapshot")


def test_the_daemon_start_builds_the_closed_result_before_the_first_poll(monkeypatch, tmp_path):
    cw, trace = world(monkeypatch, tmp_path)
    cw.at("2026-10-31", "02:00:00")
    isolate_scheduler(monkeypatch)
    mark = trace.mark()
    with monkeypatch.context() as stopped_sleep:
        stopped_sleep.setattr(app_module, "time", _SleepStops())
        with pytest.raises(_StopLoop):
            app_module.run_scheduler()

    built = wait_for_background_build(trace, mark)
    assert built, "no thread read shadow rows when the daemon started with a close stored"
    first_polls_run_nothing(cw, trace, "after the daemon start")


# ---------------------------------------------------------------------------
# Open: one query per symphony and day in a request
# ---------------------------------------------------------------------------


def test_an_open_poll_never_runs_the_same_query_twice_for_one_symphony(monkeypatch, tmp_path):
    cw, trace = world(monkeypatch, tmp_path)
    write_engine_cycle(cw.world, local(MONDAY, "10:00:20"), 1)
    cw.at(MONDAY, "10:01:00")
    cw.raw("/api/state")  # anything built once per process is built
    mark = trace.mark()
    body = cw.poll("/api/state")
    assert body["market_state"] == "open"

    per_symphony = [sql for sql in trace.shadow_history_selects(mark) if "symphony_id = '" in sql]
    assert per_symphony, "the trace recorded no per-symphony statement"
    repeated = {sql[:170]: n for sql, n in Counter(per_symphony).items() if n > 1}
    assert not repeated, (
        f"one open poll ran {len(repeated)} per-symphony statements more than once: {repeated}"
    )
