"""
RED tests — A-3 (MEDIUM): Dashboard has no CSRF protection on mutating routes.

Security audit finding A-3 confirmed that no route requires auth and no
CSRF tokens are present on the many POST routes (/api/settings, /api/sell_account,
/ai-advisor/accept, etc.).  The only mitigation is the localhost bind.

CSRF allows a malicious page open in the operator's browser (on the same
machine as the daemon) to make cross-origin POST requests to the dashboard.
On a localhost-bound service this is a realistic threat: the operator could
have both the dashboard and a malicious page open simultaneously.

Required fix: add a CSRF token check to all mutating POST routes.  The
canonical Flask approach is flask-wtf's CSRFProtect or a shared-secret
header check (e.g., X-Requested-With: XMLHttpRequest combined with
SameSite=Strict cookies, or a per-session token).

The tests here pin the contract: a POST request without a valid CSRF
token/header is rejected; a legitimate same-origin-style request (with
the expected header) is accepted.

Expected state: RED (all tests fail) until CSRF protection is implemented.
"""

from __future__ import annotations

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Routes that are mutating and must be CSRF-protected
# ---------------------------------------------------------------------------

MUTATING_ROUTES = [
    "/api/settings",
    "/ai-advisor/accept",
    "/ai-advisor/reject",
    "/api/settings/flush-resync",
]

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _reenable_csrf(monkeypatch):
    """Re-enable CSRF enforcement for tests in this file.

    The root tests/conftest.py sets _csrf_check_enabled=False globally so
    that existing POST tests don't need to inject a token header.  These
    tests specifically test CSRF enforcement, so they must override that
    global disable and set the flag back to True.

    This autouse fixture runs for every test in test_a3_csrf_protection.py
    and takes precedence over the root conftest's _disable_csrf_for_tests
    because monkeypatch restores in LIFO order within a test.
    """
    monkeypatch.setattr(app_module, "_csrf_check_enabled", True)


@pytest.fixture
def _stub_database_and_env(monkeypatch):
    """Minimal stubs so routes can start processing — CSRF check runs first."""
    from unittest.mock import MagicMock

    db_mock = MagicMock()
    db_mock.save_symphony_strategy.return_value = None
    db_mock.load_state.return_value = {}
    monkeypatch.setattr(app_module, "database", db_mock)
    monkeypatch.setattr(app_module, "set_key", lambda *a, **kw: (True, a[1], a[2]))
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", "/fake/.env")


# ---------------------------------------------------------------------------
# A-3 RED-1: POST without CSRF token is rejected (forged cross-origin request)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", MUTATING_ROUTES)
def test_post_without_csrf_token_is_rejected(route, client, _stub_database_and_env):
    """A POST to a mutating route with no CSRF token must be rejected.

    This simulates a cross-origin forged request: the attacker's page
    uses fetch() or XMLHttpRequest to POST to the dashboard.  Without a
    CSRF token in the request, the server must reject it.

    Acceptable rejection: HTTP 400, 403, or 405 (method not allowed if
    the route is CSRF-protected by WTForms).  HTTP 200 is a CSRF failure.
    HTTP 500 is also unacceptable — it means the code crashed instead of
    cleanly rejecting.
    """
    resp = client.post(route, json={})

    assert resp.status_code not in (200, 500), (
        f"POST {route} without a CSRF token must be rejected with HTTP 400/403; "
        f"got {resp.status_code}. "
        f"HTTP 200 = CSRF bypass; HTTP 500 = unhandled error (also wrong). "
        f"Body: {resp.get_data(as_text=True)[:200]!r}"
    )
    assert resp.status_code in (400, 403, 405), (
        f"POST {route} without CSRF token: expected 400/403/405, got {resp.status_code}. "
        f"Body: {resp.get_data(as_text=True)[:200]!r}"
    )


# ---------------------------------------------------------------------------
# A-3 RED-2: GET /api/csrf-token endpoint must exist and return a token
# ---------------------------------------------------------------------------


def test_csrf_token_endpoint_exists_and_returns_token(client):
    """GET /api/csrf-token must return a JSON body with a 'csrf_token' key.

    The operator dashboard's JS fetches this endpoint on page load to
    obtain the process-lifetime token for subsequent mutating POST requests.
    If this endpoint is missing or returns no token, ALL operator mutations
    would fail (the CSRF check on POST would reject every request).
    """
    resp = client.get("/api/csrf-token")
    assert resp.status_code == 200, (
        f"GET /api/csrf-token must return 200; got {resp.status_code}. "
        "This endpoint vends the CSRF token for the dashboard JS."
    )
    body = resp.get_json()
    assert isinstance(body, dict), (
        f"GET /api/csrf-token must return a JSON object; got {type(body)}: {body!r}"
    )
    assert "csrf_token" in body, (
        f"GET /api/csrf-token response must include 'csrf_token' key. Got keys: {list(body.keys())}"
    )
    token = body["csrf_token"]
    assert isinstance(token, str) and len(token) >= 32, (
        f"CSRF token must be a string of at least 32 characters (hex entropy). "
        f"Got: {token!r} (len={len(token) if isinstance(token, str) else 'N/A'})"
    )


# ---------------------------------------------------------------------------
# A-3 RED-3: POST with valid CSRF token from /api/csrf-token is accepted
# ---------------------------------------------------------------------------


def test_post_with_valid_csrf_token_is_accepted(client, _stub_database_and_env):
    """A POST to /api/settings with a valid CSRF token from /api/csrf-token succeeds.

    The operator JS workflow: (1) fetch GET /api/csrf-token, (2) include
    the token in X-CSRF-Token header on all mutating POST requests.
    This test exercises that full roundtrip.

    Expected: HTTP 200 + status=success.
    """
    # Step 1: obtain the CSRF token from the dedicated endpoint.
    token_resp = client.get("/api/csrf-token")
    assert token_resp.status_code == 200, (
        f"GET /api/csrf-token must return 200 to issue a token; got {token_resp.status_code}"
    )
    token = token_resp.get_json().get("csrf_token")
    assert token, "GET /api/csrf-token must return a non-empty csrf_token"

    # Step 2: POST with the token in the X-CSRF-Token header.
    from unittest.mock import patch

    with patch.object(
        app_module,
        "dotenv_values",
        return_value={"LIVE_EXECUTION": "False", "EXECUTION_START_TIME": "09:30"},
    ):
        post_resp = client.post(
            "/api/settings",
            json={"globals": {"EXIT_AUTHORITY": "per_symphony"}, "symphonies": {}},
            headers={"X-CSRF-Token": token},
        )

    assert post_resp.status_code == 200, (
        f"POST /api/settings with a valid CSRF token must return 200; "
        f"got {post_resp.status_code}. Body: {post_resp.get_data(as_text=True)[:200]!r}"
    )
    body = post_resp.get_json()
    assert body.get("status") == "success", (
        f"POST with valid CSRF token must return status=success; got: {body!r}"
    )


# ---------------------------------------------------------------------------
# A-3 RED-4: a fabricated CSRF token must be rejected
# ---------------------------------------------------------------------------


def test_fabricated_csrf_token_is_rejected(client, _stub_database_and_env):
    """A POST with a fabricated (attacker-supplied) CSRF token must be rejected.

    An attacker cannot read the process-lifetime token from /api/csrf-token
    due to the browser Same-Origin Policy, but they could try to guess or
    send a random value.  This test confirms that a random 64-char hex string
    (not the actual process token) is rejected with 403.
    """
    import secrets as _secrets

    fake_token = _secrets.token_hex(32)  # not the real _CSRF_TOKEN

    post_resp = client.post(
        "/api/settings",
        json={"globals": {"EXIT_AUTHORITY": "per_symphony"}, "symphonies": {}},
        headers={"X-CSRF-Token": fake_token},
    )
    assert post_resp.status_code == 403, (
        f"POST /api/settings with a fabricated CSRF token must return 403; "
        f"got {post_resp.status_code}. "
        f"Body: {post_resp.get_data(as_text=True)[:200]!r}"
    )


# ---------------------------------------------------------------------------
# A-3 RED-5: the process-lifetime token is a single stable value per process
# ---------------------------------------------------------------------------


def test_csrf_token_is_stable_within_process(client):
    """Multiple calls to GET /api/csrf-token within the same process must
    return the same token value.

    The implementation uses a process-lifetime token (module-level constant).
    If it rotated per-request, the JS would need to re-fetch before every POST,
    which is not the current design.  This test pins that stability.
    """
    resp1 = client.get("/api/csrf-token")
    resp2 = client.get("/api/csrf-token")

    assert resp1.status_code == 200 and resp2.status_code == 200
    token1 = resp1.get_json().get("csrf_token")
    token2 = resp2.get_json().get("csrf_token")

    assert token1 == token2, (
        "GET /api/csrf-token must return the same token on repeated calls within "
        "a process.  Rotating per-request would break the JS fetch-once-use-many "
        "pattern.  If rotation is intentional, this test must be updated and "
        "the JS fetch pattern must change accordingly."
    )


# ---------------------------------------------------------------------------
# A-3 RED-6: CSRF protection is configured as a before_request hook
# ---------------------------------------------------------------------------


def test_csrf_protection_is_configured_on_flask_app():
    """The Flask app must have CSRF protection registered as a before_request hook.

    The implementation uses a custom before_request hook (_csrf_before_request)
    that validates the X-CSRF-Token header on every POST request.  This test
    confirms the hook is registered — if it were accidentally removed or
    de-registered, all CSRF protection would silently disappear.

    Acceptable implementations:
      - flask_wtf.CSRFProtect initialized with the app object
      - A custom before_request hook whose name or docstring mentions csrf/token
    """
    # Check for WTF CSRF protection in app extensions.
    wtf_present = "csrf" in (app_module.app.extensions or {})

    # Check for a before_request function whose name or docstring mentions csrf/token.
    csrf_hooks = [
        fn
        for fn in app_module.app.before_request_funcs.get(None, [])
        if "csrf" in (getattr(fn, "__name__", "") + getattr(fn, "__doc__", "")).lower()
        or "token" in (getattr(fn, "__name__", "") + getattr(fn, "__doc__", "")).lower()
    ]

    assert wtf_present or csrf_hooks, (
        "CSRF protection is not registered on the Flask app. "
        "Expected either: (1) flask_wtf.CSRFProtect initialised with the app "
        "(shows in app.extensions['csrf']), or (2) a before_request hook "
        "whose name or docstring mentions 'csrf' or 'token'. "
        f"app.extensions keys: {list((app_module.app.extensions or {}).keys())}. "
        f"before_request functions: "
        f"{[getattr(f, '__name__', repr(f)) for f in app_module.app.before_request_funcs.get(None, [])]}."
    )
