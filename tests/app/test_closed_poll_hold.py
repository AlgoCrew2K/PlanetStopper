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
  * while the market is open a request never runs the same per-symphony query twice;
  * a result built while one of its parts failed (a locked database, say) still answers, but is
    not held: the next poll builds again, and the result of a complete build is held.

What is pinned is query counts and bytes, never wall time: a database statement is counted by the
trace on every connection the process opens (`SqlTrace`), and bytes are compared at a frozen host
clock. The warm-up hooks the tests drive are: the daemon start is
`run_scheduler()` taking its first pass, and a new snapshot is announced by the publication of the
cycle record of the run that stored it (outcome "eod", changed) followed by a scheduler pass.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections import Counter
from datetime import date

import pytest

import analytics
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


# ---------------------------------------------------------------------------
# A build in which a part failed answers, and is not held
# ---------------------------------------------------------------------------
# A closed market holds a result until the next snapshot, which is all weekend. A build that
# lost a part to a transient fault (the reader of the shadow rows raised) answers with that part
# missing, as it always has; holding that answer would keep the figures blank until the next
# snapshot, long after the fault has passed. The fault is injected at the reader the build calls,
# raising the error a locked database raises on its first call(s); every later call runs the real
# reader. Expected answers are the ones of a build that met no fault, read from the same world.

DATABASE_LOCKED = "database is locked"

# part -> (module, reader, calls that fail, the route polled first)
FAILED_PARTS = {
    "portfolio figures": (analytics, "get_portfolio_today_change", 1, "/api/state"),
    "default window strip": (analytics, "compute_windowed_portfolio_strip", 1, "/api/state"),
    "hero series": (analytics, "get_portfolio_daily_returns_from_shadow", 1, "/api/state"),
    "hero series and its fallback": (
        analytics,
        "get_portfolio_daily_returns_from_shadow",
        2,
        "/api/state",
    ),
    "hero chart route": (
        analytics,
        "get_portfolio_bot_and_held_daily_returns",
        1,
        "/api/hero-chart/30d",
    ),
}


def fail_first_calls(monkeypatch, owner, name: str, times: int) -> dict:
    """The first `times` calls of owner.name raise a locked-database error, the rest run the real
    function. Returns the counters {"calls", "failed"}."""
    real = getattr(owner, name)
    seen = {"calls": 0, "failed": 0}

    def faulty(*args, **kwargs):
        seen["calls"] += 1
        if seen["failed"] < times:
            seen["failed"] += 1
            raise sqlite3.OperationalError(DATABASE_LOCKED)
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, faulty)
    return seen


def read_closed_routes(cw: ClosedWorld) -> dict[str, bytes]:
    return {path: cw.raw(path) for path in CLOSED_ROUTES}


@pytest.mark.parametrize("part", sorted(FAILED_PARTS))
def test_a_poll_answered_from_a_build_with_a_failed_part_is_not_held(monkeypatch, tmp_path, part):
    cw, trace = world(monkeypatch, tmp_path)
    cw.at(*CLOSED_INSTANTS["closed_frozen"])
    reference = read_closed_routes(cw)
    app_module._CLOSED_HOLD.clear()
    owner, reader, times, path = FAILED_PARTS[part]
    seen = fail_first_calls(monkeypatch, owner, reader, times)

    answered = cw.world.client.get(path)
    assert answered.status_code == 200, f"{path}: the poll with a failed {part} did not answer"
    assert seen["failed"] == times, f"fixture sanity: the {part} fault was not reached"
    assert answered.get_data() != reference[path], (
        f"fixture sanity: the failed {part} left {path} unchanged, so nothing is proven"
    )

    next_poll = cw.raw(path)
    assert next_poll == reference[path], (
        f"{path}: the answer of a build whose {part} failed was held, so the next poll "
        "answered the same degraded bytes instead of building again"
    )
    mark = trace.mark()
    held = cw.raw(path)
    queries = request_queries(trace, mark)
    assert not queries, (
        f"{path}: the answer of a complete build was not held ({len(queries)} statements)"
    )
    assert held == next_poll
    assert read_closed_routes(cw) == reference, (
        f"after the {part} recovered, a closed route answered other than a fault-free build"
    )


def test_a_strip_build_that_raised_is_not_held(monkeypatch, tmp_path):
    """The window strip raises when it cannot be built (a 500, never a degraded body); the next
    request builds again, and the complete result is then held."""
    cw, trace = world(monkeypatch, tmp_path)
    cw.at(*CLOSED_INSTANTS["closed_frozen"])
    path = "/api/strip/30d"
    reference = cw.raw(path)
    app_module._CLOSED_HOLD.clear()
    seen = fail_first_calls(monkeypatch, analytics, "compute_windowed_portfolio_strip", 1)

    assert cw.world.client.get(path).status_code == 500
    assert seen["failed"] == 1, "fixture sanity: the fault was not reached"
    assert cw.raw(path) == reference, "the failed strip build was held"
    mark = trace.mark()
    assert cw.raw(path) == reference
    assert not request_queries(trace, mark), "the complete strip was not held"


@pytest.mark.parametrize("part", ["portfolio figures", "default window strip"])
def test_a_warm_up_build_with_a_failed_part_is_not_held(monkeypatch, tmp_path, part):
    cw, trace = world(monkeypatch, tmp_path)
    isolate_scheduler(monkeypatch)
    cw.at(*CLOSED_INSTANTS["closed_frozen"])
    reference = read_closed_routes(cw)
    app_module._CLOSED_HOLD.clear()
    owner, reader, times, _ = FAILED_PARTS[part]
    seen = fail_first_calls(monkeypatch, owner, reader, times)

    mark = trace.mark()
    app_module._start_closed_warmup()
    assert wait_for_background_build(trace, mark), "the warm-up read no shadow rows"
    for thread in threading.enumerate():
        if thread.name == "closed-warmup":
            thread.join(timeout=WARM_WAIT_S)
    assert seen["failed"] == times, f"fixture sanity: the {part} fault was not reached"

    first = read_closed_routes(cw)
    mark = trace.mark()
    again = read_closed_routes(cw)
    assert first == reference, (
        f"the first polls after a warm-up whose {part} failed answered other than a fault-free "
        "build: the warm-up held the failed result"
    )
    assert not request_queries(trace, mark), "the complete result was not held"
    assert again == first


def test_an_open_poll_after_a_failed_part_is_computed_again(monkeypatch, tmp_path):
    """An open market holds nothing: a poll after a fault answers what a fault-free poll does."""
    cw, _ = world(monkeypatch, tmp_path)
    write_engine_cycle(cw.world, local(MONDAY, "10:00:20"), 1)
    cw.at(MONDAY, "10:01:00")
    reference = cw.raw("/api/state")
    fail_first_calls(monkeypatch, analytics, "get_portfolio_today_change", 1)

    cw.world.client.get("/api/state")  # the poll that meets the fault: any answer
    assert cw.raw("/api/state") == reference
