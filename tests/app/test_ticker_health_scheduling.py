"""The dead-ticker check's starts: the 01:30 slot, the catch-up at daemon start, one run at a time,
waiting for the other in-process Composer readers, and the market gate at every start.

Contract pinned here (``app.py``, next to the regime label and incubation wrappers; the implementer
reads this file and the handoff, not the plan):

* ``_env_file_settings(keys) -> dict``: the body of the tenure review's settings reader lifted out
  unchanged. For each key the value in ``dotenv_values(ENV_FILE_PATH)`` wins (a blank value is a
  value), else ``os.environ``, else the key is left out. ``_tenure_review_settings()`` delegates to
  it with the review's own keys and returns exactly what it returned before.
* ``_ticker_health_settings()``: ``_env_file_settings`` over ``ACCOUNT_INDIVIDUAL``,
  ``ACCOUNT_ROTH``, ``ACCOUNT_TRAD``, ``ALPACA_KEY``, ``ALPACA_SECRET`` and
  ``DISCORD_WEBHOOK_URL`` and nothing else.
* ``_TICKER_HEALTH_WAITS_FOR_THREADS``: the thread names ``incubation-tick``,
  ``retirement-recommender-tick``, ``retirement-run-now`` and ``tenure-review-on-demand``.
  ``_ticker_health_other_reader_running()`` is true while a live thread carries one of them, while
  ``_RETIREMENT_RUN_LOCK`` is held, while ``tenure_review.REVIEW_RUN_STATE`` (looked up on the
  module at each call) reports running, and on any error while finding out. It is false for the
  check's own threads, so a run never waits for itself.
* ``_ticker_health_run_body(trigger)``: takes ``_TICKER_HEALTH_RUN_LOCK`` without blocking and
  returns at once, writing nothing, when it is held (one INFO line); else it imports the runner
  lazily and calls ``advisors.ticker_health_runner.run_ticker_health_check`` (looked up on the
  module at call time) with ``account_ids``, ``account_labels`` (from ``advisors.
  tenure_review_runner.configured_account_ids`` and ``configured_account_labels`` over the
  settings), ``alpaca_key``, ``alpaca_secret``, ``webhook_url``, ``trigger`` and
  ``other_reader_running`` (the predicate above), then releases the lock in a ``finally``,
  whatever escaped. An ``Exception`` is logged at WARNING or above by class name only and never
  escapes; a ``BaseException`` escapes.
* ``_run_ticker_health_tick()``: starts ``threading.Thread(target=_ticker_health_run_body,
  args=("nightly",), daemon=True, name="ticker-health-check")`` and returns at once.
* ``_ticker_health_catch_up_due(now_utc) -> bool``: false while the market is open at that instant;
  else true when no completed run ended at or after the close instant of the most recent completed
  session (half days at their early close); a database error answers false and logs the exception
  class name only.
* ``_start_ticker_health_catch_up() -> bool``: reads the clock as ``datetime.now(...)`` through
  app.py's module-level ``datetime`` name; when due starts the same body on a daemon thread named
  ``ticker-health-catch-up`` with trigger ``catch_up`` and returns True, else returns False. It
  never raises.
* ``run_scheduler`` registers ``_run_ticker_health_tick`` once, guarded, at 01:30 host time and
  never starts the catch-up. The ``__main__`` block calls ``_start_ticker_health_catch_up()`` as
  one direct statement after ``_start_account_refresh("startup")`` and before the scheduler thread.
  The run function is reached from the body only; the body from the two starts only.

Patch targets: the runner's ``datetime`` name (``advisors.ticker_health_runner.datetime``) and
app.py's ``datetime`` name are replaced to fix the clock; ``time.sleep`` is the router's;
``database.get_advisor_observations_for_role`` is the stored-run reader both the runner and the
catch-up decision use; ``threading.Thread`` is looked up on the ``threading`` module at call time.
Module-level mutable state: ``_TICKER_HEALTH_RUN_LOCK`` (a test that ends with it held fails).

World. The Composer roster, trees, Alpaca asset records and Discord replies are the captured
records behind the ticker check router (tests/advisors/_ticker_health_http_router.py). The two
symphonies of the world are the captured roster's entries with invented display names (derived,
labelled); each is served one tree derived from the captured one that names SHNY (a captured dead
ticker) and SPY (a captured healthy one). Earlier runs are written by the real runner under an
injected clock, never as hand-built rows. Nothing reaches the network and no sleep is real: a test
that holds a run mid-way blocks the router's pause on an event.
"""

from __future__ import annotations

import ast
import datetime as dt
import logging
import sqlite3
import threading
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from bs4 import BeautifulSoup

import app as app_module
import database
import market_calendar
from advisors import tenure_review
from advisors import tenure_review_runner as tenure_runner
from advisors import ticker_health as th
from advisors import ticker_health_runner as runner
from tests.advisors import _composer_http_router as h
from tests.advisors import _ticker_health_http_router as t
from tests.app import _tenure_review_job_builders as jb
from tests.app.test_scheduler_survival import _func_of, _run_loop, _set_due

ET = ZoneInfo("America/New_York")
ACCOUNT = h.ACCOUNT_ID
FIRST_ID, SECOND_ID = t.PLANET_LQD_ID, t.TROLLING_ID
FIRST_NAME, SECOND_NAME = "Quiet Harbor", "Amber Field"
DEAD, HEALTHY = "SHNY", "SPY"

# The environment file holds these; a process variable with another value proves the file wins.
OTHER_KEY = "PKZZZZZZZZZZZZZZZZZZ"
ENV_KEYS = (
    "ACCOUNT_INDIVIDUAL",
    "ACCOUNT_ROTH",
    "ACCOUNT_TRAD",
    "ALPACA_KEY",
    "ALPACA_SECRET",
    "DISCORD_WEBHOOK_URL",
    "TENURE_REVIEW_MIN_TRADING_DAYS",
    "LIVE_EXECUTION",
)

# Monday 2026-11-16 20:30 EST: after the close, the night the 01:30 UTC slot would run.
NIGHT = t.NIGHT
TUE_PRE_OPEN = datetime(2026, 11, 17, 6, 0, tzinfo=ET)
TUE_IN_SESSION = datetime(2026, 11, 17, 11, 0, tzinfo=ET)
TUE_NINE = datetime(2026, 11, 17, 9, 0, tzinfo=ET)
TUE_OPEN = datetime(2026, 11, 17, 9, 30, tzinfo=ET)

WAITED_THREADS = (
    "incubation-tick",
    "retirement-recommender-tick",
    "retirement-run-now",
    "tenure-review-on-demand",
)
CHECK_THREADS = ("ticker-health-check", "ticker-health-catch-up", "test-ticker-run")

# Longest any test waits for an event it expects; never a time a verdict is judged by.
WAIT_S = 30.0


def need(name: str):
    found = getattr(app_module, name, None)
    assert found is not None, f"app.{name} is missing"
    return found


def isolate_env(monkeypatch, tmp_path: Path, **values: str) -> Path:
    """Point the app at a scratch environment file holding exactly ``values``; the developer's
    file is never read and no process variable the check reads survives."""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / "scratch.env"
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(path))
    return path


def clocked_datetime(clock):
    """A ``datetime`` whose ``now`` reads ``clock()`` (an aware instant)."""

    class _Clocked(datetime):
        @classmethod
        def now(cls, tz=None):
            moment = clock()
            return moment.replace(tzinfo=None) if tz is None else moment.astimezone(tz)

    return _Clocked


def set_clocks(monkeypatch, router, instant: datetime, *, app_clock: bool = True) -> None:
    """The runner reads ``instant`` plus the router's recorded sleeps; the app reads ``instant``."""
    start = instant.astimezone(UTC)
    monkeypatch.setattr(runner, "datetime", clocked_datetime(t.SleepClock(router, start)))
    if app_clock:
        monkeypatch.setattr(app_module, "datetime", clocked_datetime(lambda: instant))


@pytest.fixture
def world(monkeypatch, tmp_path):
    router = t.TickerHealthRouter().install(monkeypatch)
    tree = t.tree_with(assets=[DEAD, HEALTHY])
    router.set_roster(
        t.roster_entry(FIRST_ID, name=FIRST_NAME), t.roster_entry(SECOND_ID, name=SECOND_NAME)
    )
    router.set_tree(FIRST_ID, tree)
    router.set_tree(SECOND_ID, tree)
    isolate_env(
        monkeypatch,
        tmp_path,
        ACCOUNT_ROTH=ACCOUNT,
        ALPACA_KEY=t.ALPACA_KEY,
        ALPACA_SECRET=t.ALPACA_SECRET,
    )
    return router


@pytest.fixture(autouse=True)
def _nothing_left_running():
    """No test ends with a check thread alive or the single-flight lock held."""
    yield
    for thread in threading.enumerate():
        if thread.name in CHECK_THREADS and thread.is_alive():
            thread.join(timeout=10)
    alive = [x.name for x in threading.enumerate() if x.name in CHECK_THREADS and x.is_alive()]
    lock = getattr(app_module, "_TICKER_HEALTH_RUN_LOCK", None)
    held = lock is not None and lock.locked()
    if held:
        lock.release()
    retirement_held = app_module._RETIREMENT_RUN_LOCK.locked()
    if retirement_held:
        app_module._RETIREMENT_RUN_LOCK.release()
    assert not alive, f"a check thread outlived its test: {alive}"
    assert not held, "a test ended with the single-flight lock held"
    assert not retirement_held, "a test ended with the retirement run lock held"


class Hold:
    """Blocks the thread that makes the ``at_sleep``-th pause until ``release`` is set."""

    def __init__(self, router, monkeypatch, *, at_sleep: int = 1) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self._pauses = 0

        def pause(seconds):
            router.sleep(seconds)
            self._pauses += 1
            if self._pauses == at_sleep:
                self.entered.set()
                self.release.wait(timeout=WAIT_S)

        monkeypatch.setattr(time, "sleep", pause)


def start_run(trigger: str = "nightly") -> threading.Thread:
    """The body on a real thread of the test's own (a name the check does not wait for)."""
    thread = jb.REAL_THREAD(
        target=need("_ticker_health_run_body"),
        args=(trigger,),
        name="test-ticker-run",
        daemon=True,
    )
    thread.start()
    return thread


def join_check_threads() -> None:
    for thread in threading.enumerate():
        if thread.name in ("ticker-health-check", "ticker-health-catch-up") and thread.is_alive():
            thread.join(timeout=WAIT_S)


def run_records() -> list[dict]:
    """Every stored run record, newest first."""
    rows = database.get_advisor_observations_for_role(th.RUN_ROLE, 100)
    return [row["raw_response"] for row in rows]


def seed_run(router, instant: datetime, *, completed: bool = True) -> runner.RunOutcome:
    """One real run of the real runner at ``instant``; an unfinished one when the roster read fails."""
    saved = router.statistics_default
    if not completed:
        router.statistics_default = h.error_reply(503)
    try:
        outcome = runner.run_ticker_health_check(
            account_ids=[ACCOUNT],
            account_labels={ACCOUNT: "Roth IRA"},
            alpaca_key=t.ALPACA_KEY,
            alpaca_secret=t.ALPACA_SECRET,
            webhook_url=None,
            trigger="nightly",
            other_reader_running=lambda: False,
            clock=lambda: instant.astimezone(UTC),
        )
    finally:
        router.statistics_default = saved
    assert outcome.completed is completed, f"the seeded run ended {outcome.status}"
    return outcome


@pytest.fixture
def spy(monkeypatch):
    """The runner's run function replaced by a recorder that answers a completed outcome."""
    calls: list[dict] = []

    def fake(**kwargs):
        calls.append(kwargs)
        return runner.RunOutcome("0" * 32, "completed", True)

    monkeypatch.setattr(runner, "run_ticker_health_check", fake)
    return calls


@pytest.fixture
def live_thread():
    """Start a live thread with a given name; all end at teardown."""
    started: list[threading.Thread] = []
    release = threading.Event()

    def start(name: str) -> threading.Thread:
        thread = jb.REAL_THREAD(target=release.wait, args=(WAIT_S,), name=name, daemon=True)
        thread.start()
        started.append(thread)
        return thread

    yield start
    release.set()
    for thread in started:
        thread.join(timeout=10)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_the_env_file_value_wins_then_the_process_value_then_the_key_is_left_out(
        self, monkeypatch, tmp_path
    ):
        reader = need("_env_file_settings")
        isolate_env(monkeypatch, tmp_path, ALPACA_KEY="from-the-file", ALPACA_SECRET="")
        monkeypatch.setenv("ALPACA_KEY", "from-the-process")
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "process-only")

        got = reader(("ALPACA_KEY", "ALPACA_SECRET", "DISCORD_WEBHOOK_URL", "ACCOUNT_TRAD"))

        assert got == {
            "ALPACA_KEY": "from-the-file",
            "ALPACA_SECRET": "",
            "DISCORD_WEBHOOK_URL": "process-only",
        }

    def test_the_check_reads_its_six_keys_and_nothing_else(self, monkeypatch, tmp_path):
        isolate_env(
            monkeypatch,
            tmp_path,
            ACCOUNT_ROTH=ACCOUNT,
            ALPACA_KEY=t.ALPACA_KEY,
            ALPACA_SECRET=t.ALPACA_SECRET,
            DISCORD_WEBHOOK_URL=t.WEBHOOK_URL,
            TENURE_REVIEW_MIN_TRADING_DAYS="200",
            LIVE_EXECUTION="True",
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-be-read")

        got = need("_ticker_health_settings")()

        assert got == {
            "ACCOUNT_ROTH": ACCOUNT,
            "ALPACA_KEY": t.ALPACA_KEY,
            "ALPACA_SECRET": t.ALPACA_SECRET,
            "DISCORD_WEBHOOK_URL": t.WEBHOOK_URL,
        }

    def test_the_review_settings_are_still_its_threshold_and_accounts_only(
        self, monkeypatch, tmp_path
    ):
        isolate_env(
            monkeypatch,
            tmp_path,
            ACCOUNT_TRAD=ACCOUNT,
            TENURE_REVIEW_MIN_TRADING_DAYS="200",
            ALPACA_KEY=t.ALPACA_KEY,
            DISCORD_WEBHOOK_URL=t.WEBHOOK_URL,
        )
        monkeypatch.setenv("ACCOUNT_INDIVIDUAL", "from-the-process")

        got = app_module._tenure_review_settings()

        assert got == {
            "TENURE_REVIEW_MIN_TRADING_DAYS": "200",
            "ACCOUNT_TRAD": ACCOUNT,
            "ACCOUNT_INDIVIDUAL": "from-the-process",
        }


# ---------------------------------------------------------------------------
# The predicate that says another Composer reader is running
# ---------------------------------------------------------------------------


class TestOtherReaderRunning:
    def test_nothing_running_means_no_other_reader(self):
        assert need("_ticker_health_other_reader_running")() is False

    @pytest.mark.parametrize("name", WAITED_THREADS)
    def test_a_live_thread_of_a_waited_job_means_a_reader_is_running(self, live_thread, name):
        predicate = need("_ticker_health_other_reader_running")
        assert predicate() is False, "test premise: nothing runs before the thread starts"

        live_thread(name)

        assert predicate() is True

    def test_a_waited_thread_that_has_ended_no_longer_counts(self):
        predicate = need("_ticker_health_other_reader_running")
        thread = jb.REAL_THREAD(target=lambda: None, name="incubation-tick", daemon=True)
        thread.start()
        thread.join(timeout=10)

        assert predicate() is False

    @pytest.mark.parametrize(
        "name",
        [
            "ticker-health-check",
            "ticker-health-catch-up",
            "regime-label",
            "engine-cycle",
            "account-refresh-minute",
            "closed-warmup",
        ],
    )
    def test_other_live_threads_never_count_and_the_check_never_waits_for_itself(
        self, live_thread, name
    ):
        live_thread(name)

        assert need("_ticker_health_other_reader_running")() is False

    def test_the_retirement_run_slot_held_between_its_claim_and_its_thread_counts(self):
        predicate = need("_ticker_health_other_reader_running")
        assert app_module._RETIREMENT_RUN_LOCK.acquire(blocking=False)
        try:
            running = predicate()
        finally:
            app_module._RETIREMENT_RUN_LOCK.release()

        assert running is True
        assert predicate() is False

    def test_a_tenure_review_in_flight_counts_until_it_ends(self):
        predicate = need("_ticker_health_other_reader_running")
        owner = tenure_review.REVIEW_RUN_STATE.try_start("on_demand")
        assert owner, "test premise: the review slot was free"
        try:
            running = predicate()
        finally:
            tenure_review.REVIEW_RUN_STATE.release(owner)

        assert running is True
        assert predicate() is False

    def test_an_error_while_finding_out_answers_running_so_the_check_waits(self, monkeypatch):
        predicate = need("_ticker_health_other_reader_running")

        def broken(*_args, **_kwargs):
            raise RuntimeError("state unreadable")

        with monkeypatch.context() as patch:
            patch.setattr(tenure_review.REVIEW_RUN_STATE, "snapshot", broken)
            by_state = predicate()
        with monkeypatch.context() as patch:
            patch.setattr(threading, "enumerate", broken)
            by_threads = predicate()

        assert by_state is True
        assert by_threads is True


# ---------------------------------------------------------------------------
# The body both starts run
# ---------------------------------------------------------------------------


class TestTheBodyHandsTheRunnerItsInputs:
    @pytest.mark.parametrize("trigger", ["nightly", "catch_up"])
    def test_the_file_settings_and_the_trigger_reach_the_runner(
        self, world, spy, monkeypatch, tmp_path, trigger
    ):
        isolate_env(
            monkeypatch,
            tmp_path,
            ACCOUNT_ROTH=ACCOUNT,
            ALPACA_KEY=t.ALPACA_KEY,
            ALPACA_SECRET=t.ALPACA_SECRET,
            DISCORD_WEBHOOK_URL=t.WEBHOOK_URL,
        )
        monkeypatch.setenv("ALPACA_KEY", OTHER_KEY)

        need("_ticker_health_run_body")(trigger)

        (call,) = spy
        assert set(call) - {"clock"} == {
            "account_ids",
            "account_labels",
            "alpaca_key",
            "alpaca_secret",
            "webhook_url",
            "trigger",
            "other_reader_running",
        }
        assert call["account_ids"] == [ACCOUNT]
        assert call["account_labels"] == tenure_runner.configured_account_labels(
            {"ACCOUNT_ROTH": ACCOUNT}
        )
        assert call["alpaca_key"] == t.ALPACA_KEY, "the file wins over the process variable"
        assert call["alpaca_secret"] == t.ALPACA_SECRET
        assert call["webhook_url"] == t.WEBHOOK_URL
        assert call["trigger"] == trigger

    def test_no_configured_account_reaches_the_runner_as_no_account(
        self, world, spy, monkeypatch, tmp_path
    ):
        isolate_env(monkeypatch, tmp_path, ALPACA_KEY=t.ALPACA_KEY, ALPACA_SECRET=t.ALPACA_SECRET)

        need("_ticker_health_run_body")("nightly")

        (call,) = spy
        assert call["account_ids"] == []

    def test_the_wait_callable_is_the_live_predicate(self, world, spy, live_thread):
        need("_ticker_health_run_body")("nightly")
        (call,) = spy
        wait_for_others = call["other_reader_running"]
        assert callable(wait_for_others)
        assert wait_for_others() is False

        live_thread("tenure-review-on-demand")

        assert wait_for_others() is True


class TestTheBodyNeverLeavesTheLockTaken:
    def test_an_exception_from_the_runner_is_logged_by_class_name_only_and_never_escapes(
        self, world, monkeypatch, caplog
    ):
        def failing(**_kwargs):
            raise RuntimeError("detail-that-must-not-appear")

        monkeypatch.setattr(runner, "run_ticker_health_check", failing)

        with caplog.at_level(logging.DEBUG):
            need("_ticker_health_run_body")("nightly")

        flagged = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("RuntimeError" in message for message in flagged), flagged
        assert "detail-that-must-not-appear" not in caplog.text

    @pytest.mark.parametrize(
        "failure", [RuntimeError("x"), SystemExit(3)], ids=["exception", "exit status"]
    )
    def test_the_next_start_runs_after_a_failed_one(self, world, monkeypatch, failure):
        def failing(**_kwargs):
            raise failure

        with monkeypatch.context() as patch:
            patch.setattr(runner, "run_ticker_health_check", failing)
            if isinstance(failure, SystemExit):
                with pytest.raises(SystemExit):
                    need("_ticker_health_run_body")("nightly")
            else:
                need("_ticker_health_run_body")("nightly")
        calls: list[dict] = []
        monkeypatch.setattr(
            runner,
            "run_ticker_health_check",
            lambda **kwargs: calls.append(kwargs) or runner.RunOutcome("1" * 32, "completed", True),
        )

        need("_ticker_health_run_body")("nightly")

        assert len(calls) == 1, "the lock stayed taken after the failed start"

    def test_the_lock_is_free_after_a_normal_run(self, world, monkeypatch):
        set_clocks(monkeypatch, world, NIGHT)
        body = need("_ticker_health_run_body")
        body("nightly")

        body("nightly")

        assert len(run_records()) == 2


class TestAStartedRun:
    def test_the_slot_makes_its_reads_and_stores_one_completed_row(self, world, monkeypatch):
        set_clocks(monkeypatch, world, NIGHT)
        jb.install_inline_threads(monkeypatch)

        need("_run_ticker_health_tick")()

        (record,) = run_records()
        assert record["status"] == "completed"
        assert record["completed"] is True
        assert record["trigger"] == "nightly"
        assert {w["symphony_id"] for w in record["warnings"]} == {FIRST_ID, SECOND_ID}
        assert {w["ticker"] for w in record["warnings"]} == {DEAD}
        assert world.violations == []
        assert world.score_ids() == [FIRST_ID, SECOND_ID]
        assert Counter(world.asset_symbols()) == {DEAD: 1, HEALTHY: 1}
        assert world.discord_posts() == [], "no webhook is configured: nothing is posted"
        for request in world.asset_requests():
            assert request.headers["APCA-API-KEY-ID"] == t.ALPACA_KEY
            assert request.headers["APCA-API-SECRET-KEY"] == t.ALPACA_SECRET

    def test_the_slot_starts_a_daemon_thread_with_the_nightly_trigger(self, world, monkeypatch):
        set_clocks(monkeypatch, world, NIGHT)
        started = jb.install_inline_threads(monkeypatch)

        need("_run_ticker_health_tick")()

        (thread,) = started
        assert thread.name == "ticker-health-check"
        assert thread.daemon is True
        assert run_records()[0]["trigger"] == "nightly"

    def test_the_slot_callable_returns_while_the_run_is_still_in_progress(self, world, monkeypatch):
        set_clocks(monkeypatch, world, NIGHT)
        hold = Hold(world, monkeypatch)
        tick = need("_run_ticker_health_tick")

        began = time.perf_counter()
        tick()
        elapsed = time.perf_counter() - began

        try:
            assert hold.entered.wait(timeout=10), "the run never started"
            assert elapsed < 1.0, f"the slot callable took {elapsed:.1f} s: it ran the check inline"
            thread = next(
                (x for x in threading.enumerate() if x.name == "ticker-health-check"), None
            )
            assert thread is not None, "no thread named ticker-health-check is running"
            assert thread.daemon is True
            assert thread.is_alive()
        finally:
            hold.release.set()
            join_check_threads()
        assert run_records()[0]["trigger"] == "nightly"

    @pytest.mark.parametrize(
        "instant",
        [
            datetime(2026, 11, 22, 1, 30, tzinfo=UTC),
            datetime(2026, 11, 23, 1, 30, tzinfo=UTC),
            datetime(2026, 11, 27, 1, 30, tzinfo=UTC),
            datetime(2026, 11, 26, 10, 0, tzinfo=ET),
        ],
        ids=["Saturday night", "Sunday night", "Thanksgiving night", "Thanksgiving morning"],
    )
    def test_a_weekend_or_a_holiday_runs_and_completes_as_a_trading_day_does(
        self, world, monkeypatch, instant
    ):
        set_clocks(monkeypatch, world, instant)
        jb.install_inline_threads(monkeypatch)

        need("_run_ticker_health_tick")()

        (record,) = run_records()
        assert record["completed"] is True, record
        assert len(record["warnings"]) == 2


class TestInsideASession:
    @pytest.mark.parametrize("trigger", ["nightly", "catch_up"])
    def test_a_start_stores_a_skipped_run_makes_no_request_and_keeps_the_earlier_result(
        self, world, monkeypatch, trigger
    ):
        earlier = seed_run(world, NIGHT)
        requests_before = len(world.requests)
        set_clocks(monkeypatch, world, TUE_IN_SESSION)

        need("_ticker_health_run_body")(trigger)

        assert world.requests[requests_before:] == []
        latest = runner.read_latest_runs()
        assert latest.latest["status"] == "skipped"
        assert latest.latest["reason"] == "market open"
        assert latest.latest["trigger"] == trigger
        assert latest.latest_completed["run_id"] == earlier.run_id

    def test_the_engine_spawn_and_account_refresh_still_fire_every_minute_while_the_slot_fires(
        self, world, monkeypatch, stub_engine, join_engine_threads
    ):
        tick_fn = need("_run_ticker_health_tick")
        set_clocks(monkeypatch, world, TUE_IN_SESSION, app_clock=False)
        refreshes: list[str] = []
        monkeypatch.setattr(app_module, "_start_account_refresh", lambda *a: refreshes.append("x"))

        def _before(tick, jobs):
            join_engine_threads()
            _set_due(jobs, {tick_fn: 5} if tick == 1 else {})
            for job in jobs:
                if job.unit == "minutes":
                    job.next_run = dt.datetime.now() - dt.timedelta(seconds=1)

        _run_loop(monkeypatch, ticks=3, before_tick=_before)
        join_engine_threads()
        join_check_threads()

        assert stub_engine.starts == 3, "every :00 pass must spawn the engine"
        assert len(refreshes) == 3, "every :00 pass must start the account refresh"
        assert world.requests == [], "the check made a request inside a session"
        assert run_records()[0]["status"] == "skipped"


# ---------------------------------------------------------------------------
# Waiting for the other in-process Composer readers
# ---------------------------------------------------------------------------

_READERS = [*WAITED_THREADS, "retirement run slot", "review run slot"]


def _hold_reader(kind: str):
    """Make one other Composer reader run; returns the callable that ends it."""
    if kind in WAITED_THREADS:
        release = threading.Event()
        thread = jb.REAL_THREAD(target=release.wait, args=(WAIT_S,), name=kind, daemon=True)
        thread.start()

        def end() -> None:
            release.set()
            thread.join(timeout=10)
            assert not thread.is_alive()

        return end
    if kind == "retirement run slot":
        assert app_module._RETIREMENT_RUN_LOCK.acquire(blocking=False)
        return app_module._RETIREMENT_RUN_LOCK.release
    owner = tenure_review.REVIEW_RUN_STATE.try_start("on_demand")
    assert owner, "test premise: the review slot was free"
    return lambda: tenure_review.REVIEW_RUN_STATE.release(owner)


class TestWaitingForTheOtherReaders:
    @pytest.mark.parametrize("reader", _READERS)
    def test_the_check_makes_no_composer_request_until_the_other_reader_has_ended(
        self, world, monkeypatch, reader
    ):
        set_clocks(monkeypatch, world, NIGHT)
        end = _hold_reader(reader)
        polls: list[float] = []

        def pause(seconds):
            world.sleep(seconds)
            if seconds == runner.GATE_POLL_SECONDS:
                polls.append(seconds)
                if len(polls) == 3:
                    end()
                    world.events.append(("reader_ended",))

        monkeypatch.setattr(time, "sleep", pause)

        need("_ticker_health_run_body")("nightly")

        kinds = [event[0] for event in world.events]
        assert "reader_ended" in kinds, "the check never waited for the other reader"
        assert kinds.index("reader_ended") < kinds.index("request"), (
            "a Composer request was made while the other reader was still running"
        )
        assert len(polls) == 3
        (record,) = run_records()
        assert record["completed"] is True


# ---------------------------------------------------------------------------
# One run at a time, whichever way it was started
# ---------------------------------------------------------------------------


class TestOneRunAtATime:
    @pytest.mark.parametrize(
        "second",
        ["nightly slot", "catch-up start", "direct body"],
    )
    def test_a_second_start_while_a_run_is_in_flight_starts_nothing_and_writes_nothing(
        self, world, monkeypatch, caplog, second
    ):
        set_clocks(monkeypatch, world, NIGHT)
        hold = Hold(world, monkeypatch)
        first = start_run("nightly")
        assert hold.entered.wait(timeout=10), "the first run never reached its first pause"
        requests_before = len(world.requests)
        jb.install_inline_threads(monkeypatch)
        starts = {
            "nightly slot": need("_run_ticker_health_tick"),
            "catch-up start": need("_start_ticker_health_catch_up"),
            "direct body": lambda: need("_ticker_health_run_body")("catch_up"),
        }

        with caplog.at_level(logging.INFO):
            starts[second]()
        during = len(world.requests) - requests_before
        hold.release.set()
        first.join(timeout=WAIT_S)

        assert not first.is_alive()
        assert during == 0, "the second start made requests while the first run was in flight"
        assert len(run_records()) == 1, "the second start wrote a run row"
        assert max(Counter(world.score_ids()).values()) == 1
        assert len(world.of_kind("statistics")) == 1
        assert any(
            r.levelno == logging.INFO
            and ("ticker" in r.getMessage().lower() or "check" in r.getMessage().lower())
            for r in caplog.records
        ), "the declined start must log one INFO line naming the check"

    def test_the_start_after_the_first_run_ended_is_admitted(self, world, monkeypatch):
        set_clocks(monkeypatch, world, NIGHT)
        first = start_run("nightly")
        first.join(timeout=WAIT_S)
        assert not first.is_alive()
        jb.install_inline_threads(monkeypatch)

        need("_run_ticker_health_tick")()

        assert len(run_records()) == 2


# ---------------------------------------------------------------------------
# The catch-up at daemon start
# ---------------------------------------------------------------------------

_FRI_NIGHT = datetime(2026, 11, 13, 20, 30, tzinfo=ET)
_MON_CLOSE = datetime(2026, 11, 16, 16, 0, tzinfo=ET)
_MON_NIGHT = datetime(2026, 11, 16, 20, 30, tzinfo=ET)

_CATCH_UP_CASES = [
    pytest.param(TUE_PRE_OPEN, [], True, id="no run was ever stored"),
    pytest.param(
        TUE_PRE_OPEN, [(_FRI_NIGHT, True)], True, id="the last completed run predates the close"
    ),
    pytest.param(TUE_PRE_OPEN, [(_MON_NIGHT, True)], False, id="a run completed after the close"),
    pytest.param(
        TUE_PRE_OPEN, [(_MON_CLOSE, True)], False, id="a run completed at the close instant"
    ),
    pytest.param(
        TUE_PRE_OPEN,
        [(_FRI_NIGHT, True), (_MON_NIGHT, False)],
        True,
        id="a run after the close did not complete",
    ),
    pytest.param(
        datetime(2026, 11, 16, 17, 0, tzinfo=ET),
        [(_FRI_NIGHT, True)],
        True,
        id="the evening of a trading day before the night run",
    ),
    pytest.param(
        datetime(2026, 11, 21, 10, 0, tzinfo=ET),
        [(datetime(2026, 11, 20, 20, 30, tzinfo=ET), True)],
        False,
        id="Saturday after Friday's run",
    ),
    pytest.param(
        datetime(2026, 11, 21, 10, 0, tzinfo=ET),
        [(datetime(2026, 11, 19, 20, 30, tzinfo=ET), True)],
        True,
        id="Saturday with Thursday's run the last",
    ),
    pytest.param(
        datetime(2026, 11, 26, 10, 0, tzinfo=ET),
        [(datetime(2026, 11, 25, 20, 30, tzinfo=ET), True)],
        False,
        id="Thanksgiving morning after Wednesday's run",
    ),
    pytest.param(
        datetime(2026, 11, 26, 10, 0, tzinfo=ET),
        [(datetime(2026, 11, 24, 20, 30, tzinfo=ET), True)],
        True,
        id="Thanksgiving morning with Tuesday's run the last",
    ),
    pytest.param(
        datetime(2026, 11, 27, 18, 0, tzinfo=ET),
        [(datetime(2026, 11, 27, 13, 5, tzinfo=ET), True)],
        False,
        id="after a half day's early close and a run since it",
    ),
    pytest.param(
        datetime(2026, 11, 27, 18, 0, tzinfo=ET),
        [(datetime(2026, 11, 26, 20, 30, tzinfo=ET), True)],
        True,
        id="after a half day's early close with no run since it",
    ),
    pytest.param(TUE_IN_SESSION, [], False, id="inside a session with no run"),
    pytest.param(
        TUE_IN_SESSION, [(_FRI_NIGHT, True)], False, id="inside a session with a stale run"
    ),
]


class TestTheCatchUpRule:
    @pytest.mark.parametrize(("now_et", "seeds", "expected"), _CATCH_UP_CASES)
    def test_it_is_due_exactly_when_no_run_completed_since_the_latest_close(
        self, world, now_et, seeds, expected
    ):
        due = need("_ticker_health_catch_up_due")
        for instant, completed in seeds:
            seed_run(world, instant, completed=completed)

        assert due(now_et.astimezone(UTC)) is expected

    def test_a_database_error_answers_not_due_and_logs_only_the_class_name(
        self, world, monkeypatch, caplog
    ):
        def broken(*_args, **_kwargs):
            raise sqlite3.OperationalError("table-detail-that-must-not-appear")

        monkeypatch.setattr(database, "get_advisor_observations_for_role", broken)

        with caplog.at_level(logging.DEBUG):
            answered = need("_ticker_health_catch_up_due")(TUE_PRE_OPEN.astimezone(UTC))

        assert answered is False
        flagged = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("OperationalError" in message for message in flagged), flagged
        assert "table-detail-that-must-not-appear" not in caplog.text


class TestTheCatchUpStart:
    def test_a_daemon_started_before_the_open_with_no_run_since_the_close_catches_up_in_time(
        self, world, monkeypatch
    ):
        seed_run(world, _FRI_NIGHT)
        set_clocks(monkeypatch, world, TUE_PRE_OPEN)
        started = jb.install_inline_threads(monkeypatch)

        began = need("_start_ticker_health_catch_up")()

        assert began is True
        (thread,) = started
        assert thread.name == "ticker-health-catch-up"
        assert thread.daemon is True
        latest = runner.read_latest_runs().latest
        assert latest["trigger"] == "catch_up"
        assert latest["completed"] is True
        assert datetime.fromisoformat(latest["ended_at"]) < TUE_OPEN

    def test_a_run_that_died_leaves_no_row_and_the_next_start_catches_up(self, world, monkeypatch):
        set_clocks(monkeypatch, world, TUE_PRE_OPEN)
        world.set_tree(FIRST_ID, h.raising(SystemExit(1)))
        with pytest.raises(SystemExit):
            need("_ticker_health_run_body")("nightly")
        assert run_records() == [], "a run that died stored a row"
        world.set_tree(FIRST_ID, t.tree_with(assets=[DEAD, HEALTHY]))
        started = jb.install_inline_threads(monkeypatch)

        assert need("_ticker_health_catch_up_due")(TUE_PRE_OPEN.astimezone(UTC)) is True
        began = need("_start_ticker_health_catch_up")()

        assert began is True
        assert len(started) == 1
        latest = runner.read_latest_runs().latest
        assert latest["trigger"] == "catch_up"
        assert latest["completed"] is True

    def test_a_run_completed_since_the_close_starts_nothing(self, world, monkeypatch):
        seed_run(world, _MON_NIGHT)
        requests_before = len(world.requests)
        set_clocks(monkeypatch, world, TUE_PRE_OPEN)
        started = jb.install_inline_threads(monkeypatch)

        began = need("_start_ticker_health_catch_up")()

        assert began is False
        assert started == []
        assert world.requests[requests_before:] == []
        assert len(run_records()) == 1

    def test_a_daemon_started_inside_a_session_starts_nothing_and_asks_nobody(
        self, world, monkeypatch
    ):
        set_clocks(monkeypatch, world, TUE_IN_SESSION)
        started = jb.install_inline_threads(monkeypatch)

        began = need("_start_ticker_health_catch_up")()

        assert began is False
        assert started == []
        assert world.requests == []
        assert run_records() == []

    def test_a_database_error_starts_nothing_and_raises_nothing(self, world, monkeypatch, caplog):
        set_clocks(monkeypatch, world, TUE_PRE_OPEN)
        started = jb.install_inline_threads(monkeypatch)

        def broken(*_args, **_kwargs):
            raise sqlite3.OperationalError("table-detail-that-must-not-appear")

        monkeypatch.setattr(database, "get_advisor_observations_for_role", broken)

        with caplog.at_level(logging.DEBUG):
            began = need("_start_ticker_health_catch_up")()

        assert began is False
        assert started == []
        assert "table-detail-that-must-not-appear" not in caplog.text

    def test_a_thread_that_cannot_start_is_reported_not_raised_and_frees_the_next_start(
        self, world, monkeypatch
    ):
        set_clocks(monkeypatch, world, TUE_PRE_OPEN)
        with monkeypatch.context() as patch:
            jb.install_refusing_threads(patch, RuntimeError("can't start new thread"))
            refused = need("_start_ticker_health_catch_up")()
        started = jb.install_inline_threads(monkeypatch)

        began = need("_start_ticker_health_catch_up")()

        assert refused is False
        assert began is True
        assert len(started) == 1


# ---------------------------------------------------------------------------
# From the scheduler to the page
# ---------------------------------------------------------------------------


class TestFromTheSchedulerToThePage:
    def test_a_ticker_found_dead_overnight_is_posted_once_and_on_the_page_at_the_next_open(
        self, world, monkeypatch, tmp_path, client
    ):
        isolate_env(
            monkeypatch,
            tmp_path,
            ACCOUNT_ROTH=ACCOUNT,
            ALPACA_KEY=t.ALPACA_KEY,
            ALPACA_SECRET=t.ALPACA_SECRET,
            DISCORD_WEBHOOK_URL=t.WEBHOOK_URL,
        )
        tick_fn = need("_run_ticker_health_tick")
        set_clocks(monkeypatch, world, NIGHT, app_clock=False)

        _run_loop(
            monkeypatch, ticks=1, before_tick=lambda _tick, jobs: _set_due(jobs, {tick_fn: 5})
        )
        join_check_threads()

        posts = world.discord_posts()
        assert len(posts) == 1, f"expected one Discord message, got {len(posts)}"
        assert posts[0]["allowed_mentions"] == {"parse": []}
        assert DEAD in posts[0]["content"]
        assert FIRST_ID in posts[0]["content"] and SECOND_ID in posts[0]["content"]
        assert [r["completed"] for r in run_records()] == [True]

        monkeypatch.setattr(app_module, "datetime", clocked_datetime(lambda: TUE_NINE))
        response = client.get("/")

        assert response.status_code == 200
        soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")
        warning = soup.select_one('[data-testid="ticker-health-warning"]')
        assert warning is not None, "the page has no ticker health warning"
        text = warning.get_text(" ", strip=True)
        assert DEAD in text
        assert FIRST_NAME in text and SECOND_NAME in text


# ---------------------------------------------------------------------------
# The slot's place in the day, in every host zone
# ---------------------------------------------------------------------------


def _next_open_after(moment: datetime) -> datetime:
    day = moment.date()
    for _ in range(10):
        if market_calendar.is_trading_day(day):
            opens = datetime.combine(day, dtime(9, 30), tzinfo=ET)
            if opens > moment:
                return opens
        day += timedelta(days=1)
    raise AssertionError("no session opens within ten days")


class TestTheSlotTimeFitsTheNightInEveryHostZone:
    @pytest.mark.parametrize("host_zone", ["UTC", "America/New_York"])
    @pytest.mark.parametrize(
        "day",
        [date(2026, 1, 15), date(2026, 7, 15), date(2026, 11, 28), date(2026, 3, 9)],
        ids=["winter EST", "summer EDT", "after a half day", "week after the spring change"],
    )
    def test_the_start_follows_a_close_and_leaves_room_for_the_run_before_the_next_open(
        self, monkeypatch, host_zone, day
    ):
        scheduler, _ = _run_loop(monkeypatch, ticks=1)
        tick_fn = need("_run_ticker_health_tick")
        matching = [j for j in scheduler.jobs if _func_of(j) is tick_fn]
        assert len(matching) == 1, "the 01:30 slot must be registered exactly once"
        (job,) = matching
        assert (job.unit, job.interval, job.at_time) == ("days", 1, dtime(1, 30))

        slot = datetime.combine(day, job.at_time, tzinfo=ZoneInfo(host_zone)).astimezone(ET)

        assert market_calendar.get_market_state(slot) != "open"
        previous = market_calendar.most_recent_completed_session(slot)
        assert previous is not None
        closed_at = datetime.combine(previous, market_calendar.session_close(previous), tzinfo=ET)
        assert closed_at <= slot, f"the slot at {slot} comes before the close at {closed_at}"
        room = _next_open_after(slot) - slot
        needed = timedelta(seconds=runner.GATE_WAIT_LIMIT_SECONDS) + timedelta(minutes=30)
        assert room >= needed, f"only {room} from the slot to the next open"
        if day == date(2026, 11, 28) and host_zone == "UTC":
            assert previous == date(2026, 11, 27)
            assert market_calendar.session_close(previous) == dtime(13, 0)


# ---------------------------------------------------------------------------
# Every start goes through the one body; the catch-up lives in __main__ only
# ---------------------------------------------------------------------------


def _app_source() -> str:
    return Path(app_module.__file__).read_text(encoding="utf-8")


def _functions_naming(source: str, name: str) -> set[str]:
    """Names of the module-level functions that mention ``name`` as a name or an attribute."""
    found = set()
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Name) and inner.id == name) or (
                isinstance(inner, ast.Attribute) and inner.attr == name
            ):
                found.add(node.name)
    return found


def _main_if_node(source: str) -> ast.If:
    return next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
    )


_MAIN_ORDER = ("startup_refresh", "catch_up", "scheduler_thread")


def _main_events(source: str) -> dict[str, list[int]]:
    """Index, among the direct statements of the ``__main__`` block, of each startup step."""
    events: dict[str, list[int]] = {}
    for index, stmt in enumerate(_main_if_node(source).body):
        if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
            continue
        func = stmt.value.func
        name = None
        if isinstance(func, ast.Name):
            first = stmt.value.args[0] if stmt.value.args else None
            if (
                func.id == "_start_account_refresh"
                and isinstance(first, ast.Constant)
                and first.value == "startup"
            ):
                name = "startup_refresh"
            elif func.id == "_start_ticker_health_catch_up":
                name = "catch_up"
        elif (
            isinstance(func, ast.Attribute)
            and func.attr == "start"
            and isinstance(func.value, ast.Call)
            and any(
                kw.arg == "target"
                and isinstance(kw.value, ast.Name)
                and kw.value.id == "run_scheduler"
                for kw in func.value.keywords
            )
        ):
            name = "scheduler_thread"
        if name:
            events.setdefault(name, []).append(index)
    return events


def _main_order_problems(source: str) -> list[str]:
    events = _main_events(source)
    problems = [
        f"{name} must be one direct statement of the __main__ block; found {len(events.get(name, []))}"
        for name in _MAIN_ORDER
        if len(events.get(name, [])) != 1
    ]
    if problems:
        return problems
    first, second, third = (events[name][0] for name in _MAIN_ORDER)
    if not first < second < third:
        problems.append("the catch-up must follow the startup refresh and precede the scheduler")
    return problems


_MAIN_HEAD = "if __name__ == '__main__':\n    _acquire_daemon_singleton(p)\n"
_MAIN_TAIL = (
    "    threading.Thread(target=run_scheduler, daemon=True).start()\n    app.run(port=1)\n"
)
_MISPLACED_MAIN_BLOCKS = {
    "no catch-up": _MAIN_HEAD + "    _start_account_refresh('startup')\n" + _MAIN_TAIL,
    "catch-up after the scheduler thread": (
        _MAIN_HEAD
        + "    _start_account_refresh('startup')\n"
        + "    threading.Thread(target=run_scheduler, daemon=True).start()\n"
        + "    _start_ticker_health_catch_up()\n    app.run(port=1)\n"
    ),
    "catch-up before the startup refresh": (
        _MAIN_HEAD
        + "    _start_ticker_health_catch_up()\n    _start_account_refresh('startup')\n"
        + _MAIN_TAIL
    ),
    "catch-up inside a try that may never run it": (
        _MAIN_HEAD
        + "    _start_account_refresh('startup')\n"
        + "    try:\n        _start_ticker_health_catch_up()\n    except Exception:\n        pass\n"
        + _MAIN_TAIL
    ),
}


class TestEveryStartGoesThroughTheOneBody:
    def test_the_main_block_starts_the_catch_up_between_the_refresh_and_the_scheduler(self):
        for layout, source in _MISPLACED_MAIN_BLOCKS.items():
            assert _main_order_problems(source), f"the scan missed: {layout}"

        assert _main_order_problems(_app_source()) == []

    def test_the_catch_up_is_started_nowhere_but_the_main_block(self):
        need("_start_ticker_health_catch_up")

        assert _functions_naming(_app_source(), "_start_ticker_health_catch_up") == set()

    def test_the_scheduler_function_registers_the_slot_and_never_starts_the_catch_up(self):
        source = _app_source()

        assert _functions_naming(source, "_run_ticker_health_tick") == {"run_scheduler"}
        assert "run_scheduler" not in _functions_naming(source, "_start_ticker_health_catch_up")

    def test_running_the_scheduler_starts_no_catch_up(self, world, monkeypatch):
        started: list[str] = []
        monkeypatch.setattr(
            app_module, "_start_ticker_health_catch_up", lambda: started.append("x")
        )

        _run_loop(monkeypatch, ticks=1)

        assert started == []

    def test_the_body_is_reached_from_the_two_starts_only(self):
        assert _functions_naming(_app_source(), "_ticker_health_run_body") == {
            "_run_ticker_health_tick",
            "_start_ticker_health_catch_up",
        }

    def test_the_run_function_is_reached_from_the_body_only(self):
        assert _functions_naming(_app_source(), "run_ticker_health_check") == {
            "_ticker_health_run_body"
        }

    def test_the_catch_up_decision_is_read_by_the_catch_up_start_only(self):
        assert _functions_naming(_app_source(), "_ticker_health_catch_up_due") == {
            "_start_ticker_health_catch_up"
        }

    def test_the_scan_names_a_start_that_bypasses_the_body(self):
        planted = (
            _app_source()
            + "\n\ndef _bypass():\n"
            + "    from advisors import ticker_health_runner\n"
            + "    ticker_health_runner.run_ticker_health_check()\n"
        )

        assert "_bypass" in _functions_naming(planted, "run_ticker_health_check")
