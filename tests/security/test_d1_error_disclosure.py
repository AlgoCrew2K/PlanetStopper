"""
RED tests — D-1 (MEDIUM): Raw exception strings returned to the client.

Security audit finding D-1 confirmed that several routes return `str(e)` in
the JSON error body:
  - POST /api/settings          app.py ~2096
  - GET /api/logs/<id>          app.py ~1427
  - POST /ai-advisor/suggest    app.py ~2748

A DB or transport exception can leak SQL fragments, file paths, column names,
or internal module structure to the browser.  Even though no secret has been
shown to reach these paths at authoring time, information disclosure is a
concrete finding that aids further attacks.

Required fix: return a generic message to the client; log the full detail
server-side only (the daemon log already uses exc_info=True for ai_advisor/suggest).

The tests inject exceptions that contain tell-tale strings (SQL fragments,
absolute paths, internal field names) and assert that NONE of those strings
appear in the HTTP response body.

Expected state: RED (all tests fail) until the routes are fixed.
"""

from __future__ import annotations

import pytest

import app as app_module


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _bypass_csrf(monkeypatch):
    """Disable CSRF enforcement for D-1 error-disclosure tests.

    These tests inject exceptions deep in the route's business logic to verify
    that error responses don't echo raw exception strings.  The CSRF gate fires
    BEFORE the business logic, so CSRF must be bypassed here — we are not
    testing CSRF (that's test_a3_csrf_protection.py), we are testing that the
    exception handling path returns a safe message.
    """
    monkeypatch.setattr(app_module, "_csrf_check_enabled", False)


# ---------------------------------------------------------------------------
# D-1 RED-1: POST /api/settings must not echo raw exception text
# ---------------------------------------------------------------------------


def test_post_settings_does_not_echo_raw_exception_to_client(client, monkeypatch):
    """POST /api/settings must return a generic error message, NOT the raw
    str(e) from the caught exception.

    Inject an exception that contains SQL fragments and a file path.  Assert
    that neither the SQL fragment nor the path appears in the response body.

    Current code (app.py:2094-2096):
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    This directly echoes the exception string.  A DB exception here might
    expose: table names, column names, the DB file path, or SQLite version.
    """
    internal_detail = (
        "table symphony_strategies has no column named super_secret_internal_field "
        "at /home/user/.local/lib/alphabot/alphabot.db line 42"
    )

    def _exploding_save_symphony_strategy(*_a, **_kw):
        raise RuntimeError(internal_detail)

    from unittest.mock import MagicMock, patch
    db_mock = MagicMock()
    db_mock.save_symphony_strategy.side_effect = _exploding_save_symphony_strategy
    monkeypatch.setattr(app_module, "database", db_mock)
    monkeypatch.setattr(app_module, "set_key", lambda *a, **kw: (True, a[1], a[2]))
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", "/fake/.env")
    # Provide a minimal allowlist so the route doesn't reject before hitting the DB call.
    # If the allowlist is not yet implemented this still exercises the error path.
    with patch.object(app_module, "dotenv_values", return_value={"LIVE_EXECUTION": "False"}):
        payload = {
            "globals": {},
            "symphonies": {"test_symphony": {"params": {"TRIGGER_THRESHOLD_PCT": "0.5"}, "locked_vars": []}},
        }
        resp = client.post("/api/settings", json=payload)

    body_text = resp.get_data(as_text=True)

    # The raw exception detail must NOT appear in the response.
    assert "super_secret_internal_field" not in body_text, (
        "POST /api/settings echoed the raw exception message to the client. "
        "The response must return a generic message such as 'Settings save failed'; "
        "internal detail (table names, file paths, column names) must stay server-side. "
        f"Response body excerpt: {body_text[:300]!r}"
    )
    assert "alphabot.db" not in body_text, (
        "POST /api/settings echoed a file path from the exception to the client. "
        f"Response body excerpt: {body_text[:300]!r}"
    )
    assert "/home/user" not in body_text, (
        "POST /api/settings echoed a filesystem path from the exception. "
        f"Response body excerpt: {body_text[:300]!r}"
    )

    # The response MUST still indicate an error occurred (generic message OK).
    assert resp.status_code in (400, 500) or (
        isinstance(resp.get_json(), dict) and resp.get_json().get("status") == "error"
    ), (
        "POST /api/settings must return an error indication (HTTP 4xx/5xx or "
        f"status=error in body) when an exception occurs. Got {resp.status_code}, body={body_text[:200]!r}"
    )


# ---------------------------------------------------------------------------
# D-1 RED-2: GET /api/logs/<id> must not echo raw exception text
# ---------------------------------------------------------------------------


def test_get_logs_does_not_echo_raw_exception_to_client(client, monkeypatch):
    """GET /api/logs/<symphony_id> must return a generic error, NOT str(e).

    Current code (app.py:~1426-1427):
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    Injecting a DB exception with an internal path/table detail must not
    result in that detail appearing in the response body.
    """
    internal_detail = (
        "no such table: symphony_decision_log "
        "at /var/lib/alphabot/alphabot_state.db"
    )

    from unittest.mock import MagicMock
    db_mock = MagicMock()
    db_mock.get_ro_connection.return_value = MagicMock()
    db_mock.get_symphony_logs.side_effect = RuntimeError(internal_detail)
    monkeypatch.setattr(app_module, "database", db_mock)

    resp = client.get("/api/logs/test_symphony_id")
    body_text = resp.get_data(as_text=True)

    assert "symphony_decision_log" not in body_text, (
        "GET /api/logs/<id> echoed an internal table name from the exception. "
        "Return a generic message; log detail server-side only. "
        f"Response body excerpt: {body_text[:300]!r}"
    )
    assert "/var/lib/alphabot" not in body_text, (
        "GET /api/logs/<id> echoed a filesystem path from the exception. "
        f"Response body excerpt: {body_text[:300]!r}"
    )
    assert "alphabot_state.db" not in body_text, (
        "GET /api/logs/<id> echoed the DB filename from the exception. "
        f"Response body excerpt: {body_text[:300]!r}"
    )

    # Must still signal an error.
    assert resp.status_code in (400, 500) or (
        isinstance(resp.get_json(), dict) and "error" in (resp.get_json() or {})
    ), (
        f"GET /api/logs/<id> must return an error indication; got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# D-1 RED-3: POST /ai-advisor/suggest must not echo raw exception text
# ---------------------------------------------------------------------------


def test_ai_advisor_suggest_does_not_echo_raw_exception_to_client(client, monkeypatch):
    """POST /ai-advisor/suggest must return a generic error, NOT str(e).

    Current code (app.py:~2746-2748):
        except Exception as _exc:
            _daemon_log.error("ai_advisor_suggest failed: %s", _exc, exc_info=True)
            return jsonify({"error": str(_exc)}), 200

    The exc_info=True log is correct — detail goes server-side.  But
    str(_exc) still echoes raw exception text to the browser.  Injecting
    an exception with internal detail (an Anthropic API key format hint,
    an internal module path) must not expose it.
    """
    internal_detail = (
        "Invalid Anthropic API key format: expected 'sk-ant-...' "
        "got 'test-placeholder-from-/etc/secrets/anthropic.key'"
    )

    from unittest.mock import patch
    with patch.object(app_module, "ai_advisor") as mock_advisor:
        mock_advisor.assemble_advisor_context.side_effect = RuntimeError(internal_detail)
        # DEV_ADVISOR_FIXTURE must be unset so the real code path runs.
        monkeypatch.delenv("DEV_ADVISOR_FIXTURE", raising=False)

        resp = client.post("/ai-advisor/suggest", json={"symphony_id": "test-sym"})

    body_text = resp.get_data(as_text=True)

    assert "sk-ant-" not in body_text, (
        "POST /ai-advisor/suggest echoed an API key format hint from the exception. "
        "Return a generic message; exc_info logging is already in place for server-side detail. "
        f"Response body excerpt: {body_text[:300]!r}"
    )
    assert "/etc/secrets" not in body_text, (
        "POST /ai-advisor/suggest echoed a filesystem path from the exception. "
        f"Response body excerpt: {body_text[:300]!r}"
    )
    assert "anthropic.key" not in body_text, (
        "POST /ai-advisor/suggest echoed an internal filename from the exception. "
        f"Response body excerpt: {body_text[:300]!r}"
    )


# ---------------------------------------------------------------------------
# D-1 RED-4: generic error message must be a non-empty string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route,method,payload", [
    ("/api/settings", "POST", {"globals": {}, "symphonies": {"x": {"params": {"BAD": "x"}, "locked_vars": []}}}),
    ("/api/logs/nonexistent_symphony_999", "GET", None),
])
def test_error_response_contains_generic_non_empty_message(
    route, method, payload, client, monkeypatch
):
    """On exception, routes must return a non-empty generic message string.

    The fix must not replace str(e) with an empty string or null — a blank
    error gives the operator no indication of what happened.  The message
    must be a non-empty human-readable string that does NOT contain internal
    implementation detail.

    Acceptable: "Settings save failed", "Internal error", "Request failed"
    Not acceptable: "" (empty), null, or any verbatim exception text.
    """
    from unittest.mock import MagicMock
    db_mock = MagicMock()
    db_mock.save_symphony_strategy.side_effect = RuntimeError("INJECTED_INTERNAL_DETAIL")
    db_mock.get_symphony_logs.side_effect = RuntimeError("INJECTED_INTERNAL_DETAIL")
    db_mock.get_ro_connection.return_value = MagicMock()
    db_mock.load_state.return_value = {}
    monkeypatch.setattr(app_module, "database", db_mock)
    monkeypatch.setattr(app_module, "set_key", lambda *a, **kw: (True, a[1], a[2]))
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", "/fake/.env")
    from unittest.mock import patch
    with patch.object(app_module, "dotenv_values", return_value={"LIVE_EXECUTION": "False"}):
        if method == "POST":
            resp = client.post(route, json=payload)
        else:
            resp = client.get(route)

    body = resp.get_json()
    body_text = resp.get_data(as_text=True)

    # The response must be a JSON object with an error key.
    assert isinstance(body, dict), (
        f"{method} {route}: error response must be JSON object; got {type(body)}: {body_text[:200]!r}"
    )

    # Must have either "message" or "error" key with a non-empty value.
    error_val = body.get("message") or body.get("error")
    assert error_val, (
        f"{method} {route}: error response must include a non-empty 'message' or 'error' field. "
        f"Got: {body!r}"
    )
    assert isinstance(error_val, str) and len(error_val.strip()) > 0, (
        f"{method} {route}: error message must be a non-empty string. Got: {error_val!r}"
    )

    # Must not echo the injected internal detail.
    assert "INJECTED_INTERNAL_DETAIL" not in body_text, (
        f"{method} {route}: raw exception text must not appear in response. "
        f"Body: {body_text[:300]!r}"
    )
