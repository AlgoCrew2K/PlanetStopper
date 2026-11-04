"""POST /ai-advisor/retirement/review/run: the "Run review now" action.

The route claims the single run slot, starts the review job on a daemon thread, as the nightly tick
does, and answers at once; the review itself runs on that thread. A daemon thread is what lets a
daemon stop or a deploy restart finish without waiting for a review of up to about 80 minutes. These
tests drive the real route through the Flask test client. The thread is replaced by one that runs
its target on the spot, by a real recorded thread, or by one that refuses to be built or started;
the Composer reads are replaced by a runner stand-in over the real result types (the real runner is
exercised by the end-to-end file).

Contract pinned here (the implementer reads this file and the handoff, not the plan):

* The route is ``ai_advisor_retirement_review_run``. There is no review executor: no
  ``_TENURE_REVIEW_EXECUTOR`` in the app module and no pool worker runs the job.
* The route reads no request body. It calls ``REVIEW_RUN_STATE.try_start("on_demand")`` (looked up as
  ``tenure_review.REVIEW_RUN_STATE`` at each request); when that is refused it answers 409 with
  ``{"status": "already_running", "message": "a review is already running"}`` and changes nothing.
  Otherwise it starts ``threading.Thread(..., daemon=True)`` (``threading.Thread`` looked up at each
  request) whose target runs ``_tenure_review_job("on_demand", owner)``, passing the owner token
  through the target or ``args``, and answers 202 with
  ``{"status": "started", "message": "review started"}``. When the thread cannot be built or started,
  it releases the slot with the owner token and answers 200 with ``{"error": <exception type name>}``.
* Nothing is written until the run has completed: the recommendation rows come first and the run row
  last, so a run that is abandoned (the daemon stops while the thread works) leaves no run row.
* Every path out of the job gives the slot back, including a lazy import of the job that fails.
* Authentication and CSRF come from the global hooks; the route calls neither.
* The market gate: while ``app_module.get_market_state(<now ET>)`` is ``"open"`` the route answers 409
  ``{"status": "market_open"}`` and does nothing else. ``get_market_state`` is read as the bare name
  bound in the app module (the patch target ``app_module.get_market_state``), called with the current
  Eastern time (``datetime.now(_ET)``). The check comes before ``REVIEW_RUN_STATE.try_start``, so a
  refusal claims no slot, starts no thread and writes nothing, and it answers ``market_open`` even
  while a review holds the slot. The check is in the route only: ``_tenure_review_job`` and the 03:45
  tick never read the market state. A market-state read that raises is not caught: the request fails
  with HTTP 500.
"""

from __future__ import annotations

import copy
import inspect
import logging
import threading
from datetime import date, datetime, timedelta

import pytest

import advisors.retirement_explainer as explainer_module
import advisors.retirement_recommender as recommender_module
import app as app_module
import database as db_module
import market_calendar
from tests.app import _tenure_review_job_builders as jb
from tests.app import _tenure_review_panel_builders as pb

RUN_ROUTE = "/ai-advisor/retirement/review/run"
XHR = {"X-Requested-With": "XMLHttpRequest"}
STARTED = {"status": "started", "message": "review started"}
ALREADY_RUNNING = {"status": "already_running", "message": "a review is already running"}
LEAK = "sk-ant-LEAKMARK-4d82b0e7c1"


@pytest.fixture(autouse=True)
def _no_live_calls(monkeypatch):
    """No test here may reach a real Anthropic client or a request the Composer router does
    not serve."""
    jb.block_live_calls(monkeypatch)
    yield
    jb.assert_no_unserved_requests()


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A scratch database and an environment that names no account."""
    jb.use_scratch_db(monkeypatch, tmp_path, "test_tenure_review_run_route.db")
    jb.isolate_env(monkeypatch, tmp_path)
    jb.freeze_app_clock(monkeypatch)
    return tmp_path


@pytest.fixture()
def threads(monkeypatch):
    """The threads the route starts, replaced by ones that run their target on the spot; the
    list collects each thread that was started."""
    return jb.install_inline_threads(monkeypatch)


def _post(client, **kwargs):
    response = client.post(RUN_ROUTE, **kwargs)
    assert response.status_code != 404, f"{RUN_ROUTE} is not registered"
    return response


def _live_recs():
    return [{"candidate_id": "cand-run-route", "sibling_id": "sib-run-route"}]


class TestTheRouteStartsOneReview:
    def test_only_post_is_accepted(self, store, client, threads):
        response = client.get(RUN_ROUTE)
        assert response.status_code == 405
        assert threads == []

    def test_a_request_is_acknowledged_and_the_review_runs_as_on_demand_with_the_slot_held(
        self, store, client, threads, monkeypatch
    ):
        seen = {}

        def hook(kwargs):
            seen["state"] = jb.the_run_state().snapshot()

        calls = jb.patch_runner(monkeypatch, _live_recs(), hook=hook, isolate=False)

        response = _post(client)

        assert response.status_code == 202
        assert response.get_json() == STARTED
        assert len(threads) == 1, "exactly one thread is started"
        assert len(calls) == 1
        assert calls[0]["trigger"] == "on_demand"
        assert seen["state"]["running"] is True and seen["state"]["trigger"] == "on_demand", (
            "the slot is claimed before the job runs"
        )
        assert jb.state_is_idle(), "the job gives the slot back when it ends"
        assert len(jb.run_rows()) == 1 and len(jb.rec_rows()) == 1

    def test_the_job_runs_on_a_daemon_thread_so_a_stop_never_waits_for_a_review(
        self, store, client, threads, monkeypatch
    ):
        jb.patch_runner(monkeypatch, _live_recs(), isolate=False)

        assert _post(client).status_code == 202

        assert [t.daemon for t in threads] == [True], (
            "a non-daemon thread would hold a stop or a restart until the review ended"
        )

    def test_the_response_does_not_wait_for_the_review_and_nothing_is_stored_until_it_ends(
        self, store, client, monkeypatch
    ):
        """With a real thread held by the runner, the request still returns 202 and the review
        finishes by itself afterwards. While it works no row exists, so a daemon that stops at
        that moment leaves no run row (and no recommendation row) behind."""
        recorded = jb.install_recording_threads(monkeypatch)
        blocked = jb.BlockingRunner(_live_recs()).install(monkeypatch)
        try:
            response = _post(client)
            assert response.status_code == 202
            assert blocked.entered.wait(30)
            assert jb.the_run_state().snapshot()["running"] is True
            assert len(recorded) == 1 and recorded[0].daemon is True
            assert jb.run_rows() == [] and jb.rec_rows() == [], (
                "a review in progress has written nothing"
            )
        finally:
            blocked.release.set()
            jb.join_threads(recorded)
        assert jb.state_is_idle()
        assert len(jb.run_rows()) == 1


class TestNoTestHereCanMakeALiveCall:
    def test_a_request_or_a_real_anthropic_client_is_refused_and_recorded_by_the_guard(self):
        """Mutation proof of the autouse guard: it refuses what it exists to refuse and keeps a
        record of each refusal."""
        import requests

        import ai_advisor

        with pytest.raises(AssertionError):
            requests.get("https://api.example.invalid/anything", timeout=1)
        with pytest.raises(AssertionError):
            requests.Session().request("GET", "https://api.example.invalid/anything")
        with pytest.raises(AssertionError):
            ai_advisor._build_client()

        assert jb.refused_live_calls() == [
            "requests.get",
            "requests.sessions.Session.request",
            "ai_advisor._build_client",
        ]
        with pytest.raises(AssertionError, match="live network call"):
            jb.assert_no_unserved_requests()
        assert jb.refused_live_calls() == [], "the record is cleared once it has been reported"

    def test_a_refusal_that_the_real_explainer_swallows_still_fails_the_test(self):
        """The explainer catches every error around the client it builds and answers None, so
        the raise alone would pass unseen."""
        import advisors.retirement_explainer as explainer_module

        answer = explainer_module.explain_recommendation({"candidate_id": "c", "sibling_id": "s"})

        assert answer is None, "premise: the explainer swallows the refusal"
        assert jb.refused_live_calls() == ["ai_advisor._build_client"]
        with pytest.raises(AssertionError, match="live network call"):
            jb.assert_no_unserved_requests()

    def test_a_refusal_that_the_real_composer_client_swallows_still_fails_the_test(
        self, monkeypatch
    ):
        from advisors import composer_portfolio_client as cpc

        monkeypatch.setattr(cpc.time, "sleep", lambda _seconds: None)

        read = cpc.read_symphony_statistics("account-plain-id", spacing_seconds=1.0)

        assert read.failure == "unavailable", "premise: the client swallows the refusal"
        assert jb.refused_live_calls() == ["requests.get"]
        with pytest.raises(AssertionError, match="live network call"):
            jb.assert_no_unserved_requests()

    def test_a_test_that_makes_no_such_call_passes(self):
        assert jb.refused_live_calls() == []
        jb.assert_no_unserved_requests()


class TestTheReviewHasNoWorkerPoolToWaitFor:
    def test_there_is_no_review_executor_and_no_exit_hook_that_would_wait_for_one(self):
        """A pool worker is not a daemon thread: the interpreter joins it at exit, so a stop or a
        restart waits for the review to end."""
        assert not hasattr(app_module, "_TENURE_REVIEW_EXECUTOR"), (
            "the review is started on a daemon thread, not submitted to a pool"
        )
        assert "_TENURE_REVIEW_EXECUTOR" not in inspect.getsource(app_module)


class TestTheRunRecordsWhenItStartedAndWhenItFinished:
    def test_a_review_that_takes_time_stores_its_start_and_its_own_completion_time(
        self, store, client, threads, monkeypatch
    ):
        """The run line of the panel reads "run completed <time>": that time is the moment the
        run finished, read from the clock again after the Composer reads, not the start."""
        finished_at = datetime(2026, 10, 20, 5, 5, tzinfo=jb.ET)

        def time_passes(_kwargs):
            jb.freeze_app_clock(monkeypatch, finished_at)

        jb.patch_runner(monkeypatch, _live_recs(), hook=time_passes, isolate=False)

        assert _post(client).status_code == 202

        run = jb.api_after_run(client)["review_run"]
        assert datetime.fromisoformat(run["started_at"]) == jb.NOW_ET
        assert datetime.fromisoformat(run["completed_at"]) == finished_at
        assert "2026-10-20 05:05 ET" in jb.panel(client).find("tenure-review-run-time").text()

    def test_a_failed_attempt_is_stamped_with_the_time_it_failed(
        self, store, client, threads, monkeypatch
    ):
        failed_at = datetime(2026, 10, 20, 5, 40, tzinfo=jb.ET)

        def time_passes_then_fails(_kwargs):
            jb.freeze_app_clock(monkeypatch, failed_at)

        outcome = jb.runner_module.RunOutcome(False, None, "Composer statistics unavailable")
        jb.patch_runner(monkeypatch, outcome=outcome, hook=time_passes_then_fails, isolate=False)

        assert _post(client).status_code == 202

        failed = jb.the_run_state().snapshot()["last_failed_attempt"]
        assert datetime.fromisoformat(failed["at"]) == failed_at


class TestAnOnDemandRunReplacesTheLastReview:
    def test_the_panel_and_the_api_show_the_new_run_and_none_of_the_nightly_rows(
        self, store, client, threads, monkeypatch
    ):
        nightly = pb.store_full_run(pb.RUN_ID_FIRST)
        pb.seed_roster_state(nightly)
        assert len(jb.api_after_run(client)["recommendations"]) == 1, (
            "premise: the nightly run has one"
        )
        fresh = pb.full_record(pb.RUN_ID_SECOND, trigger="on_demand")
        entries = copy.deepcopy(fresh["entries"])
        jb.patch_runner(monkeypatch, [], entries=entries, isolate=False)

        response = _post(client)

        assert response.status_code == 202
        data = jb.api_after_run(client)
        run = data["review_run"]
        assert run["run_id"] != pb.RUN_ID_FIRST
        assert run["trigger"] == "on_demand"
        assert {e["symphony_id"] for e in data["review_entries"]} == {
            e["symphony_id"] for e in entries
        }
        assert data["recommendations"] == [], (
            "the nightly run's recommendation belongs to a run that is no longer the latest"
        )
        panel = jb.panel(client)
        assert len(panel.find_all("tenure-review-entry")) == len(entries)
        assert "2026-10-20 03:45 ET" in panel.find("tenure-review-run-time").text()
        assert "on demand" in panel.find("tenure-review-trigger").text()


class TestARunInProgressRefusesASecondOne:
    def test_a_request_during_the_nightly_run_is_refused_and_writes_nothing(
        self, store, client, threads, monkeypatch
    ):
        calls = jb.patch_runner(monkeypatch, _live_recs(), isolate=False)
        state = jb.the_run_state()
        token = state.try_start("nightly")
        assert token
        held = state.snapshot()
        ids_before = jb.all_row_ids()

        response = _post(client)

        assert response.status_code == 409
        assert response.get_json() == ALREADY_RUNNING
        assert threads == [] and calls == [], "no second run starts"
        assert jb.new_rows_since(ids_before) == [], "nothing is written"
        assert state.snapshot() == held, "the refusal leaves the running review untouched"
        state.finish_success(token)
        assert _post(client).status_code == 202, "a request after the run ends starts one"

    @pytest.mark.parametrize("simultaneous", [2, 8])
    def test_requests_arriving_together_start_exactly_one_review(
        self, store, monkeypatch, simultaneous
    ):
        blocked = jb.BlockingRunner(_live_recs()).install(monkeypatch)
        review_threads = jb.install_recording_threads(monkeypatch)
        barrier = threading.Barrier(simultaneous)
        statuses: list[int] = []
        bodies: list[dict] = []
        lock = threading.Lock()

        def post_together():
            with app_module.app.test_client() as own_client:
                barrier.wait(timeout=30)
                response = own_client.post(RUN_ROUTE)
            with lock:
                statuses.append(response.status_code)
                bodies.append(response.get_json())

        posters = [jb.REAL_THREAD(target=post_together) for _ in range(simultaneous)]
        try:
            for t in posters:
                t.start()
            for t in posters:
                t.join(timeout=60)
            assert blocked.entered.wait(30)
        finally:
            blocked.release.set()
            jb.join_threads(review_threads)

        assert sorted(statuses) == [202] + [409] * (simultaneous - 1), statuses
        assert STARTED in bodies and bodies.count(ALREADY_RUNNING) == simultaneous - 1
        assert len(review_threads) == 1 and len(blocked.calls) == 1
        assert len(jb.run_rows()) == 1 and len(jb.rec_rows()) == 1, "one review, written once"
        assert jb.state_is_idle()


class TestAThreadThatCannotBeStartedGivesTheSlotBack:
    @pytest.mark.parametrize("where", ["construct", "start"])
    def test_the_response_names_the_exception_type_only_and_the_next_request_can_start(
        self, store, client, monkeypatch, caplog, where
    ):
        reached = jb.install_refusing_threads(
            monkeypatch, RuntimeError(f"can't start new thread: {LEAK}"), where=where
        )
        calls = jb.patch_runner(monkeypatch, [], isolate=False)
        ids_before = jb.all_row_ids()

        with caplog.at_level(logging.DEBUG):
            response = _post(client)

        assert response.status_code == 200
        assert response.get_json() == {"error": "RuntimeError"}
        assert LEAK not in response.get_data(as_text=True)
        assert LEAK not in caplog.text, "the log names the exception type only"
        assert [stage for stage, _ in reached] == (
            ["construct"] if where == "construct" else ["construct", "start"]
        )
        assert not any(jb.is_pool_worker(target) for _, target in reached), (
            "the job was handed to a pool worker, not to a thread of its own"
        )
        assert jb.taken_then_released(), "a thread that failed to start left the run slot taken"
        assert jb.the_run_state().snapshot()["last_failed_attempt"] is None, (
            "a job that never started is not a failed review attempt"
        )
        assert calls == [] and jb.new_rows_since(ids_before) == []
        assert jb.state_is_idle()
        jb.install_inline_threads(monkeypatch)
        assert _post(client).status_code == 202, "the next request must be able to start a run"
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# A job that fails after the 202: the failure text stays out of every surface
# ---------------------------------------------------------------------------


def _raise_in_the_runner(monkeypatch):
    def boom(_kwargs):
        raise RuntimeError(f"upstream said {LEAK}")

    jb.patch_runner(monkeypatch, _live_recs(), hook=boom, isolate=False)


def _raise_in_the_persistence(monkeypatch):
    jb.patch_runner(monkeypatch, _live_recs(), isolate=False)

    def boom(recs, **kw):
        raise RuntimeError(f"disk said {LEAK}")

    monkeypatch.setattr(recommender_module, "persist_recommendations", boom)


def _raise_in_the_run_row_write(monkeypatch):
    jb.patch_runner(monkeypatch, _live_recs(), isolate=False)
    real = db_module.insert_advisor_observation

    def write(*args, **kwargs):
        if kwargs.get("advisor_role") == jb.ROLE_RUN:
            raise RuntimeError(f"database said {LEAK}")
        return real(*args, **kwargs)

    monkeypatch.setattr(db_module, "insert_advisor_observation", write)


class TestAFailedJobNeverShowsItsExceptionText:
    @pytest.mark.parametrize(
        "where",
        [_raise_in_the_runner, _raise_in_the_persistence, _raise_in_the_run_row_write],
        ids=["runner", "recommendation write", "run row write"],
    )
    def test_a_secret_shaped_message_reaches_no_surface_and_the_previous_review_stays(
        self, store, client, threads, monkeypatch, caplog, where
    ):
        previous = pb.store_full_run(pb.RUN_ID_FIRST)
        pb.seed_roster_state(previous)
        before = jb.api_after_run(client)
        where(monkeypatch)

        with caplog.at_level(logging.DEBUG):
            response = _post(client)

        assert response.status_code == 202, "the failure happens after the request was answered"
        assert LEAK not in response.get_data(as_text=True)
        data = jb.api_after_run(client)
        assert data["review_run"] == before["review_run"], "the previous run stays the latest"
        assert data["review_entries"] == before["review_entries"]
        failed = data["review_status"]["last_failed_attempt"]
        assert failed is not None and isinstance(failed["reason"], str) and failed["reason"].strip()
        assert LEAK not in str(data), "the API must not carry the exception text"
        assert LEAK not in jb.panel(client).everything(), "the panel must not carry it"
        assert LEAK not in str(jb.the_run_state().snapshot())
        assert LEAK not in caplog.text, "the log names the exception type only"
        assert "RuntimeError" in caplog.text or "RuntimeError" in failed["reason"]
        assert all(r.exc_info is None for r in caplog.records), (
            "a logged traceback would carry the exception message"
        )
        assert jb.state_is_idle() and jb.taken_then_released()
        assert len(jb.run_rows()) == 1, "no run row for a run that failed"


class TestEveryPathOutOfTheJobGivesTheSlotBack:
    """The job's lazy imports sit inside the guarded block, so an import that fails is a failed
    attempt like any other: the slot comes back and nothing is stored. The job never raises."""

    @pytest.mark.parametrize("claimed_by_caller", [True, False], ids=["token given", "own claim"])
    def test_a_runner_module_that_cannot_be_imported_is_a_failed_attempt_that_frees_the_slot(
        self, store, monkeypatch, claimed_by_caller
    ):
        calls = jb.patch_runner(monkeypatch, _live_recs(), isolate=False)
        jb.fail_imports_of(monkeypatch, "advisors.tenure_review_runner")
        owner = jb.the_run_state().try_start("on_demand") if claimed_by_caller else None
        assert owner or not claimed_by_caller
        ids_before = jb.all_row_ids()

        app_module._tenure_review_job("on_demand", owner)

        assert calls == [], "the review was run although its module could not be imported"
        assert jb.state_is_idle() and jb.taken_then_released(), "the slot stayed taken"
        failed = jb.the_run_state().snapshot()["last_failed_attempt"]
        assert failed is not None, "a failed import is a failed attempt and is recorded"
        assert failed["reason"] == "ImportError", "the exception type name only"
        assert jb.new_rows_since(ids_before) == []

    @pytest.mark.parametrize("claimed_by_caller", [True, False], ids=["token given", "own claim"])
    def test_a_recommender_module_that_cannot_be_imported_stores_nothing_and_frees_the_slot(
        self, store, monkeypatch, claimed_by_caller
    ):
        calls = jb.patch_runner(monkeypatch, _live_recs(), isolate=False)
        jb.fail_imports_of(monkeypatch, "advisors.retirement_recommender")
        owner = jb.the_run_state().try_start("on_demand") if claimed_by_caller else None
        assert owner or not claimed_by_caller
        ids_before = jb.all_row_ids()

        app_module._tenure_review_job("on_demand", owner)

        assert len(calls) == 1, "premise: the review itself ran"
        assert jb.state_is_idle() and jb.taken_then_released(), "the slot stayed taken"
        failed = jb.the_run_state().snapshot()["last_failed_attempt"]
        assert failed is not None and failed["reason"] == "ImportError"
        assert jb.new_rows_since(ids_before) == [], "no row of a run that did not complete"

    def test_the_job_never_raises_even_when_the_run_state_module_cannot_be_imported(
        self, store, monkeypatch
    ):
        calls = jb.patch_runner(monkeypatch, _live_recs(), isolate=False)
        jb.fail_imports_of(monkeypatch, "advisors.tenure_review")
        ids_before = jb.all_row_ids()

        app_module._tenure_review_job("nightly")

        assert calls == [] and jb.new_rows_since(ids_before) == []
        assert jb.state_is_idle()


class TestRowsOfARunThatFailedAfterTheyWereWrittenNeverShowBeforeTheFirstRun:
    """Recommendation rows are stored before the run row. A run that fails in between leaves
    rows that carry the id of a run no row describes; before any run has completed, the reader
    used to fall back to the newest date and listed them as the current recommendations."""

    def test_the_api_and_the_panel_list_none_of_them(self, store, client, threads, monkeypatch):
        _raise_in_the_run_row_write(monkeypatch)

        assert _post(client).status_code == 202

        stored = jb.rec_rows()
        assert len(stored) == 1 and jb.run_rows() == [], "premise: an orphan row, no run row"
        assert stored[0]["raw_response"]["review_run_id"], "premise: the row names its run"
        data = jb.api(client)
        assert data["review_run"] is None
        assert data["recommendations"] == [], "the row of a run that never completed was listed"
        panel = jb.panel(client)
        assert panel.find_all("retirement-recommendation-card") == []
        assert panel.find("tenure-review-empty") is not None, "still 'no review yet'"
        assert jb.the_run_state().snapshot()["last_failed_attempt"] is not None

    def test_the_job_docstring_says_what_really_happens_to_those_rows(self):
        doc = " ".join((app_module._tenure_review_job.__doc__ or "").split())
        assert "never for a failed attempt" not in doc, (
            "rows can remain from a run that failed after writing them"
        )
        assert "review_run_id" in doc, "the docstring names how readers ignore such rows"


# ---------------------------------------------------------------------------
# Refusals by the global hooks: nothing starts, nothing is written
# ---------------------------------------------------------------------------

_PASSWORD = "test-pass-review-run-abc123"
_SECRET_KEY = "test-secret-review-run-xyz789"


def _nothing_started(threads, calls, ids_before):
    assert threads == [], "a refused request must not start a thread"
    assert calls == [], "a refused request must not start a review"
    assert jb.new_rows_since(ids_before) == [], "a refused request writes nothing"
    assert jb.state_is_idle() and jb.taken_then_released()


@pytest.fixture()
def refusal_world(store, threads, monkeypatch):
    calls = jb.patch_runner(monkeypatch, _live_recs(), isolate=False)
    return calls, jb.all_row_ids()


class TestARequestWithoutTheCsrfTokenIsRefused:
    @pytest.fixture()
    def signed_in(self, client, monkeypatch):
        """Signed in while authentication and the CSRF check are both enabled, so a 403 is the
        CSRF check's doing and not the gate's."""
        monkeypatch.setenv("DASHBOARD_PASSWORD", _PASSWORD)
        monkeypatch.setenv("SECRET_KEY", _SECRET_KEY)
        monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        login = client.post("/login", data={"password": _PASSWORD}, follow_redirects=False)
        assert login.status_code in (302, 303)
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
        return client

    @pytest.mark.parametrize("token", [None, "", "not-the-token"], ids=["absent", "empty", "wrong"])
    def test_a_missing_or_wrong_token_gets_403_and_starts_nothing(
        self, refusal_world, threads, signed_in, token
    ):
        calls, ids_before = refusal_world
        headers = dict(XHR)
        if token is not None:
            headers["X-CSRF-Token"] = token

        response = _post(signed_in, headers=headers)

        assert response.status_code == 403
        _nothing_started(threads, calls, ids_before)

    def test_the_right_token_is_not_refused(self, refusal_world, threads, signed_in):
        calls, _ = refusal_world
        token = signed_in.get("/api/csrf-token").get_json()["csrf_token"]

        response = _post(signed_in, headers={**XHR, "X-CSRF-Token": token})

        assert response.status_code == 202
        assert len(calls) == 1


class TestAnUnauthenticatedRequestIsRefused:
    @pytest.fixture()
    def gate(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_PASSWORD", _PASSWORD)
        monkeypatch.setenv("SECRET_KEY", _SECRET_KEY)
        monkeypatch.setattr(app_module, "_auth_check_enabled", True)

    def test_an_xhr_request_gets_401_json(self, refusal_world, threads, client, gate):
        calls, ids_before = refusal_world

        response = _post(client, headers=XHR)

        assert response.status_code == 401
        assert response.get_json() == {"error": "unauthenticated"}
        _nothing_started(threads, calls, ids_before)

    def test_a_page_style_request_is_redirected_to_the_login_page(
        self, refusal_world, threads, client, gate
    ):
        calls, ids_before = refusal_world

        response = _post(client, follow_redirects=False)

        assert response.status_code == 302 and "/login" in response.headers["Location"]
        _nothing_started(threads, calls, ids_before)

    def test_a_signed_in_operator_is_not_refused(self, refusal_world, threads, client, gate):
        calls, _ = refusal_world
        with client.session_transaction() as session:
            session["authenticated"] = True

        response = _post(client, headers=XHR)

        assert response.status_code == 202
        assert len(calls) == 1


class TestARequestWhenAuthenticationIsNotConfiguredIsRefused:
    @pytest.mark.parametrize("missing", ["secret key", "credential"])
    def test_an_xhr_request_gets_503_and_a_page_style_request_is_redirected(
        self, refusal_world, threads, client, monkeypatch, missing
    ):
        calls, ids_before = refusal_world
        monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        for key in (
            "SECRET_KEY",
            "FLASK_SECRET_KEY",
            "DASHBOARD_PASSWORD",
            "DASHBOARD_PASSWORD_HASH",
        ):
            monkeypatch.delenv(key, raising=False)
        if missing == "secret key":
            monkeypatch.setenv("DASHBOARD_PASSWORD", _PASSWORD)
        else:
            monkeypatch.setenv("SECRET_KEY", _SECRET_KEY)

        xhr = _post(client, headers=XHR)
        page = _post(client, follow_redirects=False)

        assert xhr.status_code == 503
        assert xhr.get_json() == {"error": "misconfigured"}
        assert page.status_code == 302 and "/login" in page.headers["Location"]
        _nothing_started(threads, calls, ids_before)


# ---------------------------------------------------------------------------
# The market gate: the review is refused between the open and the close
# ---------------------------------------------------------------------------

MARKET_OPEN = {"status": "market_open"}
RUN_NOW_ROUTE = "/ai-advisor/retirement/run"
TRADING_DAY = date(2026, 10, 20)  # a Tuesday; the premise test below reads the real calendar


def _at(hour: int, minute: int, second: int = 0, *, day: date = TRADING_DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=jb.ET)


def _calendar_days(first: date, count: int = 120):
    return [first + timedelta(days=n) for n in range(count)]


def _an_early_close_day() -> date:
    """A day the real exchange calendar closes at the half-day time."""
    return next(
        d
        for d in _calendar_days(date(2026, 10, 1))
        if market_calendar.is_trading_day(d)
        and market_calendar.session_close(d) == market_calendar.HALF_DAY_CLOSE_TIME
    )


def _a_weekday_holiday() -> date:
    """A Monday to Friday the real exchange calendar has closed."""
    return next(
        d
        for d in _calendar_days(date(2026, 10, 1))
        if d.weekday() < 5 and not market_calendar.is_trading_day(d)
    )


def _a_saturday() -> date:
    return next(d for d in _calendar_days(date(2026, 10, 1)) if d.weekday() == 5)


# (instant, the state the real calendar reports, the status the route answers)
_BOUNDARIES = [
    pytest.param(_at(9, 29, 59), "pre_market", 202, id="one second before the open"),
    pytest.param(_at(9, 30, 0), "open", 409, id="at the open"),
    pytest.param(_at(11, 0), "open", 409, id="mid session"),
    pytest.param(_at(15, 59, 59), "open", 409, id="one second before a full-day close"),
    pytest.param(_at(16, 0, 0), "closed_frozen", 202, id="at a full-day close"),
    pytest.param(
        _at(12, 59, 59, day=_an_early_close_day()), "open", 409, id="before an early close"
    ),
    pytest.param(
        _at(13, 0, 0, day=_an_early_close_day()), "closed_frozen", 202, id="at an early close"
    ),
    pytest.param(_at(11, 0, day=_a_saturday()), "closed_frozen", 202, id="a Saturday"),
    pytest.param(
        _at(11, 0, day=_a_weekday_holiday()), "closed_frozen", 202, id="a weekday holiday"
    ),
]


@pytest.fixture()
def gated(store, threads, monkeypatch):
    """The runner replaced, and every claim of the run slot recorded: ``claims`` collects the
    trigger of each ``try_start`` call, so a test can tell a refusal that came before the claim
    from one that claimed the slot and gave it back."""
    calls = jb.patch_runner(monkeypatch, _live_recs(), isolate=False)
    state = jb.the_run_state()
    claims: list[str] = []
    real_try_start = state.try_start

    def recording_try_start(trigger):
        claims.append(trigger)
        return real_try_start(trigger)

    monkeypatch.setattr(state, "try_start", recording_try_start)

    class World:
        pass

    world = World()
    world.calls, world.claims = calls, claims
    return world


class TestTheCalendarPremises:
    @pytest.mark.parametrize("instant, state, _status", _BOUNDARIES)
    def test_each_instant_is_in_the_state_the_cases_below_claim(self, instant, state, _status):
        assert market_calendar.get_market_state(instant) == state

    def test_the_early_close_day_closes_earlier_than_a_full_day(self):
        assert market_calendar.session_close(_an_early_close_day()) < (
            market_calendar.session_close(TRADING_DAY)
        )


class TestTheRouteIsRefusedWhileTheMarketIsOpen:
    def test_a_request_gets_409_market_open_and_nothing_happens_and_the_slot_stays_free(
        self, gated, client, threads, monkeypatch
    ):
        jb.freeze_app_clock(monkeypatch, _at(11, 0))
        ids_before = jb.all_row_ids()

        response = _post(client)

        assert response.status_code == 409
        assert response.is_json and response.get_json() == MARKET_OPEN, (
            "the body is exactly the one Run recommender answers with: not already_running"
        )
        assert gated.claims == [], "the market check comes before the run slot is claimed"
        assert threads == [] and gated.calls == [], "no thread is built and no review starts"
        assert jb.new_rows_since(ids_before) == [], "no advisory row is written"
        assert jb.state_is_idle() and jb.the_run_state().snapshot()["last_failed_attempt"] is None
        jb.freeze_app_clock(monkeypatch, _at(16, 0))
        assert _post(client).status_code == 202, "a request after the close can start a review"
        assert len(gated.calls) == 1

    def test_an_open_market_wins_over_a_review_that_holds_the_slot(
        self, gated, client, threads, monkeypatch
    ):
        jb.freeze_app_clock(monkeypatch, _at(11, 0))
        state = jb.the_run_state()
        token = state.try_start("nightly")
        assert token
        held = state.snapshot()
        ids_before = jb.all_row_ids()

        response = _post(client)

        assert response.status_code == 409
        assert response.get_json() == MARKET_OPEN, "not already_running"
        assert gated.claims == ["nightly"], "only the test's own claim; the route made none"
        assert threads == [] and gated.calls == []
        assert jb.new_rows_since(ids_before) == []
        assert state.snapshot() == held, "the running review is untouched"
        state.finish_success(token)

    @pytest.mark.parametrize("instant, state, status", _BOUNDARIES)
    def test_the_answer_follows_the_exchange_calendar_at_each_boundary(
        self, gated, client, threads, monkeypatch, instant, state, status
    ):
        jb.freeze_app_clock(monkeypatch, instant)

        response = _post(client)

        assert response.status_code == status
        if status == 409:
            assert response.get_json() == MARKET_OPEN
            assert gated.calls == [] and threads == []
        else:
            assert response.get_json() == STARTED
            assert len(gated.calls) == 1 and gated.calls[0]["trigger"] == "on_demand"
            assert jb.state_is_idle()

    def test_the_gate_asks_the_calendar_about_the_current_eastern_time(
        self, gated, client, threads, monkeypatch
    ):
        instant = _at(11, 0)
        jb.freeze_app_clock(monkeypatch, instant)
        seen: list = []
        real = app_module.get_market_state

        def recording(current_et, *args, **kwargs):
            seen.append(current_et)
            return real(current_et, *args, **kwargs)

        monkeypatch.setattr(app_module, "get_market_state", recording)

        _post(client)

        assert seen, "the route never consulted app_module.get_market_state"
        assert getattr(seen[-1].tzinfo, "key", None) == "America/New_York"
        assert seen[-1] == instant, "the time asked about is the clock's now"

    def test_a_market_state_read_that_fails_fails_the_request_and_starts_nothing(
        self, gated, client, threads, monkeypatch
    ):
        """The same fault in Run recommender fails its request (HTTP 500) because the read is not
        wrapped, and the plan states 500 for this case; a gate that swallowed the error would
        fail open and start a review during the session."""
        monkeypatch.setitem(app_module.app.config, "TESTING", False)
        monkeypatch.setitem(app_module.app.config, "PROPAGATE_EXCEPTIONS", False)

        def failing(_current_et, *_args, **_kwargs):
            raise RuntimeError(f"calendar said {LEAK}")

        monkeypatch.setattr(app_module, "get_market_state", failing)
        ids_before = jb.all_row_ids()

        response = _post(client)

        assert response.status_code == 500
        assert LEAK not in response.get_data(as_text=True)
        assert threads == [] and gated.calls == []
        assert jb.new_rows_since(ids_before) == []
        assert jb.state_is_idle() and jb.taken_then_released(), "the slot stays free"


class TestARunThatStartedBeforeTheOpenIsLeftAlone:
    def test_the_open_arrives_mid_run_the_run_finishes_and_a_second_request_is_refused(
        self, store, client, monkeypatch
    ):
        recorded = jb.install_recording_threads(monkeypatch)
        blocked = jb.BlockingRunner(_live_recs()).install(monkeypatch)
        jb.freeze_app_clock(monkeypatch, _at(9, 25))
        try:
            assert _post(client).status_code == 202
            assert blocked.entered.wait(30)
            running = jb.the_run_state().snapshot()
            assert running["running"] is True and running["trigger"] == "on_demand"

            jb.freeze_app_clock(monkeypatch, _at(9, 31))
            second = _post(client)

            assert second.status_code == 409
            assert second.get_json() == MARKET_OPEN, "the open is reported, not already_running"
            assert jb.the_run_state().snapshot() == running, "the running review is not touched"
            assert len(blocked.calls) == 1 and len(recorded) == 1
        finally:
            blocked.release.set()
            jb.join_threads(recorded)

        assert jb.state_is_idle()
        assert jb.the_run_state().snapshot()["last_failed_attempt"] is None
        assert len(jb.run_rows()) == 1, "the held review finished and recorded its run"


class TestThePageRendersTheSameDuringTheSession:
    def test_the_panel_lists_the_stored_review_and_offers_the_run_button_at_11_00(
        self, store, client, monkeypatch
    ):
        record = pb.store_full_run(pb.RUN_ID_FIRST)
        pb.seed_roster_state(record)
        jb.freeze_app_clock(monkeypatch, _at(11, 0))

        panel = jb.panel(client)

        assert len(panel.find_all("tenure-review-entry")) == len(record["entries"])
        assert panel.find("tenure-review-run-btn") is not None
        assert panel.find("tenure-review-run-message") is not None
        assert panel.find("retirement-run-now-btn") is not None


class TestTheMarketGateFollowsTheAuthenticationAndCsrfChecks:
    """At 11:00 on a trading day a request the global hooks refuse is refused by them, before the
    route's market check ever runs."""

    @pytest.fixture()
    def during_the_session(self, gated, threads, client, monkeypatch):
        jb.freeze_app_clock(monkeypatch, _at(11, 0))
        asked: list = []
        real = app_module.get_market_state

        def spy(current_et, *args, **kwargs):
            asked.append(current_et)
            return real(current_et, *args, **kwargs)

        monkeypatch.setattr(app_module, "get_market_state", spy)
        monkeypatch.setenv("DASHBOARD_PASSWORD", _PASSWORD)
        monkeypatch.setenv("SECRET_KEY", _SECRET_KEY)
        monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        return asked

    def test_no_session_with_the_xhr_marker_gets_401_before_the_market_check(
        self, during_the_session, gated, client, threads
    ):
        ids_before = jb.all_row_ids()

        response = _post(client, headers=XHR)

        assert response.status_code == 401
        assert during_the_session == [], "the route's market check ran for a refused request"
        _nothing_started(threads, gated.calls, ids_before)

    def test_a_signed_in_request_without_the_token_gets_403_before_the_market_check(
        self, during_the_session, gated, client, threads, monkeypatch
    ):
        login = client.post("/login", data={"password": _PASSWORD}, follow_redirects=False)
        assert login.status_code in (302, 303)
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
        ids_before = jb.all_row_ids()

        response = _post(client, headers=XHR)

        assert response.status_code == 403
        assert during_the_session == []
        _nothing_started(threads, gated.calls, ids_before)

    def test_a_signed_in_request_with_the_token_reaches_the_market_check(
        self, during_the_session, gated, client, threads, monkeypatch
    ):
        login = client.post("/login", data={"password": _PASSWORD}, follow_redirects=False)
        assert login.status_code in (302, 303)
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
        token = client.get("/api/csrf-token").get_json()["csrf_token"]

        response = _post(client, headers={**XHR, "X-CSRF-Token": token})

        assert response.status_code == 409 and response.get_json() == MARKET_OPEN
        assert len(during_the_session) == 1


@pytest.mark.parametrize("route", [RUN_ROUTE, RUN_NOW_ROUTE], ids=["tenure review", "recommender"])
class TestARefusedRunNeverReachesTheExplainer:
    """The two requests that lead to the explainer (the tenure job and the recommender worker)
    start nothing and call no explainer when the global hooks refuse them."""

    @pytest.fixture()
    def world(self, store, monkeypatch):
        explained: list = []
        # Installed first: patch_runner leaves an explainer that is not the module's own alone.
        monkeypatch.setattr(
            explainer_module, "explain_recommendation", lambda rec: explained.append(rec)
        )
        started = jb.install_inline_threads(monkeypatch)
        calls = jb.patch_runner(monkeypatch, _live_recs(), isolate=False)
        monkeypatch.setenv("DASHBOARD_PASSWORD", _PASSWORD)
        monkeypatch.setenv("SECRET_KEY", _SECRET_KEY)
        monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        return explained, started, calls

    def test_no_session_is_refused_and_nothing_starts(self, world, client, route):
        explained, started, calls = world
        ids_before = jb.all_row_ids()

        response = client.post(route, headers=XHR)

        assert response.status_code == 401
        assert explained == [] and started == [] and calls == []
        assert not app_module._RETIREMENT_RUN_LOCK.locked()
        assert jb.new_rows_since(ids_before) == [] and jb.state_is_idle()

    def test_a_signed_in_request_without_the_token_is_refused_and_nothing_starts(
        self, world, client, route, monkeypatch
    ):
        explained, started, calls = world
        login = client.post("/login", data={"password": _PASSWORD}, follow_redirects=False)
        assert login.status_code in (302, 303)
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
        ids_before = jb.all_row_ids()

        response = client.post(route, headers=XHR)

        assert response.status_code == 403
        assert explained == [] and started == [] and calls == []
        assert not app_module._RETIREMENT_RUN_LOCK.locked()
        assert jb.new_rows_since(ids_before) == [] and jb.state_is_idle()
