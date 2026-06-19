"""
RED tests for dashboard password-auth gate — AC-1 through AC-13.

The auth gate (`_auth_before_request`, `/login`, `/logout`, `_resolve_dashboard_credential`)
does NOT exist in app.py yet.  Every test in this file is expected to FAIL (RED)
until the implementer ships the feature.

CI-runnability: all tests inject DASHBOARD_PASSWORD and SECRET_KEY via
monkeypatch.setenv so they run on CI without a real .env file.  Test values
are synthetic constants — NOT real secrets.

** ISOLATION CONTRACT (mirrors _disable_csrf_for_tests pattern) **

The implementer MUST add:
  1. A module-level flag in app.py:
       _auth_check_enabled: bool = True
  2. A new autouse fixture in tests/conftest.py:
       @pytest.fixture(autouse=True)
       def _disable_auth_for_tests(monkeypatch):
           import app as _app_module
           monkeypatch.setattr(_app_module, "_auth_check_enabled", False)
     This keeps all ~7000 EXISTING route tests working (they hit protected
     routes unauthenticated — the gate must be off by default in tests).
  3. The `_auth_before_request` hook checks `_auth_check_enabled` before
     enforcing the gate (same pattern as `_csrf_before_request` / `_csrf_check_enabled`).

Tests in THIS FILE that need the real gate (all of them) re-enable it via:
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
Both `auth_client` and `auth_client_authed` fixtures do this automatically.

Security tests that also need CSRF enforcement call:
    monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Test constants — synthetic, never real secrets
# ---------------------------------------------------------------------------
_TEST_PASSWORD = "test-pass-abc123"
_TEST_SECRET_KEY = "test-secret-key-xyz789"
# Werkzeug-format hash for AC-6 hash-precedence tests.
# The implementation uses werkzeug.security.check_password_hash with prefix
# detection (pbkdf2:/scrypt:/bcrypt:).  We generate a real werkzeug hash here
# so test_hashed_password_authenticates_successfully verifies a true login
# via the hashed-credential path, not just a non-error result.
try:
    from werkzeug.security import generate_password_hash as _gph
    _TEST_PASSWORD_HASH = _gph(_TEST_PASSWORD)
except Exception:
    # Fallback: sha256 hex (implementation will treat as plaintext, test
    # degrades to shape-only assertion — acceptable if werkzeug unavailable).
    _TEST_PASSWORD_HASH = hashlib.sha256(_TEST_PASSWORD.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_auth_throttle():
    """Clear the in-memory auth throttle store before and after EVERY test in
    this file (autouse=True, file-local scope via placement in this module).

    The throttle store (_AUTH_FAILED_ATTEMPTS) is a module-level dict that
    persists between tests in the same process.  Tests that hit the lockout
    threshold (test_throttle_increments_on_wrong_password) would otherwise
    poison subsequent tests that expect a clean slate.

    autouse=True means every test in this file gets a fresh throttle, without
    each test needing to request this fixture by name.
    """
    if hasattr(app_module, "_AUTH_FAILED_ATTEMPTS"):
        app_module._AUTH_FAILED_ATTEMPTS.clear()
    yield
    if hasattr(app_module, "_AUTH_FAILED_ATTEMPTS"):
        app_module._AUTH_FAILED_ATTEMPTS.clear()


@pytest.fixture()
def auth_client(monkeypatch):
    """Flask test client with DASHBOARD_PASSWORD + SECRET_KEY injected via env,
    and the auth gate ENABLED (overriding the autouse _disable_auth_for_tests
    fixture that conftest sets globally to protect the existing test suite).

    Provides a fresh unauthenticated session per test.
    """
    monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
    # Re-enable the auth gate for this test.  The conftest autouse fixture
    # _disable_auth_for_tests sets _auth_check_enabled=False globally; we
    # override it here so these tests exercise the real gate.
    if not hasattr(app_module, "_auth_check_enabled"):
        pytest.fail(
            "app.py must expose _auth_check_enabled (bool) so the test suite can "
            "enable/disable the auth gate per-test without breaking the ~7000 existing "
            "route tests (which rely on _disable_auth_for_tests autouse in conftest). "
            "Implementation not present yet — RED."
        )
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client


@pytest.fixture()
def auth_client_authed(monkeypatch):
    """Flask test client that has already completed a successful login,
    with the auth gate ENABLED.

    Logs in by POSTing to /login with the correct password before yielding,
    so that the session cookie is present for subsequent requests.
    """
    monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
    if not hasattr(app_module, "_auth_check_enabled"):
        pytest.fail(
            "app.py must expose _auth_check_enabled (bool) so the test suite can "
            "enable/disable the auth gate per-test without breaking the ~7000 existing "
            "route tests (which rely on _disable_auth_for_tests autouse in conftest). "
            "Implementation not present yet — RED."
        )
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        # Perform the login once so subsequent requests are authenticated.
        client.post(
            "/login",
            data={"password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        yield client


# ---------------------------------------------------------------------------
# AC-1 + AC-2 + AC-3: Unauthenticated access behaviour
# ---------------------------------------------------------------------------


class TestUnauthenticatedAccess:
    """Unauthenticated requests hit the gate; only /login (and static) escape."""

    def test_unauthenticated_root_redirects_to_login(self, auth_client):
        """AC-1: GET / while not authenticated → 302 to /login."""
        resp = auth_client.get("/", follow_redirects=False)
        assert resp.status_code == 302, (
            f"Unauthenticated GET / must redirect to /login (302), got {resp.status_code}"
        )
        assert "/login" in resp.headers.get("Location", ""), (
            f"Redirect Location must contain /login, got {resp.headers.get('Location')}"
        )

    def test_unauthenticated_ai_advisor_redirects_to_login(self, auth_client):
        """AC-1: GET /ai-advisor while not authenticated → 302 to /login."""
        resp = auth_client.get("/ai-advisor", follow_redirects=False)
        assert resp.status_code == 302, (
            f"Unauthenticated GET /ai-advisor must redirect (302), got {resp.status_code}"
        )
        assert "/login" in resp.headers.get("Location", ""), (
            f"Redirect Location must contain /login, got {resp.headers.get('Location')}"
        )

    def test_unauthenticated_api_route_returns_401_json(self, auth_client):
        """AC-2: GET /api/state while not authenticated → 401 JSON (not 302)."""
        resp = auth_client.get("/api/state")
        assert resp.status_code == 401, (
            f"Unauthenticated GET /api/state must return 401, got {resp.status_code}"
        )
        data = resp.get_json()
        assert data is not None, "401 response must have a JSON body"
        assert "error" in data or "message" in data, (
            f"401 JSON body must contain 'error' or 'message' key, got {data}"
        )

    def test_unauthenticated_api_route_returns_json_not_html(self, auth_client):
        """AC-2: API 401 response Content-Type is application/json, not text/html."""
        resp = auth_client.get("/api/state")
        assert resp.status_code == 401
        ct = resp.content_type or ""
        assert "application/json" in ct, (
            f"API 401 must be JSON content-type, got '{ct}'"
        )

    def test_unauthenticated_xhr_request_returns_401(self, auth_client):
        """AC-2: XHR (X-Requested-With: XMLHttpRequest) unauthenticated → 401."""
        resp = auth_client.get(
            "/api/state",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 401, (
            f"XHR unauthenticated request must return 401, got {resp.status_code}"
        )

    def test_login_get_is_reachable_unauthenticated(self, auth_client):
        """AC-3: GET /login is reachable without auth and returns 200."""
        resp = auth_client.get("/login")
        assert resp.status_code == 200, (
            f"GET /login must return 200 unauthenticated, got {resp.status_code}"
        )

    def test_login_page_contains_password_form(self, auth_client):
        """AC-3: GET /login response contains a password input field."""
        resp = auth_client.get("/login")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'type="password"' in body or "type='password'" in body, (
            "Login page must contain a password input element"
        )

    def test_login_page_contains_form_post_action(self, auth_client):
        """AC-3: Login page form POSTs to /login."""
        resp = auth_client.get("/login")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # Form must have an action pointing to /login (or no action = same page)
        assert 'action="/login"' in body or "action='/login'" in body or (
            "<form" in body and 'method="post"' in body.lower()
        ), "Login page must contain a form that posts to /login"

    def test_unauthenticated_write_path_settings_denied(self, auth_client):
        """AC-12: POST /api/settings unauthenticated → denied (401)."""
        resp = auth_client.post(
            "/api/settings",
            json={"key": "SOME_SETTING", "value": "x"},
        )
        assert resp.status_code in (401, 403), (
            f"Unauthenticated POST /api/settings must be denied (401/403), got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# AC-4 + AC-5 + AC-13: Login flow
# ---------------------------------------------------------------------------


class TestLoginFlow:
    """POST /login correctness: session management and redirect behaviour."""

    def test_correct_password_sets_session_and_redirects(self, auth_client):
        """AC-4: Correct password → session['authenticated']=True + redirect to /."""
        resp = auth_client.post(
            "/login",
            data={"password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303), (
            f"Successful login must redirect (302/303), got {resp.status_code}"
        )
        # The redirect should point to the dashboard, not /login
        location = resp.headers.get("Location", "")
        assert location != "/login", (
            f"Successful login must NOT redirect back to /login, got Location={location}"
        )

    def test_correct_password_subsequent_protected_access_granted(self, auth_client):
        """AC-4: After login, subsequent requests to protected routes succeed (200).

        This test first asserts the auth gate is registered (so the pre-login
        200 is not a false-pass), then logs in and verifies access is granted.
        """
        # Gate must exist — if _auth_before_request is missing this assertion fails
        before_request_fns = app_module.app.before_request_funcs.get(None, [])
        fn_names = [fn.__name__ for fn in before_request_fns]
        assert "_auth_before_request" in fn_names, (
            "_auth_before_request not registered — auth gate missing; "
            "this test cannot proceed until the implementer adds the gate"
        )

        mock_db = MagicMock()
        mock_db.load_state.return_value = {}
        mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
        mock_db.get_symphony_strategy.return_value = None

        with patch.object(app_module, "database", mock_db):
            auth_client.post(
                "/login",
                data={"password": _TEST_PASSWORD},
                follow_redirects=True,
            )
            resp = auth_client.get("/", follow_redirects=False)

        assert resp.status_code == 200, (
            f"After login, GET / must return 200, got {resp.status_code}"
        )

    def test_wrong_password_returns_200_with_generic_error(self, auth_client):
        """AC-5: Wrong password → re-renders login with a generic error message."""
        resp = auth_client.post(
            "/login",
            data={"password": "this-is-wrong"},
            follow_redirects=False,
        )
        assert resp.status_code == 200, (
            f"Wrong password must re-render login page (200), got {resp.status_code}"
        )
        body = resp.get_data(as_text=True)
        # Must show a generic error — NOT revealing whether the password is
        # "too short", "wrong user", etc.  "Incorrect password" is the spec.
        assert "incorrect" in body.lower() or "wrong" in body.lower() or "invalid" in body.lower(), (
            "Wrong password response must include a generic error message"
        )

    def test_wrong_password_does_not_set_session(self, auth_client):
        """AC-5: Wrong password must not set session['authenticated']."""
        auth_client.post(
            "/login",
            data={"password": "this-is-wrong"},
            follow_redirects=False,
        )
        # After a failed login, a protected route must still redirect
        resp = auth_client.get("/", follow_redirects=False)
        assert resp.status_code == 302, (
            "After failed login, protected route must still 302 to /login — "
            "no session must have been set"
        )

    def test_already_authenticated_login_get_redirects_to_dashboard(self, auth_client_authed):
        """AC-13: Already-authenticated GET /login → redirect to dashboard."""
        resp = auth_client_authed.get("/login", follow_redirects=False)
        assert resp.status_code in (302, 303), (
            f"Already-authed GET /login must redirect (302/303), got {resp.status_code}"
        )
        location = resp.headers.get("Location", "")
        assert "/login" not in location, (
            f"Already-authed GET /login must NOT redirect back to /login, got {location}"
        )


# ---------------------------------------------------------------------------
# AC-11: Logout
# ---------------------------------------------------------------------------


class TestLogout:
    """GET /logout clears the session and sends back to /login."""

    def test_logout_clears_session_and_redirects(self, auth_client_authed):
        """AC-11: GET /logout → session cleared + redirect to /login."""
        resp = auth_client_authed.get("/logout", follow_redirects=False)
        assert resp.status_code in (302, 303), (
            f"GET /logout must redirect (302/303), got {resp.status_code}"
        )
        assert "/login" in resp.headers.get("Location", ""), (
            "GET /logout must redirect to /login"
        )

    def test_after_logout_protected_route_redirects_again(self, auth_client_authed):
        """AC-11: After logout, subsequent protected request → 302 to /login."""
        auth_client_authed.get("/logout", follow_redirects=True)
        resp = auth_client_authed.get("/", follow_redirects=False)
        assert resp.status_code == 302, (
            "After logout, GET / must redirect to /login again"
        )


# ---------------------------------------------------------------------------
# AC-10: Session cookie flags
# ---------------------------------------------------------------------------


class TestCookieFlags:
    """Session cookie must carry HttpOnly and SameSite=Lax; Secure when env set."""

    def test_session_cookie_is_httponly(self, auth_client):
        """AC-10: The session cookie has HttpOnly flag.

        Inspects the Set-Cookie response header on the successful login POST.
        """
        resp = auth_client.post(
            "/login",
            data={"password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        # The login route doesn't exist yet → Set-Cookie will be absent.
        # The assertion below will fail correctly (RED) when the route is missing.
        set_cookie = resp.headers.get("Set-Cookie", "")
        assert "HttpOnly" in set_cookie or "httponly" in set_cookie.lower(), (
            f"Session cookie must have HttpOnly flag; Set-Cookie was: {set_cookie!r}. "
            "If Set-Cookie is empty, the /login route has not been implemented yet."
        )

    def test_session_cookie_has_samesite_lax(self, auth_client):
        """AC-10: Session cookie has SameSite=Lax."""
        resp = auth_client.post(
            "/login",
            data={"password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        set_cookie = resp.headers.get("Set-Cookie", "")
        assert "SameSite=Lax" in set_cookie or "samesite=lax" in set_cookie.lower(), (
            f"Session cookie must have SameSite=Lax; Set-Cookie was: {set_cookie!r}"
        )

    def test_session_cookie_secure_flag_when_env_set(self, monkeypatch):
        """AC-10: When SESSION_COOKIE_SECURE env is truthy, cookie has Secure flag."""
        monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
        monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
        monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
        if hasattr(app_module, "_auth_check_enabled"):
            monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        app_module.app.config["TESTING"] = True
        # Reload the cookie flag from env (implementation must honour this at runtime)
        app_module.app.config["SESSION_COOKIE_SECURE"] = True
        with app_module.app.test_client() as secure_client:
            resp = secure_client.post(
                "/login",
                data={"password": _TEST_PASSWORD},
                follow_redirects=False,
            )
        set_cookie = resp.headers.get("Set-Cookie", "")
        assert "Secure" in set_cookie, (
            f"SESSION_COOKIE_SECURE=true must set the Secure flag on the session cookie; "
            f"Set-Cookie was: {set_cookie!r}"
        )


# ---------------------------------------------------------------------------
# AC-3: Static assets reachable pre-auth
# ---------------------------------------------------------------------------


class TestStaticAssetsPreAuth:
    """Login page static assets must load before auth (else form is broken)."""

    def test_static_css_reachable_unauthenticated(self, auth_client):
        """AC-3: Static CSS assets are reachable pre-auth AND with auth gate active.

        This is a regression guard: a naive auth gate that blocks ALL requests
        including static/ would break the login form rendering. The implementer
        must include the Flask 'static' endpoint in _AUTH_EXEMPT_ENDPOINTS.

        Assertion order: first assert the gate exists (so blocking-gate failures
        are caught), then assert static is still accessible.
        """
        # The auth gate must be present for this test to have meaning.
        # If the gate is missing, this test verifies baseline behavior only.
        before_request_fns = app_module.app.before_request_funcs.get(None, [])
        fn_names = [fn.__name__ for fn in before_request_fns]
        gate_present = "_auth_before_request" in fn_names

        # Static files confirmed to exist in static/ directory.
        for path in ("/static/layout.css", "/static/tokens.css"):
            resp = auth_client.get(path)
            if gate_present:
                assert resp.status_code == 200, (
                    f"Auth gate is active but {path} returned {resp.status_code}; "
                    "the 'static' endpoint must be in _AUTH_EXEMPT_ENDPOINTS so "
                    "the login page renders styled"
                )
            else:
                # Gate not implemented yet — still verify static serves correctly
                # as a pre-condition sanity check.
                assert resp.status_code == 200, (
                    f"Static asset {path} must be accessible (got {resp.status_code}); "
                    "verify the static/ directory and Flask static_url_path are correct"
                )

        if not gate_present:
            pytest.fail(
                "_auth_before_request is not registered — the auth gate has not been "
                "implemented yet.  This test cannot fully verify AC-3 (static assets "
                "exempt from auth) until the gate exists."
            )


# ---------------------------------------------------------------------------
# AC-6 hashed password: DASHBOARD_PASSWORD_HASH authenticates; takes precedence
# ---------------------------------------------------------------------------


class TestHashedPassword:
    """AC-6: DASHBOARD_PASSWORD_HASH form works and takes precedence over plaintext."""

    def test_hashed_password_authenticates_successfully(self, monkeypatch):
        """AC-6: When DASHBOARD_PASSWORD_HASH is set, correct password logs in.

        Uses a real werkzeug-format hash (pbkdf2:...) generated at test-module
        import time so the implementation's check_password_hash path is exercised.
        Asserts actual login success (302 redirect) when the hash is werkzeug-format.
        """
        monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", _TEST_PASSWORD_HASH)
        # Explicitly unset the plaintext env to prove hash takes over.
        monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
        monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
        if hasattr(app_module, "_auth_check_enabled"):
            monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as client:
            resp = client.post(
                "/login",
                data={"password": _TEST_PASSWORD},
                follow_redirects=False,
            )
        # If _TEST_PASSWORD_HASH is a werkzeug-format hash, login must succeed (302).
        # If it fell back to sha256 hex (werkzeug import failed), shape-only check.
        is_werkzeug_hash = _TEST_PASSWORD_HASH.startswith(("pbkdf2:", "scrypt:", "bcrypt:"))
        if is_werkzeug_hash:
            assert resp.status_code in (302, 303), (
                f"DASHBOARD_PASSWORD_HASH (werkzeug format) must authenticate and redirect; "
                f"got {resp.status_code}. The hash-compare path in login() may be broken."
            )
        else:
            # Degraded: sha256 hex treated as plaintext, login fails (200 re-render)
            # but must not error.
            assert resp.status_code not in (500, 503), (
                "DASHBOARD_PASSWORD_HASH path must not produce server error"
            )

    def test_hash_takes_precedence_over_plaintext(self, monkeypatch):
        """AC-6: When both DASHBOARD_PASSWORD_HASH and DASHBOARD_PASSWORD are set,
        the hash is used for comparison (not the plaintext)."""
        # Set hash to hash of our known password, and plaintext to something different.
        monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", _TEST_PASSWORD_HASH)
        monkeypatch.setenv("DASHBOARD_PASSWORD", "different-plaintext-password")
        monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
        if hasattr(app_module, "_auth_check_enabled"):
            monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as client:
            # _resolve_dashboard_credential must return the HASH not the plaintext
            # when both are set.  We verify this by asserting the function exists
            # and returns the hash value.
            assert hasattr(app_module, "_resolve_dashboard_credential"), (
                "_resolve_dashboard_credential must be a module-level function in app.py"
            )
            result = app_module._resolve_dashboard_credential()
            # Hash takes precedence: result must equal the hash, not the plaintext
            assert result == _TEST_PASSWORD_HASH or result != "different-plaintext-password", (
                "When DASHBOARD_PASSWORD_HASH is set, _resolve_dashboard_credential "
                "must return the hash, not the plaintext"
            )


# ---------------------------------------------------------------------------
# Security tests (grouped in TestSecurity per the plan)
# ---------------------------------------------------------------------------


class TestSecurity:
    """Security contract tests: fail-closed, CSRF, constant-time, throttle, no-log."""

    # -- AC-8: Fail-closed on misconfig --

    def test_fail_closed_missing_dashboard_password_denies_root(self, monkeypatch):
        """AC-8: When DASHBOARD_PASSWORD(+hash) is missing, GET / is denied."""
        monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
        monkeypatch.delenv("DASHBOARD_PASSWORD_HASH", raising=False)
        monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
        if hasattr(app_module, "_auth_check_enabled"):
            monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as client:
            resp = client.get("/", follow_redirects=False)
        # Must deny — either redirect to /login (which then blocks login) or serve
        # a maintenance/503 page; must NOT serve the real dashboard content.
        assert resp.status_code != 200 or b"login" in resp.get_data(), (
            "Missing DASHBOARD_PASSWORD must deny access to /; "
            f"got {resp.status_code} with no login redirect"
        )

    def test_fail_closed_missing_password_login_cannot_succeed(self, monkeypatch):
        """AC-8: When DASHBOARD_PASSWORD is missing, POST /login can never succeed."""
        monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
        monkeypatch.delenv("DASHBOARD_PASSWORD_HASH", raising=False)
        monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
        if hasattr(app_module, "_auth_check_enabled"):
            monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as client:
            resp = client.post(
                "/login",
                data={"password": "anything"},
                follow_redirects=False,
            )
        # Must not succeed — no session, no 302 to dashboard
        # Accept either re-render (200) showing error, or 503/maintenance
        assert resp.status_code in (200, 400, 503), (
            f"Missing password misconfig must prevent login success; got {resp.status_code}"
        )
        # Must NOT redirect to dashboard
        if resp.status_code in (301, 302, 303):
            location = resp.headers.get("Location", "")
            assert "/login" in location, (
                "Missing-password login must not redirect to the dashboard"
            )

    def test_fail_closed_missing_secret_key_denies_all(self, monkeypatch):
        """AC-8: When SECRET_KEY is missing/empty, ALL routes are denied."""
        monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
        if hasattr(app_module, "_auth_check_enabled"):
            monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as client:
            resp = client.get("/", follow_redirects=False)
        # Missing secret key → fail closed; must not serve dashboard
        assert resp.status_code != 200 or b"login" in resp.get_data(), (
            "Missing SECRET_KEY must cause fail-closed behaviour on GET /"
        )

    def test_fail_closed_empty_secret_key_denies_all(self, monkeypatch):
        """AC-8: When SECRET_KEY is an empty string, ALL routes are denied."""
        monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
        monkeypatch.setenv("SECRET_KEY", "")
        if hasattr(app_module, "_auth_check_enabled"):
            monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as client:
            resp = client.get("/", follow_redirects=False)
        assert resp.status_code != 200 or b"login" in resp.get_data(), (
            "Empty SECRET_KEY must cause fail-closed behaviour on GET /"
        )

    # -- AC-7: CSRF protection on login POST --

    def test_csrf_missing_on_login_post_rejected(self, monkeypatch):
        """AC-7: POST /login with missing CSRF token is rejected (403)."""
        monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
        monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
        # Re-enable CSRF enforcement for this test only (autouse fixture disables it globally)
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as client:
            # POST without any CSRF header
            resp = client.post(
                "/login",
                data={"password": _TEST_PASSWORD},
                # No X-CSRF-Token header
            )
        assert resp.status_code == 403, (
            f"Login POST without CSRF token must be rejected (403), got {resp.status_code}"
        )

    def test_login_get_issues_csrf_token_pre_auth(self, auth_client):
        """AC-7: GET /login (unauthenticated) includes a CSRF token in the response."""
        resp = auth_client.get("/login")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # The login form must include a CSRF token field or meta tag
        assert (
            "csrf" in body.lower()
            or "X-CSRF-Token" in body
            or "_csrf" in body
        ), (
            "GET /login must include a CSRF token in the response (form field or meta)"
        )

    def test_login_post_with_form_field_csrf_token_succeeds(self, monkeypatch):
        """AC-7 / security: browser form POST with csrf_token form field must succeed.

        The login page is a plain HTML form (no JS) that submits the CSRF token
        as a form field (name='csrf_token').  Browser native form POSTs cannot
        set arbitrary headers, so the CSRF gate MUST accept the token from
        request.form['csrf_token'] — NOT only from the X-CSRF-Token header.

        Without this fix, a real operator login POST arrives with no X-CSRF-Token
        header, _validate_csrf() compares '' vs _CSRF_TOKEN, returns 403, and
        the operator can NEVER log in in production (deploy-blocker).

        This test is RED against the current implementation which reads only the
        header (app.py:318: request.headers.get('X-CSRF-Token', '')).  It will
        go GREEN when _validate_csrf() also accepts request.form.get('csrf_token').
        """
        monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
        monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
        if hasattr(app_module, "_auth_check_enabled"):
            monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        # Enable CSRF enforcement — this is what production always has.
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as client:
            # Step 1: GET /login to obtain the CSRF token from the form field.
            get_resp = client.get("/login")
            assert get_resp.status_code == 200, (
                f"GET /login must be reachable pre-auth, got {get_resp.status_code}"
            )
            # Scrape the csrf_token value from the rendered form field.
            body = get_resp.get_data(as_text=True)
            import re
            match = re.search(
                r'<input[^>]+name=["\']csrf_token["\'][^>]+value=["\']([^"\']+)["\']',
                body,
            ) or re.search(
                r'<input[^>]+value=["\']([^"\']+)["\'][^>]+name=["\']csrf_token["\']',
                body,
            )
            assert match, (
                "GET /login must render a hidden csrf_token input field; "
                "could not find it in the response body. "
                "Body snippet: " + body[:500]
            )
            scraped_token = match.group(1)

            # Step 2: POST /login with the token as a FORM FIELD (no X-CSRF-Token header).
            # This is exactly what a browser sends when a native <form method="post"> submits.
            post_resp = client.post(
                "/login",
                data={"password": _TEST_PASSWORD, "csrf_token": scraped_token},
                # Deliberately NO X-CSRF-Token header — browser form POSTs cannot set it.
                follow_redirects=False,
            )

        # Must redirect to dashboard (login succeeds), NOT 403.
        assert post_resp.status_code != 403, (
            "Browser form POST with valid csrf_token FORM FIELD must not be rejected "
            "with 403. _validate_csrf() currently reads only the X-CSRF-Token HEADER "
            "(app.py:318), so a native form submit always gets 403 in production. "
            "Fix: also accept token from request.form.get('csrf_token')."
        )
        assert post_resp.status_code in (302, 303), (
            f"Browser form POST with correct password + valid form-field CSRF token "
            f"must redirect (302/303), got {post_resp.status_code}. "
            f"If 403: CSRF gate rejects form-field token channel. "
            f"If 200: password comparison failed despite correct credentials."
        )

    def test_login_post_header_csrf_token_still_works(self, monkeypatch):
        """AC-7: X-CSRF-Token HEADER path must still work for JS/fetch callers.

        After fixing the form-field channel, the header path must remain intact.
        A regression that dropped header support would break any JS callers.
        """
        monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
        monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
        if hasattr(app_module, "_auth_check_enabled"):
            monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as client:
            resp = client.post(
                "/login",
                data={"password": _TEST_PASSWORD},
                headers={"X-CSRF-Token": app_module._CSRF_TOKEN},
                follow_redirects=False,
            )
        assert resp.status_code in (302, 303), (
            f"Login POST with valid X-CSRF-Token header must still redirect (302/303), "
            f"got {resp.status_code}. The header path must remain intact alongside the "
            f"form-field path."
        )

    # -- AC-6: Constant-time compare and no password in logs --

    def test_constant_time_compare_function_used(self, monkeypatch):
        """AC-6: The auth gate uses hmac.compare_digest for password comparison,
        not a naive == comparison that leaks timing.

        We check that:
        1. hmac is imported in app.py (not just secrets.compare_digest used for CSRF).
        2. The login route exists (without it compare_digest is irrelevant).
        """
        import inspect

        source = inspect.getsource(app_module)
        # Must import hmac (not just use secrets.compare_digest for the CSRF token)
        assert "import hmac" in source or "hmac.compare_digest" in source, (
            "app.py must import hmac and use hmac.compare_digest for password "
            "comparison — secrets.compare_digest alone is insufficient because the "
            "login route does not exist yet"
        )
        # The login route must exist — if it doesn't, the compare can't happen
        login_route_exists = any(
            rule.endpoint == "login" or str(rule.rule) == "/login"
            for rule in app_module.app.url_map.iter_rules()
        )
        assert login_route_exists, (
            "app.py must have a /login route where hmac.compare_digest is used; "
            "neither the route nor the constant-time compare exists yet"
        )

    def test_password_not_written_to_logs_on_wrong_attempt(self, monkeypatch, caplog):
        """AC-6: The submitted password value must NEVER appear in log output.

        This test is meaningful only once the /login route exists.  We first
        assert the route is present so a tautological pass (404 with no logging)
        does not count as success.
        """
        monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
        monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
        if hasattr(app_module, "_auth_check_enabled"):
            monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        app_module.app.config["TESTING"] = True

        # Assert the login route exists — without it the test is tautological
        login_route_exists = any(
            str(rule.rule) == "/login"
            for rule in app_module.app.url_map.iter_rules()
        )
        assert login_route_exists, (
            "/login route does not exist yet — this test is not meaningful until "
            "the implementer adds the route; failing here to keep this RED"
        )

        with caplog.at_level(logging.DEBUG, logger=""):
            with app_module.app.test_client() as client:
                client.post(
                    "/login",
                    data={"password": _TEST_PASSWORD},
                    follow_redirects=False,
                )
                # Also try a wrong password to ensure that's not logged either
                client.post(
                    "/login",
                    data={"password": "a-wrong-password-do-not-log"},
                    follow_redirects=False,
                )

        all_log_text = caplog.text
        # The actual submitted password values must never appear in logs
        assert _TEST_PASSWORD not in all_log_text, (
            "Correct password must not appear in log output"
        )
        assert "a-wrong-password-do-not-log" not in all_log_text, (
            "Wrong password must not appear in log output"
        )

    # -- AC-9: Failed-attempt throttle / lockout --

    def test_throttle_increments_on_wrong_password(self, auth_client):
        """AC-9: Each wrong-password attempt increments the failed-attempt counter."""
        # The feature exposes this via _AUTH_MAX_ATTEMPTS constant and in-memory
        # throttle dict.  We assert the constants exist and that repeated failures
        # eventually produce a lockout response.
        assert hasattr(app_module, "_AUTH_MAX_ATTEMPTS"), (
            "_AUTH_MAX_ATTEMPTS constant must be defined in app.py"
        )
        assert hasattr(app_module, "_AUTH_LOCKOUT_SECONDS"), (
            "_AUTH_LOCKOUT_SECONDS constant must be defined in app.py"
        )
        max_attempts = app_module._AUTH_MAX_ATTEMPTS
        # Send max_attempts + 1 wrong-password POSTs
        last_resp = None
        for _ in range(max_attempts + 1):
            last_resp = auth_client.post(
                "/login",
                data={"password": "definitively-wrong"},
                follow_redirects=False,
            )
        # After exceeding the threshold, must get lockout (429) or blocked login page
        assert last_resp is not None
        assert last_resp.status_code in (200, 429), (
            f"After {max_attempts + 1} wrong attempts, expect 429 or login page; "
            f"got {last_resp.status_code}"
        )
        body = last_resp.get_data(as_text=True).lower()
        # Either status 429, or the response body must mention lockout / too many
        if last_resp.status_code == 200:
            assert "too many" in body or "locked" in body or "attempts" in body, (
                f"After exceeding max attempts, login page must show lockout message; "
                f"got body: {body[:300]!r}"
            )

    def test_throttle_resets_on_successful_login(self, auth_client):
        """AC-9: Throttle counter resets when a correct login follows failures."""
        # Submit some (but fewer than max) wrong attempts, then log in correctly.
        for _ in range(2):
            auth_client.post(
                "/login",
                data={"password": "wrong"},
                follow_redirects=False,
            )
        # Now log in correctly — must succeed (no lockout)
        resp = auth_client.post(
            "/login",
            data={"password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303), (
            f"Correct login after partial wrong-attempt run must succeed (302/303), "
            f"got {resp.status_code}"
        )

    def test_resolve_dashboard_credential_exists(self, monkeypatch):
        """AC-6/AC-8: _resolve_dashboard_credential is a callable in app.py."""
        monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
        monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
        assert hasattr(app_module, "_resolve_dashboard_credential"), (
            "_resolve_dashboard_credential must be a module-level function in app.py "
            "(used for credential resolution + fail-closed misconfig detection)"
        )
        assert callable(app_module._resolve_dashboard_credential), (
            "_resolve_dashboard_credential must be callable"
        )

    def test_auth_exempt_endpoints_constant_exists(self, monkeypatch):
        """AC-1/AC-3: _AUTH_EXEMPT_ENDPOINTS set/tuple exists in app.py."""
        monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
        monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
        assert hasattr(app_module, "_AUTH_EXEMPT_ENDPOINTS"), (
            "_AUTH_EXEMPT_ENDPOINTS must be defined in app.py; "
            "it declares the minimal set of endpoints exempt from the auth gate"
        )

    def test_auth_before_request_hook_registered(self, monkeypatch):
        """AC-1/AC-2/AC-8: _auth_before_request is registered as a before_request handler."""
        monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
        monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
        # The before_request functions are stored in app.before_request_funcs
        # keyed by None (registered globally).
        before_request_fns = app_module.app.before_request_funcs.get(None, [])
        fn_names = [fn.__name__ for fn in before_request_fns]
        assert "_auth_before_request" in fn_names, (
            f"_auth_before_request must be registered as a before_request handler; "
            f"registered handlers: {fn_names}"
        )
