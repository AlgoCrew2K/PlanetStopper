"""A dashboard session ends 12 hours after login and a logout ends every earlier session.

Contract pinned (the tests below read the daemon only through its routes and four named seams):

* A session carries the instant of its login. A request whose session was issued longer ago than the
  session lifetime (12 hours, or the whole number of hours from 1 to 720 in the optional ``.env`` key
  ``DASHBOARD_SESSION_LIFETIME_HOURS``) gets the unauthenticated outcome: an HTML request is redirected
  (302) to ``/login``, an ``/api/`` or XHR request gets 401 ``{"error": "unauthenticated"}``. Requests
  made during the session never extend it.
* ``POST /logout`` (header CSRF token only) records a revocation instant. Every session issued at or
  before it is refused from then on, on every browser, also after a daemon restart. A session issued by
  a later login is served. A logout without a valid session revokes nothing.
* When the revocation cannot be written, every earlier session stays refused for the life of the
  process, one ERROR line names the exception type only, and the login page says other sessions end
  when the daemon restarts. When the stored revocation cannot be read at start, every session issued
  before the start is refused.

Seams: ``app._session_now_ns`` (the clock, patched through ``clock``), ``app._session_revoked_before_ns``
and ``app._PROCESS_STARTED_NS`` (reset to simulate a restart), and ``database.record_session_revocation``
/ ``database.get_session_revocation_floor_ns`` (the stored revocation, patched to raise). Every cookie is
issued by the real ``POST /login``; the real ``.env`` is never read.
"""

from __future__ import annotations

import ast
import inspect
import json
import logging
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from datetime import timedelta

import pytest

import app as app_module
import database

_PASSWORD = "session-test-pass-4f8a1c"
_SECRET = "session-test-secret-9b2d7e"
_ACCOUNTS = "/api/accounts"
_NS = 1_000_000_000
_XHR = {"X-Requested-With": "XMLHttpRequest"}
_UNAUTHENTICATED = {"error": "unauthenticated"}
_LIFETIME_KEY = "DASHBOARD_SESSION_LIFETIME_HOURS"
_NOT_DURABLE_LINE = "Signed out. Other sessions are ended until the daemon restarts."
_RUN_URL = "/ai-advisor/retirement/run"
_APP_PY = pathlib.Path(app_module.__file__)
_SENTINEL_DETAIL = "sentinel-database-detail-7c1e"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def gate(monkeypatch, tmp_path):
    """The auth gate on, a known credential and secret, a 12 hour lifetime, a temporary ``.env``."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", _PASSWORD)
    monkeypatch.setenv("SECRET_KEY", _SECRET)
    for name in ("DASHBOARD_PASSWORD_HASH", "FLASK_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("EXECUTION_START_TIME=09:35\n", encoding="utf-8")
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(env_file))
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    monkeypatch.setitem(app_module.app.config, "SESSION_COOKIE_SECURE", False)
    monkeypatch.setattr(app_module.app, "permanent_session_lifetime", timedelta(hours=12))
    app_module.app.config["TESTING"] = True
    return env_file


class _Clock:
    """The session clock: moves only when a test says so (``tick`` moves it on every read)."""

    def __init__(self) -> None:
        self.now = time.time_ns()
        self.tick = 0
        self._lock = threading.Lock()

    def __call__(self) -> int:
        with self._lock:
            value = self.now
            self.now += self.tick
            return value

    def advance(self, **delta) -> None:
        self.now += int(timedelta(**delta).total_seconds() * _NS)


@pytest.fixture
def clock(gate, monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(app_module, "_session_now_ns", fake, raising=False)
    return fake


def _client():
    return app_module.app.test_client()


def _login(client) -> None:
    response = client.post("/login", data={"password": _PASSWORD})
    assert response.status_code == 302, f"login answered {response.status_code}"


def _signed_in(clock) -> object:
    client = _client()
    _login(client)
    return client


def _copy_session(source, target) -> None:
    name = app_module.app.config["SESSION_COOKIE_NAME"]
    cookie = source.get_cookie(name)
    assert cookie is not None, "the login set no session cookie"
    target.set_cookie(name, cookie.value)


def _csrf_headers(**extra) -> dict:
    return {"X-CSRF-Token": app_module._CSRF_TOKEN, **extra}


def _post_logout(client, **kwargs):
    kwargs.setdefault("headers", _csrf_headers())
    return client.post("/logout", **kwargs)


def _require_post_logout() -> None:
    methods = set()
    for rule in app_module.app.url_map.iter_rules():
        if rule.rule == "/logout":
            methods |= rule.methods
    assert "POST" in methods, "/logout must accept POST: signing out is a CSRF-checked POST"


def _assert_refused(client, label: str) -> None:
    response = client.get(_ACCOUNTS)
    assert response.status_code == 401, f"{label}: answered {response.status_code}, not 401"
    assert response.get_json() == _UNAUTHENTICATED, label


def _assert_served(client, label: str) -> None:
    response = client.get(_ACCOUNTS)
    assert response.status_code == 200, f"{label}: answered {response.status_code}, not 200"


def _assert_page_redirected(client, label: str) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302, f"{label}: GET / answered {response.status_code}"
    assert "/login" in response.headers.get("Location", ""), label


def _assert_logged_in_at_logout_redirect(response) -> None:
    assert response.status_code in (302, 303), f"POST /logout answered {response.status_code}"
    assert response.headers.get("Location", "").endswith("/login"), response.headers.get("Location")


@pytest.fixture
def run_now_spy(monkeypatch):
    """Records what the Run-now route starts; the route never reaches the Composer producer."""
    started: list[int] = []
    lock = app_module._RETIREMENT_RUN_LOCK

    def _body(*_a, **_k):
        started.append(1)
        if lock.locked():
            lock.release()

    monkeypatch.setattr(app_module, "_retirement_run_now_body", _body)
    monkeypatch.setattr(app_module, "get_market_state", lambda *_a, **_k: "closed_frozen")
    yield started
    if lock.locked():
        lock.release()


def _nothing_started(started: list) -> None:
    time.sleep(0.2)
    assert started == [], "a refused request started a run"
    assert not app_module._RETIREMENT_RUN_LOCK.locked(), "a refused request took the run lock"


def _records_naming(caplog, level: int, needle: str):
    return [r for r in caplog.records if r.levelno == level and needle in r.getMessage()]


# ---------------------------------------------------------------------------
# Lifetime configuration
# ---------------------------------------------------------------------------


class TestTheSessionSeamsExist:
    @pytest.mark.parametrize("name", ["_session_now_ns", "_PROCESS_STARTED_NS"])
    def test_the_module_exposes_the_seam(self, name):
        assert hasattr(app_module, name), f"app.py must expose {name}"

    def test_the_module_holds_the_cached_floor_as_a_module_level_name(self):
        """Read from source: the suite fixture creates the name when it is absent."""
        tree = ast.parse(_APP_PY.read_text(encoding="utf-8"))
        names = set()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
        assert "_session_revoked_before_ns" in names

    def test_the_clock_seam_returns_integer_nanoseconds_near_the_wall_clock(self):
        assert hasattr(app_module, "_session_now_ns"), "app.py must expose _session_now_ns"
        before = time.time_ns()
        stamp = app_module._session_now_ns()
        assert isinstance(stamp, int) and before <= stamp <= time.time_ns()

    def test_the_process_start_instant_is_an_integer_before_now(self):
        assert hasattr(app_module, "_PROCESS_STARTED_NS"), "app.py must expose _PROCESS_STARTED_NS"
        assert isinstance(app_module._PROCESS_STARTED_NS, int)
        assert time.time_ns() >= app_module._PROCESS_STARTED_NS


class TestLifetimeHours:
    def _lifetime_fn(self):
        assert hasattr(app_module, "_session_lifetime_hours"), (
            "app.py must define _session_lifetime_hours(raw: str | None) -> int"
        )
        return app_module._session_lifetime_hours

    @pytest.mark.parametrize("raw", [None, ""], ids=["absent", "empty"])
    def test_absent_or_empty_is_twelve_hours_and_says_nothing(self, raw, caplog):
        fn = self._lifetime_fn()
        with caplog.at_level(logging.DEBUG, logger="alphabot"):
            assert fn(raw) == 12
        assert [r for r in caplog.records if r.name == "alphabot"] == []

    @pytest.mark.parametrize(
        "raw, hours",
        [("1", 1), ("6", 6), ("05", 5), ("12", 12), ("720", 720)],
    )
    def test_a_whole_number_of_hours_from_one_to_720_is_used(self, raw, hours, caplog):
        fn = self._lifetime_fn()
        with caplog.at_level(logging.DEBUG, logger="alphabot"):
            assert fn(raw) == hours
        assert [r for r in caplog.records if r.name == "alphabot"] == []

    @pytest.mark.parametrize(
        "raw",
        ["abc", "0", "00", "-3", "1.5", "721", "100000", "+5", "1e2", "٣", "5h", "twelve"],
    )
    def test_any_other_value_is_twelve_hours_with_one_warning(self, raw, caplog):
        fn = self._lifetime_fn()
        with caplog.at_level(logging.DEBUG, logger="alphabot"):
            assert fn(raw) == 12
        warnings = [
            r for r in caplog.records if r.name == "alphabot" and r.levelno == logging.WARNING
        ]
        assert len(warnings) == 1, f"{raw!r} logged {len(warnings)} warnings"
        text = warnings[0].getMessage()
        assert _LIFETIME_KEY in text and "12" in text, text

    def test_the_warning_never_prints_the_value_or_the_session_secret(self, caplog, monkeypatch):
        fn = self._lifetime_fn()
        monkeypatch.setenv("SECRET_KEY", _SECRET)
        raw = "secret-looking-value-e41f"
        with caplog.at_level(logging.DEBUG):
            fn(raw)
        assert raw not in caplog.text and _SECRET not in caplog.text

    def test_the_app_lifetime_is_the_function_of_the_environment_value(self):
        import os

        fn = self._lifetime_fn()
        expected = timedelta(hours=fn(os.environ.get(_LIFETIME_KEY)))
        assert app_module.app.permanent_session_lifetime == expected

    def test_the_module_sets_the_lifetime_from_that_key_at_import(self):
        """A module-level assignment reads the key through the function; no other key feeds it."""
        tree = ast.parse(_APP_PY.read_text(encoding="utf-8"))
        found = []
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(t, ast.Attribute) and t.attr == "permanent_session_lifetime"
                for t in node.targets
            ):
                continue
            text = ast.unparse(node.value)
            found.append(text)
        assert found, "app.py never assigns app.permanent_session_lifetime at module level"
        assert any("_session_lifetime_hours" in t and _LIFETIME_KEY in t for t in found), found

    def test_the_key_is_not_settable_from_the_dashboard(self):
        assert _LIFETIME_KEY not in app_module._SETTINGS_WRITE_ALLOWLIST


# ---------------------------------------------------------------------------
# A session ends at its lifetime (AC-2)
# ---------------------------------------------------------------------------


class TestASessionEndsAtItsLifetime:
    def test_a_session_is_served_just_inside_twelve_hours(self, clock):
        client = _signed_in(clock)
        with client.session_transaction() as session:
            assert isinstance(session.get("issued_at_ns"), int), "the login must stamp its instant"
        clock.advance(hours=11, minutes=59)
        _assert_served(client, "T + 11 h 59 min")

    def test_a_session_is_served_at_exactly_the_lifetime_and_refused_one_nanosecond_later(
        self, clock
    ):
        client = _signed_in(clock)
        clock.advance(hours=12)
        _assert_served(client, "exactly at the lifetime")
        clock.now += 1
        _assert_refused(client, "one nanosecond past the lifetime")

    def test_every_kind_of_request_is_refused_after_twelve_hours(self, clock, run_now_spy):
        client = _signed_in(clock)
        clock.advance(hours=12, seconds=1)

        page = client.get("/", follow_redirects=False)
        assert page.status_code == 302 and "/login" in page.headers["Location"]
        _assert_refused(client, "API read")
        post = client.post(_RUN_URL, headers=_XHR)
        assert post.status_code == 401 and post.get_json() == _UNAUTHENTICATED
        _nothing_started(run_now_spy)

    def test_requests_during_the_session_do_not_extend_it(self, clock):
        client = _signed_in(clock)
        for minute in range(1, 12 * 60 + 1):
            clock.advance(minutes=1)
            response = client.get(_ACCOUNTS)
            assert response.status_code == 200, f"minute {minute} answered {response.status_code}"
            assert "Set-Cookie" not in response.headers, (
                f"minute {minute}: the response re-issued the session cookie"
            )
        clock.advance(minutes=1)
        _assert_refused(client, "the first poll after 12 hours of polling")

    def test_two_tabs_on_one_session_are_both_refused_and_neither_extends_it(self, clock):
        first = _signed_in(clock)
        second = _client()
        _copy_session(first, second)
        clock.advance(hours=12, seconds=1)
        for client in (first, second):
            response = client.get(_ACCOUNTS)
            assert response.status_code == 401
            assert "Set-Cookie" not in response.headers

    def test_a_lifetime_of_one_hour_serves_at_59_minutes_and_refuses_after(
        self, clock, monkeypatch
    ):
        monkeypatch.setattr(app_module.app, "permanent_session_lifetime", timedelta(hours=1))
        client = _signed_in(clock)
        clock.advance(minutes=59)
        _assert_served(client, "59 minutes")
        clock.advance(minutes=1, seconds=1)
        _assert_refused(client, "1 h 0 min 1 s")

    def test_a_lifetime_of_720_hours_serves_at_719_hours_and_refuses_after(
        self, clock, monkeypatch
    ):
        monkeypatch.setattr(app_module.app, "permanent_session_lifetime", timedelta(hours=720))
        client = _signed_in(clock)
        clock.advance(hours=719)
        _assert_served(client, "719 hours")
        clock.advance(hours=1, seconds=1)
        _assert_refused(client, "720 h 0 min 1 s")

    def test_the_lifetime_is_read_at_request_time(self, clock, monkeypatch):
        """Shortening the lifetime after the login ends the session at the new bound."""
        client = _signed_in(clock)
        clock.advance(hours=2)
        _assert_served(client, "inside the 12 hour lifetime")
        monkeypatch.setattr(app_module.app, "permanent_session_lifetime", timedelta(hours=1))
        _assert_refused(client, "2 hours old under a 1 hour lifetime")

    def test_the_login_page_gives_the_form_to_an_expired_session(self, clock):
        client = _signed_in(clock)
        assert client.get("/login", follow_redirects=False).status_code == 302
        clock.advance(hours=12, seconds=1)

        response = client.get("/login", follow_redirects=False)

        assert response.status_code == 200, (
            "an expired session must get the form, not the dashboard"
        )
        page = response.get_data(as_text=True)
        assert '<form action="/login" method="post">' in page and 'id="password"' in page

    def test_a_settings_save_with_an_expired_session_writes_nothing(self, clock, gate):
        client = _signed_in(clock)
        before = gate.read_bytes()
        clock.advance(hours=12, seconds=1)

        response = client.post(
            "/api/settings",
            json={"globals": {"EXECUTION_START_TIME": "09:40"}, "symphonies": {}},
            headers=_XHR,
        )

        assert response.status_code == 401 and response.get_json() == _UNAUTHENTICATED
        assert gate.read_bytes() == before

    def test_a_restart_neither_ends_nor_extends_a_session(self, clock, monkeypatch):
        client = _signed_in(clock)
        clock.advance(hours=1)
        monkeypatch.setattr(app_module, "_session_revoked_before_ns", None, raising=False)
        monkeypatch.setattr(app_module, "_PROCESS_STARTED_NS", clock.now, raising=False)
        clock.advance(hours=1)
        _assert_served(client, "T + 2 h after a restart at T + 1 h")
        clock.advance(hours=10, seconds=1)
        _assert_refused(client, "T + 12 h 0 min 1 s: the restart did not extend it")

    def test_a_valid_session_with_authentication_unconfigured_is_refused_not_served(
        self, clock, monkeypatch
    ):
        client = _signed_in(clock)
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)

        api = client.get(_ACCOUNTS, headers=_XHR)
        page = client.get("/", follow_redirects=False)

        assert api.status_code == 503 and api.get_json() == {"error": "misconfigured"}
        assert page.status_code == 302 and "/login" in page.headers["Location"]


class TestASessionWithoutAValidIssueTimeIsRefused:
    @pytest.mark.parametrize(
        "issued",
        ["missing", None, True, False, 0, -1, "digits", "float"],
        ids=["missing", "none", "true", "false", "zero", "negative", "string", "float"],
    )
    def test_a_signed_cookie_with_no_integer_issue_time_is_refused(self, clock, issued):
        client = _client()
        with client.session_transaction() as session:
            session["authenticated"] = True
            if issued == "digits":
                session["issued_at_ns"] = str(clock.now)
            elif issued == "float":
                session["issued_at_ns"] = float(clock.now)
            elif issued != "missing":
                session["issued_at_ns"] = issued
        _assert_refused(client, f"issue time {issued!r}")
        _assert_page_redirected(client, f"issue time {issued!r}")

    def test_a_cookie_issued_before_this_change_is_refused_and_a_new_login_is_served(self, clock):
        client = _client()
        with client.session_transaction() as session:
            session["authenticated"] = True
        _assert_refused(client, "authenticated flag without an issue time")

        _login(client)

        _assert_served(client, "after a new login")

    def test_a_cookie_signed_with_another_key_is_refused(self, clock, monkeypatch):
        client = _signed_in(clock)
        monkeypatch.setattr(app_module.app, "secret_key", "another-key-than-the-one-signing")
        _assert_refused(client, "signature does not verify")


class TestLoginKeepsItsCookieContract:
    def test_the_cookie_is_http_only_same_site_lax_and_secure_when_configured(
        self, gate, monkeypatch
    ):
        monkeypatch.setitem(app_module.app.config, "SESSION_COOKIE_SECURE", True)
        response = _client().post("/login", data={"password": _PASSWORD})
        cookie = response.headers.get("Set-Cookie", "")
        lowered = cookie.lower()
        assert "httponly" in lowered and "samesite=lax" in lowered and "secure" in lowered, cookie

    def test_login_clears_the_old_session_before_stamping_the_new_one(self, clock):
        client = _client()
        with client.session_transaction() as session:
            session["planted_before_login"] = "attacker-value"
            session["issued_at_ns"] = 1
        _login(client)
        with client.session_transaction() as session:
            assert "planted_before_login" not in session
            assert session.get("authenticated") is True
            assert session.get("issued_at_ns") == clock.now
        _assert_served(client, "the new session")

    def test_a_failed_login_issues_no_session(self, gate):
        client = _client()
        response = client.post("/login", data={"password": "not-the-password"})
        assert response.status_code != 302
        _assert_refused(client, "after a failed login")


# ---------------------------------------------------------------------------
# Logout revokes (AC-3)
# ---------------------------------------------------------------------------


class TestLogoutEndsEverySessionIssuedBefore:
    def test_a_copied_cookie_is_refused_after_the_logout(self, clock):
        session_a = _signed_in(clock)
        stolen = _client()
        _copy_session(session_a, stolen)
        _assert_served(stolen, "before the logout")
        clock.advance(seconds=1)

        _assert_logged_in_at_logout_redirect(_post_logout(session_a))

        _assert_refused(stolen, "the copied cookie")
        _assert_page_redirected(stolen, "the copied cookie")
        _assert_refused(session_a, "the signing-out browser")

    def test_a_second_browser_is_ended_too(self, clock):
        session_a = _signed_in(clock)
        clock.advance(seconds=1)
        session_b = _signed_in(clock)
        clock.advance(seconds=1)
        _assert_served(session_b, "before the logout")

        _assert_logged_in_at_logout_redirect(_post_logout(session_a))

        _assert_refused(session_b, "the other browser")

    def test_a_login_made_after_the_logout_is_served(self, clock):
        session_a = _signed_in(clock)
        clock.advance(seconds=1)
        _assert_logged_in_at_logout_redirect(_post_logout(session_a))
        clock.advance(seconds=1)

        session_c = _signed_in(clock)

        _assert_served(session_c, "the later login")

    def test_a_login_in_the_same_clock_tick_as_the_logout_is_served(self, clock):
        session_a = _signed_in(clock)
        copied = _client()
        _copy_session(session_a, copied)
        _assert_logged_in_at_logout_redirect(_post_logout(session_a))

        session_c = _signed_in(clock)

        _assert_served(session_c, "a login in the tick of the logout")
        _assert_refused(copied, "a copy of the session issued in the tick of the logout")

    def test_two_simultaneous_logouts_both_redirect_and_leave_the_later_login_served(self, clock):
        clock.tick = 1_000
        session_a = _signed_in(clock)
        clients = []
        for _ in range(2):
            twin = _client()
            _copy_session(session_a, twin)
            clients.append(twin)
        stamps: list[int] = []
        real_record = database.record_session_revocation

        def _recording(floor_ns):
            stamps.append(floor_ns)
            return real_record(floor_ns)

        responses = []
        barrier = threading.Barrier(2)

        def _logout(twin):
            barrier.wait()
            responses.append(_post_logout(twin))

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(database, "record_session_revocation", _recording, raising=False)
            threads = [threading.Thread(target=_logout, args=(twin,)) for twin in clients]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=20)

        assert len(responses) == 2
        for response in responses:
            _assert_logged_in_at_logout_redirect(response)
        # The twin whose hook runs after the first logout is refused by the hook and records
        # nothing; both record only when both hooks pass first. Either way the stored floor is
        # the latest stamp any logout recorded.
        assert stamps and database.get_session_revocation_floor_ns() == max(stamps), (
            "the stored floor must be the later of the recorded logouts"
        )
        for twin in clients:
            _assert_refused(twin, "a twin of the signed-out session")
        clock.advance(seconds=1)
        _assert_served(_signed_in(clock), "a login after both logouts")

    def test_a_logout_with_no_valid_session_revokes_nothing(self, clock, monkeypatch):
        _require_post_logout()
        expiring = _signed_in(clock)
        clock.advance(hours=6)
        session_b = _signed_in(clock)
        clock.advance(hours=6, seconds=1)
        floor_before = database.get_session_revocation_floor_ns()
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)

        anonymous = _post_logout(_client())
        assert anonymous.status_code == 302 and "/login" in anonymous.headers["Location"]
        lapsed = _post_logout(expiring)
        assert lapsed.status_code == 302 and "/login" in lapsed.headers["Location"]

        assert database.get_session_revocation_floor_ns() == floor_before
        _assert_served(session_b, "the unrelated session")

    def test_a_logout_with_an_already_revoked_session_revokes_nothing_more(
        self, clock, monkeypatch
    ):
        _require_post_logout()
        session_a = _signed_in(clock)
        stale = _client()
        _copy_session(session_a, stale)
        clock.advance(seconds=1)
        _assert_logged_in_at_logout_redirect(_post_logout(session_a))
        clock.advance(seconds=1)
        session_b = _signed_in(clock)
        floor_after_first = database.get_session_revocation_floor_ns()
        clock.advance(seconds=1)
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)

        response = _post_logout(stale)

        assert response.status_code == 302 and "/login" in response.headers["Location"]
        assert database.get_session_revocation_floor_ns() == floor_after_first
        _assert_served(session_b, "the later login")

    def test_a_revoked_session_cannot_start_a_run(self, clock, run_now_spy, monkeypatch):
        session_a = _signed_in(clock)
        stolen = _client()
        _copy_session(session_a, stolen)
        clock.advance(seconds=1)
        _assert_logged_in_at_logout_redirect(_post_logout(session_a))
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)

        response = stolen.post(_RUN_URL, headers=_csrf_headers(**_XHR))

        assert response.status_code == 401 and response.get_json() == _UNAUTHENTICATED
        _nothing_started(run_now_spy)

    def test_a_logout_leaves_the_failed_login_count_alone(self, clock):
        session_a = _signed_in(clock)
        for _ in range(3):
            _client().post("/login", data={"password": "wrong-password-for-throttle"})
        recorded = dict(app_module._AUTH_FAILED_ATTEMPTS)
        assert recorded, "premise: three failed logins were recorded"
        clock.advance(seconds=1)

        _assert_logged_in_at_logout_redirect(_post_logout(session_a))

        assert dict(app_module._AUTH_FAILED_ATTEMPTS) == recorded

    def test_a_request_in_flight_when_a_logout_completes_finishes_and_the_next_is_refused(
        self, clock, monkeypatch
    ):
        session_a = _signed_in(clock)
        other_tab = _client()
        _copy_session(session_a, other_tab)
        clock.advance(seconds=1)
        calls: list[int] = []

        def _logout_from_the_other_tab_then_read(*_a, **_k):
            if not calls:
                calls.append(1)
                _assert_logged_in_at_logout_redirect(_post_logout(other_tab))
            return {}

        monkeypatch.setattr(
            app_module._dotenv_module, "dotenv_values", _logout_from_the_other_tab_then_read
        )

        in_flight = session_a.get(_ACCOUNTS)

        assert calls, "premise: the logout ran while the request was being served"
        assert in_flight.status_code == 200 and in_flight.get_json() == {"accounts": []}
        _assert_refused(session_a, "the next request")


class TestTheRevocationSurvivesARestart:
    def _restart(self, clock, monkeypatch) -> None:
        monkeypatch.setattr(app_module, "_session_revoked_before_ns", None, raising=False)
        monkeypatch.setattr(app_module, "_PROCESS_STARTED_NS", clock.now, raising=False)

    def test_the_logout_stores_its_own_instant(self, clock):
        session_a = _signed_in(clock)
        clock.advance(seconds=5)
        stamp = clock.now

        _assert_logged_in_at_logout_redirect(_post_logout(session_a))

        assert database.get_session_revocation_floor_ns() == stamp

    def test_a_revoked_cookie_stays_refused_and_a_live_one_stays_served(self, clock, monkeypatch):
        session_a = _signed_in(clock)
        stolen = _client()
        _copy_session(session_a, stolen)
        clock.advance(seconds=1)
        _assert_logged_in_at_logout_redirect(_post_logout(session_a))
        clock.advance(seconds=1)
        session_c = _signed_in(clock)
        clock.advance(minutes=1)

        self._restart(clock, monkeypatch)
        clock.advance(seconds=1)

        _assert_refused(stolen, "the revoked cookie after the restart")
        _assert_served(session_c, "the session issued after the logout")

    def test_the_floor_is_read_from_the_database_once_per_process(self, clock, monkeypatch):
        session_a = _signed_in(clock)
        self._restart(clock, monkeypatch)
        reads: list[int] = []
        real_read = database.get_session_revocation_floor_ns

        def _counting():
            reads.append(1)
            return real_read()

        monkeypatch.setattr(database, "get_session_revocation_floor_ns", _counting, raising=False)

        for _ in range(5):
            _assert_served(session_a, "a poll")

        assert len(reads) <= 1, f"{len(reads)} database reads for 5 authenticated requests"

    def test_an_unreadable_record_at_start_refuses_every_earlier_session_and_serves_a_new_login(
        self, clock, monkeypatch, caplog
    ):
        session_a = _signed_in(clock)
        clock.advance(minutes=1)
        monkeypatch.setattr(app_module, "_session_revoked_before_ns", None, raising=False)
        monkeypatch.setattr(app_module, "_PROCESS_STARTED_NS", clock.now, raising=False)

        def _unreadable():
            raise sqlite3.OperationalError(_SENTINEL_DETAIL)

        monkeypatch.setattr(database, "get_session_revocation_floor_ns", _unreadable, raising=False)
        clock.advance(minutes=1)

        with caplog.at_level(logging.DEBUG):
            _assert_refused(session_a, "a session issued before the start")
            new_session = _signed_in(clock)
            _assert_served(new_session, "a login after the start")

        named = _records_naming(caplog, logging.ERROR, "OperationalError")
        assert len(named) == 1, f"{len(named)} ERROR lines name the exception type"
        assert _SENTINEL_DETAIL not in caplog.text, "the log carried the exception's detail"
        assert all(r.exc_info is None for r in named), "the ERROR line must not carry a traceback"


class TestAnUnwritableRevocation:
    @pytest.fixture
    def failing_write(self, monkeypatch):
        def _raise(_floor_ns):
            raise sqlite3.OperationalError(_SENTINEL_DETAIL)

        monkeypatch.setattr(database, "record_session_revocation", _raise, raising=False)

    def test_every_earlier_session_stays_refused_while_the_process_runs(
        self, clock, failing_write, caplog
    ):
        session_a = _signed_in(clock)
        clock.advance(seconds=1)
        session_b = _signed_in(clock)
        clock.advance(seconds=1)

        with caplog.at_level(logging.DEBUG):
            response = _post_logout(session_a)

        _assert_logged_in_at_logout_redirect(response)
        _assert_refused(session_a, "the signing-out session")
        _assert_refused(session_b, "the other earlier session")
        named = _records_naming(caplog, logging.ERROR, "OperationalError")
        assert len(named) == 1, f"{len(named)} ERROR lines name the exception type"
        assert _SENTINEL_DETAIL not in caplog.text
        assert all(r.exc_info is None for r in named)
        clock.advance(seconds=1)
        _assert_served(_signed_in(clock), "a login after the failed write")

    def test_the_login_page_says_other_sessions_end_when_the_daemon_restarts(
        self, clock, failing_write
    ):
        session_a = _signed_in(clock)
        clock.advance(seconds=1)

        _assert_logged_in_at_logout_redirect(_post_logout(session_a))

        page = session_a.get("/login")
        assert page.status_code == 200
        assert _NOT_DURABLE_LINE in page.get_data(as_text=True)
        assert _NOT_DURABLE_LINE not in _client().get("/login").get_data(as_text=True), (
            "another browser must not be told about this logout"
        )

    def test_following_the_logout_redirect_lands_on_the_login_page_with_the_line(
        self, clock, failing_write
    ):
        session_a = _signed_in(clock)
        confirm = session_a.get("/logout")
        assert confirm.status_code == 200, "premise: GET /logout is the confirm page"
        clock.advance(seconds=1)

        landed = _post_logout(session_a, follow_redirects=True)

        assert landed.status_code == 200
        page = landed.get_data(as_text=True)
        assert _NOT_DURABLE_LINE in page and 'id="password"' in page

    def test_the_line_goes_with_the_next_successful_login(self, clock, monkeypatch):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                database,
                "record_session_revocation",
                lambda _floor: (_ for _ in ()).throw(sqlite3.OperationalError("x")),
                raising=False,
            )
            session_a = _signed_in(clock)
            clock.advance(seconds=1)
            _post_logout(session_a)
        assert _NOT_DURABLE_LINE in session_a.get("/login").get_data(as_text=True)
        clock.advance(seconds=1)
        _login(session_a)
        clock.advance(seconds=1)

        _assert_logged_in_at_logout_redirect(_post_logout(session_a))

        assert _NOT_DURABLE_LINE not in session_a.get("/login").get_data(as_text=True)

    def test_a_durable_logout_shows_no_such_line(self, clock):
        session_a = _signed_in(clock)
        clock.advance(seconds=1)
        _assert_logged_in_at_logout_redirect(_post_logout(session_a))
        assert _NOT_DURABLE_LINE not in session_a.get("/login").get_data(as_text=True)


# ---------------------------------------------------------------------------
# The logout POST is CSRF-checked and reads nothing (AC-5)
# ---------------------------------------------------------------------------


class TestThePostIsRefusedUnlessItCarriesTheHeaderToken:
    @pytest.fixture
    def signed_in(self, clock, monkeypatch):
        client = _signed_in(clock)
        clock.advance(seconds=1)
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
        self.client = client
        return client

    def _nothing_revoked(self, client) -> None:
        _require_post_logout()
        assert database.get_session_revocation_floor_ns() == 0, "a refused logout revoked"
        _assert_served(client, "after a refused logout")

    def test_no_header_token_is_a_403(self, signed_in):
        response = signed_in.post("/logout")
        assert response.status_code == 403
        self._nothing_revoked(signed_in)

    @pytest.mark.parametrize("site", ["same-site", "cross-site"])
    def test_a_neighbouring_or_foreign_site_is_a_403_even_with_the_token(self, signed_in, site):
        response = _post_logout(signed_in, headers=_csrf_headers(**{"Sec-Fetch-Site": site}))
        assert response.status_code == 403
        self._nothing_revoked(signed_in)

    @pytest.mark.parametrize("token", ["0" * 64, "tökén", "", " "])
    def test_a_wrong_or_non_ascii_header_token_is_a_403_never_a_500(
        self, signed_in, monkeypatch, token
    ):
        monkeypatch.setitem(app_module.app.config, "PROPAGATE_EXCEPTIONS", False)
        response = signed_in.post("/logout", headers={"X-CSRF-Token": token})
        assert response.status_code == 403
        self._nothing_revoked(signed_in)

    @pytest.mark.parametrize("encoding", ["urlencoded", "multipart"])
    def test_the_token_in_a_form_field_is_a_403(self, signed_in, encoding):
        kwargs = {} if encoding == "urlencoded" else {"content_type": "multipart/form-data"}
        response = signed_in.post("/logout", data={"csrf_token": app_module._CSRF_TOKEN}, **kwargs)
        assert response.status_code == 403
        self._nothing_revoked(signed_in)

    @pytest.mark.parametrize(
        "headers", [{"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}, {}]
    )
    def test_a_same_origin_a_user_initiated_and_a_script_post_with_the_token_sign_out(
        self, signed_in, headers
    ):
        response = _post_logout(signed_in, headers=_csrf_headers(**headers))
        _assert_logged_in_at_logout_redirect(response)
        _assert_refused(signed_in, "after the sign out")

    def test_a_70_kib_form_body_is_never_read_so_it_signs_out_and_is_not_a_413(self, signed_in):
        body = {"filler": "A" * (70 * 1024)}
        response = signed_in.post("/logout", data=body, headers=_csrf_headers())
        _assert_logged_in_at_logout_redirect(response)
        _assert_refused(signed_in, "after the sign out")

    def test_a_get_with_the_foreign_site_headers_signs_nobody_out(self, signed_in):
        response = signed_in.get(
            "/logout", headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Dest": "image"}
        )
        assert response.status_code == 200, (
            "a GET must answer the confirm page, not end the session"
        )
        self._nothing_revoked(signed_in)


class TestAConfirmPageRequestWithoutAValidSessionIsSentToLogin:
    def test_no_cookie_an_expired_cookie_and_a_revoked_cookie_are_redirected(self, clock):
        anonymous = _client().get("/logout", follow_redirects=False)
        assert anonymous.status_code == 302 and "/login" in anonymous.headers["Location"]

        expired = _signed_in(clock)
        clock.advance(hours=12, seconds=1)
        response = expired.get("/logout", follow_redirects=False)
        assert response.status_code == 302 and "/login" in response.headers["Location"]

        clock.advance(seconds=1)
        owner = _signed_in(clock)
        stale = _client()
        _copy_session(owner, stale)
        clock.advance(seconds=1)
        _assert_logged_in_at_logout_redirect(_post_logout(owner))
        response = stale.get("/logout", follow_redirects=False)
        assert response.status_code == 302 and "/login" in response.headers["Location"]


# ---------------------------------------------------------------------------
# HEAD and OPTIONS never act as the write method (sign-out and sign-in)
# ---------------------------------------------------------------------------
#
# Werkzeug adds HEAD to every rule that has GET and routes it to the same view, while the CSRF hook
# checks only POST. A view that branches on ``== "GET"`` therefore runs its write branch for a HEAD
# that carries no token, which any script-sent request can make.

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_PEER = {"REMOTE_ADDR": "198.51.100.9"}
_FOREIGN_SITE = {"Sec-Fetch-Site": "same-site"}


def _session_set_cookies(response) -> list[str]:
    name = app_module.app.config["SESSION_COOKIE_NAME"]
    return [c for c in response.headers.getlist("Set-Cookie") if c.startswith(name + "=")]


def _revocation_state() -> tuple[int, int]:
    return database.get_session_revocation_floor_ns(), app_module._revocation_floor_ns()


class TestHeadAndOptionsOnLogoutEndNoSession:
    @pytest.mark.parametrize(
        "with_token, headers",
        [(False, _FOREIGN_SITE), (False, {}), (True, {})],
        ids=["sibling-site no token", "no headers", "with the process token"],
    )
    def test_an_authenticated_head_revokes_clears_and_changes_nothing(
        self, clock, monkeypatch, with_token, headers
    ):
        session_a = _signed_in(clock)
        clock.advance(seconds=1)
        session_b = _signed_in(clock)
        clock.advance(seconds=1)
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
        if with_token:
            headers = _csrf_headers()
        state_before = _revocation_state()
        get_status = session_a.get("/logout").status_code

        response = session_a.head("/logout", headers=headers)

        assert response.status_code == get_status, (
            f"HEAD /logout answered {response.status_code}; HEAD is GET without a body ({get_status})"
        )
        assert _revocation_state() == state_before, "a HEAD moved the revocation floor"
        assert _session_set_cookies(response) == [], "a HEAD changed the session cookie"
        _assert_served(session_a, "the session that sent the HEAD")
        _assert_served(session_b, "another browser's session")

    @pytest.mark.parametrize("method", ["OPTIONS", "PUT", "PATCH", "DELETE"])
    def test_every_other_non_post_method_revokes_nothing(self, clock, monkeypatch, method):
        session_a = _signed_in(clock)
        clock.advance(seconds=1)
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
        state_before = _revocation_state()

        session_a.open("/logout", method=method, headers=_FOREIGN_SITE)

        assert _revocation_state() == state_before, f"{method} moved the revocation floor"
        _assert_served(session_a, f"after {method} /logout")

    def test_a_head_with_no_valid_session_is_sent_to_login_and_revokes_nothing(self, clock):
        owner = _signed_in(clock)
        clock.advance(seconds=1)
        state_before = _revocation_state()

        response = _client().head("/logout")

        assert response.status_code == 302 and "/login" in response.headers["Location"]
        assert _revocation_state() == state_before
        _assert_served(owner, "the unrelated session")

    def test_a_head_repeated_on_a_timer_keeps_the_operator_signed_in(self, clock, monkeypatch):
        """The abuse the fix closes: a page that keeps sending HEAD must not hold the operator out."""
        operator = _signed_in(clock)
        clock.advance(seconds=1)
        other_browser = _signed_in(clock)
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
        for _ in range(5):
            clock.advance(seconds=1)
            operator.head("/logout", headers=_FOREIGN_SITE)
        _assert_served(operator, "the original session after five HEADs")
        _assert_served(other_browser, "the other browser after five HEADs")


class TestHeadAndOptionsOnLoginCountNoAttempt:
    @pytest.fixture
    def peer_login(self, gate, clock):
        app_module._AUTH_FAILED_ATTEMPTS.clear()
        return _client()

    @pytest.mark.parametrize(
        "via_proxy", [False, True], ids=["peer address", "forwarded address (TRUST_PROXY)"]
    )
    def test_head_never_runs_the_password_branch_or_locks_the_peer_out(
        self, peer_login, monkeypatch, via_proxy
    ):
        headers = {}
        if via_proxy:
            monkeypatch.setenv("TRUST_PROXY", "1")
            headers = {"X-Forwarded-For": "203.0.113.7"}
        else:
            monkeypatch.delenv("TRUST_PROXY", raising=False)
        get_status = peer_login.get("/login", environ_base=_PEER, headers=headers).status_code

        for _ in range(app_module._AUTH_MAX_ATTEMPTS + 2):
            response = peer_login.head("/login", environ_base=_PEER, headers=headers)
            assert response.status_code == get_status, "HEAD /login must answer as GET does"

        assert app_module._AUTH_FAILED_ATTEMPTS == {}, "a HEAD was counted as a failed login"
        signed_in = peer_login.post(
            "/login", data={"password": _PASSWORD}, environ_base=_PEER, headers=headers
        )
        assert signed_in.status_code == 302, (
            f"the correct password answered {signed_in.status_code}: the peer was locked out by HEADs"
        )

    def test_head_with_a_signed_in_session_changes_nothing(self, clock):
        app_module._AUTH_FAILED_ATTEMPTS.clear()
        operator = _signed_in(clock)
        clock.advance(seconds=1)
        get_status = operator.get("/login", environ_base=_PEER).status_code

        response = operator.head("/login", environ_base=_PEER)

        assert response.status_code == get_status
        assert _session_set_cookies(response) == [], "a HEAD changed the session cookie"
        assert app_module._AUTH_FAILED_ATTEMPTS == {}
        _assert_served(operator, "after HEAD /login")

    def test_options_counts_no_attempt(self, peer_login):
        for _ in range(app_module._AUTH_MAX_ATTEMPTS + 2):
            peer_login.open("/login", method="OPTIONS", environ_base=_PEER)
        assert app_module._AUTH_FAILED_ATTEMPTS == {}


def _dual_method_paths() -> list[str]:
    methods: dict[str, set[str]] = {}
    for rule in app_module.app.url_map.iter_rules():
        methods.setdefault(rule.rule, set()).update(rule.methods)
    return sorted(p for p, m in methods.items() if "GET" in m and m & _WRITE_METHODS)


def _dual_method_endpoints() -> list[str]:
    methods: dict[str, set[str]] = {}
    for rule in app_module.app.url_map.iter_rules():
        methods.setdefault(rule.endpoint, set()).update(rule.methods)
    return sorted(e for e, m in methods.items() if "GET" in m and m & _WRITE_METHODS)


def _concrete(path: str) -> str:
    return re.sub(
        r"<(?:int:)?[^>]+>", lambda m: "1" if m.group(0).startswith("<int:") else "x", path
    )


def _branches_on_get(view) -> bool:
    tree = ast.parse(textwrap.dedent(inspect.getsource(inspect.unwrap(view))))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        is_method = any(ast.unparse(o) == "request.method" for o in operands)
        names_get = any(isinstance(o, ast.Constant) and o.value == "GET" for o in operands)
        if is_method and names_get and any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
            return True
    return False


def _state_database_dump() -> str:
    connection = sqlite3.connect(database._db_file())
    try:
        return "\n".join(connection.iterdump())
    finally:
        connection.close()


_PROBE_HASH = "SweepProbeHash0000"
_PROBE_NAME = "Sweep Probe Alpha"


def _strategy_rows() -> list[str]:
    connection = sqlite3.connect(database._db_file())
    try:
        return [
            r[0]
            for r in connection.execute(
                "SELECT symphony_name FROM symphony_strategies ORDER BY symphony_name"
            )
        ]
    finally:
        connection.close()


class TestNoRouteTreatsHeadOrOptionsAsAWrite:
    def test_the_sweep_finds_the_sign_in_and_sign_out_routes(self):
        """Non-vacuity: the sweep below runs over a set that contains the two known routes."""
        assert {"/login", "/logout"} <= set(_dual_method_paths())
        assert {"login", "logout"} <= set(_dual_method_endpoints())

    @pytest.mark.parametrize("endpoint", _dual_method_endpoints())
    def test_a_view_that_serves_get_and_a_write_method_never_branches_on_get(self, endpoint):
        """HEAD runs the GET rule's view, so ``== "GET"`` sends a HEAD into the write branch."""
        view = app_module.app.view_functions[endpoint]
        assert not _branches_on_get(view), (
            f"{endpoint} compares request.method to 'GET': branch on the write method instead"
        )

    @pytest.mark.parametrize("method", ["HEAD", "OPTIONS"])
    @pytest.mark.parametrize("path", _dual_method_paths())
    def test_an_authenticated_head_or_options_mutates_nothing(
        self, clock, gate, monkeypatch, path, method
    ):
        operator = _signed_in(clock)
        clock.advance(seconds=1)
        database.save_state({_PROBE_HASH: {"name": _PROBE_NAME, "account": "ACC-1"}})
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
        app_module._AUTH_FAILED_ATTEMPTS.clear()
        env_before = gate.read_bytes()
        db_before = _state_database_dump()
        revocation_before = _revocation_state()

        operator.open(_concrete(path), method=method, headers=_FOREIGN_SITE, environ_base=_PEER)

        assert gate.read_bytes() == env_before, f"{method} {path} wrote the .env file"
        assert _state_database_dump() == db_before, f"{method} {path} wrote the state database"
        assert _revocation_state() == revocation_before, f"{method} {path} moved the floor"
        assert app_module._AUTH_FAILED_ATTEMPTS == {}, f"{method} {path} counted a failed login"
        _assert_served(operator, f"after {method} {path}")


class TestReadingSymphonySettingsCreatesNoRow:
    """A GET (and so a HEAD) of a settings route must not write a default strategy row.

    The engine's own read creates the row on first use; a dashboard read must not, because the
    path segment names any symphony the caller likes and each name would add a row.
    """

    @pytest.fixture
    def operator(self, clock, monkeypatch):
        client = _signed_in(clock)
        clock.advance(seconds=1)
        database.save_state({_PROBE_HASH: {"name": _PROBE_NAME, "account": "ACC-1"}})
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
        return client

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    @pytest.mark.parametrize(
        "segment", ["no-such-symphony", _PROBE_HASH.lower()], ids=["unknown name", "live symphony"]
    )
    def test_the_symphony_settings_read_adds_no_row(self, operator, segment, method):
        rows_before = _strategy_rows()

        response = operator.open(f"/api/symphony-settings/{segment}", method=method)

        assert response.status_code == 200
        assert _strategy_rows() == rows_before, f"{method} created a strategy row for {segment}"

    def test_an_unseen_symphony_is_still_answered_with_the_defaults(self, operator):
        body = operator.get("/api/symphony-settings/no-such-symphony").get_json()

        assert body["parameters"] == database.DEFAULT_STRATEGY
        assert body["locked_vars"] == database.DEFAULT_LOCKED_VARS
        assert body["live_mode"] is False

    def test_a_stored_strategy_is_still_answered_as_stored(self, operator):
        stored = {**database.DEFAULT_STRATEGY, "TRIGGER_THRESHOLD_PCT": 11.0}
        database.save_symphony_strategy(_PROBE_NAME, stored, ["VWAP_BLEED_TICKS"])

        body = operator.get(
            f"/api/symphony-settings/{database.normalize_name(_PROBE_NAME)}"
        ).get_json()

        assert body["parameters"] == stored
        assert body["locked_vars"] == ["VWAP_BLEED_TICKS"]

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_the_settings_list_read_adds_no_row_for_a_live_symphony_without_one(
        self, operator, method
    ):
        rows_before = _strategy_rows()

        response = operator.open("/api/settings", method=method)

        assert response.status_code == 200
        assert _strategy_rows() == rows_before, f"{method} /api/settings created a strategy row"

    def test_the_settings_list_still_answers_the_defaults_for_a_live_symphony(self, operator):
        body = operator.get("/api/settings").get_json()

        entry = body["symphonies"].get(database.normalize_name(_PROBE_NAME))
        assert entry is not None, "the live symphony must be listed"
        assert entry["params"] == database.DEFAULT_STRATEGY
        assert entry["locked_vars"] == database.DEFAULT_LOCKED_VARS
        assert entry["live_mode"] is False


# ---------------------------------------------------------------------------
# A logout ends every earlier session, whatever the clock did (AC-3)
# ---------------------------------------------------------------------------


class TestALogoutEndsASessionStampedPastTheFloor:
    def test_a_login_in_the_same_tick_as_a_logout_is_ended_by_the_next_logout(self, clock):
        first = _signed_in(clock)
        _assert_logged_in_at_logout_redirect(_post_logout(first))
        second = _signed_in(clock)
        twin = _client()
        _copy_session(second, twin)
        _assert_served(twin, "the same-tick login before its logout")
        floor_before = database.get_session_revocation_floor_ns()

        _assert_logged_in_at_logout_redirect(_post_logout(second))

        _assert_refused(twin, "a copy of the session stamped one past the floor")
        assert database.get_session_revocation_floor_ns() >= max(clock.now, floor_before + 1)
        _assert_served(_signed_in(clock), "a login after the second logout")

    def test_logouts_on_a_stopped_clock_store_a_strictly_higher_floor_each_time(self, clock):
        floors = []
        for _ in range(3):
            session = _signed_in(clock)
            _assert_logged_in_at_logout_redirect(_post_logout(session))
            floors.append(database.get_session_revocation_floor_ns())
        assert floors[0] < floors[1] < floors[2], floors

    def test_a_login_after_the_clock_stepped_back_is_ended_by_the_next_logout(self, clock):
        first = _signed_in(clock)
        clock.advance(hours=1)
        _assert_logged_in_at_logout_redirect(_post_logout(first))
        floor_before = database.get_session_revocation_floor_ns()
        clock.advance(hours=-1)
        second = _signed_in(clock)
        twin = _client()
        _copy_session(second, twin)
        _assert_served(twin, "the login made after the step back")
        clock.advance(seconds=1)

        _assert_logged_in_at_logout_redirect(_post_logout(second))

        _assert_refused(twin, "a copy of the session issued after the backward step")
        _assert_refused(second, "the signing-out browser")
        assert database.get_session_revocation_floor_ns() >= floor_before + 1

    def test_that_logout_still_ends_the_session_after_a_restart(self, clock, monkeypatch):
        first = _signed_in(clock)
        clock.advance(hours=1)
        _assert_logged_in_at_logout_redirect(_post_logout(first))
        clock.advance(hours=-1)
        second = _signed_in(clock)
        twin = _client()
        _copy_session(second, twin)
        clock.advance(seconds=1)
        _assert_logged_in_at_logout_redirect(_post_logout(second))
        monkeypatch.setattr(app_module, "_session_revoked_before_ns", None, raising=False)
        monkeypatch.setattr(app_module, "_PROCESS_STARTED_NS", clock.now, raising=False)

        _assert_refused(twin, "the copy, after the daemon restarted")
        clock.advance(seconds=1)
        _assert_served(_signed_in(clock), "a login after the restart")

    def test_a_login_made_after_both_logouts_is_served(self, clock):
        first = _signed_in(clock)
        clock.advance(hours=1)
        _post_logout(first)
        clock.advance(hours=-1)
        second = _signed_in(clock)
        _post_logout(second)
        _assert_served(_signed_in(clock), "a login after both logouts")


# ---------------------------------------------------------------------------
# An issue time that is not a sane instant is refused, and a login made after a backward clock step
# keeps its real lifetime
# ---------------------------------------------------------------------------

_HOUR_NS = 3600 * _NS
_DAY_NS = 24 * _HOUR_NS

# Each value is built from the clock's present reading at the moment the cookie is forged.
_UNSANE_ISSUE_TIMES = [
    pytest.param(lambda now: str(now), id="the-instant-as-a-string"),
    pytest.param(lambda now: float(now), id="the-instant-as-a-float"),
    pytest.param(lambda now: True, id="true"),
    pytest.param(lambda now: -1, id="negative-one"),
    pytest.param(lambda now: None, id="null"),
    pytest.param(lambda now: [now], id="a-list"),
    pytest.param(lambda now: {"issued": now}, id="an-object"),
    pytest.param(lambda now: 10**399 + 7, id="a-400-digit-integer"),
    pytest.param(lambda now: float("inf"), id="infinity"),
    pytest.param(lambda now: float("nan"), id="not-a-number"),
    pytest.param(lambda now: now + _HOUR_NS, id="an-hour-ahead-of-now"),
    pytest.param(lambda now: now + _DAY_NS, id="a-day-ahead-of-now"),
    pytest.param(lambda now: now + 3650 * _DAY_NS, id="ten-years-ahead-of-now"),
]


def _forge_issue_time(client, value) -> None:
    """Replace only the issue field of a cookie a real login set; the app's own serializer re-signs it."""
    with client.session_transaction() as session:
        session["issued_at_ns"] = value


class TestAnIssueTimeThatIsNotASaneInstantIsRefused:
    def test_the_unforged_cookie_of_the_same_login_is_served(self, clock):
        client = _signed_in(clock)
        _assert_served(client, "the real login, before any change to its issue field")
        assert client.get("/", follow_redirects=False).status_code == 200

    @pytest.mark.parametrize("make_value", _UNSANE_ISSUE_TIMES)
    def test_an_api_request_gets_401_json_never_200_or_500(self, clock, make_value):
        client = _signed_in(clock)
        _forge_issue_time(client, make_value(clock.now))

        response = client.get(_ACCOUNTS, headers=_XHR)

        assert response.status_code == 401, f"answered {response.status_code}, not 401"
        assert response.get_json() == _UNAUTHENTICATED

    @pytest.mark.parametrize("make_value", _UNSANE_ISSUE_TIMES)
    def test_a_page_request_is_sent_to_login_never_served_or_500(self, clock, make_value):
        client = _signed_in(clock)
        _forge_issue_time(client, make_value(clock.now))

        _assert_page_redirected(client, "the forged issue time")

    @pytest.mark.parametrize("make_value", _UNSANE_ISSUE_TIMES)
    def test_a_refused_forged_session_starts_no_run(self, clock, make_value, run_now_spy):
        client = _signed_in(clock)
        _forge_issue_time(client, make_value(clock.now))

        response = client.post(_RUN_URL, headers=_csrf_headers())

        assert response.status_code in (401, 403), f"answered {response.status_code}"
        _nothing_started(run_now_spy)

    @pytest.mark.parametrize("make_value", _UNSANE_ISSUE_TIMES[-3:])
    def test_a_later_genuine_login_is_served_after_a_forged_future_session_was_refused(
        self, clock, make_value
    ):
        client = _signed_in(clock)
        _forge_issue_time(client, make_value(clock.now))
        _assert_refused(client, "the forged session")

        _assert_served(_signed_in(clock), "a genuine login after the refusal")


_BACKWARD_STEPS = [
    pytest.param({"minutes": -1}, id="one-minute-back"),
    pytest.param({"hours": -3}, id="three-hours-back"),
    pytest.param({"days": -2}, id="two-days-back"),
]


def _login_after_a_backward_step(clock, step: dict):
    """A logout at T, the clock stepped back, then a login: returns (browser, login instant)."""
    first = _signed_in(clock)
    _assert_logged_in_at_logout_redirect(_post_logout(first))
    clock.advance(**step)
    second = _signed_in(clock)
    return second, clock.now


class TestALoginAfterABackwardClockStepKeepsItsRealLifetime:
    @pytest.mark.parametrize("step", _BACKWARD_STEPS)
    def test_the_login_is_served_at_once(self, clock, step):
        browser, _ = _login_after_a_backward_step(clock, step)

        _assert_served(browser, "the login made after the step back")
        assert browser.get("/", follow_redirects=False).status_code == 200

    @pytest.mark.parametrize("step", _BACKWARD_STEPS)
    def test_it_is_served_one_second_inside_twelve_hours_after_the_real_login(self, clock, step):
        browser, login_at = _login_after_a_backward_step(clock, step)
        clock.advance(hours=12, seconds=-1)

        assert clock.now - login_at == 12 * _HOUR_NS - _NS
        _assert_served(browser, "one second inside twelve hours of the real login")

    @pytest.mark.parametrize("step", _BACKWARD_STEPS)
    def test_it_is_refused_one_second_past_twelve_hours_after_the_real_login(self, clock, step):
        browser, login_at = _login_after_a_backward_step(clock, step)
        clock.advance(hours=12, seconds=1)

        assert clock.now - login_at == 12 * _HOUR_NS + _NS
        _assert_refused(browser, "one second past twelve hours of the real login")
        _assert_page_redirected(browser, "one second past twelve hours of the real login")

    @pytest.mark.parametrize("step", _BACKWARD_STEPS)
    def test_a_copied_cookie_ends_with_the_original_and_requests_never_extend_it(self, clock, step):
        browser, login_at = _login_after_a_backward_step(clock, step)
        twin = _client()
        _copy_session(browser, twin)
        for _ in range(4):
            clock.advance(hours=3)
            browser.get(_ACCOUNTS)
            twin.get(_ACCOUNTS)
        clock.advance(seconds=1)

        assert clock.now - login_at == 12 * _HOUR_NS + _NS
        _assert_refused(browser, "the original, past twelve hours")
        _assert_refused(twin, "the copy, past twelve hours")

    @pytest.mark.parametrize("step", _BACKWARD_STEPS)
    def test_a_logout_still_ends_it_and_a_later_login_is_served(self, clock, step):
        browser, _ = _login_after_a_backward_step(clock, step)
        twin = _client()
        _copy_session(browser, twin)
        clock.advance(seconds=1)
        floor_before = database.get_session_revocation_floor_ns()

        _assert_logged_in_at_logout_redirect(_post_logout(browser))

        _assert_refused(twin, "a copy of the session issued after the backward step")
        _assert_refused(browser, "the signing-out browser")
        assert database.get_session_revocation_floor_ns() >= floor_before + 1
        _assert_served(_signed_in(clock), "a login after that logout")

    @pytest.mark.parametrize("step", _BACKWARD_STEPS)
    def test_the_revocation_still_holds_after_a_restart(self, clock, step, monkeypatch):
        browser, _ = _login_after_a_backward_step(clock, step)
        twin = _client()
        _copy_session(browser, twin)
        clock.advance(seconds=1)
        _assert_logged_in_at_logout_redirect(_post_logout(browser))
        monkeypatch.setattr(app_module, "_session_revoked_before_ns", None, raising=False)
        monkeypatch.setattr(app_module, "_PROCESS_STARTED_NS", clock.now, raising=False)

        _assert_refused(twin, "the copy, after the daemon restarted")


class TestAStampFarBeyondTheFloorAfterABackwardStepIsRefused:
    """The stamp a login writes after a step back (one past the floor) is served; a forged stamp
    far beyond it is not. Together with the future-stamp rows above this fixes the rule: a stamp
    may exceed now only up to max(now, floor + 1) plus a small tolerance."""

    @pytest.mark.parametrize(
        "extra_ns",
        [_DAY_NS, 30 * _DAY_NS, 3650 * _DAY_NS, 10**399],
        ids=["a-day-past", "thirty-days-past", "ten-years-past", "400-digit-past"],
    )
    @pytest.mark.parametrize("step", _BACKWARD_STEPS)
    def test_a_stamp_far_beyond_the_floor_plus_one_is_refused(self, clock, step, extra_ns):
        browser, _ = _login_after_a_backward_step(clock, step)
        _assert_served(browser, "the genuine login, before the forgery")
        floor = database.get_session_revocation_floor_ns()
        assert floor > clock.now, "the step back must leave the floor ahead of the clock"

        _forge_issue_time(browser, floor + 1 + extra_ns)

        _assert_refused(browser, "a stamp far beyond the floor plus one")
        _assert_page_redirected(browser, "a stamp far beyond the floor plus one")

    @pytest.mark.parametrize("step", _BACKWARD_STEPS)
    def test_the_stamp_login_wrote_is_served_and_the_floor_itself_is_not(self, clock, step):
        browser, _ = _login_after_a_backward_step(clock, step)
        floor = database.get_session_revocation_floor_ns()

        _forge_issue_time(browser, floor + 1)
        _assert_served(browser, "a stamp of exactly floor plus one")

        _forge_issue_time(browser, floor)
        _assert_refused(browser, "a stamp equal to the floor")


# ---------------------------------------------------------------------------
# An invalid lifetime key warns into the daemon log file (AC-2)
# ---------------------------------------------------------------------------

_IMPORT_DRIVER = r"""
import json, logging, os, sys
repo, log_path = sys.argv[1], sys.argv[2]
sys.path.insert(0, repo)
import dotenv
dotenv.load_dotenv = lambda *a, **k: False
dotenv.dotenv_values = lambda *a, **k: {}
_real_handler = logging.FileHandler
class _TempFileHandler(_real_handler):
    def __init__(self, filename, *a, **k):
        super().__init__(log_path, *a, **k)
logging.FileHandler = _TempFileHandler
import app
for handler in logging.getLogger("alphabot").handlers:
    handler.flush()
sys.stdout.write("__HOURS__" + json.dumps(app.app.permanent_session_lifetime.total_seconds() / 3600))
"""


def _import_app_in_a_fresh_process(tmp_path, lifetime_value: str | None):
    """Import app in a new process with the daemon log redirected; return (hours, log text)."""
    log_path = tmp_path / "daemon.log"
    env = {k: v for k, v in os.environ.items() if k != _LIFETIME_KEY}
    env.update(
        DB_PATH=str(tmp_path / "state.db"),
        SECRET_KEY=_SECRET,
        DASHBOARD_PASSWORD=_PASSWORD,
        PYTHONIOENCODING="utf-8",
    )
    if lifetime_value is not None:
        env[_LIFETIME_KEY] = lifetime_value
    repo = str(_APP_PY.parent)
    proc = subprocess.run(
        [sys.executable, "-I", "-c", _IMPORT_DRIVER, repo, str(log_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
        cwd=repo,
        env=env,
    )
    marker = proc.stdout.rfind("__HOURS__")
    assert marker >= 0, f"app did not import (exit {proc.returncode}):\n{proc.stderr[-1500:]}"
    hours = json.loads(proc.stdout[marker + len("__HOURS__") :].strip())
    log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    return hours, log_text


class TestTheInvalidLifetimeWarningReachesTheDaemonLogFile:
    def test_one_line_in_the_log_file_names_the_key_and_never_the_value_or_the_secret(
        self, tmp_path
    ):
        junk = "zq9-lifetime-junk"

        hours, log_text = _import_app_in_a_fresh_process(tmp_path, junk)

        assert hours == 12
        lines = [line for line in log_text.splitlines() if _LIFETIME_KEY in line]
        assert len(lines) == 1, (
            f"the daemon log holds {len(lines)} lines naming the key: {log_text!r}"
        )
        assert "12" in lines[0]
        assert junk not in log_text and _SECRET not in log_text and _PASSWORD not in log_text

    def test_a_valid_value_is_used_and_logs_nothing_about_the_key(self, tmp_path):
        hours, log_text = _import_app_in_a_fresh_process(tmp_path, "6")

        assert hours == 6
        assert _LIFETIME_KEY not in log_text

    def test_an_absent_key_is_twelve_hours_and_logs_nothing_about_the_key(self, tmp_path):
        hours, log_text = _import_app_in_a_fresh_process(tmp_path, None)

        assert hours == 12
        assert _LIFETIME_KEY not in log_text
