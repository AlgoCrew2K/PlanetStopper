"""
RED tests -- the daily retirement tick: scheduling slot, one run at a time, and the review it runs.

The 03:45 tick used to call the retirement recommender's producer directly. It now starts the
symphony tenure review (the same job the "Run review now" action runs), so this file pins what
the scheduler can rely on:

* the tick returns at once: it claims the single run slot, starts a daemon thread and leaves the
  review to that thread (the 1-minute execution path is never blocked, and the scheduler thread
  never waits for Composer);
* the slot is claimed BEFORE the thread exists, and is given back at once when the thread cannot
  be started, so a failed start can never block every later run;
* a tick that finds a run in progress starts nothing and writes nothing;
* the thread's target takes no arguments (the owner token of the claimed slot is bound into it),
  because the scheduler and the test stand-ins call ``target()``;
* the tick's worker keeps its name, ``_retirement_recommender_tick_worker``, and now takes its
  recommendations from the review run (``advisors.tenure_review_runner.run_tenure_review``), not
  from ``build_recommendations``;
* the tick starts nothing when the newest completed NIGHTLY run already covers the as-of day and
  the threshold the new run would use (the weekend and holiday ticks would repeat a full review of
  the same session): it writes nothing and logs one INFO line. The decision is the tick's, made
  before it claims the slot; the worker and the job called directly never skip, and an on-demand
  run is never skipped.

Mirrors tests/app/test_incubation_scheduling.py's thread-patching style: the wrapper does an inline
``import threading`` then ``threading.Thread(...).start()``, so the patch target is
``threading.Thread`` in the stdlib module, not a binding in app.py.
"""

from __future__ import annotations

import inspect
import logging
import threading
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

import advisors.tenure_review as tenure_review
import app as app_module
import database as db_module
from tests.advisors import _composer_http_router as h
from tests.app import _tenure_review_job_builders as jb


@pytest.fixture(autouse=True)
def _no_live_calls(monkeypatch):
    """No test here may reach a real Anthropic client or a request the Composer router does
    not serve."""
    jb.block_live_calls(monkeypatch)
    yield
    jb.assert_no_unserved_requests()


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    jb.use_scratch_db(monkeypatch, tmp_path, "test_retirement_recommendations_scheduling.db")
    jb.isolate_env(monkeypatch, tmp_path)
    return tmp_path


class TestRetirementRecommendationsTickThreadSpawn:
    def test_run_retirement_recommender_tick_spawns_a_daemon_thread(self, isolated_db):
        """The scheduler-facing wrapper must spawn a thread and return immediately -- never
        block the 1-minute execution path (arch constraint 1)."""
        assert hasattr(app_module, "_run_retirement_recommender_tick")

        mock_thread_instance = MagicMock()
        mock_thread_cls = MagicMock(return_value=mock_thread_instance)

        with patch("threading.Thread", mock_thread_cls):
            app_module._run_retirement_recommender_tick()

        assert mock_thread_instance.start.call_count == 1, (
            "_run_retirement_recommender_tick() must call thread.start() exactly once -- the "
            "tick runs in a background daemon thread, never inline on the scheduler thread."
        )

    def test_run_retirement_recommender_tick_thread_is_daemon(self, isolated_db):
        captured_kwargs = {}

        def _capturing_thread_cls(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock()

        with patch("threading.Thread", _capturing_thread_cls):
            app_module._run_retirement_recommender_tick()

        assert captured_kwargs.get("daemon") is True, (
            f"threading.Thread(...) must be called with daemon=True, got kwargs: {captured_kwargs}"
        )


class TestRetirementRecommendationsTickSchedulingSlot:
    def test_run_scheduler_source_registers_retirement_tick_at_0345(self):
        """run_scheduler() is a `while True` loop, so it cannot be called directly in a test.
        Assert its source registers the tick at 03:45 via schedule.every().day.at(...)."""
        source = inspect.getsource(app_module.run_scheduler)
        assert '"03:45"' in source or "'03:45'" in source
        assert "_run_retirement_recommender_tick" in source, (
            "the 03:45 slot must call the retirement tick wrapper specifically"
        )

    def test_retirement_slot_is_distinct_from_incubation_and_lens_pipeline_slots(self):
        source = inspect.getsource(app_module.run_scheduler)
        assert '"03:00"' in source
        assert '"03:30"' in source
        assert '"03:45"' in source

    def test_the_engine_cycle_registration_is_unchanged(self):
        """The review never moves the 1-minute execution cycle: it is still registered every
        minute at :00 as the same function."""
        source = inspect.getsource(app_module.run_scheduler)
        assert 'schedule.every().minute.at(":00").do(threaded_trigger)' in source


# ---------------------------------------------------------------------------
# The worker is the review job
# ---------------------------------------------------------------------------


class TestRetirementRecommendationsTickWorkerRunsTheReview:
    def test_the_tick_runs_the_review_as_nightly_and_persists_what_it_recommends(
        self, isolated_db, monkeypatch
    ):
        """The part that closes the completeness gap: the tick's worker must reach the REAL
        review run and persist what it returns -- a thread-spawn test alone would pass with an
        empty target. The old producer is no longer called."""
        import advisors.retirement_recommender as rr_module

        sentinel = [{"candidate_id": "sentinel-candidate", "sibling_id": "sentinel-sibling"}]
        calls = jb.patch_runner(monkeypatch, sentinel)
        build_mock = MagicMock(return_value=[{"candidate_id": "must-not-be-used"}])
        persist_mock = MagicMock(return_value=1)
        monkeypatch.setattr(rr_module, "build_recommendations", build_mock)
        monkeypatch.setattr(rr_module, "persist_recommendations", persist_mock)
        jb.freeze_app_clock(monkeypatch)

        with patch("threading.Thread", jb.ImmediateThread):
            app_module._run_retirement_recommender_tick()

        assert len(calls) == 1, "the tick never reached the review run"
        assert calls[0]["trigger"] == "nightly"
        assert build_mock.call_count == 0, "the old producer must not run any more"
        assert persist_mock.call_count == 1
        call = persist_mock.call_args
        persisted = call.args[0] if call.args else call.kwargs["recs"]
        assert [r["candidate_id"] for r in persisted] == ["sentinel-candidate"], (
            "persist_recommendations must get the review run's own recommendations"
        )
        assert persisted[0]["review_run_id"] == calls[0]["run_id"]
        assert len(jb.run_rows()) == 1

    def test_a_runner_failure_never_crashes_the_scheduler_thread(self, isolated_db, monkeypatch):
        """D-1: matches the other ticks' log-the-type-and-continue contract."""

        def _boom(_kwargs):
            raise RuntimeError("simulated runner failure")

        jb.patch_runner(monkeypatch, hook=_boom)
        with patch("threading.Thread", jb.ImmediateThread):
            try:
                app_module._run_retirement_recommender_tick()
            except Exception as exc:  # noqa: BLE001
                raise AssertionError(
                    f"_run_retirement_recommender_tick() propagated {type(exc).__name__} from a "
                    "runner failure -- the tick must catch and log, never crash the scheduler"
                ) from exc
        assert jb.state_is_idle(), "a failed run must give the run slot back"


# ---------------------------------------------------------------------------
# One run at a time
# ---------------------------------------------------------------------------


class TestTheTickClaimsTheRunSlot:
    def test_the_slot_is_held_for_the_thread_it_starts_and_the_thread_finishes_it(
        self, isolated_db, monkeypatch
    ):
        """The slot must already be taken when the tick returns (so a second tick or a request
        in that window is refused), the thread target must be callable with no arguments, and
        running it must release the slot."""
        jb.patch_runner(monkeypatch, [])
        jb.freeze_app_clock(monkeypatch)
        built = {}

        def _thread(*args, **kwargs):
            built.update(kwargs)
            return MagicMock()

        with patch("threading.Thread", _thread):
            app_module._run_retirement_recommender_tick()

        snap = tenure_review.REVIEW_RUN_STATE.snapshot()
        assert snap["running"] is True and snap["trigger"] == "nightly"
        assert not tenure_review.REVIEW_RUN_STATE.try_start("on_demand")
        target = built.get("target")
        assert callable(target), "the tick must hand its thread a target by keyword"
        target()
        assert jb.state_is_idle(), "the thread's own run must release the slot when it ends"
        assert len(jb.run_rows()) == 1

    def test_a_tick_during_a_run_starts_no_thread_and_writes_nothing(
        self, isolated_db, monkeypatch, caplog
    ):
        calls = jb.patch_runner(monkeypatch, [{"candidate_id": "c", "sibling_id": "s"}])
        state = tenure_review.REVIEW_RUN_STATE
        token = state.try_start("on_demand")
        assert token
        before = state.snapshot()
        ids_before = jb.all_row_ids()
        thread_cls = MagicMock()

        with patch("threading.Thread", thread_cls), caplog.at_level(logging.INFO):
            app_module._run_retirement_recommender_tick()

        assert thread_cls.call_count == 0, "a tick during a run must not start a second thread"
        assert calls == [], "no second review run"
        assert jb.new_rows_since(ids_before) == [], "a refused tick writes nothing"
        assert state.snapshot() == before, "the run in progress keeps its slot and its details"
        assert any(r.levelno == logging.INFO for r in caplog.records), (
            "the skipped tick must be logged at INFO"
        )
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        state.finish_success(token)

    def test_a_thread_that_cannot_be_started_gives_the_slot_back_at_once(
        self, isolated_db, monkeypatch, caplog
    ):
        calls = jb.patch_runner(monkeypatch, [])
        ids_before = jb.all_row_ids()

        class _Unstartable:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                raise RuntimeError("can't start new thread LEAKMARK-thread-start")

        with patch("threading.Thread", _Unstartable), caplog.at_level(logging.DEBUG):
            try:
                app_module._run_retirement_recommender_tick()
            except Exception as exc:  # noqa: BLE001
                raise AssertionError(
                    f"the tick propagated {type(exc).__name__}: an exception here ends the "
                    "scheduler loop for good"
                ) from exc

        assert jb.taken_then_released(), "a failed thread start left the run slot taken"
        assert calls == []
        assert jb.new_rows_since(ids_before) == []
        assert tenure_review.REVIEW_RUN_STATE.snapshot()["last_failed_attempt"] is None, (
            "a thread that never ran is not a failed review attempt"
        )
        assert "LEAKMARK-thread-start" not in caplog.text, "log the exception type only"

    def test_a_thread_that_cannot_be_built_gives_the_slot_back_at_once(
        self, isolated_db, monkeypatch
    ):
        jb.patch_runner(monkeypatch, [])

        def _refuse(*args, **kwargs):
            raise RuntimeError("no threads")

        with patch("threading.Thread", _refuse):
            try:
                app_module._run_retirement_recommender_tick()
            except Exception as exc:  # noqa: BLE001
                raise AssertionError(
                    f"the tick propagated {type(exc).__name__} from a refused thread"
                ) from exc

        assert jb.taken_then_released()


class TestTheTickDoesNotBlockTheScheduler:
    def test_the_tick_returns_while_the_review_is_still_running(self, isolated_db, monkeypatch):
        """A real thread and a runner that waits for the test: the tick must come back at once,
        with the run still going, and the review must finish by itself afterwards."""
        runner = jb.BlockingRunner().install(monkeypatch)
        jb.hide_environment(monkeypatch)
        jb.freeze_app_clock(monkeypatch)
        jb.RecordingThread.instances.clear()
        monkeypatch.setattr(threading, "Thread", jb.RecordingThread)
        started = time.monotonic()
        try:
            app_module._run_retirement_recommender_tick()
            returned_after = time.monotonic() - started

            assert returned_after < 5, f"the tick took {returned_after:.1f} s: it ran inline"
            assert runner.entered.wait(30), "the review thread never reached the runner"
            assert tenure_review.REVIEW_RUN_STATE.snapshot()["running"] is True
            threads = list(jb.RecordingThread.instances)
            assert len(threads) == 1 and threads[0].daemon is True
        finally:
            runner.release.set()
            for t in jb.RecordingThread.instances:
                t.join(timeout=60)
        assert jb.state_is_idle()
        assert len(jb.run_rows()) == 1


# ---------------------------------------------------------------------------
# The tick end to end: the real runner over the captured Composer records
# ---------------------------------------------------------------------------


class TestTheNightlyTickProducesAReviewPerLiveSymphony:
    def test_the_panel_and_the_api_show_one_row_per_live_symphony_with_the_runs_dates(
        self, tmp_path, monkeypatch, client
    ):
        jb.use_scratch_db(monkeypatch, tmp_path)
        jb.isolate_env(monkeypatch, tmp_path, accounts=[("ACCOUNT_ROTH", h.ACCOUNT_ID)])
        jb.install_world(monkeypatch)
        jb.freeze_app_clock(monkeypatch)

        with patch("threading.Thread", jb.ImmediateThread):
            app_module._run_retirement_recommender_tick()

        data = jb.api_after_run(client)
        live = jb.stored_and_listed_ids()
        assert sorted(jb.entries_by_id(data)) == sorted(live)
        assert len(data["review_entries"]) == len(live), "one row per live symphony, no repeats"
        run = data["review_run"]
        assert run["trigger"] == "nightly"
        assert run["as_of_trading_day"] == "2026-10-19"
        panel = jb.panel(client)
        assert len(panel.find_all("tenure-review-entry")) == len(live)
        assert "2026-10-19" in panel.find("tenure-review-as-of").text()
        assert "2026-10-20 03:45 ET" in panel.find("tenure-review-run-time").text()
        assert jb.state_is_idle()
        assert len(jb.run_rows()) == 1


# ---------------------------------------------------------------------------
# The nightly tick starts nothing for a day it has already reviewed
# ---------------------------------------------------------------------------

NIGHT_ONE = jb.NOW_ET  # Tuesday 03:45: the as-of day is Monday 2026-10-19
NIGHT_TWO = datetime(2026, 10, 21, 3, 45, tzinfo=jb.ET)  # as-of Tuesday 2026-10-20


def _tick() -> None:
    with patch("threading.Thread", jb.ImmediateThread):
        app_module._run_retirement_recommender_tick()


@pytest.fixture()
def env_file(isolated_db):
    return isolated_db / "scratch.env"


@pytest.fixture()
def first_night(env_file, monkeypatch):
    """The first nightly review of the as-of day has run and is stored; returns the runner calls."""
    jb.freeze_app_clock(monkeypatch, NIGHT_ONE)
    calls = jb.patch_runner(monkeypatch, [], isolate=False)
    _tick()
    assert len(calls) == 1 and len(jb.run_rows()) == 1, "premise: the first night's review ran"
    return calls


class TestTheNightlyTickStartsNothingForADayItHasAlreadyReviewed:
    def test_it_starts_nothing_writes_nothing_and_logs_one_info_line(self, first_night, caplog):
        ids_before = jb.all_row_ids()
        state_before = tenure_review.REVIEW_RUN_STATE.snapshot()
        caplog.clear()

        with caplog.at_level(logging.INFO):
            _tick()

        assert len(first_night) == 1, "the review ran again for the same as-of day"
        assert jb.new_rows_since(ids_before) == [], "a skipped tick writes nothing"
        assert jb.state_is_idle() and jb.taken_then_released(), "a skipped tick holds no slot"
        assert tenure_review.REVIEW_RUN_STATE.snapshot() == state_before
        notes = [r for r in caplog.records if r.name == "alphabot" and r.levelno == logging.INFO]
        assert len(notes) == 1, f"expected one INFO line from the daemon logger, got {len(notes)}"
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING], (
            "skipping a day already reviewed is not a warning"
        )

    def test_a_later_as_of_day_is_reviewed(self, first_night, monkeypatch):
        jb.freeze_app_clock(monkeypatch, NIGHT_TWO)

        _tick()

        assert len(first_night) == 2 and len(jb.run_rows()) == 2

    def test_a_different_threshold_is_reviewed_and_then_skipped_in_its_turn(
        self, first_night, env_file
    ):
        jb.rewrite_env(env_file, threshold="100")

        _tick()
        assert len(first_night) == 2, "a changed threshold gives a different review"
        _tick()
        assert len(first_night) == 2, "the same day at the same threshold starts nothing"
        assert [r["raw_response"]["threshold_in_effect"] for r in jb.run_rows()] == [100, 126]

    def test_the_threshold_compared_is_the_one_in_effect_not_the_text_configured(
        self, first_night, env_file
    ):
        jb.rewrite_env(env_file, threshold="abc")  # unusable: the default 126 is in effect

        _tick()

        assert len(first_night) == 1, "126 was in effect for both reviews"

    def test_the_weekend_and_monday_ticks_after_a_fridays_review_start_nothing(
        self, isolated_db, monkeypatch
    ):
        calls = jb.patch_runner(monkeypatch, [], isolate=False)
        saturday = datetime(2026, 10, 24, 3, 45, tzinfo=jb.ET)
        instants = [
            (saturday, 1),
            (datetime(2026, 10, 25, 3, 45, tzinfo=jb.ET), 1),  # Sunday
            (datetime(2026, 10, 26, 3, 45, tzinfo=jb.ET), 1),  # Monday, before the open
            (datetime(2026, 10, 27, 3, 45, tzinfo=jb.ET), 2),  # Tuesday: Monday has closed
        ]
        for instant, expected_runs in instants:
            jb.freeze_app_clock(monkeypatch, instant)
            _tick()
            assert len(calls) == expected_runs, f"{instant:%A}: {len(calls)} runs"
        assert [r["raw_response"]["as_of_trading_day"] for r in jb.run_rows()] == [
            "2026-10-26",
            "2026-10-23",
        ]

    def test_a_worker_called_directly_is_not_skipped(self, first_night):
        """The decision belongs to the tick, before it claims the slot: the worker the tick's
        thread runs, and the job it calls, run whatever is stored."""
        app_module._retirement_recommender_tick_worker()

        assert len(first_night) == 2


class TestAnOnDemandRunIsNeverSkipped:
    def test_a_request_after_a_nightly_review_of_the_same_day_runs_a_new_review(
        self, first_night, client, monkeypatch
    ):
        jb.install_inline_threads(monkeypatch)

        response = client.post("/ai-advisor/retirement/review/run")

        assert response.status_code == 202
        assert len(first_night) == 2 and len(jb.run_rows()) == 2
        assert jb.run_rows()[0]["raw_response"]["trigger"] == "on_demand"

    def test_a_stored_on_demand_run_does_not_stop_the_nightly_tick(
        self, isolated_db, client, monkeypatch
    ):
        jb.freeze_app_clock(monkeypatch, NIGHT_ONE)
        calls = jb.patch_runner(monkeypatch, [], isolate=False)
        jb.install_inline_threads(monkeypatch)
        assert client.post("/ai-advisor/retirement/review/run").status_code == 202
        assert [c["trigger"] for c in calls] == ["on_demand"]

        _tick()

        assert [c["trigger"] for c in calls] == ["on_demand", "nightly"], (
            "the newest completed NIGHTLY run is the one that counts"
        )


class TestAStoredRunTheTickCannotReadDoesNotStopIt:
    @pytest.mark.parametrize(
        "stored",
        [
            '"plain text"',
            '["not", "a", "record"]',
            "this is not json {",
            {},
            {"trigger": "nightly"},
            {"trigger": "nightly", "as_of_trading_day": None, "threshold_in_effect": None},
            {"trigger": "nightly", "as_of_trading_day": "2026-10-19"},
            {"trigger": "nightly", "as_of_trading_day": "not a date", "threshold_in_effect": 126},
        ],
        ids=[
            "json text",
            "json list",
            "unparseable",
            "empty",
            "no day or threshold",
            "null day and threshold",
            "no threshold",
            "unreadable day",
        ],
    )
    def test_the_review_runs(self, isolated_db, monkeypatch, stored):
        jb.freeze_app_clock(monkeypatch, NIGHT_ONE)
        calls = jb.patch_runner(monkeypatch, [], isolate=False)
        db_module.insert_advisor_observation(
            advisor_role=jb.ROLE_RUN,
            subject_type="tenure_review_run",
            subject_id="1" * 32,
            verdict="completed",
            raw_response=stored,
        )

        _tick()

        assert len(calls) == 1, "a stored run the tick cannot read must not stop the review"

    def test_a_failed_read_of_the_stored_runs_does_not_stop_the_review_or_crash_the_tick(
        self, isolated_db, monkeypatch
    ):
        jb.freeze_app_clock(monkeypatch, NIGHT_ONE)
        calls = jb.patch_runner(monkeypatch, [], isolate=False)
        real = db_module.get_advisor_observations_for_role

        def selective(role, *args, **kwargs):
            if role == jb.ROLE_RUN:
                raise RuntimeError("simulated read failure")
            return real(role, *args, **kwargs)

        monkeypatch.setattr(db_module, "get_advisor_observations_for_role", selective)

        try:
            _tick()
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"the tick propagated {type(exc).__name__} from a failed read: that ends the "
                "scheduler loop for good"
            ) from exc

        assert len(calls) == 1
