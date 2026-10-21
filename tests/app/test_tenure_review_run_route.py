"""POST /ai-advisor/retirement/review/run: the "Run review now" action.

The route claims the single run slot, submits the review job to the dedicated executor and answers
at once; the review itself runs on the executor's worker. These tests drive the real route through
the Flask test client. The executor is replaced by one that runs the job on the spot, one backed by
a real worker, or one that refuses the submission; the Composer reads are replaced by a runner
stand-in over the real result types (the real runner is exercised by the end-to-end file).

Contract pinned here (the implementer reads this file and the handoff, not the plan):

* ``app._TENURE_REVIEW_EXECUTOR`` is a module-level single-worker executor (patched here by name;
  the route must look it up on the module at each request) and the route is
  ``ai_advisor_retirement_review_run``.
* The route reads no request body. It calls ``REVIEW_RUN_STATE.try_start("on_demand")`` (looked up as
  ``tenure_review.REVIEW_RUN_STATE`` at each request); when that is refused it answers 409 with
  ``{"status": "already_running", "message": "a review is already running"}`` and changes nothing.
  Otherwise it submits the job with the owner token and answers 202 with
  ``{"status": "started", "message": "review started"}``. When the submission itself raises, it
  releases the slot with the owner token and answers 200 with ``{"error": <exception type name>}``.
* Authentication and CSRF come from the global hooks; the route calls neither.
"""

from __future__ import annotations

import concurrent.futures
import copy
import inspect
import logging
import threading

import pytest

import advisors.retirement_recommender as recommender_module
import app as app_module
import database as db_module
from tests.app import _tenure_review_job_builders as jb
from tests.app import _tenure_review_panel_builders as pb

RUN_ROUTE = "/ai-advisor/retirement/review/run"
XHR = {"X-Requested-With": "XMLHttpRequest"}
STARTED = {"status": "started", "message": "review started"}
ALREADY_RUNNING = {"status": "already_running", "message": "a review is already running"}
LEAK = "sk-ant-LEAKMARK-4d82b0e7c1"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A scratch database and an environment that names no account."""
    jb.use_scratch_db(monkeypatch, tmp_path, "test_tenure_review_run_route.db")
    jb.isolate_env(monkeypatch, tmp_path)
    jb.freeze_app_clock(monkeypatch)
    return tmp_path


@pytest.fixture()
def executor(monkeypatch):
    """The executor the route submits to, replaced by one that runs the job at once."""
    immediate = jb.ImmediateExecutor()
    monkeypatch.setattr(app_module, "_TENURE_REVIEW_EXECUTOR", immediate, raising=False)
    return immediate


def _post(client, **kwargs):
    response = client.post(RUN_ROUTE, **kwargs)
    assert response.status_code != 404, f"{RUN_ROUTE} is not registered"
    return response


def _live_recs():
    return [{"candidate_id": "cand-run-route", "sibling_id": "sib-run-route"}]


class TestTheRouteStartsOneReview:
    def test_only_post_is_accepted(self, store, client, executor):
        response = client.get(RUN_ROUTE)
        assert response.status_code == 405
        assert executor.submitted == []

    def test_a_request_is_acknowledged_and_the_review_runs_as_on_demand_with_the_slot_held(
        self, store, client, executor, monkeypatch
    ):
        seen = {}

        def hook(kwargs):
            seen["state"] = jb.the_run_state().snapshot()

        calls = jb.patch_runner(monkeypatch, _live_recs(), hook=hook, isolate=False)

        response = _post(client)

        assert response.status_code == 202
        assert response.get_json() == STARTED
        assert len(executor.submitted) == 1, "exactly one job is submitted"
        assert len(calls) == 1
        assert calls[0]["trigger"] == "on_demand"
        assert seen["state"]["running"] is True and seen["state"]["trigger"] == "on_demand", (
            "the slot is claimed before the job runs"
        )
        assert jb.state_is_idle(), "the job gives the slot back when it ends"
        assert len(jb.run_rows()) == 1 and len(jb.rec_rows()) == 1

    def test_the_response_does_not_wait_for_the_review(self, store, client, monkeypatch):
        """With a real worker held by the runner, the request still returns 202 and the review
        finishes by itself afterwards."""
        threaded = jb.ThreadedExecutor()
        monkeypatch.setattr(app_module, "_TENURE_REVIEW_EXECUTOR", threaded, raising=False)
        blocked = jb.BlockingRunner().install(monkeypatch)
        try:
            response = _post(client)
            assert response.status_code == 202
            assert blocked.entered.wait(30)
            assert jb.the_run_state().snapshot()["running"] is True
        finally:
            blocked.release.set()
            threaded.wait_all()
            threaded.shutdown()
        assert jb.state_is_idle()
        assert len(jb.run_rows()) == 1


class TestTheReviewHasItsOwnWorker:
    def test_the_executor_is_dedicated_single_worker_and_shut_down_at_exit(self):
        """A review reads Composer for many minutes, so it must neither share the worker of the
        short dismiss writes nor the frontrunner build's worker, and runs one at a time."""
        executor = getattr(app_module, "_TENURE_REVIEW_EXECUTOR", None)
        assert executor is not None, "app._TENURE_REVIEW_EXECUTOR is missing"
        assert isinstance(executor, concurrent.futures.ThreadPoolExecutor)
        assert executor is not app_module._DISMISS_EXECUTOR
        assert executor is not app_module._FRONTRUNNER_BUILD_EXECUTOR
        assert executor._max_workers == 1
        source = inspect.getsource(app_module)
        assert "atexit.register(_TENURE_REVIEW_EXECUTOR.shutdown" in source


class TestAnOnDemandRunReplacesTheLastReview:
    def test_the_panel_and_the_api_show_the_new_run_and_none_of_the_nightly_rows(
        self, store, client, executor, monkeypatch
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
        self, store, client, executor, monkeypatch
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
        assert executor.submitted == [] and calls == [], "no second run starts"
        assert jb.new_rows_since(ids_before) == [], "nothing is written"
        assert state.snapshot() == held, "the refusal leaves the running review untouched"
        state.finish_success(token)
        assert _post(client).status_code == 202, "a request after the run ends starts one"

    @pytest.mark.parametrize("simultaneous", [2, 8])
    def test_requests_arriving_together_start_exactly_one_review(
        self, store, monkeypatch, simultaneous
    ):
        threaded = jb.ThreadedExecutor()
        monkeypatch.setattr(app_module, "_TENURE_REVIEW_EXECUTOR", threaded, raising=False)
        blocked = jb.BlockingRunner(_live_recs()).install(monkeypatch)
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

        threads = [threading.Thread(target=post_together) for _ in range(simultaneous)]
        try:
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
            assert blocked.entered.wait(30)
        finally:
            blocked.release.set()
            threaded.wait_all()
            threaded.shutdown()

        assert sorted(statuses) == [202] + [409] * (simultaneous - 1), statuses
        assert STARTED in bodies and bodies.count(ALREADY_RUNNING) == simultaneous - 1
        assert len(threaded.submitted) == 1 and len(blocked.calls) == 1
        assert len(jb.run_rows()) == 1 and len(jb.rec_rows()) == 1, "one review, written once"
        assert jb.state_is_idle()


class TestASubmissionThatFailsGivesTheSlotBack:
    def test_the_response_names_the_exception_type_only_and_the_next_request_can_start(
        self, store, client, monkeypatch, caplog
    ):
        refusing = jb.RefusingExecutor(RuntimeError(f"cannot schedule: {LEAK}"))
        monkeypatch.setattr(app_module, "_TENURE_REVIEW_EXECUTOR", refusing, raising=False)
        ids_before = jb.all_row_ids()

        with caplog.at_level(logging.DEBUG):
            response = _post(client)

        assert response.status_code == 200
        assert response.get_json() == {"error": "RuntimeError"}
        assert LEAK not in response.get_data(as_text=True)
        assert LEAK not in caplog.text, "the log names the exception type only"
        assert refusing.calls == 1
        assert jb.taken_then_released(), "a failed submission left the run slot taken"
        assert jb.the_run_state().snapshot()["last_failed_attempt"] is None, (
            "a job that never started is not a failed review attempt"
        )
        assert jb.new_rows_since(ids_before) == []
        working = jb.ImmediateExecutor()
        monkeypatch.setattr(app_module, "_TENURE_REVIEW_EXECUTOR", working, raising=False)
        jb.patch_runner(monkeypatch, [], isolate=False)
        assert _post(client).status_code == 202, "the next request must be able to start a run"


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
        self, store, client, executor, monkeypatch, caplog, where
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


# ---------------------------------------------------------------------------
# Refusals by the global hooks: nothing starts, nothing is written
# ---------------------------------------------------------------------------

_PASSWORD = "test-pass-review-run-abc123"
_SECRET_KEY = "test-secret-review-run-xyz789"


def _nothing_started(executor, calls, ids_before):
    assert executor.submitted == [], "a refused request must not reach the executor"
    assert calls == [], "a refused request must not start a review"
    assert jb.new_rows_since(ids_before) == [], "a refused request writes nothing"
    assert jb.state_is_idle() and jb.taken_then_released()


@pytest.fixture()
def refusal_world(store, executor, monkeypatch):
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
        self, refusal_world, executor, signed_in, token
    ):
        calls, ids_before = refusal_world
        headers = dict(XHR)
        if token is not None:
            headers["X-CSRF-Token"] = token

        response = _post(signed_in, headers=headers)

        assert response.status_code == 403
        _nothing_started(executor, calls, ids_before)

    def test_the_right_token_is_not_refused(self, refusal_world, executor, signed_in):
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

    def test_an_xhr_request_gets_401_json(self, refusal_world, executor, client, gate):
        calls, ids_before = refusal_world

        response = _post(client, headers=XHR)

        assert response.status_code == 401
        assert response.get_json() == {"error": "unauthenticated"}
        _nothing_started(executor, calls, ids_before)

    def test_a_page_style_request_is_redirected_to_the_login_page(
        self, refusal_world, executor, client, gate
    ):
        calls, ids_before = refusal_world

        response = _post(client, follow_redirects=False)

        assert response.status_code == 302 and "/login" in response.headers["Location"]
        _nothing_started(executor, calls, ids_before)

    def test_a_signed_in_operator_is_not_refused(self, refusal_world, executor, client, gate):
        calls, _ = refusal_world
        with client.session_transaction() as session:
            session["authenticated"] = True

        response = _post(client, headers=XHR)

        assert response.status_code == 202
        assert len(calls) == 1


class TestARequestWhenAuthenticationIsNotConfiguredIsRefused:
    @pytest.mark.parametrize("missing", ["secret key", "credential"])
    def test_an_xhr_request_gets_503_and_a_page_style_request_is_redirected(
        self, refusal_world, executor, client, monkeypatch, missing
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
        _nothing_started(executor, calls, ids_before)
