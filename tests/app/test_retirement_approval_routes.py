"""RED tests -- POST /ai-advisor/retirement/approve and /reject (AC-5).

feature-plans/retirement-approval-lifecycle.md AC-5: two retirement-SPECIFIC
routes (NOT the frontrunner /ai-advisor/proposal/approve, which reaches
save_symphony). Each parses {candidate_id: str}, writes the status via
database.upsert_retirement_decision(...), returns {success, approval_status,
error}. CSRF/auth via the global hooks; D-1 (type(exc).__name__, 200); NOT
in _SETTINGS_WRITE_ALLOWLIST; reach NO Composer/exec/LIVE_EXECUTION/trade
primitive. Approve writes status ONLY -- it does not itself render or
execute the checklist.

Fixture pattern (client/auth_client/_reenable_csrf/_get_csrf_token) copied
verbatim from tests/app/test_frontrunner_builder_route.py -- that file's own
docstring establishes fixtures are not cross-file-shared in this repo.

Expected state: RED until app.py gains ai_advisor_retirement_approve /
ai_advisor_retirement_reject (pinned names, .claude/tdd-handoff.md).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import app as app_module

_APPROVE_URL = "/ai-advisor/retirement/approve"
_REJECT_URL = "/ai-advisor/retirement/reject"


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture(autouse=False)
def _reenable_csrf(monkeypatch):
    monkeypatch.setattr(app_module, "_csrf_check_enabled", True)


@pytest.fixture(autouse=False)
def auth_client(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-pass-retirement-route-abc123")
    monkeypatch.setenv("SECRET_KEY", "test-secret-retirement-route-xyz789")
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _assert_route_exists(resp, route: str) -> None:
    assert resp.status_code != 404, (
        f"{route} returned 404 -- the route has not been added to app.py yet."
    )


def _get_csrf_token(client) -> str:
    token_resp = client.get("/api/csrf-token")
    assert token_resp.status_code == 200
    token = token_resp.get_json().get("csrf_token")
    assert token
    return token


# ===========================================================================
# Group A: POST /ai-advisor/retirement/approve -- CSRF enforcement
# ===========================================================================


def test_approve_without_csrf_token_returns_403(client, _reenable_csrf):
    resp = client.post(
        _APPROVE_URL, json={"candidate_id": "cand-1"}, content_type="application/json"
    )
    _assert_route_exists(resp, _APPROVE_URL)
    assert resp.status_code == 403


def test_approve_with_wrong_csrf_token_returns_403(client, _reenable_csrf):
    import secrets

    resp = client.post(
        _APPROVE_URL,
        json={"candidate_id": "cand-1"},
        content_type="application/json",
        headers={"X-CSRF-Token": secrets.token_hex(32)},
    )
    _assert_route_exists(resp, _APPROVE_URL)
    assert resp.status_code == 403


def test_approve_with_valid_csrf_token_returns_200(client, _reenable_csrf):
    token = _get_csrf_token(client)
    with patch("database.upsert_retirement_decision", return_value=True):
        resp = client.post(
            _APPROVE_URL,
            json={"candidate_id": "cand-1"},
            content_type="application/json",
            headers={"X-CSRF-Token": token},
        )
    _assert_route_exists(resp, _APPROVE_URL)
    assert resp.status_code == 200


# ===========================================================================
# Group B: POST /ai-advisor/retirement/approve -- auth + response shape + D-1
# ===========================================================================


def test_approve_requires_authentication(auth_client):
    with patch("database.upsert_retirement_decision") as mock_upsert:
        resp = auth_client.post(
            _APPROVE_URL, json={"candidate_id": "cand-1"}, content_type="application/json"
        )
    _assert_route_exists(resp, _APPROVE_URL)
    assert resp.status_code in (302, 401)
    mock_upsert.assert_not_called()


def test_approve_writes_approved_status_and_returns_it(client):
    with patch("database.upsert_retirement_decision", return_value=True) as mock_upsert:
        resp = client.post(
            _APPROVE_URL, json={"candidate_id": "cand-42"}, content_type="application/json"
        )
    _assert_route_exists(resp, _APPROVE_URL)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    assert data.get("success") is True
    assert data.get("approval_status") == "approved"

    mock_upsert.assert_called_once()
    _, call_kwargs = mock_upsert.call_args
    args = mock_upsert.call_args[0]
    assert "cand-42" in args or call_kwargs.get("candidate_id") == "cand-42"
    assert call_kwargs.get("approval_status") == "approved"


def test_approve_missing_candidate_id_returns_invalid_error_without_calling_the_db(client):
    with patch("database.upsert_retirement_decision") as mock_upsert:
        resp = client.post(_APPROVE_URL, json={}, content_type="application/json")
    _assert_route_exists(resp, _APPROVE_URL)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    assert data.get("success") is False
    assert data.get("error")
    mock_upsert.assert_not_called()


def test_approve_engine_error_returns_static_error_token_never_str_exc(client):
    secret_bearing_message = "sqlite write failed: path=C:\\secret\\db.sqlite token=xyz789"
    with patch(
        "database.upsert_retirement_decision", side_effect=RuntimeError(secret_bearing_message)
    ):
        resp = client.post(
            _APPROVE_URL, json={"candidate_id": "cand-1"}, content_type="application/json"
        )
    _assert_route_exists(resp, _APPROVE_URL)
    data = resp.get_json()
    assert data is not None
    assert "xyz789" not in str(data)
    assert "secret" not in str(data).lower()
    assert data.get("error") == "RuntimeError"


def test_approve_response_never_contains_live_execution_key(client):
    with patch("database.upsert_retirement_decision", return_value=True):
        resp = client.post(
            _APPROVE_URL, json={"candidate_id": "cand-1"}, content_type="application/json"
        )
    _assert_route_exists(resp, _APPROVE_URL)
    assert "LIVE_EXECUTION" not in str(resp.get_json())


def test_approve_never_touches_composer_draft_client(client):
    with (
        patch("database.upsert_retirement_decision", return_value=True),
        patch("advisors.composer_draft_client.save_symphony") as mock_save,
        patch("advisors.composer_draft_client.verify_undeployed") as mock_verify,
    ):
        client.post(_APPROVE_URL, json={"candidate_id": "cand-1"}, content_type="application/json")
    mock_save.assert_not_called()
    mock_verify.assert_not_called()


def test_approve_never_calls_the_llm_client_seam(client):
    """Companion runtime-mock belt-and-suspenders to the static AST
    call-graph proof in tests/security/test_retirement_action_no_trade_
    boundary.py's TestApproveRejectRoutesNeverReachLlmOrComposerDraftClient
    (review finding, 2026-08-26, ret2-review) -- mirrors this file's own
    existing composer_draft_client mock-assertion pattern immediately
    above, applied to the LLM seam instead."""
    with (
        patch("database.upsert_retirement_decision", return_value=True),
        patch("ai_advisor._build_client") as mock_build_client,
    ):
        client.post(_APPROVE_URL, json={"candidate_id": "cand-1"}, content_type="application/json")
    mock_build_client.assert_not_called()


def test_approve_is_idempotent_on_repeat_calls(client):
    """AC-3/AC-5: re-approving an already-decided candidate is a no-op
    UPSERT, never an error."""
    with patch("database.upsert_retirement_decision", return_value=True) as mock_upsert:
        resp1 = client.post(
            _APPROVE_URL, json={"candidate_id": "cand-repeat"}, content_type="application/json"
        )
        resp2 = client.post(
            _APPROVE_URL, json={"candidate_id": "cand-repeat"}, content_type="application/json"
        )
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.get_json().get("success") is True
    assert resp2.get_json().get("success") is True
    assert mock_upsert.call_count == 2


def test_approve_does_not_itself_render_or_build_the_checklist():
    """AC-5: 'Approve writes status ONLY -- it does not itself render or
    execute the checklist.' The checklist is assembled at render time
    (app.py's ai_advisor_tab), not inside the approve route handler."""
    with (
        patch("database.upsert_retirement_decision", return_value=True),
        patch("advisors.retirement_checklist.build_checklist") as mock_checklist,
    ):
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            c.post(_APPROVE_URL, json={"candidate_id": "cand-1"}, content_type="application/json")
    mock_checklist.assert_not_called()


# ===========================================================================
# Group C: POST /ai-advisor/retirement/reject
# ===========================================================================


def test_reject_without_csrf_token_returns_403(client, _reenable_csrf):
    resp = client.post(
        _REJECT_URL, json={"candidate_id": "cand-1"}, content_type="application/json"
    )
    _assert_route_exists(resp, _REJECT_URL)
    assert resp.status_code == 403


def test_reject_with_valid_csrf_token_returns_200(client, _reenable_csrf):
    token = _get_csrf_token(client)
    with patch("database.upsert_retirement_decision", return_value=True):
        resp = client.post(
            _REJECT_URL,
            json={"candidate_id": "cand-1"},
            content_type="application/json",
            headers={"X-CSRF-Token": token},
        )
    _assert_route_exists(resp, _REJECT_URL)
    assert resp.status_code == 200


def test_reject_requires_authentication(auth_client):
    with patch("database.upsert_retirement_decision") as mock_upsert:
        resp = auth_client.post(
            _REJECT_URL, json={"candidate_id": "cand-1"}, content_type="application/json"
        )
    _assert_route_exists(resp, _REJECT_URL)
    assert resp.status_code in (302, 401)
    mock_upsert.assert_not_called()


def test_reject_writes_rejected_status_and_returns_it(client):
    with patch("database.upsert_retirement_decision", return_value=True) as mock_upsert:
        resp = client.post(
            _REJECT_URL, json={"candidate_id": "cand-99"}, content_type="application/json"
        )
    _assert_route_exists(resp, _REJECT_URL)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("success") is True
    assert data.get("approval_status") == "rejected"

    _, call_kwargs = mock_upsert.call_args
    assert call_kwargs.get("approval_status") == "rejected"


def test_reject_missing_candidate_id_returns_invalid_error_without_touching_the_db(client):
    with patch("database.upsert_retirement_decision") as mock_upsert:
        resp = client.post(_REJECT_URL, json={}, content_type="application/json")
    _assert_route_exists(resp, _REJECT_URL)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("success") is False
    assert data.get("error")
    mock_upsert.assert_not_called()


def test_reject_engine_error_returns_static_error_token_never_str_exc(client):
    with patch(
        "database.upsert_retirement_decision",
        side_effect=RuntimeError("internal db path leaked: C:\\Users\\operator\\secret.db"),
    ):
        resp = client.post(
            _REJECT_URL, json={"candidate_id": "cand-1"}, content_type="application/json"
        )
    _assert_route_exists(resp, _REJECT_URL)
    data = resp.get_json()
    assert "secret.db" not in str(data)
    assert data.get("error") == "RuntimeError"


def test_reject_never_touches_composer_draft_client(client):
    with (
        patch("database.upsert_retirement_decision", return_value=True),
        patch("advisors.composer_draft_client.save_symphony") as mock_save,
        patch("advisors.composer_draft_client.verify_undeployed") as mock_verify,
    ):
        client.post(_REJECT_URL, json={"candidate_id": "cand-1"}, content_type="application/json")
    mock_save.assert_not_called()
    mock_verify.assert_not_called()


def test_reject_never_calls_the_llm_client_seam(client):
    """Companion runtime-mock belt-and-suspenders — see test_approve_never_
    calls_the_llm_client_seam's docstring above for the full rationale."""
    with (
        patch("database.upsert_retirement_decision", return_value=True),
        patch("ai_advisor._build_client") as mock_build_client,
    ):
        client.post(_REJECT_URL, json={"candidate_id": "cand-1"}, content_type="application/json")
    mock_build_client.assert_not_called()


def test_reject_response_never_contains_live_execution_key(client):
    with patch("database.upsert_retirement_decision", return_value=True):
        resp = client.post(
            _REJECT_URL, json={"candidate_id": "cand-1"}, content_type="application/json"
        )
    assert "LIVE_EXECUTION" not in str(resp.get_json())


# ===========================================================================
# Group D: settings-write-allowlist exclusion (cheap redundant sweep --
# authoritative structural coverage lives in
# tests/security/test_retirement_action_no_trade_boundary.py)
# ===========================================================================


def test_settings_write_allowlist_does_not_contain_retirement_routes_or_live_execution():
    allowlist = app_module._SETTINGS_WRITE_ALLOWLIST
    forbidden = {"retirement", _APPROVE_URL, _REJECT_URL, "LIVE_EXECUTION", "candidate_id"}
    hit = forbidden & set(allowlist)
    assert not hit, f"_SETTINGS_WRITE_ALLOWLIST must not contain any of {forbidden}. Found: {hit}"
