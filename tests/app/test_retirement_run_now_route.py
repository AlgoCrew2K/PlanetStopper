"""RED tests -- POST /ai-advisor/retirement/run ("Run now"; Cycle 2e AC-7).

Contract (feature-plans/retirement-visibility.md AC-7; .claude/tdd-handoff.md):

  * ``POST /ai-advisor/retirement/run`` -- CSRF (global hook) + auth (global
    hook); NOT in ``_SETTINGS_WRITE_ALLOWLIST``; no LIVE_EXECUTION read.
  * Single-flight: a module-level lock shared with the 03:45 tick. If a run
    is in flight -> ``409 {"status": "already_running"}``; else the SAME
    worker (``_retirement_recommender_tick_worker``) is started on a daemon
    thread and the route answers immediately ``202 {"status": "started"}``.
  * The scheduled tick (``_run_retirement_recommender_tick``) still always
    spawns its daemon thread (thread name ``retirement-recommender-tick``);
    inside that thread it SKIPS -- logging an INFO line containing "skip" --
    when a run is already in flight.
  * The lock is released when the worker finishes OR fails.

These tests are lock-name-agnostic: an in-flight run is modelled by a fake
``build_recommendations_with_summary`` that blocks on an Event, driven through
the REAL worker/route/thread machinery. Only the producer (network-adjacent)
and the explainer (LLM) are faked; the DB is a real temp DB.

CSRF note: the autouse ``_disable_csrf_for_tests`` fixture sets
``app._csrf_check_enabled = False`` for every test; the CSRF tests below
re-enable it per-test (same pattern as tests/app/test_retirement_approval_routes.py).
"""

from __future__ import annotations

import logging
import threading
import time
from unittest.mock import patch

import pytest

import app as app_module
import database as db_module
from tests.app._retirement_run_summary_stub import make_summary

_RUN_URL = "/ai-advisor/retirement/run"
_WAIT_SECONDS = 5.0


def _wait_until(predicate, timeout=_WAIT_SECONDS, interval=0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class _BlockingBuild:
    """Fake ``build_recommendations_with_summary``: records calls, optionally
    blocks until released, can be told to raise."""

    def __init__(self, *, block=False, raises=None):
        self.block = block
        self.raises = raises
        self.calls: list[dict] = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        if not block:
            self.release.set()

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        self.started.set()
        try:
            self.release.wait(timeout=_WAIT_SECONDS)
            if self.raises is not None:
                raise self.raises
            return [], make_summary()
        finally:
            self.finished.set()


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_retirement_run_now.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()
    db_module.run_migrations()
    return db_path


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def build(monkeypatch, isolated_db):
    """Install a controllable fake producer; guarantee no thread outlives the test."""
    import advisors.retirement_explainer as explainer_module  # noqa: PLC0415
    import advisors.retirement_recommender as recommender_module  # noqa: PLC0415

    baseline = set(threading.enumerate())
    holder: dict[str, _BlockingBuild] = {}

    def _install(**kwargs) -> _BlockingBuild:
        fake = _BlockingBuild(**kwargs)
        holder["fake"] = fake
        monkeypatch.setattr(recommender_module, "build_recommendations_with_summary", fake)
        monkeypatch.setattr(explainer_module, "explain_recommendation", lambda rec: "x")
        return fake

    yield _install

    fake = holder.get("fake")
    if fake is not None:
        fake.release.set()
    for t in set(threading.enumerate()) - baseline:
        if t.daemon and t is not threading.current_thread():
            t.join(timeout=_WAIT_SECONDS)


@pytest.fixture()
def _reenable_csrf(monkeypatch):
    monkeypatch.setattr(app_module, "_csrf_check_enabled", True)


@pytest.fixture()
def auth_client(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-pass-retirement-run-abc123")
    monkeypatch.setenv("SECRET_KEY", "test-secret-retirement-run-xyz789")
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _post(client, **kwargs):
    return client.post(_RUN_URL, **kwargs)


def _post_until_not_conflict(client) -> int:
    """POST until the response is not 409 (the lock was released); returns the
    first non-409 status. Each 202 starts a quick no-op run."""
    status = {"code": 0}

    def _attempt():
        status["code"] = _post(client).status_code
        return status["code"] != 409

    assert _wait_until(_attempt), "single-flight lock was never released"
    return status["code"]


# ===========================================================================
# Route registration
# ===========================================================================


class TestRouteRegistration:
    def test_run_route_is_registered_for_post_only(self):
        rules = [r for r in app_module.app.url_map.iter_rules() if r.rule == _RUN_URL]
        assert rules, f"{_RUN_URL} is not registered"
        methods = set().union(*(r.methods for r in rules))
        assert "POST" in methods
        assert "GET" not in methods

    def test_run_route_handler_is_named_ai_advisor_retirement_run(self):
        (rule,) = [r for r in app_module.app.url_map.iter_rules() if r.rule == _RUN_URL]
        assert rule.endpoint == "ai_advisor_retirement_run"

    def test_get_on_the_run_route_is_405(self, client):
        assert client.get(_RUN_URL).status_code == 405

    def test_run_route_is_not_in_the_settings_write_allowlist(self):
        allowlist = app_module._SETTINGS_WRITE_ALLOWLIST
        assert _RUN_URL not in allowlist
        assert not any("retirement" in str(k).lower() for k in allowlist)


# ===========================================================================
# 202 started / non-blocking
# ===========================================================================


class TestStarted:
    def test_post_starts_the_same_worker_and_answers_202_started(self, client, build):
        fake = build()
        resp = _post(client)

        assert resp.status_code == 202
        assert resp.is_json
        assert resp.get_json() == {"status": "started"}
        assert fake.started.wait(_WAIT_SECONDS), "the worker never ran"
        assert fake.calls == [{"source": "composer"}], "same worker as the 03:45 tick"

    def test_the_route_answers_before_the_run_finishes(self, client, build):
        fake = build(block=True)
        resp = _post(client)

        assert resp.status_code == 202
        assert fake.started.wait(_WAIT_SECONDS)
        assert not fake.finished.is_set(), (
            "the route ran the worker inline -- it must hand off to a daemon thread"
        )

    def test_a_completed_run_persists_its_summary_row(self, client, build):
        fake = build()
        _post(client)
        assert fake.finished.wait(_WAIT_SECONDS)
        assert _wait_until(
            lambda: len(db_module.get_advisor_observations_for_role("RETIREMENT_RUN_SUMMARY")) == 1
        )

    def test_the_worker_thread_is_a_daemon(self, client, build):
        fake = build(block=True)
        before = set(threading.enumerate())
        _post(client)
        assert fake.started.wait(_WAIT_SECONDS)
        new_threads = [t for t in threading.enumerate() if t not in before]
        assert new_threads, "no worker thread was started"
        assert all(t.daemon for t in new_threads)


# ===========================================================================
# Single-flight
# ===========================================================================


class TestSingleFlight:
    def test_a_second_post_while_a_run_is_in_flight_is_409_already_running(self, client, build):
        fake = build(block=True)
        assert _post(client).status_code == 202
        assert fake.started.wait(_WAIT_SECONDS)

        second = _post(client)
        assert second.status_code == 409
        assert second.get_json() == {"status": "already_running"}
        assert len(fake.calls) == 1, "no second worker may start"

    def test_the_lock_is_released_after_a_run_completes(self, client, build):
        fake = build(block=True)
        assert _post(client).status_code == 202
        assert fake.started.wait(_WAIT_SECONDS)
        assert _post(client).status_code == 409

        fake.release.set()
        assert fake.finished.wait(_WAIT_SECONDS)
        assert _post_until_not_conflict(client) == 202

    def test_the_lock_is_released_even_when_the_worker_fails(self, client, build):
        fake = build(raises=RuntimeError("boom"))
        assert _post(client).status_code == 202
        assert fake.finished.wait(_WAIT_SECONDS)
        assert _post_until_not_conflict(client) == 202

    def test_concurrent_posts_start_exactly_one_run(self, client, build):
        fake = build(block=True)
        results: list[int] = []
        lock = threading.Lock()
        barrier = threading.Barrier(5)

        def _hit():
            with app_module.app.test_client() as c:
                barrier.wait(timeout=_WAIT_SECONDS)
                code = _post(c).status_code
            with lock:
                results.append(code)

        threads = [threading.Thread(target=_hit) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=_WAIT_SECONDS * 2)

        assert sorted(results) == [202, 409, 409, 409, 409]
        assert fake.started.wait(_WAIT_SECONDS)
        assert len(fake.calls) == 1

    def test_a_scheduled_tick_skips_when_a_run_is_in_flight(self, client, build, caplog):
        fake = build(block=True)
        assert _post(client).status_code == 202
        assert fake.started.wait(_WAIT_SECONDS)

        with caplog.at_level(logging.INFO):
            app_module._run_retirement_recommender_tick()
            skipped = _wait_until(
                lambda: any(
                    r.levelno == logging.INFO and "skip" in r.getMessage().lower()
                    for r in caplog.records
                )
            )

        assert skipped, "the skipped tick must log an INFO line containing 'skip'"
        assert len(fake.calls) == 1, "the skipped tick must not start a second run"

    def test_a_scheduled_tick_runs_when_nothing_is_in_flight(self, build):
        fake = build()
        app_module._run_retirement_recommender_tick()
        assert fake.started.wait(_WAIT_SECONDS)
        assert fake.calls == [{"source": "composer"}]

    def test_a_post_while_the_scheduled_tick_is_running_is_409(self, client, build):
        fake = build(block=True)
        app_module._run_retirement_recommender_tick()
        assert fake.started.wait(_WAIT_SECONDS)

        resp = _post(client)
        assert resp.status_code == 409
        assert resp.get_json() == {"status": "already_running"}

    def test_the_tick_wrapper_still_always_spawns_a_daemon_thread(self):
        """Existing contract (tests/app/test_retirement_recommendations_scheduling.py):
        the wrapper never decides synchronously -- it always spawns+starts a
        daemon thread, and the single-flight decision happens inside it."""
        captured: dict = {}

        class _FakeThread:
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)
                self.started = False

            def start(self):
                self.started = True
                captured["started"] = True

        with patch("threading.Thread", _FakeThread):
            app_module._run_retirement_recommender_tick()
            app_module._run_retirement_recommender_tick()  # twice: no lock may leak

        assert captured.get("daemon") is True
        assert captured.get("started") is True


# ===========================================================================
# CSRF + auth
# ===========================================================================


def _csrf_token(client) -> str:
    resp = client.get("/api/csrf-token")
    assert resp.status_code == 200
    token = resp.get_json().get("csrf_token")
    assert token
    return token


class TestCsrfAndAuth:
    def test_post_without_a_csrf_token_is_rejected_and_starts_nothing(
        self, client, build, _reenable_csrf
    ):
        fake = build()
        resp = _post(client)
        assert resp.status_code == 403
        time.sleep(0.1)
        assert fake.calls == []

    def test_post_with_a_wrong_csrf_token_is_rejected(self, client, build, _reenable_csrf):
        import secrets  # noqa: PLC0415

        fake = build()
        resp = _post(client, headers={"X-CSRF-Token": secrets.token_hex(32)})
        assert resp.status_code == 403
        time.sleep(0.1)
        assert fake.calls == []

    def test_post_with_a_valid_csrf_token_is_accepted(self, client, build, _reenable_csrf):
        fake = build()
        token = _csrf_token(client)
        resp = _post(client, headers={"X-CSRF-Token": token})
        assert resp.status_code == 202
        assert fake.started.wait(_WAIT_SECONDS)

    def test_unauthenticated_post_is_redirected_or_401_and_starts_nothing(self, auth_client, build):
        fake = build()
        resp = _post(auth_client)
        assert resp.status_code in (302, 401)
        time.sleep(0.1)
        assert fake.calls == []
